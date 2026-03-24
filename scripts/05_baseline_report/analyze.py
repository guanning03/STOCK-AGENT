from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SELECTION = Path(__file__).with_name("selection.json")
DEFAULT_OUTPUT_DIR = Path(__file__).with_name("outputs")
WANDB_DIRS = [
    ROOT_DIR / "wandb",
    ROOT_DIR / "stockbench" / "wandb",
]
REPORT_DIR_CANDIDATES = [
    ROOT_DIR / "storage" / "reports" / "backtest",
    ROOT_DIR / "stockbench" / "storage" / "reports" / "backtest",
]
FAMILY_COLORS = {
    "DeepSeek-V3.1": "#0b3c5d",
    "GPT-4o-mini": "#f28e2b",
    "Gemini-3-Flash-Preview": "#59a14f",
    "Claude-Sonnet-4.5": "#e15759",
    "GLM-4-Flash": "#76b7b2",
    "MiniMax-M2.5": "#4e79a7",
    "Qwen2.5-7B": "#af7aa1",
    "Other": "#9c755f",
}
LINE_STYLES = ["-", "--", ":", "-."]


@dataclass
class RunRecord:
    run_id: str
    output_dir: Path
    raw_dir: Path
    summary_path: Path
    summary: dict[str, Any]
    family: str
    provider: str


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def resolve_repo_relative_path(path_str: str) -> Path:
    original = Path(path_str)
    if original.exists():
        return original

    for marker in ("STOCK-Agent-copy/", "STOCK-AGENT/"):
        if marker in path_str:
            tail = path_str.split(marker, 1)[1]
            candidate = ROOT_DIR / tail
            if candidate.exists():
                return candidate

    for report_dir in REPORT_DIR_CANDIDATES:
        candidate = report_dir / original.name
        if candidate.exists():
            return candidate
    return original


def infer_family(run_id: str) -> str:
    normalized = run_id.upper()
    if "GLM_4_FLASH" in normalized:
        return "GLM-4-Flash"
    if "MINIMAX" in normalized:
        return "MiniMax-M2.5"
    if "DEEPSEEK" in normalized:
        return "DeepSeek-V3.1"
    if "GPT_4O_MINI" in normalized:
        return "GPT-4o-mini"
    if "GEMINI_3_FLASH" in normalized:
        return "Gemini-3-Flash-Preview"
    if "CLAUDE_SONNET" in normalized:
        return "Claude-Sonnet-4.5"
    if "QWEN2_5_7B" in normalized:
        return "Qwen2.5-7B"
    return "Other"


def infer_provider(run_id: str) -> str:
    normalized = run_id.upper()
    if run_id.startswith("QINGYUNTOP_"):
        return "qingyuntop"
    if any(token in normalized for token in ("GLM_4_FLASH", "MINIMAX", "GROK_4_1_FAST")):
        return "qingyuntop"
    return "default"


def find_full_year_runs(selection_cfg: dict[str, Any]) -> dict[str, RunRecord]:
    period = selection_cfg.get("full_year_period", {})
    start_token = str(period.get("start", "20250301"))
    end_token = str(period.get("end", "20260228"))
    exclude_tokens = tuple(selection_cfg.get("exclude_tokens", []))
    registry: dict[str, RunRecord] = {}

    summary_paths: list[Path] = []
    for wandb_dir in WANDB_DIRS:
        if wandb_dir.exists():
            summary_paths.extend(sorted(wandb_dir.glob("run-*/files/wandb-summary.json")))

    for summary_path in summary_paths:
        summary = load_json(summary_path)
        run_id = str(summary.get("run_id") or "").strip()
        if not run_id:
            continue
        if start_token not in run_id or end_token not in run_id:
            continue
        if any(token in run_id for token in exclude_tokens):
            continue
        if summary.get("cum_return") is None:
            continue

        output_dir_raw = str(summary.get("output_dir") or "").strip()
        if not output_dir_raw:
            continue
        output_dir = resolve_repo_relative_path(output_dir_raw)
        if not output_dir.exists():
            continue

        raw_dir = None
        for report_dir in REPORT_DIR_CANDIDATES:
            candidate = report_dir / run_id
            if candidate.exists():
                raw_dir = candidate
                break
        if raw_dir is None:
            candidate = output_dir.parent / run_id
            if candidate.exists():
                raw_dir = candidate
        if raw_dir is None:
            continue

        registry[run_id] = RunRecord(
            run_id=run_id,
            output_dir=output_dir,
            raw_dir=raw_dir,
            summary_path=summary_path,
            summary=summary,
            family=infer_family(run_id),
            provider=infer_provider(run_id),
        )
    return registry


