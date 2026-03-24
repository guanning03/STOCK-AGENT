from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
BASELINE_REPORT_DIR = ROOT_DIR / "scripts" / "05_baseline_report"
DEFAULT_SELECTION = BASELINE_REPORT_DIR / "selection.json"
DEFAULT_OUTPUT_DIR = BASELINE_REPORT_DIR / "outputs"

sys.path.insert(0, str(BASELINE_REPORT_DIR))
import analyze as baseline_analyze  # noqa: E402


def ensure_output_parent(path: Path) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def load_registry(selection_path: Path = DEFAULT_SELECTION):
    selection_cfg = baseline_analyze.load_json(selection_path)
    registry = baseline_analyze.find_full_year_runs(selection_cfg)
    return selection_cfg, registry


def load_primary_payload(selection_path: Path = DEFAULT_SELECTION) -> list[dict[str, Any]]:
    selection_cfg, registry = load_registry(selection_path)
    payload: list[dict[str, Any]] = []
    for item in selection_cfg.get("primary_runs", []):
        run_id = item["run_id"]
        payload.append(baseline_analyze.build_payload(registry[run_id], label=item["label"]))
    return payload


def load_repeatability_payload(selection_path: Path = DEFAULT_SELECTION) -> list[dict[str, Any]]:
    selection_cfg, registry = load_registry(selection_path)
    payload: list[dict[str, Any]] = []
    for group in selection_cfg.get("repeatability_groups", []):
        for run_id in group.get("run_ids", []):
            payload.append(baseline_analyze.build_payload(registry[run_id], label=group["label"]))
    return payload


def load_extended_rows(selection_path: Path = DEFAULT_SELECTION) -> list[dict[str, Any]]:
    _, registry = load_registry(selection_path)
    rows: list[dict[str, Any]] = []
    for run in registry.values():
        metrics = baseline_analyze.load_json(run.output_dir / "metrics.json")
        snapshots = baseline_analyze.load_snapshots(run)
        trades = baseline_analyze.load_trades(run)
        behavior = baseline_analyze.compute_behavior(trades, snapshots)
        rows.append(
            {
                "run_id": run.run_id,
                "family": run.family,
                "provider": run.provider,
                "cum_return": float(metrics["cum_return"]),
                "excess_return_total": float(metrics["excess_return_total"]),
                "max_drawdown": float(metrics["max_drawdown"]),
                "sharpe": float(metrics["sharpe"]),
                "sortino": float(metrics["sortino"]),
                "trades_count": int(metrics["trades_count"]),
                "turnover": float(behavior["turnover"]),
                "median_trade_value": float(behavior["median_trade_value"]),
                "small_trade_ratio": float(behavior["small_trade_ratio"]),
            }
        )
    return sorted(rows, key=lambda row: row["trades_count"])
