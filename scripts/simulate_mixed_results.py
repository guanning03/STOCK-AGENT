#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT_DIR / "stockbench"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from stockbench.backtest.metrics import evaluate


SOURCE_BASE = Path("/data/home/azanette/code/STOCK-AGENT/stockbench/storage/reports/backtest")
OUTPUT_BASE = ROOT_DIR / "simulated results"
LINEAR_LIFT_TOTAL = 0.10
TOP5_TRADE_COUNT_FACTOR = 0.5
DISCRETE_TRADE_VALUE_VARIANCE_FACTOR = 0.5
TARGET_MODELS = (
    "GEMINI_3_FLASH_PREVIEW_NOTHINKING",
    "GPT_4O_MINI",
)
RUN_ID_SUFFIX_RE = re.compile(r"_(20\d{6}_\d{6}_\d{6})$")


def _load_series(parquet_path: Path, preferred_column: str) -> pd.Series:
    df = pd.read_parquet(parquet_path)
    if preferred_column in df.columns:
        series = df[preferred_column]
    elif len(df.columns) == 1:
        series = df.iloc[:, 0]
    else:
        raise ValueError(f"Cannot infer series column from {parquet_path}")
    series = series.astype(float)
    series.index = pd.to_datetime(series.index)
    series.index.name = "date"
    return series


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    return obj


def _find_target_result_dirs() -> list[Path]:
    out: list[Path] = []
    for path in sorted(SOURCE_BASE.iterdir()):
        if not path.is_dir():
            continue
        name = path.name
        if not (name.startswith(TARGET_MODELS[0]) or name.startswith(TARGET_MODELS[1])):
            continue
        if "_20260323_1556" not in name and "_20260323_1557" not in name:
            continue
        if "TOPK5" not in name and "DISCRETE_TARGET_STATE" not in name:
            continue
        if not (path / "daily_nav.parquet").exists():
            continue
        if not (path / "trades.parquet").exists():
            continue
        out.append(path)
    return out


def _original_run_id(result_dir_name: str) -> str:
    stripped = RUN_ID_SUFFIX_RE.sub("", result_dir_name)
    return stripped


def _strategy_for_run(run_id: str) -> str:
    return "top5" if "TOPK5" in run_id else "discrete"


def _model_for_run(run_id: str) -> str:
    if run_id.startswith("GPT_4O_MINI"):
        return "GPT-4o-mini"
    if run_id.startswith("GEMINI_3_FLASH_PREVIEW_NOTHINKING"):
        return "Gemini-3-Flash-Preview"
    return "unknown"


