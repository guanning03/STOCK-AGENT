# Qingyuntop Notes

Last verified: `2026-03-16`

This directory tracks the Qingyuntop gateway settings we use for `STOCK-AGENT`.

## Current Integration

- Env var: `QINGYUNTOP_API_KEY`
- Base URL: `https://api.qingyuntop.top/v1`
- Default profile in this repo: `qingyuntop`
- Default model in this repo: `deepseek-v3.1`

The repo now supports profile-specific API key env vars through `llm.api_key_env`, so Qingyuntop can coexist with other OpenAI-compatible providers.

## Files

- `smoke_test.py`: minimal connectivity test for `/v1/models` and `/chat/completions`
- `models_openai.txt`: snapshot of model IDs that advertised OpenAI-compatible endpoints when we queried `/v1/models`
- `models_snapshot.json`: raw `/v1/models` snapshot from Qingyuntop

## Quick Checks

Activate the environment first:

```bash
conda activate stockagent
```

List Qingyuntop models:

```bash
python qingyuntop/smoke_test.py --list-models
```

Refresh the local model snapshot files:

```bash
python qingyuntop/smoke_test.py --list-models --write-files
```

Send a minimal chat request:

```bash
python qingyuntop/smoke_test.py --model deepseek-v3.1
```

Verify the repository's own `LLMClient` path:

```bash
python qingyuntop/verify_stockbench_client.py
```

Run the project with the Qingyuntop profile:

```bash
cd stockbench
python -m stockbench.apps.run_backtest \
  --cfg config.yaml \
  --start 2025-03-01 \
  --end 2025-03-05 \
  --llm-profile qingyuntop \
  --offline true
```

## Official References

- Docs: `https://qingyuntop.apifox.cn/`
- Pricing / models page: `https://api.qingyuntop.top/pricing`
