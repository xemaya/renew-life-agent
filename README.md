# renew-life-agent · 人生重启

A2H Market shop agent delivering Chinese four-pillar bazi (四柱八字)
analysis. Implements **Agent Protocol v2** — a long-running HTTP service
that talks to the buyer through the shop page's `<a2h-chatbox>` component.

## Files

- `agent.yaml` — v2 platform manifest (sandbox runtime, worker pool
  defaults, tool allowlist)
- `server.py` — FastAPI app implementing `POST /chat` + `GET /health`
- `BAZI_SYSTEM_PROMPT.md` — ~6k-char classical reference prompt the
  model reads as its system message
- `Dockerfile` — extends `a2h/agent-base:python-3.12-http`

## What it does

1. Collects birth info (name / date / time / gender / birthplace) via
   natural conversation
2. Confirms the info with the buyer, emits `[INFO_CONFIRMED]` marker
3. Surfaces an `open_order` UI directive so the buyer can pay for the
   deeper reading
4. After `payment.succeeded` system event → acknowledges and (Phase 2)
   kicks off async delivery of the full bazi analysis

## Submit to a shop

```bash
a2h-shopdiy agent:submit \
  --shop <shopId> \
  --source https://github.com/xemaya/renew-life-agent.git \
  --version 2.0.0
```

Platform runs CodeBuild → pushes the image to ECR → starts
`pool.initial_workers` workers. Buyers land on the shop page and begin
chatting.

## Local dry-run

```bash
# From kit-v2/:
cd kit-v2/agent-sdk && python3 -m venv .venv && .venv/bin/pip install -e .

# Then in this repo:
cd renew-life-agent
python3 -m venv .venv
.venv/bin/pip install ../../diy_shop/kit-v2/agent-sdk \
                      fastapi uvicorn boto3 anthropic
export AWS_REGION=us-east-1
export A2H_TOKEN=dummy
export A2H_SHOP_ID=76
.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8080

# In another terminal:
curl -N -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "sess-local",
    "shop_id": 76,
    "works_id": "d3a7599998894d5a840353842dbe42797f2f",
    "buyer": {"id": "u-test", "nickname": "测试买家"},
    "history": [],
    "message": {"text": "我想算八字"}
  }'
```

## Version history

- **2.0.0** (2026-04-17) — rewrite for Agent Protocol v2's Worker Pool +
  chatbox HTTP model. FastAPI service + streamed SSE. The old
  `runtime.type: llm` + `/workspace/input.json` envelope model is gone.
- **1.x** — legacy runtime.type:llm variant (deprecated).

## License

MIT. Classical reference tables are public domain.