def read_series(parquet_path: Path, fallback_name: str) -> pd.Series:
    frame = pd.read_parquet(parquet_path)
    if isinstance(frame, pd.Series):
        series = frame.copy()
    else:
        if fallback_name in frame.columns:
            series = frame[fallback_name].copy()
        elif len(frame.columns) == 1:
            series = frame.iloc[:, 0].copy()
        else:
            series = frame.select_dtypes(include=[np.number]).iloc[:, 0].copy()
    series.index = pd.to_datetime(series.index)
    return series.sort_index()


def load_snapshots(run: RunRecord) -> pd.DataFrame:
    path = run.raw_dir / "detailed_portfolio_snapshots.jsonl"
    rows = []
    for row in load_jsonl(path):
        positions = row.get("positions") or {}
        total_equity = float(row.get("total_equity") or 0.0)
        cash = float(row.get("cash") or 0.0)
        rows.append(
            {
                "date": pd.to_datetime(row.get("date")),
                "cash": cash,
                "total_equity": total_equity,
                "nav": float(row.get("nav") or 0.0),
                "holdings_count": len(positions),
                "cash_ratio": (cash / total_equity) if total_equity else np.nan,
            }
        )
    frame = pd.DataFrame(rows)
    frame = frame.sort_values("date").set_index("date")
    return frame


def load_trades(run: RunRecord) -> pd.DataFrame:
    trades = pd.read_parquet(run.output_dir / "trades.parquet").copy()
    trades["date"] = pd.to_datetime(trades["ts"]).dt.normalize()
    trades["trade_value_abs"] = trades["trade_value"].abs()
    return trades


def compute_behavior(trades: pd.DataFrame, snapshots: pd.DataFrame) -> dict[str, Any]:
    trade_values = trades["trade_value_abs"]
    trade_days = int(trades["date"].nunique()) if not trades.empty else 0
    trading_days = int(len(snapshots))
    avg_equity = float(snapshots["total_equity"].mean()) if trading_days else np.nan
    daily_trade_counts = trades.groupby("date").size() if not trades.empty else pd.Series(dtype=float)
    daily_notional = (
        trades.groupby("date")["trade_value_abs"].sum() if not trades.empty else pd.Series(dtype=float)
    )

    return {
        "trade_days": trade_days,
        "trade_day_ratio": (trade_days / trading_days) if trading_days else np.nan,
        "avg_trades_per_active_day": (len(trades) / trade_days) if trade_days else np.nan,
        "avg_trades_per_day": (len(trades) / trading_days) if trading_days else np.nan,
        "turnover": (float(trade_values.sum()) / avg_equity) if avg_equity else np.nan,
        "avg_trade_value": float(trade_values.mean()) if len(trades) else np.nan,
        "median_trade_value": float(trade_values.median()) if len(trades) else np.nan,
        "trade_value_std": float(trade_values.std(ddof=0)) if len(trades) else np.nan,
        "trade_value_cv": (
            float(trade_values.std(ddof=0) / trade_values.mean())
            if len(trades) and float(trade_values.mean()) != 0.0
            else np.nan
        ),
        "trade_value_q10": float(trade_values.quantile(0.10)) if len(trades) else np.nan,
        "trade_value_q90": float(trade_values.quantile(0.90)) if len(trades) else np.nan,
        "small_trade_ratio": float((trade_values < 1000.0).mean()) if len(trades) else np.nan,
        "max_trade_value": float(trade_values.max()) if len(trades) else np.nan,
        "daily_trade_count_std": float(daily_trade_counts.std(ddof=0)) if len(daily_trade_counts) else np.nan,
        "avg_daily_notional": float(daily_notional.mean()) if len(daily_notional) else np.nan,
        "max_daily_notional": float(daily_notional.max()) if len(daily_notional) else np.nan,
        "mean_cash_ratio": float(snapshots["cash_ratio"].mean()) if trading_days else np.nan,
        "final_cash_ratio": float(snapshots["cash_ratio"].iloc[-1]) if trading_days else np.nan,
        "mean_holdings_count": float(snapshots["holdings_count"].mean()) if trading_days else np.nan,
    }


