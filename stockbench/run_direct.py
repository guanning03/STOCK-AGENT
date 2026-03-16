"""
Direct backtest runner - bypasses typer/click entirely.
Usage: python run_direct.py [start] [end] [llm_profile] [llm_model]
"""
import sys
import os
import re
import yaml

# Ensure package is importable
sys.path.insert(0, os.path.dirname(__file__))

from stockbench.backtest.pipeline import run_backtest
from stockbench.backtest.reports import resolve_run_id
from stockbench.backtest.strategies.llm_decision import Strategy as LlmDecision
from stockbench.core.data_hub import set_data_mode

# ── Config ──────────────────────────────────────────────────────────────────
START      = sys.argv[1] if len(sys.argv) > 1 else "2025-03-03"
END        = sys.argv[2] if len(sys.argv) > 2 else "2025-03-14"
LLM_PROFILE = sys.argv[3] if len(sys.argv) > 3 else "openrouter"
LLM_MODEL  = sys.argv[4] if len(sys.argv) > 4 else os.getenv("LLM_MODEL", "")
CFG_PATH   = os.path.join(os.path.dirname(__file__), "config.yaml")

with open(CFG_PATH, "r") as f:
    cfg = yaml.safe_load(f)

# Apply data mode from config
data_mode = (cfg.get("data", {}) or {}).get("mode", "auto")
set_data_mode(data_mode)

# Apply LLM profile
profiles = cfg.get("llm_profiles", {})
if LLM_PROFILE in profiles:
    cfg["llm"] = profiles[LLM_PROFILE]
    print(f"[INFO] Using LLM profile: {LLM_PROFILE} → model={cfg['llm'].get('model')}")
else:
    print(f"[ERROR] LLM profile '{LLM_PROFILE}' not found in config. Available: {list(profiles.keys())}")
    sys.exit(1)

if LLM_MODEL:
    cfg["llm"]["model"] = LLM_MODEL
    cfg["llm"]["backtest_report_model"] = LLM_MODEL
    if "fundamental_filter_model" in cfg["llm"]:
        cfg["llm"]["fundamental_filter_model"] = LLM_MODEL
    if "decision_agent_model" in cfg["llm"]:
        cfg["llm"]["decision_agent_model"] = LLM_MODEL
    print(f"[INFO] Overriding profile model with: {LLM_MODEL}")

symbols = cfg.get("symbols_universe", [])
run_id = f"{LLM_PROFILE.upper()}_{START}_{END}".replace("-", "")
if LLM_MODEL:
    model_slug = re.sub(r"[^A-Za-z0-9]+", "_", LLM_MODEL).strip("_").upper()
    if model_slug:
        run_id = f"{LLM_PROFILE.upper()}_{model_slug}_{START}_{END}".replace("-", "")
run_id = resolve_run_id(run_id=run_id, cfg=cfg)

print(f"[INFO] Backtest: {START} → {END}, {len(symbols)} symbols, run_id={run_id}")

strategy = LlmDecision(cfg)
result = run_backtest(cfg, strategy, START, END, symbols, run_id=run_id)

metrics = result.get("metrics", {})
print("\n=== Results ===")
print(f"  cum_return  : {metrics.get('cum_return', 0):.4f}")
print(f"  max_drawdown: {metrics.get('max_drawdown', 0):.4f}")
print(f"  sortino     : {metrics.get('sortino', 0):.4f}")
print(f"  trades_count: {metrics.get('trades_count', 0)}")
print(f"  output_dir  : {result.get('output_dir', 'N/A')}")
