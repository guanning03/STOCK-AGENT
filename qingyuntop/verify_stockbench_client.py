#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    project_root = repo_root / "stockbench"

    os.chdir(project_root)
    sys.path.insert(0, str(project_root))

    from stockbench.llm.llm_client import LLMClient, LLMConfig

    with (project_root / "config.yaml").open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    raw = cfg["llm_profiles"]["qingyuntop"]
    llm_cfg = LLMConfig(
        provider=str(raw.get("provider", "openai")),
        base_url=str(raw.get("base_url", "https://api.openai.com/v1")),
        model=str(raw.get("model", "deepseek-v3.1")),
        temperature=0.0,
        max_tokens=80,
        timeout_sec=float(raw.get("timeout_sec", 60)),
        max_retries=0,
        backoff_factor=float(raw.get("retry", {}).get("backoff_factor", 0.5)),
        cache_enabled=False,
        auth_required=raw.get("auth_required"),
        api_key_env=str(raw.get("api_key_env", "OPENAI_API_KEY")),
    )

    client = LLMClient()
    result, meta = client.generate_json(
        role="smoke_test",
        cfg=llm_cfg,
        system_prompt="Reply with a one-line JSON object.",
        user_prompt='Return {"status":"ok","source":"stockbench-llm-client"}',
    )

    print(json.dumps({"result": result, "meta": meta}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
