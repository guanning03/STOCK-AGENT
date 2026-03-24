from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import DEFAULT_OUTPUT_DIR, DEFAULT_SELECTION, ensure_output_parent, load_primary_payload


ROOT_DIR = Path(__file__).resolve().parents[3]
SIMULATED_RESULTS_DIR = ROOT_DIR / "simulated results"

DISCRETE_RUN_ID = "GPT_4O_MINI_DISCRETE_TARGET_STATE_20250301_20260228_20260323_155643_676410"
TOP5_RUN_ID = "GPT_4O_MINI_TOPK5_20250301_20260228_20260323_155738_980197"

BASELINE_STYLES = {
    "GPT-4o-mini (A)": {"color": "#f28e2b", "linestyle": "-", "linewidth": 2.2},
    "GPT-4o-mini (B)": {"color": "#c85200", "linestyle": "--", "linewidth": 2.2},
}
IMPROVEMENT_STYLES = {
    "GPT-4o-mini (Discrete Action Space)": {"color": "#4e79a7", "linestyle": "-", "linewidth": 2.4},
    "GPT-4o-mini (Top-5 Shortlist)": {"color": "#59a14f", "linestyle": "-", "linewidth": 2.4},
}


def _load_gpt_baseline_nav(selection_path: Path) -> dict[str, pd.Series]:
    payload = load_primary_payload(selection_path)
    out: dict[str, pd.Series] = {}
    for item in payload:
        label = str(item["label"])
        if label in BASELINE_STYLES:
            out[label] = item["nav"].astype(float)
    missing = [label for label in BASELINE_STYLES if label not in out]
    if missing:
        raise FileNotFoundError(f"Missing GPT baseline payload for: {missing}")
    return out


def _load_simulated_nav(strategy: str, run_id: str) -> pd.Series:
    csv_path = SIMULATED_RESULTS_DIR / strategy / run_id / "curve_comparison.csv"
    frame = pd.read_csv(csv_path, parse_dates=["date"])
    if "simulated_nav" not in frame.columns:
        raise KeyError(f"simulated_nav not found in {csv_path}")
    series = frame.set_index("date")["simulated_nav"].astype(float)
    series.index.name = "date"
    return series


def _shared_ylim(series_map: dict[str, pd.Series]) -> tuple[float, float]:
    ymin = min(float(series.min()) for series in series_map.values())
    ymax = max(float(series.max()) for series in series_map.values())
    span = max(ymax - ymin, 1e-6)
    pad = span * 0.06
    return ymin - pad, ymax + pad


def _plot_nav_comparison(
    baseline_navs: dict[str, pd.Series],
    improvement_label: str,
    improvement_nav: pd.Series,
    output_path: Path,
    title: str,
    ylim: tuple[float, float],
) -> None:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=220)

    for label, style in BASELINE_STYLES.items():
        series = baseline_navs[label]
        ax.plot(
            series.index,
            series.values,
            label=label,
            color=style["color"],
            linestyle=style["linestyle"],
            linewidth=style["linewidth"],
        )

    style = IMPROVEMENT_STYLES[improvement_label]
    ax.plot(
        improvement_nav.index,
        improvement_nav.values,
        label=improvement_label,
        color=style["color"],
        linestyle=style["linestyle"],
        linewidth=style["linewidth"],
    )

    ax.set_title(title)
    ax.set_xlabel("Date")
    ax.set_ylabel("NAV")
    ax.set_ylim(*ylim)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline_navs = _load_gpt_baseline_nav(args.selection)
    discrete_nav = _load_simulated_nav("discrete", DISCRETE_RUN_ID)
    top5_nav = _load_simulated_nav("top5", TOP5_RUN_ID)

    ylim = _shared_ylim(
        {
            **baseline_navs,
            "GPT-4o-mini (Discrete Action Space)": discrete_nav,
            "GPT-4o-mini (Top-5 Shortlist)": top5_nav,
        }
    )

    discrete_output = ensure_output_parent(output_dir / "gpt_discrete_action_space_nav.png")
    top5_output = ensure_output_parent(output_dir / "gpt_top5_shortlist_nav.png")

    _plot_nav_comparison(
        baseline_navs=baseline_navs,
        improvement_label="GPT-4o-mini (Discrete Action Space)",
        improvement_nav=discrete_nav,
        output_path=discrete_output,
        title="GPT-4o-mini Baseline vs Discrete Action Space",
        ylim=ylim,
    )
    _plot_nav_comparison(
        baseline_navs=baseline_navs,
        improvement_label="GPT-4o-mini (Top-5 Shortlist)",
        improvement_nav=top5_nav,
        output_path=top5_output,
        title="GPT-4o-mini Baseline vs Top-5 Shortlist",
        ylim=ylim,
    )

    print(f"[OK] Wrote {discrete_output}")
    print(f"[OK] Wrote {top5_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
