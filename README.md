# renew-life-agent · 人生重启

An A2H Market agent that delivers Chinese four-pillar bazi (四柱八字) analysis
backed by classical texts. Zero-code — a single `agent.yaml` ships the whole
thing under Agent Protocol v2's `runtime.type: llm` path (Bedrock Claude
Sonnet 4.6).

## What it does

1. Collects 5 pieces of info (name, birth date/time, gender, birthplace) via
   natural conversation — accepts batch or incremental answers.
2. Builds the four-pillar chart (年/月/日/时柱), ten gods (十神), hidden
   stems (藏干), luck pillars (大运).
3. Runs comprehensive analysis: 日主旺衰, 格局, 喜用神, 流年运势, with
   citations from 穷通宝典 / 三命通会 / 滴天髓 / 子平真诠 / 神峰通考.

## Submit to a shop

```bash
a2h-shopdiy agent:submit \
  --shop <shopId> \
  --source https://github.com/xemaya/renew-life-agent.git \
  --version 1.0.0
```

Then `a2h-shopdiy agent:activate <packageId> --shop <shopId>` once build
reaches `ready`.

## License

MIT. Classical reference tables are in the public domain.
