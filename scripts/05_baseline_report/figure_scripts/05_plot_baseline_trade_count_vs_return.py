from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_OUTPUT_DIR, DEFAULT_SELECTION, baseline_analyze, ensure_output_parent, load_primary_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "baseline_trade_count_vs_return.png",
    )
    args = parser.parse_args()

    primary_payload = load_primary_payload(args.selection)
    rows = []
    for item in primary_payload:
        metrics = item["metrics"]
        rows.append(
            {
                "run_id": item["run_id"],
                "family": item["family"],
                "provider": item["provider"],
                "cum_return": float(metrics["cum_return"]),
                "excess_return_total": float(metrics["excess_return_total"]),
                "max_drawdown": float(metrics["max_drawdown"]),
                "sharpe": float(metrics["sharpe"]),
                "sortino": float(metrics["sortino"]),
                "trades_count": int(metrics["trades_count"]),
            }
        )
    rows = sorted(rows, key=lambda row: row["trades_count"])
    output_path = ensure_output_parent(args.output)
    baseline_analyze.plot_trade_count_vs_return(
        rows,
        output_path,
        title="Trade Count vs Return Across Selected Two Runs per Model",
    )
    print(f"[OK] Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
