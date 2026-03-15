#!/usr/bin/env python3
"""
Overnight Pre-Download Script for Stock Data
=============================================
Downloads all required data to local cache for offline backtesting.

Designed for FREE tier API rate limits:
  - Polygon free: 5 requests/minute  -> 12s between requests
  - Finnhub free: 60 requests/minute -> 1.2s between requests

Features:
  - Resume support: tracks progress in a checkpoint file, skips completed items
  - Separate phases: prices -> news -> indicators -> financials -> corp_actions
  - Aggressive rate limit handling: exponential backoff on 429
  - Estimated runtime display
  - Can be interrupted and resumed safely (Ctrl+C)

Usage:
  export POLYGON_API_KEY="..."
  export FINNHUB_API_KEY="..."
  python pre_download_data.py                          # Download all missing data
  python pre_download_data.py --start 2025-01-01       # Custom start date
  python pre_download_data.py --phase news             # Only download news
  python pre_download_data.py --reset-checkpoint       # Clear progress and start fresh
"""
from __future__ import annotations

import json
import os
import sys
import time
import signal
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set, Tuple

# Add project to path
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import pandas as pd
import yaml

from stockbench.core import data_hub as hub

# ─── Configuration ─────────────────────────────────────────────────────────────

# Rate limit delays (seconds) - tuned for free tier
POLYGON_DELAY = 12.5   # Polygon free: 5 req/min -> 12s + margin
FINNHUB_DELAY = 1.5    # Finnhub free: 60 req/min -> 1.0s + margin
NEWS_DELAY = 13.0      # News calls trigger both Finnhub + Polygon internally -> use Polygon rate
BATCH_DELAY = 90.0     # Delay when hitting 429 rate limit
MAX_RETRIES = 5         # Max retries per API call
CHECKPOINT_FILE = _SCRIPT_DIR / "storage" / ".download_checkpoint.json"

# Default symbols (DJIA 20 + SPY benchmark)
DEFAULT_SYMBOLS = [
    "GS", "MSFT", "HD", "V", "SHW", "CAT", "MCD", "UNH", "AXP", "AMGN",
    "TRV", "CRM", "JPM", "IBM", "HON", "BA", "AMZN", "AAPL", "PG", "JNJ",
    "SPY",
]

# ─── Helpers ───────────────────────────────────────────────────────────────────

_interrupted = False

def _signal_handler(sig, frame):
    global _interrupted
    print("\n\n⚠️  Interrupt received! Saving checkpoint and exiting gracefully...")
    _interrupted = True

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def _load_checkpoint() -> dict:
    try:
        if CHECKPOINT_FILE.exists():
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {"completed": {}}


def _save_checkpoint(cp: dict):
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(CHECKPOINT_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cp, f, indent=2)
    os.replace(tmp, str(CHECKPOINT_FILE))


def _is_done(cp: dict, phase: str, key: str) -> bool:
    return key in cp.get("completed", {}).get(phase, [])


def _mark_done(cp: dict, phase: str, key: str):
    cp.setdefault("completed", {}).setdefault(phase, [])
    if key not in cp["completed"][phase]:
        cp["completed"][phase].append(key)


def _safe_api_call(func, *args, delay: float = 1.5, max_retries: int = MAX_RETRIES, **kwargs):
    """Call an API function with rate limiting and retry logic.
    Returns (result, error_string_or_None).
    """
    for attempt in range(max_retries + 1):
        if _interrupted:
            return None, "interrupted"
        try:
            if attempt > 0 or delay > 0:
                time.sleep(delay)
            result = func(*args, **kwargs)
            return result, None
        except Exception as e:
            err = str(e).lower()
            if "429" in err or "rate limit" in err or "api limit" in err:
                wait = min(BATCH_DELAY * (1.5 ** attempt), 300)
                _log(f"  ⏳ Rate limited (attempt {attempt+1}/{max_retries+1}), waiting {wait:.0f}s ...", "WARN")
                time.sleep(wait)
                continue
            elif "timeout" in err or "network" in err or "connection" in err:
                wait = min(delay * (2 ** attempt), 60)
                _log(f"  🔄 Network error (attempt {attempt+1}): {e}, retry in {wait:.0f}s", "WARN")
                time.sleep(wait)
                continue
            else:
                return None, str(e)
    return None, "max retries exceeded"


