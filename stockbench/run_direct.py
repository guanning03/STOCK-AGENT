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


def _parse_optional_int(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return int(text)


def _parse_optional_trading_frequency(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "":
        return None
    aliases = {
        "daily": "daily",
        "every2": "every_2_trading_days",
        "every_2": "every_2_trading_days",
        "every_2_trading_days": "every_2_trading_days",
        "weekly": "weekly",
    }
    normalized = aliases.get(text, text)
    if normalized not in {"daily", "every_2_trading_days", "weekly"}:
        raise ValueError(
            f"Unsupported trading frequency '{value}'. "
            "Expected one of: daily, every_2_trading_days, weekly"
        )
    return normalized


# ── Config ──────────────────────────────────────────────────────────────────
START      = sys.argv[1] if len(sys.argv) > 1 else "2025-03-03"
END        = sys.argv[2] if len(sys.argv) > 2 else "2025-03-14"
LLM_PROFILE = sys.argv[3] if len(sys.argv) > 3 else "openrouter"
LLM_MODEL  = sys.argv[4] if len(sys.argv) > 4 else os.getenv("LLM_MODEL", "")
NEWS_ENABLED = _parse_optional_bool(sys.argv[5] if len(sys.argv) > 5 else os.getenv("NEWS_ENABLED"))
DECISION_SPACE_MODE = _parse_optional_decision_space_mode(
    sys.argv[6] if len(sys.argv) > 6 else os.getenv("DECISION_SPACE_MODE")
)
TRADING_FREQUENCY = _parse_optional_trading_frequency(os.getenv("TRADING_FREQUENCY"))
TOP_K_SHORTLIST_ENABLED = _parse_optional_bool(os.getenv("TOP_K_SHORTLIST_ENABLED"))
TOP_K_SHORTLIST_K = _parse_optional_int(os.getenv("TOP_K_SHORTLIST_K"))
BACKTEST_MAX_POSITIONS = _parse_optional_int(os.getenv("BACKTEST_MAX_POSITIONS"))
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

if TRADING_FREQUENCY is not None:
    cfg.setdefault("backtest", {}).setdefault("trading_frequency", {})["mode"] = TRADING_FREQUENCY
    print(f"[INFO] Trading frequency: {TRADING_FREQUENCY}")

if TOP_K_SHORTLIST_ENABLED is not None:
    cfg.setdefault("top_k_shortlist", {})["enabled"] = TOP_K_SHORTLIST_ENABLED
    print(f"[INFO] Top-K shortlist enabled: {TOP_K_SHORTLIST_ENABLED}")

if TOP_K_SHORTLIST_K is not None:
    cfg.setdefault("top_k_shortlist", {})["k"] = TOP_K_SHORTLIST_K
    print(f"[INFO] Top-K shortlist k: {TOP_K_SHORTLIST_K}")

if BACKTEST_MAX_POSITIONS is not None:
    cfg.setdefault("backtest", {})["max_positions"] = BACKTEST_MAX_POSITIONS
    cfg.setdefault("risk", {})["max_positions"] = BACKTEST_MAX_POSITIONS
    print(f"[INFO] Backtest max_positions: {BACKTEST_MAX_POSITIONS}")
    print(f"[INFO] Risk max_positions: {BACKTEST_MAX_POSITIONS}")

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
effective_trading_frequency = str(
    (((cfg.get("backtest") or {}).get("trading_frequency") or {}).get("mode") or "daily")
).strip().lower()
top_k_cfg = (cfg.get("top_k_shortlist") or {})
top_k_enabled = bool(top_k_cfg.get("enabled"))
top_k_k = int(top_k_cfg.get("k", 0) or 0)
run_id_parts = []
model_slug = re.sub(r"[^A-Za-z0-9]+", "_", effective_model).strip("_").upper()
if model_slug:
    run_id_parts.append(model_slug)
if effective_decision_mode and effective_decision_mode != "continuous":
    run_id_parts.append(effective_decision_mode.upper())
if effective_trading_frequency and effective_trading_frequency != "daily":
    run_id_parts.append(re.sub(r"[^A-Za-z0-9]+", "_", effective_trading_frequency).strip("_").upper())
if top_k_enabled and top_k_k > 0:
    run_id_parts.append(f"TOPK{top_k_k}")
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
