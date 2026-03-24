from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_OUTPUT_DIR, DEFAULT_SELECTION, baseline_analyze, ensure_output_parent, load_extended_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "baseline_trade_count_vs_return_all.png",
    )
    args = parser.parse_args()

    extended_rows = load_extended_rows(args.selection)
    output_path = ensure_output_parent(args.output)
    baseline_analyze.plot_trade_count_vs_return(
        extended_rows,
        output_path,
        title="Trade Count vs Return Across All Full-Year Baseline Runs",
    )
    print(f"[OK] Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
