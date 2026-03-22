"""
Direct backtest runner - bypasses typer/click entirely.
Usage: python run_direct.py [start] [end] [llm_profile] [llm_model] [news_enabled] [decision_space_mode]
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


def _parse_optional_bool(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "":
        return None
    return text not in {"false", "0", "no", "none", "off"}


def _parse_optional_decision_space_mode(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "":
        return None
    if text not in {"continuous", "discrete_target_state"}:
        raise ValueError(
            f"Unsupported decision_space.mode '{value}'. "
            "Expected one of: continuous, discrete_target_state"
        )
    return text


# ── Config ──────────────────────────────────────────────────────────────────
START      = sys.argv[1] if len(sys.argv) > 1 else "2025-03-03"
END        = sys.argv[2] if len(sys.argv) > 2 else "2025-03-14"
LLM_PROFILE = sys.argv[3] if len(sys.argv) > 3 else "openrouter"
LLM_MODEL  = sys.argv[4] if len(sys.argv) > 4 else os.getenv("LLM_MODEL", "")
NEWS_ENABLED = _parse_optional_bool(sys.argv[5] if len(sys.argv) > 5 else os.getenv("NEWS_ENABLED"))
DECISION_SPACE_MODE = _parse_optional_decision_space_mode(
    sys.argv[6] if len(sys.argv) > 6 else os.getenv("DECISION_SPACE_MODE")
)
CFG_PATH   = os.path.join(os.path.dirname(__file__), "config.yaml")

with open(CFG_PATH, "r") as f:
    cfg = yaml.safe_load(f)

# Apply data mode from config
data_mode = (cfg.get("data", {}) or {}).get("mode", "auto")
set_data_mode(data_mode)

if NEWS_ENABLED is not None:
    cfg.setdefault("news", {})["enabled"] = NEWS_ENABLED
    print(f"[INFO] News enabled: {NEWS_ENABLED}")

if DECISION_SPACE_MODE is not None:
    cfg.setdefault("decision_space", {})["mode"] = DECISION_SPACE_MODE
    print(f"[INFO] Decision space mode: {DECISION_SPACE_MODE}")

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
effective_model = cfg.get("llm", {}).get("model", "")
effective_decision_mode = str(
    ((cfg.get("decision_space") or {}).get("mode") or "continuous")
).strip().lower()
run_id_parts = []
model_slug = re.sub(r"[^A-Za-z0-9]+", "_", effective_model).strip("_").upper()
if model_slug:
    run_id_parts.append(model_slug)
if effective_decision_mode and effective_decision_mode != "continuous":
    run_id_parts.append(effective_decision_mode.upper())
run_id_parts.extend([START, END])
run_id = "_".join(run_id_parts).replace("-", "")
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
