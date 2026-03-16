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


def log_to_wandb(
    result: Dict,
    cfg: Optional[Dict] = None,
    run_id: Optional[str] = None,
) -> None:
    """Log backtest outputs to Weights & Biases."""
    try:
        import wandb
    except ImportError:
        print("[W&B] wandb not installed, skipping W&B logging")
        return

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
                    run.summary[key] = float(val)
                except Exception:
                    pass

        if isinstance(nav_s, pd.Series) and len(nav_s) >= 2:
            n = len(nav_s)
            nav_start = float(nav_s.iloc[0])
            nav_end = float(nav_s.iloc[-1])
            if nav_start > 0:
                cagr = (nav_end / nav_start) ** (252.0 / max(1, n)) - 1.0
                run.summary["cagr"] = float(cagr)

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
                row["core-curves/strategy_vs_spy/strategy_nav"] = strategy_nav

            benchmark_nav = _get(bench_s)
            if benchmark_nav is not None:
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
                wandb.log(row, step=i)

    finally:
        wandb.finish()
