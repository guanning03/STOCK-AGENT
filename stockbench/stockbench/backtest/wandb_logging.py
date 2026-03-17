from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def _sanitize_wandb_config(obj: Any) -> Any:
    """Keep wandb config JSON-like and drop runtime-only private keys."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).startswith("_"):
                continue
            cleaned[str(key)] = _sanitize_wandb_config(value)
        return cleaned
    if isinstance(obj, (list, tuple)):
        return [_sanitize_wandb_config(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


def _extract_cash_ratio(portfolio_snapshots: list) -> pd.Series:
    dates, ratios = [], []
    for snap in portfolio_snapshots:
        try:
            if hasattr(snap, "cash"):
                cash = float(snap.cash)
                total_equity = float(snap.total_equity)
                date = pd.Timestamp(snap.date)
            else:
                cash = float(snap["cash"])
                total_equity = float(snap["total_equity"])
                date = pd.Timestamp(snap.get("date") or snap.get("timestamp", ""))
            if total_equity > 0:
                dates.append(date)
                ratios.append(cash / total_equity)
        except Exception:
            continue
    if not dates:
        return pd.Series(dtype=float)
    return pd.Series(ratios, index=pd.DatetimeIndex(dates)).sort_index()


def _extract_trade_net_costs(trade_records: list, symbols: List[str]) -> pd.DataFrame:
    rows = []
    symbol_set = set(symbols)
    for rec in trade_records or []:
        try:
            if hasattr(rec, "timestamp"):
                ts = rec.timestamp
                sym = rec.symbol
                net_cost = rec.net_cost
            else:
                ts = rec.get("timestamp") or rec.get("ts")
                sym = rec.get("symbol")
                net_cost = rec.get("net_cost")
            if sym not in symbol_set:
                continue
            rows.append(
                {
                    "date": pd.Timestamp(ts).normalize(),
                    "symbol": str(sym),
                    "net_cost": float(net_cost),
                }
            )
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "net_cost"])
    return (
        pd.DataFrame(rows)
        .groupby(["date", "symbol"], as_index=False)["net_cost"]
        .sum()
    )


def _build_per_symbol_strategy_nav(
    trade_records: list,
    per_symbol_position_values: pd.DataFrame,
    symbols: List[str],
    initial_cash: float,
) -> pd.DataFrame:
    """Build a comparable per-symbol sleeve NAV from actual symbol cashflows + marked position value."""
    if (
        per_symbol_position_values is None
        or per_symbol_position_values.empty
        or not symbols
        or initial_cash <= 0
    ):
        return pd.DataFrame()

    dates = pd.DatetimeIndex(per_symbol_position_values.index).sort_values().unique()
    position_values = (
        per_symbol_position_values.reindex(columns=symbols, fill_value=0.0)
        .reindex(dates)
        .fillna(0.0)
        .astype(float)
    )

    trade_costs = _extract_trade_net_costs(trade_records, symbols)
    flow_pivot = (
        trade_costs.pivot(index="date", columns="symbol", values="net_cost")
        if not trade_costs.empty
        else pd.DataFrame(index=dates)
    )
    flow_pivot = flow_pivot.reindex(index=dates, columns=symbols, fill_value=0.0).fillna(0.0)

    sleeve_initial_cash = float(initial_cash) / max(len(symbols), 1)
    out = pd.DataFrame(index=dates)
    for sym in symbols:
        cash_series = sleeve_initial_cash - flow_pivot[sym].cumsum()
        equity_series = cash_series + position_values[sym]
        out[sym] = equity_series / sleeve_initial_cash if sleeve_initial_cash > 0 else 0.0
    return out.astype(float)


def _as_series(obj: Any, preferred_names: tuple[str, ...] = ("nav",)) -> pd.Series | None:
    if isinstance(obj, pd.Series):
        return obj.astype(float)
    if isinstance(obj, pd.DataFrame) and not obj.empty:
        for name in preferred_names:
            if name in obj.columns:
                return obj[name].astype(float)
        return obj.iloc[:, 0].astype(float)
    return None


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _wandb_runtime():
    try:
        import wandb
    except ImportError:
        print("[W&B] wandb not installed, skipping W&B logging")
        return None
    return wandb


def init_wandb_run(
    cfg: Optional[Dict] = None,
    run_id: Optional[str] = None,
):
    """Initialize a W&B run early so long backtests show up immediately."""
    wandb = _wandb_runtime()
    if wandb is None:
        return None

    wb_cfg = ((cfg or {}).get("wandb") or {})
    project = wb_cfg.get("project", "stock-agent")
    entity = wb_cfg.get("entity") or None
    wb_run_id = (run_id or "backtest").replace(".", "-").replace("/", "-")
    safe_cfg = _sanitize_wandb_config(cfg or {})

    run = wandb.init(
        project=project,
        entity=entity,
        name=wb_run_id,
        id=wb_run_id,
        config=safe_cfg,
        resume="never",
    )

    try:
        run.summary["status"] = "running"
        run.summary["run_id"] = wb_run_id
    except Exception:
        pass

    return run


def log_progress_to_wandb(
    progress: Dict[str, Any],
    run: Any = None,
) -> None:
    """Stream one backtest progress row to the active W&B run."""
    if run is None:
        return

    row: Dict[str, Any] = {}
    step = progress.get("step")
    metrics = progress.get("metrics") or {}

    if step is not None:
        try:
            row["progress/trading_day"] = int(step)
        except Exception:
            pass

    date_value = progress.get("date")
    if date_value is not None:
        try:
            row["progress/date"] = pd.Timestamp(date_value).strftime("%Y-%m-%d")
        except Exception:
            row["progress/date"] = str(date_value)

    scalar_mappings = {
        "core-curves/strategy_nav": progress.get("nav"),
        "core-curves/strategy_vs_benchmark/strategy_nav": progress.get("nav"),
        "core-curves/strategy_vs_spy/strategy_nav": progress.get("nav"),
        "core-curves/drawdown": progress.get("drawdown"),
        "core-curves/cash_ratio": progress.get("cash_ratio"),
        "portfolio/total_equity": progress.get("total_equity"),
        "portfolio/cash": progress.get("cash"),
        "portfolio/position_value": progress.get("total_position_value"),
        "portfolio/unrealized_pnl": progress.get("unrealized_pnl"),
        "portfolio/holdings_count": progress.get("holdings_count"),
        "trading/trades_count_today": progress.get("trades_count_today"),
        "trading/trades_notional_today": progress.get("trades_notional_today"),
        "core-curves/strategy_vs_benchmark/benchmark_nav": progress.get("benchmark_nav"),
        "core-curves/strategy_vs_spy/spy_nav": progress.get("benchmark_nav"),
        "core-curves/excess_return_cum": progress.get("excess_return_cum"),
    }
    for key, value in scalar_mappings.items():
        coerced = _coerce_float(value)
        if coerced is not None:
            row[key] = coerced

    metric_mappings = {
        "core-metrics/cum_return": metrics.get("cum_return"),
        "core-metrics/max_drawdown": metrics.get("max_drawdown"),
        "core-metrics/sortino": metrics.get("sortino"),
        "core-metrics/sortino_annual": metrics.get("sortino_annual"),
        "core-metrics/volatility_daily": metrics.get("volatility_daily"),
        "trading/trades_count": metrics.get("trades_count"),
        "trading/trades_notional": metrics.get("trades_notional"),
    }
    for key, value in metric_mappings.items():
        coerced = _coerce_float(value)
        if coerced is not None:
            row[key] = coerced

    positions = progress.get("position_values") or {}
    if isinstance(positions, dict):
        for symbol, value in positions.items():
            coerced = _coerce_float(value)
            if coerced is not None:
                row[f"positions/{symbol}/value"] = coerced

    if not row:
        return

    try:
        log_kwargs = {}
        if step is not None:
            log_kwargs["step"] = int(step)
        run.log(row, **log_kwargs)
        if "progress/date" in row:
            run.summary["last_completed_date"] = row["progress/date"]
        if "core-curves/strategy_nav" in row:
            run.summary["latest_nav"] = row["core-curves/strategy_nav"]
        if "core-curves/strategy_vs_benchmark/benchmark_nav" in row:
            run.summary["latest_benchmark_nav"] = row["core-curves/strategy_vs_benchmark/benchmark_nav"]
        if "core-curves/strategy_vs_spy/spy_nav" in row:
            run.summary["latest_benchmark_nav"] = row["core-curves/strategy_vs_spy/spy_nav"]
    except Exception as exc:
        print(f"[W&B] incremental logging failed: {exc}")


def finish_wandb_run(run: Any = None, error: Any = None) -> None:
    """Finish a W&B run, optionally marking it as failed."""
    if run is None:
        return

    try:
        if error is None:
            run.summary["status"] = "completed"
            exit_code = 0
        else:
            run.summary["status"] = "failed"
            run.summary["error"] = str(error)
            exit_code = 1
    except Exception:
        exit_code = 1 if error is not None else 0

    try:
        run.finish(exit_code=exit_code)
    except TypeError:
        try:
            run.finish()
        except Exception:
            pass
    except Exception:
        pass


def log_to_wandb(
    result: Dict,
    cfg: Optional[Dict] = None,
    run_id: Optional[str] = None,
    run: Any = None,
    log_history: bool = True,
    finish: bool = True,
) -> None:
    """Log backtest outputs to Weights & Biases."""
    managed_run = run
    if managed_run is None:
        managed_run = init_wandb_run(cfg=cfg, run_id=run_id)
    if managed_run is None:
        return

    try:
        nav_s = _as_series(result.get("nav"), ("nav",))
        bench_s = _as_series(result.get("benchmark_nav"), ("benchmark_nav", "nav"))
        metrics: Dict = result.get("metrics") or {}
        portfolio_snapshots = result.get("portfolio_snapshots") or []
        trade_records = result.get("trade_records") or []
        per_symbol_bh_nav = result.get("per_symbol_benchmark_nav")
        per_symbol_position_values = result.get("per_symbol_position_values")
        initial_cash = float(result.get("initial_cash") or 1_000_000)

        symbols: List[str] = (
            list(per_symbol_bh_nav.columns)
            if isinstance(per_symbol_bh_nav, pd.DataFrame) and not per_symbol_bh_nav.empty
            else []
        )

        summary_keys = [
            "cum_return",
            "max_drawdown",
            "sortino",
            "sortino_annual",
            "sharpe",
            "volatility",
            "volatility_daily",
            "trades_count",
            "trades_notional",
            "excess_return_total",
            "information_ratio",
            "information_ratio_daily",
            "tracking_error",
            "tracking_error_daily",
            "hit_ratio_active",
            "beta",
            "corr",
            "up_capture",
            "down_capture",
            "sortino_excess",
        ]
        for key in summary_keys:
            val = metrics.get(key)
            if val is not None:
                try:
                    managed_run.summary[key] = float(val)
                except Exception:
                    pass

        if isinstance(nav_s, pd.Series) and len(nav_s) >= 2:
            n = len(nav_s)
            nav_start = float(nav_s.iloc[0])
            nav_end = float(nav_s.iloc[-1])
            if nav_start > 0:
                cagr = (nav_end / nav_start) ** (252.0 / max(1, n)) - 1.0
                managed_run.summary["cagr"] = float(cagr)
        elif isinstance(nav_s, pd.Series) and len(nav_s) == 1:
            try:
                managed_run.summary["final_nav"] = float(nav_s.iloc[-1])
            except Exception:
                pass

        summary_fields = {
            "status": "completed",
            "output_dir": result.get("output_dir"),
            "nl_summary": result.get("nl_summary"),
        }
        if isinstance(nav_s, pd.Series) and len(nav_s) > 0:
            summary_fields["final_nav"] = float(nav_s.iloc[-1])
            summary_fields["start_date"] = str(nav_s.index[0].date())
            summary_fields["end_date"] = str(nav_s.index[-1].date())
            summary_fields["trading_days"] = int(len(nav_s))
        if isinstance(bench_s, pd.Series) and len(bench_s) > 0:
            summary_fields["benchmark_final_nav"] = float(bench_s.iloc[-1])
        for key, value in summary_fields.items():
            if value is not None:
                try:
                    managed_run.summary[key] = value
                except Exception:
                    pass

        drawdown_s = (
            (nav_s / nav_s.cummax() - 1.0)
            if isinstance(nav_s, pd.Series) and len(nav_s) > 0
            else None
        )

        excess_cum_s = None
        if isinstance(nav_s, pd.Series) and isinstance(bench_s, pd.Series):
            aligned = pd.concat([nav_s.rename("n"), bench_s.rename("b")], axis=1).dropna()
            if len(aligned) >= 2:
                r_e = aligned["n"].pct_change().fillna(0.0) - aligned["b"].pct_change().fillna(0.0)
                excess_cum_s = r_e.cumsum()

        cash_ratio_s = (
            _extract_cash_ratio(portfolio_snapshots)
            if portfolio_snapshots
            else pd.Series(dtype=float)
        )

        per_sym_strat_nav = (
            _build_per_symbol_strategy_nav(
                trade_records=trade_records,
                per_symbol_position_values=per_symbol_position_values,
                symbols=symbols,
                initial_cash=initial_cash,
            )
            if isinstance(per_symbol_position_values, pd.DataFrame)
            else pd.DataFrame()
        )

        if log_history:
            all_dates = sorted(nav_s.index) if isinstance(nav_s, pd.Series) and len(nav_s) > 0 else []

            for i, date in enumerate(all_dates):
                row: Dict[str, Any] = {}

                def _get(series: Optional[pd.Series]) -> Optional[float]:
                    if series is None:
                        return None
                    try:
                        value = series.loc[date]
                        return float(value) if pd.notna(value) else None
                    except KeyError:
                        return None

                strategy_nav = _get(nav_s)
                if strategy_nav is not None:
                    row["core-curves/strategy_nav"] = strategy_nav
                    row["core-curves/strategy_vs_benchmark/strategy_nav"] = strategy_nav
                    row["core-curves/strategy_vs_spy/strategy_nav"] = strategy_nav

                benchmark_nav = _get(bench_s)
                if benchmark_nav is not None:
                    row["core-curves/strategy_vs_benchmark/benchmark_nav"] = benchmark_nav
                    row["core-curves/strategy_vs_spy/spy_nav"] = benchmark_nav

                excess_cum = _get(excess_cum_s)
                if excess_cum is not None:
                    row["core-curves/excess_return_cum"] = excess_cum

                drawdown = _get(drawdown_s)
                if drawdown is not None:
                    row["core-curves/drawdown"] = drawdown

                cash_ratio = _get(cash_ratio_s)
                if cash_ratio is not None:
                    row["core-curves/cash_ratio"] = cash_ratio

                for sym in symbols:
                    bh_val = None
                    if isinstance(per_symbol_bh_nav, pd.DataFrame) and sym in per_symbol_bh_nav.columns:
                        try:
                            raw = per_symbol_bh_nav.loc[date, sym]
                            bh_val = float(raw) if pd.notna(raw) else None
                        except KeyError:
                            pass
                    if bh_val is not None:
                        row[f"per-asset-curves/{sym}/buy_and_hold_baseline"] = bh_val

                    strat_val = None
                    if not per_sym_strat_nav.empty and sym in per_sym_strat_nav.columns:
                        try:
                            raw = per_sym_strat_nav.loc[date, sym]
                            strat_val = float(raw) if pd.notna(raw) else None
                        except KeyError:
                            pass
                    if strat_val is not None:
                        row[f"per-asset-curves/{sym}/strategy"] = strat_val

                    key_s = f"per-asset-curves/{sym}/strategy"
                    key_b = f"per-asset-curves/{sym}/buy_and_hold_baseline"
                    if key_s in row and key_b in row:
                        row[f"per-asset-curves/{sym}/excess_return"] = row[key_s] - row[key_b]

                if row:
                    managed_run.log(row, step=i)
    finally:
        if finish:
            finish_wandb_run(managed_run)