def percent_text(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value * 100:.{digits}f}%"


def number_text(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:.{digits}f}"


def money_text(value: float | None, digits: int = 0) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"${value:,.{digits}f}"


def write_markdown_table(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("| " + " | ".join(columns) + " |\n")
        handle.write("| " + " | ".join(["---"] * len(columns)) + " |\n")
        for row in rows:
            handle.write("| " + " | ".join(row.get(column, "") for column in columns) + " |\n")


def resolve_primary_styles(primary_payload: list[dict[str, Any]]) -> list[tuple[str, str]]:
    family_counters: Counter[str] = Counter()
    styles: list[tuple[str, str]] = []
    for item in primary_payload:
        family = item["family"]
        family_idx = family_counters[family]
        family_counters[family] += 1
        styles.append((FAMILY_COLORS.get(family, FAMILY_COLORS["Other"]), LINE_STYLES[family_idx % len(LINE_STYLES)]))
    return styles


def plot_primary_nav(primary_payload: list[dict[str, Any]], benchmark: pd.Series, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=220)
    styles = resolve_primary_styles(primary_payload)
    for (color, linestyle), item in zip(styles, primary_payload):
        nav = item["nav"]
        ax.plot(nav.index, nav.values, label=item["label"], linewidth=2.0, color=color, linestyle=linestyle)
    ax.plot(benchmark.index, benchmark.values, label="Benchmark", linewidth=2.0, color="#333333", linestyle="--")
    ax.set_title("Baseline NAV Curves")
    ax.set_ylabel("NAV")
    ax.set_xlabel("Date")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_primary_relative_performance(primary_payload: list[dict[str, Any]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=220)
    styles = resolve_primary_styles(primary_payload)
    for (color, linestyle), item in zip(styles, primary_payload):
        frame = pd.concat([item["nav"].rename("nav"), item["benchmark"].rename("benchmark")], axis=1).dropna()
        rel = frame["nav"] / frame["benchmark"] - 1.0
        ax.plot(rel.index, rel.values, label=item["label"], linewidth=2.0, color=color, linestyle=linestyle)
    ax.axhline(0.0, color="#333333", linestyle="--", linewidth=1.0)
    ax.set_title("Relative Performance vs Benchmark")
    ax.set_ylabel("Excess Return")
    ax.set_xlabel("Date")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_primary_drawdown(primary_payload: list[dict[str, Any]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=220)
    styles = resolve_primary_styles(primary_payload)
    for (color, linestyle), item in zip(styles, primary_payload):
        nav = item["nav"]
        drawdown = nav / nav.cummax() - 1.0
        ax.plot(drawdown.index, drawdown.values, label=item["label"], linewidth=2.0, color=color, linestyle=linestyle)
    ax.set_title("Drawdown Curves")
    ax.set_ylabel("Drawdown")
    ax.set_xlabel("Date")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_trade_value_boxplot(primary_payload: list[dict[str, Any]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 7), dpi=220)
    labels = [item["label"] for item in primary_payload]
    data = [item["trades"]["trade_value_abs"].values for item in primary_payload]
    box = ax.boxplot(data, patch_artist=True, tick_labels=labels, showfliers=False)
    colors = [FAMILY_COLORS.get(item["family"], FAMILY_COLORS["Other"]) for item in primary_payload]
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    ax.set_yscale("log")
    ax.set_title("Trade Value Distribution")
    ax.set_ylabel("Trade Value (USD, log scale)")
    ax.grid(alpha=0.25, axis="y")
    ymax = max(float(np.max(values)) for values in data)
    for idx, item in enumerate(primary_payload, start=1):
        ratio = item["behavior"]["small_trade_ratio"]
        ax.text(idx, ymax * 1.12, f"<$1k {ratio * 100:.1f}%", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def plot_log_metric_vs_return(
    extended_rows: list[dict[str, Any]],
    output_path: Path,
    title: str,
    metric_key: str,
    x_label: str,
    corr_label: str,
    corr_raw_key: str,
    corr_log_key: str,
) -> dict[str, float]:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=220)
    family_counters: Counter[str] = Counter()
    xs: list[float] = []
    ys: list[float] = []

    for row in extended_rows:
        family = row["family"]
        family_counters[family] += 1
        short_label = f"{family.split('-')[0]}-{family_counters[family]}"
        x = float(row[metric_key])
        y = float(row["cum_return"])
        xs.append(x)
        ys.append(y)
        ax.scatter(
            x,
            y,
            s=60,
            color=FAMILY_COLORS.get(family, FAMILY_COLORS["Other"]),
            alpha=0.85,
            edgecolors="white",
            linewidths=0.8,
        )
        ax.annotate(short_label, (x, y), xytext=(5, 4), textcoords="offset points", fontsize=8)

    x_array = np.array(xs, dtype=float)
    y_array = np.array(ys, dtype=float)
    x_log = np.log10(x_array)

    slopes: list[float] = []
    for idx in range(len(x_log)):
        for jdx in range(idx + 1, len(x_log)):
            if x_log[jdx] != x_log[idx]:
                slopes.append((y_array[jdx] - y_array[idx]) / (x_log[jdx] - x_log[idx]))
    robust_slope = float(np.median(slopes))
    robust_intercept = float(np.median(y_array - robust_slope * x_log))

    coeff = np.polyfit(x_log, y_array, deg=1)
    x_line = np.geomspace(x_array.min(), x_array.max(), 200)
    y_line = coeff[0] * np.log10(x_line) + coeff[1]
    y_line_robust = robust_slope * np.log10(x_line) + robust_intercept
    ax.plot(x_line, y_line, color="#999999", linestyle="--", linewidth=1.2, label="OLS fit")
    ax.plot(x_line, y_line_robust, color="#333333", linestyle="-", linewidth=2.0, label="Robust log fit")

    corr_raw = float(np.corrcoef(x_array, y_array)[0, 1])
    corr_log = float(np.corrcoef(x_log, y_array)[0, 1])

    ax.set_xscale("log")
    ax.set_title(title)
    ax.set_xlabel(f"{x_label} (log scale)")
    ax.set_ylabel("Cum Return")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.grid(alpha=0.25)
    ax.legend(loc="lower left", fontsize=9)
    ax.text(
        0.02,
        0.98,
        f"n = {len(extended_rows)}\n"
        f"corr({corr_label}, return) = {corr_raw:.2f}\n"
        f"corr(log {corr_label}, return) = {corr_log:.2f}",
        transform=ax.transAxes,
        va="top",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.85},
        fontsize=9,
    )

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return {
        corr_raw_key: corr_raw,
        corr_log_key: corr_log,
        "robust_log_slope": robust_slope,
        "robust_log_intercept": robust_intercept,
    }


def plot_trade_count_vs_return(
    extended_rows: list[dict[str, Any]],
    output_path: Path,
    title: str,
) -> dict[str, float]:
    return plot_log_metric_vs_return(
        extended_rows,
        output_path,
        title,
        metric_key="trades_count",
        x_label="Trades Count",
        corr_label="trades",
        corr_raw_key="corr_trades_vs_return",
        corr_log_key="corr_log_trades_vs_return",
    )


def plot_turnover_vs_return(
    extended_rows: list[dict[str, Any]],
    output_path: Path,
    title: str,
) -> dict[str, float]:
    return plot_log_metric_vs_return(
        extended_rows,
        output_path,
        title,
        metric_key="turnover",
        x_label="Turnover",
        corr_label="turnover",
        corr_raw_key="corr_turnover_vs_return",
        corr_log_key="corr_log_turnover_vs_return",
    )


def plot_repeatability(repeat_payload: list[dict[str, Any]], output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=220)
    palette = ["#0b3c5d", "#2f6690", "#4c956c", "#f28e2b", "#c85200"]
    for color, item in zip(palette, repeat_payload):
        nav = item["nav"]
        label = f"{item['label']} | {item['metrics']['trades_count']} trades | {item['metrics']['cum_return'] * 100:.1f}%"
        ax.plot(nav.index, nav.values, color=color, linewidth=1.9, label=label)
    ax.set_title("DeepSeek Full-Year Baseline Repeatability")
    ax.set_ylabel("NAV")
    ax.set_xlabel("Date")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def build_payload(run: RunRecord, label: str | None = None) -> dict[str, Any]:
    metrics = load_json(run.output_dir / "metrics.json")
    nav = read_series(run.output_dir / "daily_nav.parquet", "nav")
    benchmark = read_series(run.output_dir / "benchmark_nav.parquet", "benchmark_nav")
    trades = load_trades(run)
    snapshots = load_snapshots(run)
    behavior = compute_behavior(trades, snapshots)
    return {
        "run": run,
        "run_id": run.run_id,
        "label": label or run.family,
        "family": run.family,
        "provider": run.provider,
        "metrics": metrics,
        "nav": nav,
        "benchmark": benchmark,
        "trades": trades,
        "snapshots": snapshots,
        "behavior": behavior,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    selection_cfg = load_json(args.selection)
    output_dir = args.output_dir.resolve()
    ensure_dir(output_dir)

    registry = find_full_year_runs(selection_cfg)

    primary_payload: list[dict[str, Any]] = []
    for item in selection_cfg.get("primary_runs", []):
        run_id = item["run_id"]
        if run_id not in registry:
            raise FileNotFoundError(f"Primary run not found: {run_id}")
        primary_payload.append(build_payload(registry[run_id], label=item["label"]))

    benchmark_series = primary_payload[0]["benchmark"]
    plot_primary_nav(primary_payload, benchmark_series, output_dir / "baseline_primary_nav.png")
    plot_primary_relative_performance(primary_payload, output_dir / "baseline_primary_relative.png")
    plot_primary_drawdown(primary_payload, output_dir / "baseline_primary_drawdown.png")
    plot_trade_value_boxplot(primary_payload, output_dir / "baseline_primary_trade_values.png")

    extended_rows: list[dict[str, Any]] = []
    for run in registry.values():
        metrics = load_json(run.output_dir / "metrics.json")
        snapshots = load_snapshots(run)
        trades = load_trades(run)
        behavior = compute_behavior(trades, snapshots)
        extended_rows.append(
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
    extended_rows = sorted(extended_rows, key=lambda row: row["trades_count"])
    selected_run_ids = {item["run_id"] for item in primary_payload}
    selected_rows = [row for row in extended_rows if row["run_id"] in selected_run_ids]
    selected_rows = sorted(selected_rows, key=lambda row: row["trades_count"])
    all_relationship = plot_trade_count_vs_return(
        extended_rows,
        output_dir / "baseline_trade_count_vs_return_all.png",
        title="Trade Count vs Return Across All Full-Year Baseline Runs",
    )
    selected_relationship = plot_trade_count_vs_return(
        selected_rows,
        output_dir / "baseline_trade_count_vs_return.png",
        title="Trade Count vs Return Across Selected Two Runs per Model",
    )
    selected_turnover_relationship = plot_turnover_vs_return(
        selected_rows,
        output_dir / "baseline_turnover_vs_return.png",
        title="Turnover vs Return Across Selected Two Runs per Model",
    )

    repeatability_payload: list[dict[str, Any]] = []
    for group in selection_cfg.get("repeatability_groups", []):
        for run_id in group.get("run_ids", []):
            if run_id not in registry:
                raise FileNotFoundError(f"Repeatability run not found: {run_id}")
            repeatability_payload.append(build_payload(registry[run_id], label=group["label"]))
    if repeatability_payload:
        plot_repeatability(repeatability_payload, output_dir / "baseline_deepseek_repeatability.png")

    primary_metrics_rows: list[dict[str, str]] = []
    primary_behavior_rows: list[dict[str, str]] = []
    summary_primary_models: list[dict[str, Any]] = []
    for item in primary_payload:
        metrics = item["metrics"]
        behavior = item["behavior"]
        benchmark_return = float(item["benchmark"].iloc[-1] - 1.0)
        primary_metrics_rows.append(
            {
                "Model": item["label"],
                "Cum Return": percent_text(float(metrics["cum_return"])),
                "Excess Return": percent_text(float(metrics["excess_return_total"])),
                "Max Drawdown": percent_text(float(metrics["max_drawdown"])),
                "Sharpe": number_text(float(metrics["sharpe"])),
                "Sortino": number_text(float(metrics["sortino"])),
                "Trades": str(int(metrics["trades_count"])),
                "Benchmark Return": percent_text(benchmark_return),
            }
        )
        primary_behavior_rows.append(
            {
                "Model": item["label"],
                "Trade Days": str(int(behavior["trade_days"])),
                "Trade Day Ratio": percent_text(float(behavior["trade_day_ratio"])),
                "Turnover": number_text(float(behavior["turnover"]), digits=2) + "x",
                "Median Trade": money_text(float(behavior["median_trade_value"]), digits=0),
                "Trade Value CV": number_text(float(behavior["trade_value_cv"]), digits=2),
                "<$1k Trades": percent_text(float(behavior["small_trade_ratio"])),
                "Avg Trades/Day": number_text(float(behavior["avg_trades_per_day"]), digits=2),
            }
        )
        summary_primary_models.append(
            {
                "label": item["label"],
                "run_id": item["run_id"],
                "benchmark_return": benchmark_return,
                "cum_return": float(metrics["cum_return"]),
                "excess_return_total": float(metrics["excess_return_total"]),
                "max_drawdown": float(metrics["max_drawdown"]),
                "sharpe": float(metrics["sharpe"]),
                "sortino": float(metrics["sortino"]),
                "trades_count": int(metrics["trades_count"]),
                "trade_days": int(behavior["trade_days"]),
                "turnover": float(behavior["turnover"]),
                "median_trade_value": float(behavior["median_trade_value"]),
                "trade_value_cv": float(behavior["trade_value_cv"]),
                "trade_value_q10": float(behavior["trade_value_q10"]),
                "trade_value_q90": float(behavior["trade_value_q90"]),
                "small_trade_ratio": float(behavior["small_trade_ratio"]),
                "mean_cash_ratio": float(behavior["mean_cash_ratio"]),
            }
        )

    write_markdown_table(
        output_dir / "primary_metrics_table.md",
        primary_metrics_rows,
        ["Model", "Cum Return", "Excess Return", "Max Drawdown", "Sharpe", "Sortino", "Trades", "Benchmark Return"],
    )
    write_markdown_table(
        output_dir / "primary_behavior_table.md",
        primary_behavior_rows,
        ["Model", "Trade Days", "Trade Day Ratio", "Turnover", "Median Trade", "Trade Value CV", "<$1k Trades", "Avg Trades/Day"],
    )

    paper_families = {
        "DeepSeek-V3.1",
        "GPT-4o-mini",
        "Gemini-3-Flash-Preview",
        "Claude-Sonnet-4.5",
        "GLM-4-Flash",
        "MiniMax-M2.5",
    }
    remaining_rows = [
        row for row in extended_rows if row["run_id"] not in selected_run_ids and row["family"] in paper_families
    ]
    appendix_table_rows = []
    for row in remaining_rows:
        appendix_table_rows.append(
            {
                "Model": row["family"],
                "Provider": row["provider"],
                "Trades": str(int(row["trades_count"])),
                "Cum Return": percent_text(float(row["cum_return"])),
                "Excess Return": percent_text(float(row["excess_return_total"])),
                "Max Drawdown": percent_text(float(row["max_drawdown"])),
            }
        )
    write_markdown_table(
        output_dir / "appendix_remaining_runs_table.md",
        appendix_table_rows,
        ["Model", "Provider", "Trades", "Cum Return", "Excess Return", "Max Drawdown"],
    )

    extended_frame = pd.DataFrame(extended_rows)
    extended_frame.to_csv(output_dir / "extended_full_year_runs.csv", index=False)
    paper_family_rows = [row for row in extended_rows if row["family"] in paper_families]
    paper_family_table_rows = []
    for row in paper_family_rows:
        paper_family_table_rows.append(
            {
                "Model": row["family"],
                "Provider": row["provider"],
                "Trades": str(int(row["trades_count"])),
                "Cum Return": percent_text(float(row["cum_return"])),
                "Excess Return": percent_text(float(row["excess_return_total"])),
                "Max Drawdown": percent_text(float(row["max_drawdown"])),
            }
        )
    write_markdown_table(
        output_dir / "paper_family_runs_table.md",
        paper_family_table_rows,
        ["Model", "Provider", "Trades", "Cum Return", "Excess Return", "Max Drawdown"],
    )

    repeatability_summary = {}
    if repeatability_payload:
        repeat_returns = [float(item["metrics"]["cum_return"]) for item in repeatability_payload]
        repeat_trades = [int(item["metrics"]["trades_count"]) for item in repeatability_payload]
        repeatability_summary = {
            "count": len(repeatability_payload),
            "cum_return_min": float(min(repeat_returns)),
            "cum_return_max": float(max(repeat_returns)),
            "cum_return_mean": float(sum(repeat_returns) / len(repeat_returns)),
            "cum_return_std": float(np.std(repeat_returns, ddof=0)),
            "trades_count_min": int(min(repeat_trades)),
            "trades_count_max": int(max(repeat_trades)),
        }

    summary_payload = {
        "selection_path": str(args.selection.resolve()),
        "output_dir": str(output_dir),
        "primary_models": summary_primary_models,
        "extended_relationship_all_runs": all_relationship,
        "extended_relationship_paper_families": plot_trade_count_vs_return(
            paper_family_rows,
            output_dir / "baseline_trade_count_vs_return_paper_families.png",
            title="Trade Count vs Return Across DeepSeek / GPT / Gemini / Claude / GLM Runs",
        ),
        "selected_relationship_primary": selected_relationship,
        "selected_turnover_relationship_primary": selected_turnover_relationship,
        "extended_run_count": len(extended_rows),
        "paper_family_run_count": len(paper_family_rows),
        "selected_primary_run_count": len(selected_rows),
        "appendix_remaining_run_count": len(remaining_rows),
        "repeatability_summary": repeatability_summary,
    }
    with (output_dir / "analysis_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary_payload, handle, ensure_ascii=False, indent=2)

    print(f"[OK] Wrote analysis outputs to {output_dir}")
    print(f"[OK] Primary metrics table: {output_dir / 'primary_metrics_table.md'}")
    print(f"[OK] Primary behavior table: {output_dir / 'primary_behavior_table.md'}")
    print(f"[OK] Summary json: {output_dir / 'analysis_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
