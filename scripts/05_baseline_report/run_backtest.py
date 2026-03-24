"""
Run a baseline backtest from the standalone baseline-report directory.

This wrapper keeps future baseline reruns separate from the historical
experiments and supports a dedicated W&B project via environment variables.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
STOCKBENCH_DIR = ROOT_DIR / "stockbench"
sys.path.insert(0, str(STOCKBENCH_DIR))

from stockbench.backtest.pipeline import run_backtest  # noqa: E402
from stockbench.backtest.reports import resolve_run_id  # noqa: E402
from stockbench.backtest.strategies.llm_decision import Strategy as LlmDecision  # noqa: E402
from stockbench.core.data_hub import set_data_mode  # noqa: E402


def _parse_optional_bool(value: str | None):
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text not in {"false", "0", "no", "none", "off"}


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-03-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-02-28"
    llm_profile = sys.argv[3] if len(sys.argv) > 3 else "qingyuntop"
    llm_model = sys.argv[4] if len(sys.argv) > 4 else os.getenv("LLM_MODEL", "")
    news_enabled = _parse_optional_bool(
        sys.argv[5] if len(sys.argv) > 5 else os.getenv("NEWS_ENABLED")
    )

    cfg_path = STOCKBENCH_DIR / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)

    data_mode = (cfg.get("data", {}) or {}).get("mode", "auto")
    set_data_mode(data_mode)

    if news_enabled is not None:
        cfg.setdefault("news", {})["enabled"] = news_enabled
        print(f"[INFO] News enabled: {news_enabled}")

    profiles = cfg.get("llm_profiles", {})
    if llm_profile not in profiles:
        print(
            f"[ERROR] LLM profile '{llm_profile}' not found. "
            f"Available: {sorted(profiles.keys())}"
        )
        return 1

    cfg["llm"] = profiles[llm_profile]
    print(f"[INFO] Using LLM profile: {llm_profile}")

    if llm_model:
        cfg["llm"]["model"] = llm_model
        cfg["llm"]["backtest_report_model"] = llm_model
        if "fundamental_filter_model" in cfg["llm"]:
            cfg["llm"]["fundamental_filter_model"] = llm_model
        if "decision_agent_model" in cfg["llm"]:
            cfg["llm"]["decision_agent_model"] = llm_model
        print(f"[INFO] Overriding model: {llm_model}")

    wandb_project = os.getenv("WANDB_PROJECT", "").strip()
    wandb_entity = os.getenv("WANDB_ENTITY", "").strip()
    if wandb_project or wandb_entity:
        wb_cfg = cfg.setdefault("wandb", {})
        if wandb_project:
            wb_cfg["project"] = wandb_project
            print(f"[INFO] W&B project override: {wandb_project}")
        if wandb_entity:
            wb_cfg["entity"] = wandb_entity
            print(f"[INFO] W&B entity override: {wandb_entity}")

    effective_model = str((cfg.get("llm") or {}).get("model") or "")
    model_slug = re.sub(r"[^A-Za-z0-9]+", "_", effective_model).strip("_").upper()
    run_id = "_".join(part for part in [model_slug, start, end] if part)
    run_id = run_id.replace("-", "")
    run_id = resolve_run_id(run_id=run_id, cfg=cfg)

    symbols = cfg.get("symbols_universe", [])
    print(
        f"[INFO] Backtest: {start} -> {end}, "
        f"{len(symbols)} symbols, run_id={run_id}"
    )

    strategy = LlmDecision(cfg)
    result = run_backtest(cfg, strategy, start, end, symbols, run_id=run_id)

    metrics = result.get("metrics", {})
    print("\n=== Results ===")
    print(f"  cum_return  : {metrics.get('cum_return', 0):.4f}")
    print(f"  max_drawdown: {metrics.get('max_drawdown', 0):.4f}")
    print(f"  sortino     : {metrics.get('sortino', 0):.4f}")
    print(f"  trades_count: {metrics.get('trades_count', 0)}")
    print(f"  output_dir  : {result.get('output_dir', 'N/A')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
