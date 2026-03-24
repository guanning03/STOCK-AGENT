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
        default=DEFAULT_OUTPUT_DIR / "baseline_primary_trade_values.png",
    )
    args = parser.parse_args()

    primary_payload = load_primary_payload(args.selection)
    output_path = ensure_output_parent(args.output)
    baseline_analyze.plot_trade_value_boxplot(primary_payload, output_path)
    print(f"[OK] Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