def _get_trading_days(start: str, end: str) -> List[pd.Timestamp]:
    """Generate list of trading days (business days, filtered by hub.is_trading_day)."""
    dates = pd.date_range(start=start, end=end, freq="B")
    return [d for d in dates if hub.is_trading_day(d)]


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.0f}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"


# ─── Scan existing data ───────────────────────────────────────────────────────

def _scan_parquet_range(ticker: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (earliest_date, latest_date) for a ticker's parquet files."""
    base = _SCRIPT_DIR / "storage" / "parquet" / ticker / "day"
    if not base.is_dir():
        return None, None
    files = sorted(f.stem for f in base.glob("*.parquet"))
    if not files:
        return None, None
    return files[0], files[-1]


def _scan_news_days(ticker: str) -> Set[str]:
    """Return set of dates that have news cached for a ticker."""
    base = _SCRIPT_DIR / "storage" / "cache" / "news_by_day" / ticker
    if not base.is_dir():
        return set()
    return {f.stem for f in base.glob("*.json")}


def _scan_indicator_days(ticker: str) -> Set[str]:
    """Return set of dates that have indicators cached for a ticker."""
    base = _SCRIPT_DIR / "storage" / "cache" / "stock_indicators"
    if not base.is_dir():
        return set()
    prefix = f"{ticker}_"
    return {f.stem.replace(prefix, "") for f in base.glob(f"{prefix}*.json")}


# ─── Download phases ──────────────────────────────────────────────────────────

def phase_prices(symbols: List[str], start: str, end: str, cfg: dict, cp: dict):
    """Phase 1: Download daily OHLCV bars from Polygon."""
    _log("=" * 60)
    _log("PHASE 1/5: Daily Price Bars (Polygon API)")
    _log(f"  Range: {start} -> {end}")
    _log(f"  Delay: {POLYGON_DELAY}s per request (Polygon free tier: 5 req/min)")
    _log("=" * 60)

    total = len(symbols)
    for i, sym in enumerate(symbols):
        if _interrupted:
            break
        key = f"{sym}|{start}|{end}"
        if _is_done(cp, "prices", key):
            _log(f"  [{i+1}/{total}] {sym}: already cached, skip")
            continue

        # Check existing coverage
        earliest, latest = _scan_parquet_range(sym)
        if earliest and latest and earliest <= start and latest >= end:
            _log(f"  [{i+1}/{total}] {sym}: local data covers {earliest}~{latest}, skip API")
            _mark_done(cp, "prices", key)
            _save_checkpoint(cp)
            continue

        eta = _format_eta((total - i) * POLYGON_DELAY)
        _log(f"  [{i+1}/{total}] {sym}: fetching bars {start}~{end}  (ETA: {eta})")

        result, error = _safe_api_call(
            hub.get_bars, sym, start, end,
            multiplier=1, timespan="day", adjusted=True, cfg=cfg,
            delay=POLYGON_DELAY,
        )
        if result is not None and hasattr(result, '__len__'):
            rows = len(result) if not (hasattr(result, 'empty') and result.empty) else 0
            _log(f"    ✅ {sym}: {rows} rows saved")
            _mark_done(cp, "prices", key)
        else:
            _log(f"    ❌ {sym}: failed - {error}", "ERROR")

        _save_checkpoint(cp)


def phase_news(symbols: List[str], start: str, end: str, cfg: dict, cp: dict):
    """Phase 2: Download news per trading day per symbol.
    This is the most API-intensive phase.
    News strategy: for decision on day D, fetch news from [D-lookback, D-1].
    We cache the actual day-level news files.
    """
    _log("=" * 60)
    _log("PHASE 2/5: News (Finnhub + Polygon supplement)")
    _log(f"  Range: {start} -> {end}")
    _log(f"  Delay: {NEWS_DELAY}s per request (covers both Finnhub + Polygon internally)")
    _log("=" * 60)

    lookback_days = int((cfg.get("news", {}) or {}).get("lookback_days", 2))
    page_limit = int((cfg.get("news", {}) or {}).get("page_limit", 100))
    trading_days = _get_trading_days(start, end)

    # Pre-scan existing news cache to count skips
    _log(f"  Scanning existing news cache for {len(symbols)} symbols...")
    existing_news: dict = {}
    for sym in symbols:
        existing_news[sym] = _scan_news_days(sym)

    total_tasks = len(trading_days) * len(symbols)
    completed = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    for d_idx, d in enumerate(trading_days):
        if _interrupted:
            break
        d_str = d.strftime("%Y-%m-%d")

        for sym in symbols:
            if _interrupted:
                break
            completed += 1
            key = f"{sym}|{d_str}"

            if _is_done(cp, "news", key):
                skipped += 1
                continue

            # Check if day cache files cover the lookback range
            end_d = (d - timedelta(days=1)).strftime("%Y-%m-%d")
            start_d = (d - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

            # Check if all days in [start_d, end_d] have news cached
            check_dates = pd.date_range(start=start_d, end=end_d, freq="D")
            all_cached = all(dd.strftime("%Y-%m-%d") in existing_news.get(sym, set()) for dd in check_dates)
            if all_cached:
                _mark_done(cp, "news", key)
                skipped += 1
                continue

            # Fetch news via data_hub (which handles Finnhub -> Polygon fallback + caching)
            # Use NEWS_DELAY (13s) since get_news internally calls both Finnhub AND Polygon
            result, error = _safe_api_call(
                hub.get_news, sym, start_d, end_d,
                limit=page_limit, cfg=cfg,
                delay=NEWS_DELAY,
            )

            if result is not None:
                news_count = len(result[0]) if isinstance(result, tuple) and len(result) > 0 else 0
                if news_count > 0:
                    # Update our local tracking
                    for dd in check_dates:
                        existing_news.setdefault(sym, set()).add(dd.strftime("%Y-%m-%d"))
                _mark_done(cp, "news", key)
            else:
                failed += 1
                _log(f"    ❌ {sym}@{d_str}: {error}", "ERROR")

            # Progress report every 20 tasks
            if completed % 20 == 0:
                elapsed = time.time() - start_time
                rate = (completed - skipped) / max(elapsed, 1)
                remaining = total_tasks - completed
                eta = _format_eta(remaining / max(rate, 0.001))
                pct = completed / total_tasks * 100
                _log(f"  📈 Progress: {completed}/{total_tasks} ({pct:.1f}%) | "
                     f"skipped={skipped} failed={failed} | ETA: {eta}")

            # Save checkpoint periodically
            if completed % 50 == 0:
                _save_checkpoint(cp)

        # End-of-day summary
        if (d_idx + 1) % 5 == 0:
            _log(f"  📅 Completed {d_idx+1}/{len(trading_days)} trading days")

    _save_checkpoint(cp)
    _log(f"  News phase done: {completed} tasks, {skipped} skipped, {failed} failed")


def phase_indicators(symbols: List[str], start: str, end: str, cfg: dict, cp: dict):
    """Phase 3: Download stock indicators per trading day per symbol."""
    _log("=" * 60)
    _log("PHASE 3/5: Stock Indicators (Finnhub -> Polygon fallback)")
    _log(f"  Range: {start} -> {end}")
    _log(f"  Delay: {FINNHUB_DELAY}s per request")
    _log("=" * 60)

    trading_days = _get_trading_days(start, end)

    # Pre-scan
    existing: dict = {}
    for sym in symbols:
        existing[sym] = _scan_indicator_days(sym)

    total_tasks = len(trading_days) * len(symbols)
    completed = 0
    skipped = 0
    failed = 0
    start_time = time.time()

    for d_idx, d in enumerate(trading_days):
        if _interrupted:
            break
        d_str = d.strftime("%Y-%m-%d")

        for sym in symbols:
            if _interrupted:
                break
            completed += 1
            key = f"{sym}|{d_str}"

            if _is_done(cp, "indicators", key):
                skipped += 1
                continue

            # Check local cache
            if d_str in existing.get(sym, set()):
                _mark_done(cp, "indicators", key)
                skipped += 1
                continue

            result, error = _safe_api_call(
                hub.get_stock_indicators, sym,
                date=d_str, use_cache=True, cfg=cfg,
                delay=FINNHUB_DELAY,
            )

            if result is not None:
                has_data = isinstance(result, dict) and any(result.get(k, 0) > 0 for k in ["market_cap", "pe_ratio", "week_52_high"])
                if has_data:
                    existing.setdefault(sym, set()).add(d_str)
                _mark_done(cp, "indicators", key)
            else:
                failed += 1
                _log(f"    ❌ {sym}@{d_str}: {error}", "ERROR")

            if completed % 40 == 0:
                elapsed = time.time() - start_time
                rate = (completed - skipped) / max(elapsed, 1)
                remaining = total_tasks - completed
                eta = _format_eta(remaining / max(rate, 0.001))
                pct = completed / total_tasks * 100
                _log(f"  📊 Progress: {completed}/{total_tasks} ({pct:.1f}%) | "
                     f"skipped={skipped} failed={failed} | ETA: {eta}")

            if completed % 50 == 0:
                _save_checkpoint(cp)

        if (d_idx + 1) % 10 == 0:
            _log(f"  📅 Completed {d_idx+1}/{len(trading_days)} trading days")

    _save_checkpoint(cp)
    _log(f"  Indicators phase done: {completed} tasks, {skipped} skipped, {failed} failed")


def phase_financials(symbols: List[str], cfg: dict, cp: dict):
    """Phase 4: Download financial statements (Polygon)."""
    _log("=" * 60)
    _log("PHASE 4/5: Financial Statements (Polygon API)")
    _log(f"  Delay: {POLYGON_DELAY}s per request")
    _log("=" * 60)

    for i, sym in enumerate(symbols):
        if _interrupted:
            break
        key = sym
        if _is_done(cp, "financials", key):
            _log(f"  [{i+1}/{len(symbols)}] {sym}: already cached, skip")
            continue

        success = 0
        for timeframe in [None, "annual", "quarterly"]:
            tf_name = timeframe or "all"
            cache_path = _SCRIPT_DIR / "storage" / "cache" / "financials" / f"{sym}.{tf_name}.json"
            if cache_path.exists() and cache_path.stat().st_size > 10:
                success += 1
                continue

            result, error = _safe_api_call(
                hub.get_financials, sym,
                timeframe=timeframe, limit=50, use_cache=True, cfg=cfg,
                delay=POLYGON_DELAY,
            )
            if result is not None:
                success += 1
            else:
                _log(f"    ❌ {sym} ({tf_name}): {error}", "ERROR")

        _log(f"  [{i+1}/{len(symbols)}] {sym}: {success}/3 timeframes")
        _mark_done(cp, "financials", key)
        _save_checkpoint(cp)


def phase_corp_actions(symbols: List[str], cfg: dict, cp: dict):
    """Phase 5: Download dividends and stock splits (Polygon)."""
    _log("=" * 60)
    _log("PHASE 5/5: Corporate Actions - Dividends & Splits (Polygon API)")
    _log(f"  Delay: {POLYGON_DELAY}s per request")
    _log("=" * 60)

    for i, sym in enumerate(symbols):
        if _interrupted:
            break
        key = sym
        if _is_done(cp, "corp_actions", key):
            _log(f"  [{i+1}/{len(symbols)}] {sym}: already cached, skip")
            continue

        # Check existing
        div_path = _SCRIPT_DIR / "storage" / "cache" / "corporate_actions" / f"{sym}.dividends.json"
        split_path = _SCRIPT_DIR / "storage" / "cache" / "corporate_actions" / f"{sym}.splits.json"

        parts = []
        if div_path.exists() and div_path.stat().st_size > 5:
            parts.append("div:cached")
        else:
            result, error = _safe_api_call(
                hub.get_dividends, sym, cfg=cfg,
                delay=POLYGON_DELAY,
            )
            if result is not None:
                count = len(result) if hasattr(result, '__len__') else 0
                parts.append(f"div:{count}")
            else:
                parts.append(f"div:FAIL")
                _log(f"    ❌ {sym} dividends: {error}", "ERROR")

        if split_path.exists() and split_path.stat().st_size > 5:
            parts.append("split:cached")
        else:
            result, error = _safe_api_call(
                hub.get_splits, sym, cfg=cfg,
                delay=POLYGON_DELAY,
            )
            if result is not None:
                count = len(result) if hasattr(result, '__len__') else 0
                parts.append(f"split:{count}")
            else:
                parts.append(f"split:FAIL")
                _log(f"    ❌ {sym} splits: {error}", "ERROR")

        _log(f"  [{i+1}/{len(symbols)}] {sym}: {', '.join(parts)}")
        _mark_done(cp, "corp_actions", key)
        _save_checkpoint(cp)


# ─── Time estimation ──────────────────────────────────────────────────────────

def _estimate_runtime(symbols: List[str], start: str, end: str, phases: List[str]) -> str:
    """Estimate total runtime based on rate limits and task count."""
    trading_days = _get_trading_days(start, end)
    n_sym = len(symbols)
    n_days = len(trading_days)
    total_sec = 0

    if "prices" in phases:
        total_sec += n_sym * POLYGON_DELAY
    if "news" in phases:
        total_sec += n_sym * n_days * NEWS_DELAY
    if "indicators" in phases:
        total_sec += n_sym * n_days * FINNHUB_DELAY
    if "financials" in phases:
        total_sec += n_sym * 3 * POLYGON_DELAY  # 3 timeframes
    if "corp_actions" in phases:
        total_sec += n_sym * 2 * POLYGON_DELAY  # div + split

    return _format_eta(total_sec)


# ─── Report current coverage ─────────────────────────────────────────────────

def report_coverage(symbols: List[str]):
    """Print a summary of current data coverage."""
    _log("=" * 60)
    _log("CURRENT DATA COVERAGE REPORT")
    _log("=" * 60)

    print(f"\n{'Ticker':<8} {'Prices':<24} {'News Days':<12} {'Indicators':<12} {'Financials':<12} {'CorpAct':<10}")
    print("-" * 80)

    for sym in symbols:
        # Prices
        earliest, latest = _scan_parquet_range(sym)
        price_str = f"{earliest}~{latest}" if earliest else "NONE"

        # News
        news_days = _scan_news_days(sym)
        news_str = str(len(news_days))

        # Indicators
        ind_days = _scan_indicator_days(sym)
        ind_str = str(len(ind_days))

        # Financials
        fin_count = 0
        for tf in ["all", "annual", "quarterly"]:
            p = _SCRIPT_DIR / "storage" / "cache" / "financials" / f"{sym}.{tf}.json"
            if p.exists() and p.stat().st_size > 10:
                fin_count += 1
        fin_str = f"{fin_count}/3"

        # Corp actions
        div_ok = (_SCRIPT_DIR / "storage" / "cache" / "corporate_actions" / f"{sym}.dividends.json").exists()
        spl_ok = (_SCRIPT_DIR / "storage" / "cache" / "corporate_actions" / f"{sym}.splits.json").exists()
        corp_str = f"{'D' if div_ok else '-'}{'S' if spl_ok else '-'}"

        print(f"{sym:<8} {price_str:<24} {news_str:<12} {ind_str:<12} {fin_str:<12} {corp_str:<10}")

    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global POLYGON_DELAY, FINNHUB_DELAY

    parser = argparse.ArgumentParser(
        description="Overnight data pre-download for offline backtesting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pre_download_data.py                              # Download everything to 2025-12-31
  python pre_download_data.py --phase news                 # Only download news
  python pre_download_data.py --phase prices --phase news  # Download prices then news
  python pre_download_data.py --report                     # Show current coverage
  python pre_download_data.py --reset-checkpoint           # Clear progress, start fresh
  python pre_download_data.py --start 2024-10-01           # Extend start date further back
        """
    )
    parser.add_argument("--start", default="2024-12-01",
                        help="Start date (default: 2024-12-01, slightly before existing data)")
    parser.add_argument("--end", default="2025-12-31",
                        help="End date (default: 2025-12-31)")
    parser.add_argument("--symbols", default=None,
                        help="Comma-separated symbols (default: DJIA 20 + SPY)")
    parser.add_argument("--phase", action="append", default=None,
                        choices=["prices", "news", "indicators", "financials", "corp_actions"],
                        help="Run only specific phase(s). Can repeat. Default: all phases.")
    parser.add_argument("--polygon-delay", type=float, default=POLYGON_DELAY,
                        help=f"Seconds between Polygon API calls (default: {POLYGON_DELAY})")
    parser.add_argument("--finnhub-delay", type=float, default=FINNHUB_DELAY,
                        help=f"Seconds between Finnhub API calls (default: {FINNHUB_DELAY})")
    parser.add_argument("--report", action="store_true",
                        help="Only show current data coverage report")
    parser.add_argument("--reset-checkpoint", action="store_true",
                        help="Clear checkpoint file and start fresh")
    parser.add_argument("--config", default=str(_SCRIPT_DIR / "config.yaml"),
                        help="Path to config.yaml")
    args = parser.parse_args()

    # Update global delays
    POLYGON_DELAY = args.polygon_delay
    FINNHUB_DELAY = args.finnhub_delay

    # Parse symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(DEFAULT_SYMBOLS)

    # Report mode
    if args.report:
        report_coverage(symbols)
        return

    # Reset checkpoint
    if args.reset_checkpoint:
        if CHECKPOINT_FILE.exists():
            CHECKPOINT_FILE.unlink()
            _log("Checkpoint cleared.")
        else:
            _log("No checkpoint to clear.")
        return

    # Verify API keys
    poly_key = os.getenv("POLYGON_API_KEY", "")
    finn_key = os.getenv("FINNHUB_API_KEY", "")
    if not poly_key:
        _log("WARNING: POLYGON_API_KEY not set! Prices/financials/corp_actions will fail.", "WARN")
    if not finn_key:
        _log("WARNING: FINNHUB_API_KEY not set! News/indicators may use Polygon fallback (slower).", "WARN")

    # Load config - set data mode to 'auto' to enable API calls
    cfg_path = Path(args.config)
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}

    # Force auto mode for downloading
    cfg.setdefault("data", {})["mode"] = "auto"
    hub.set_data_mode("auto")

    # Determine phases
    phases = args.phase or ["prices", "news", "indicators", "financials", "corp_actions"]

    # Load checkpoint
    cp = _load_checkpoint()

    # Print summary
    trading_days = _get_trading_days(args.start, args.end)
    estimated = _estimate_runtime(symbols, args.start, args.end, phases)

    _log("=" * 60)
    _log("OVERNIGHT DATA PRE-DOWNLOAD")
    _log("=" * 60)
    _log(f"  Date range:    {args.start} -> {args.end}")
    _log(f"  Trading days:  {len(trading_days)}")
    _log(f"  Symbols:       {len(symbols)} -> {symbols}")
    _log(f"  Phases:        {phases}")
    _log(f"  Polygon delay: {POLYGON_DELAY}s  |  Finnhub delay: {FINNHUB_DELAY}s")
    _log(f"  API keys:      Polygon={'✅' if poly_key else '❌'}  Finnhub={'✅' if finn_key else '❌'}")
    _log(f"  Checkpoint:    {CHECKPOINT_FILE}")
    _log(f"  Max estimated: ~{estimated} (upper bound, skips cached data)")
    _log("")
    _log("  Press Ctrl+C to interrupt gracefully (progress is saved)")
    _log("=" * 60)
    print()

    # Execute phases
    run_start = time.time()

    if "prices" in phases and not _interrupted:
        phase_prices(symbols, args.start, args.end, cfg, cp)

    if "news" in phases and not _interrupted:
        phase_news(symbols, args.start, args.end, cfg, cp)

    if "indicators" in phases and not _interrupted:
        phase_indicators(symbols, args.start, args.end, cfg, cp)

    if "financials" in phases and not _interrupted:
        phase_financials(symbols, cfg, cp)

    if "corp_actions" in phases and not _interrupted:
        phase_corp_actions(symbols, cfg, cp)

    # Final save
    _save_checkpoint(cp)

    elapsed = time.time() - run_start
    _log("")
    _log("=" * 60)
    if _interrupted:
        _log(f"INTERRUPTED after {_format_eta(elapsed)}. Progress saved to checkpoint.")
        _log(f"Run again to resume from where you left off.")
    else:
        _log(f"ALL DONE! Total time: {_format_eta(elapsed)}")
        _log(f"Your local storage is now ready for offline backtesting.")
    _log("=" * 60)

    # Show final coverage
    report_coverage(symbols)


if __name__ == "__main__":
    main()