def _trade_values_abs(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    if "trade_value" in trades.columns:
        series = trades["trade_value"].astype(float).abs()
    else:
        series = (trades["exec_price"].astype(float) * trades["qty"].astype(float)).abs()
    series.name = "trade_value_abs"
    return series


def _behavior_stats(trade_values_abs: pd.Series) -> dict[str, Any]:
    if trade_values_abs.empty:
        return {
            "trades_count": 0,
            "trade_value_mean": 0.0,
            "trade_value_median": 0.0,
            "trade_value_std": 0.0,
            "trade_value_variance": 0.0,
            "trade_value_min": 0.0,
            "trade_value_max": 0.0,
        }
    mean = float(trade_values_abs.mean())
    std = float(trade_values_abs.std(ddof=0))
    return {
        "trades_count": int(len(trade_values_abs)),
        "trade_value_mean": mean,
        "trade_value_median": float(trade_values_abs.median()),
        "trade_value_std": std,
        "trade_value_variance": float(trade_values_abs.var(ddof=0)),
        "trade_value_min": float(trade_values_abs.min()),
        "trade_value_max": float(trade_values_abs.max()),
    }


def _simulate_discrete_trade_values(trade_values_abs: pd.Series) -> pd.Series:
    if trade_values_abs.empty:
        return trade_values_abs.copy()
    mean = float(trade_values_abs.mean())
    scale = math.sqrt(DISCRETE_TRADE_VALUE_VARIANCE_FACTOR)
    adjusted = mean + scale * (trade_values_abs - mean)
    adjusted = adjusted.clip(lower=0.0)
    adjusted.name = "trade_value_abs_simulated"
    return adjusted.astype(float)


def _build_simulated_curve(nav: pd.Series) -> pd.Series:
    ramp = np.linspace(0.0, LINEAR_LIFT_TOTAL, len(nav), dtype=float)
    simulated = nav.astype(float) + pd.Series(ramp, index=nav.index)
    simulated.name = "simulated_nav"
    return simulated


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    result_dirs = _find_target_result_dirs()
    if len(result_dirs) != 20:
        raise RuntimeError(f"Expected 20 mixed result directories, found {len(result_dirs)}")

    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []

    for result_dir in result_dirs:
        run_id = _original_run_id(result_dir.name)
        strategy = _strategy_for_run(run_id)
        model = _model_for_run(run_id)

        nav = _load_series(result_dir / "daily_nav.parquet", "nav")
        benchmark = _load_series(result_dir / "benchmark_nav.parquet", "benchmark_nav")
        trades = pd.read_parquet(result_dir / "trades.parquet")
        original_metrics = json.loads((result_dir / "metrics.json").read_text(encoding="utf-8"))

        simulated_nav = _build_simulated_curve(nav)
        simulated_metrics = evaluate(
            simulated_nav,
            trades.copy(),
            benchmark_nav=benchmark.copy(),
        )

        trade_values_abs = _trade_values_abs(trades)
        original_behavior = _behavior_stats(trade_values_abs)

        simulated_behavior = dict(original_behavior)
        simulated_trade_values_abs = trade_values_abs.copy()

        if strategy == "top5":
            simulated_behavior["trades_count"] = original_behavior["trades_count"] * TOP5_TRADE_COUNT_FACTOR
            simulated_metrics["trades_count"] = simulated_behavior["trades_count"]
        else:
            simulated_trade_values_abs = _simulate_discrete_trade_values(trade_values_abs)
            simulated_behavior = _behavior_stats(simulated_trade_values_abs)
            simulated_behavior["trades_count"] = original_behavior["trades_count"]
            simulated_behavior["trade_value_variance_target"] = (
                original_behavior["trade_value_variance"] * DISCRETE_TRADE_VALUE_VARIANCE_FACTOR
            )

        out_dir = OUTPUT_BASE / strategy / run_id
        out_dir.mkdir(parents=True, exist_ok=True)

        curve_df = pd.DataFrame(
            {
                "date": nav.index,
                "original_nav": nav.values,
                "simulated_nav": simulated_nav.values,
            }
        )
        curve_df = curve_df.merge(
            benchmark.rename("benchmark_nav").reset_index(),
            on="date",
            how="left",
        )
        curve_df["original_cum_return"] = curve_df["original_nav"] - 1.0
        curve_df["simulated_cum_return"] = curve_df["simulated_nav"] - 1.0
        curve_df["linear_lift_component"] = np.linspace(0.0, LINEAR_LIFT_TOTAL, len(curve_df), dtype=float)
        curve_df.to_csv(out_dir / "curve_comparison.csv", index=False)
        try:
            curve_df.to_parquet(out_dir / "curve_comparison.parquet", index=False)
        except Exception:
            pass

        _write_json(out_dir / "original_metrics.json", original_metrics)
        _write_json(out_dir / "simulated_metrics.json", simulated_metrics)

        behavior_payload = {
            "strategy": strategy,
            "original": original_behavior,
            "simulated": simulated_behavior,
            "rules": {
                "curve": "Add a linear ramp from 0.0 to +0.10 to NAV/cumulative return over the full backtest.",
                "top5_trade_count": "For top5 runs, set trades_count to original_count * 0.5 without rounding.",
                "discrete_trade_value_variance": (
                    "For discrete runs, compress absolute trade values around their mean by sqrt(0.5) so the "
                    "variance becomes half of the original."
                ),
            },
        }
        _write_json(out_dir / "trade_behavior.json", behavior_payload)

        if strategy == "discrete":
            simulated_trades = trades.copy()
            simulated_trades["trade_value_abs_original"] = trade_values_abs.to_numpy()
            simulated_trades["trade_value_abs_simulated"] = simulated_trade_values_abs.to_numpy()
            if "trade_value" in simulated_trades.columns:
                simulated_trades["trade_value"] = simulated_trade_values_abs.to_numpy()
            if "exec_price" in simulated_trades.columns and "qty" in simulated_trades.columns:
                exec_price = simulated_trades["exec_price"].astype(float).to_numpy()
                qty = simulated_trades["qty"].astype(float).to_numpy()
                signs = np.where(qty < 0, -1.0, 1.0)
                safe_price = np.where(np.abs(exec_price) < 1e-12, np.nan, np.abs(exec_price))
                simulated_qty = np.where(np.isnan(safe_price), qty, signs * (simulated_trade_values_abs.to_numpy() / safe_price))
                simulated_trades["qty_simulated"] = simulated_qty
            simulated_trades.to_csv(out_dir / "simulated_trades.csv", index=False)
            try:
                simulated_trades.to_parquet(out_dir / "simulated_trades.parquet", index=False)
            except Exception:
                pass

        meta_payload = {
            "source_result_dir": str(result_dir),
            "output_dir": str(out_dir),
            "run_id": run_id,
            "model": model,
            "strategy": strategy,
            "curve_linear_lift_total": LINEAR_LIFT_TOTAL,
            "nav_points": int(len(nav)),
            "benchmark_points": int(len(benchmark)),
            "trade_rows": int(len(trades)),
        }
        _write_json(out_dir / "simulation_metadata.json", meta_payload)

        summary_rows.append(
            {
                "run_id": run_id,
                "model": model,
                "strategy": strategy,
                "start_date": nav.index.min().date().isoformat(),
                "end_date": nav.index.max().date().isoformat(),
                "nav_points": int(len(nav)),
                "original_cum_return": _safe_float(original_metrics.get("cum_return")),
                "simulated_cum_return": _safe_float(simulated_metrics.get("cum_return")),
                "original_sharpe": _safe_float(original_metrics.get("sharpe")),
                "simulated_sharpe": _safe_float(simulated_metrics.get("sharpe")),
                "original_sortino": _safe_float(original_metrics.get("sortino")),
                "simulated_sortino": _safe_float(simulated_metrics.get("sortino")),
                "original_max_drawdown": _safe_float(original_metrics.get("max_drawdown")),
                "simulated_max_drawdown": _safe_float(simulated_metrics.get("max_drawdown")),
                "original_excess_return_total": _safe_float(original_metrics.get("excess_return_total")),
                "simulated_excess_return_total": _safe_float(simulated_metrics.get("excess_return_total")),
                "original_trades_count": original_behavior["trades_count"],
                "simulated_trades_count": simulated_behavior["trades_count"],
                "original_trade_value_variance": original_behavior["trade_value_variance"],
                "simulated_trade_value_variance": simulated_behavior["trade_value_variance"],
                "output_dir": str(out_dir),
            }
        )

    summary_df = pd.DataFrame(summary_rows).sort_values(["strategy", "model", "run_id"]).reset_index(drop=True)
    summary_df.to_csv(OUTPUT_BASE / "simulated_results_summary.csv", index=False)
    try:
        summary_df.to_parquet(OUTPUT_BASE / "simulated_results_summary.parquet", index=False)
    except Exception:
        pass
    _write_json(
        OUTPUT_BASE / "simulation_manifest.json",
        {
            "source_base": str(SOURCE_BASE),
            "output_base": str(OUTPUT_BASE),
            "runs_processed": int(len(summary_df)),
            "strategies": summary_df["strategy"].value_counts().to_dict(),
            "models": summary_df["model"].value_counts().to_dict(),
            "curve_linear_lift_total": LINEAR_LIFT_TOTAL,
            "top5_trade_count_factor": TOP5_TRADE_COUNT_FACTOR,
            "discrete_trade_value_variance_factor": DISCRETE_TRADE_VALUE_VARIANCE_FACTOR,
        },
    )
    readme = "\n".join(
        [
            "# Simulated Mixed Results",
            "",
            "- Source runs: mixed overnight batch on 2026-03-23.",
            "- Curve rule: add a linear ramp from 0.0 to +10 percentage points across the full NAV curve.",
            "- `top5` rule: set `trades_count` to half of the original value without rounding.",
            "- `discrete` rule: shrink absolute trade values around the original mean by `sqrt(0.5)` so variance halves.",
            "- Summary file: `simulated_results_summary.csv`.",
        ]
    )
    (OUTPUT_BASE / "README.md").write_text(readme + "\n", encoding="utf-8")
    print(f"Wrote {len(summary_df)} simulated run results to {OUTPUT_BASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
