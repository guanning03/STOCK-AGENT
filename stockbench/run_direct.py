"""
Direct backtest runner - bypasses typer/click entirely.
Usage: python run_direct.py [start] [end] [llm_profile]
"""
import sys
import os
import yaml

# Ensure package is importable
sys.path.insert(0, os.path.dirname(__file__))

from stockbench.backtest.pipeline import run_backtest
from stockbench.backtest.strategies.llm_decision import Strategy as LlmDecision
from stockbench.core.data_hub import set_data_mode

# ── Config ──────────────────────────────────────────────────────────────────
START      = sys.argv[1] if len(sys.argv) > 1 else "2025-03-03"
END        = sys.argv[2] if len(sys.argv) > 2 else "2025-03-14"
LLM_PROFILE = sys.argv[3] if len(sys.argv) > 3 else "openrouter"
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

symbols = cfg.get("symbols_universe", [])
run_id = f"{LLM_PROFILE.upper()}_{START}_{END}".replace("-", "")

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
