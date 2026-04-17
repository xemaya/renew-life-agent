"""renew-life-agent — a2hmarket shop agent for 四柱八字 (bazi) analysis.

Implements Agent Protocol v2 (see docs/agent-protocol-v2.md in the
diy_shop repo):

* ``POST /chat`` — streams a Claude response back to the chatbox as
  Server-Sent Events, optionally appending a ``ui.open_order`` directive
  once the buyer has confirmed their birth info.
* ``GET /health`` — liveness probe for the worker pool.

Everything else — intent detection, information gathering, dispatching to
the LLM, deciding when to surface the order CTA — happens right here. The
agent is stateless: chatbox sends ``history[]`` on every request, we read
from it, we write output, we're done.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from a2h_agent import ChatRequest, done, error, text, ui


LOG = logging.getLogger("renew_life")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ─── Configuration ──────────────────────────────────────────────────────


MODEL_ID = os.environ.get("A2H_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
MAX_TOKENS_PER_TURN = int(os.environ.get("MAX_TOKENS_PER_TURN", "3000"))

# The full bazi system prompt lives in a sibling Markdown file. It's 6k chars
# of prompt + classical reference tables (天干五合 / 地支六冲 / 十神推导 /
# 十二长生 / 六十甲子 / 穷通宝典 etc.) — keeping it as Markdown lets anyone
# (including Claude Code) read / edit without wading through Python string
# escapes.
SYSTEM_PROMPT = (Path(__file__).parent / "BAZI_SYSTEM_PROMPT.md").read_text(encoding="utf-8")

# How many prior turns to feed Claude. The 10-turn cap keeps input tokens
# bounded; the bazi prompt itself is already ~3k tokens and long histories
# on top blow up context.
HISTORY_WINDOW = 10

# Marker the prompt instructs the model to emit once it has confirmed info.
# When we see it in the last assistant turn, we know the next buyer reply
# triggers analysis — a convenient place to ALSO push an `open_order` CTA.
INFO_CONFIRMED = "[INFO_CONFIRMED]"


# ─── Bedrock client (lazy) ──────────────────────────────────────────────


_bedrock_lock = asyncio.Lock()
_bedrock_client = None


async def bedrock_client():
    """Lazy-init the bedrock-runtime client. Single instance per worker."""
    global _bedrock_client
    async with _bedrock_lock:
        if _bedrock_client is None:
            _bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_client


# ─── FastAPI app ────────────────────────────────────────────────────────


app = FastAPI(title="renew-life-agent", version="2.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    """Worker pool probe. Stays cheap — no Bedrock calls here."""
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: Request) -> StreamingResponse:
    body = await request.json()
    req = ChatRequest.from_json(body)
    LOG.info("chat session=%s shop=%s event=%s text=%r",
             req.session_id, req.shop_id, req.event_type, req.text[:80])

    async def stream():
        try:
            # System events (payment.succeeded, session.resumed…) don't carry
            # user text — agent can acknowledge and, for renew-life, kick off
            # the deeper delivery workflow. MVP response: a short reply that
            # tells the buyer we're on it.
            if req.is_event:
                async for frame in handle_event(req):
                    yield frame
                return

            # Normal user message → stream Claude.
            async for frame in stream_claude(req):
                yield frame

            # First-turn heuristic: if the model just emitted [INFO_CONFIRMED],
            # surface the order CTA so the buyer can immediately pay for the
            # deeper reading. We sniff the assistant's own reply (it's in
            # history on the next turn) rather than hardcoding "any first turn".
            last_assistant_text = _last_assistant_text(req.history)
            if INFO_CONFIRMED in last_assistant_text and req.works_id:
                yield ui(
                    "open_order",
                    works_id=req.works_id,
                    title="完整八字命盘分析",
                )

            yield done()
        except Exception:  # noqa: BLE001 — last-resort protocol-safe error
            LOG.exception("chat handler crashed")
            yield error("INTERNAL_ERROR", "主理人临时开小差，请稍后再试")
            yield done()

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── Streaming Claude ──────────────────────────────────────────────────


async def stream_claude(req: ChatRequest):
    """Build Anthropic messages from the envelope, call Bedrock's Converse
    streaming API, forward every text delta as an SSE ``text`` event."""
    import json as _json

    messages = req.anthropic_messages()
    # Keep the tail — older context is drowned out by the long system prompt anyway.
    if len(messages) > HISTORY_WINDOW:
        messages = messages[-HISTORY_WINDOW:]

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_TOKENS_PER_TURN,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
    }

    client = await bedrock_client()

    def _invoke_stream():
        # boto3's bedrock-runtime streaming is sync; run in a thread and
        # marshal chunks back to the async generator.
        return client.invoke_model_with_response_stream(
            modelId=MODEL_ID,
            body=_json.dumps(body).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )

    try:
        resp = await asyncio.to_thread(_invoke_stream)
    except Exception as ex:  # noqa: BLE001
        LOG.error("bedrock invoke_model_with_response_stream failed: %s", ex)
        yield error("LLM_UNAVAILABLE", f"模型服务暂时不可用：{ex.__class__.__name__}")
        return

    event_stream = resp["body"]

    def _drain():
        """Yield decoded event dicts (sync generator)."""
        for ev in event_stream:
            chunk = ev.get("chunk")
            if chunk is None:
                continue
            try:
                yield _json.loads(chunk["bytes"].decode("utf-8"))
            except Exception:  # noqa: BLE001
                continue

    # Hop chunks between threads one at a time. A proper production agent would
    # use aioboto3; here we prioritise readability.
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _producer():
        try:
            for payload in _drain():
                asyncio.run_coroutine_threadsafe(queue.put(payload), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    producer = asyncio.create_task(asyncio.to_thread(_producer))

    while True:
        payload = await queue.get()
        if payload is None:
            break
        etype = payload.get("type")
        if etype == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                yield text(delta["text"])
        # ignore message_start / message_delta / message_stop — chatbox doesn't
        # need them; usage is reported via the final done() event.

    await producer


# ─── System event handlers ─────────────────────────────────────────────


async def handle_event(req: ChatRequest):
    """Respond to platform-pushed system events.

    Only ``payment.succeeded`` is wired up for now. Everything else becomes
    a friendly no-op so the chatbox stays responsive.
    """
    etype = req.event_type or ""
    order_id = str((req.event or {}).get("order_id") or "")

    if etype == "payment.succeeded":
        yield text(
            "收到付款！我会用心为您排一份完整的命盘，生成好后通过对话窗口发您。"
        )
        if order_id:
            yield text(f"\n\n订单号：`{order_id}`，请留存。")
        # Phase 2 enhancement: kick off an async job to generate the PDF /
        # audio reading and push a `ui.show_file` event via session.resumed.
        # For now, the subsequent user turns will pull the analysis live.

    elif etype == "session.resumed":
        yield text("欢迎回来。需要继续之前的排盘，还是重新来一份？")

    else:
        LOG.info("ignoring unhandled event: %s", etype)

    yield done()


# ─── Helpers ────────────────────────────────────────────────────────────


def _last_assistant_text(history: list[dict]) -> str:
    """Return the last agent turn's text, or empty string.

    The chatbox sends history oldest-first, so we scan from the end.
    """
    for h in reversed(history or []):
        if h.get("role") == "agent":
            return str(h.get("text") or "")
    return ""
