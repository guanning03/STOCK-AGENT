from __future__ import annotations

import logging
from typing import Dict, List, Optional
import os
import json
from datetime import datetime

logger = logging.getLogger(__name__)

from stockbench.llm.llm_client import LLMClient, LLMConfig
from stockbench.utils.formatting import round_numbers_in_obj
from stockbench.agents.fundamental_filter_agent import filter_stocks_needing_fundamental
from stockbench.core.features import build_features_for_prompt


def _prompt_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_prompt_dir(), name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "System: You are a decision agent responsible for making trading decisions based on filtered data. Output compliant decisions in JSON format."


def _prompt_version(name: str) -> str:
    base = os.path.splitext(name)[0]
    return base.replace("_", "/")


_DEFAULT_DISCRETE_TARGET_STATES = {
    "flat": 0.0,
    "pilot": 0.04,
    "core": 0.08,
    "conviction": 0.15,
}


def _get_decision_space_config(cfg: Dict | None) -> Dict[str, object]:
    raw_cfg = ((cfg or {}).get("decision_space") or {})
    mode = str(raw_cfg.get("mode", "continuous")).strip().lower()
    if mode not in {"continuous", "discrete_target_state"}:
        logger.warning(f"[DECISION_SPACE] Unknown mode '{mode}', falling back to continuous")
        mode = "continuous"

    discrete_cfg = (raw_cfg.get("discrete_target_state") or {})
    raw_states = discrete_cfg.get("states") or _DEFAULT_DISCRETE_TARGET_STATES
    state_weights: Dict[str, float] = {}
    for name, value in raw_states.items():
        try:
            state_weights[str(name).strip().lower()] = max(0.0, float(value))
        except Exception:
            logger.warning(f"[DECISION_SPACE] Invalid state weight ignored: {name}={value}")

    if "flat" not in state_weights:
        state_weights["flat"] = 0.0

    ordered_states = sorted(state_weights.items(), key=lambda item: item[1])

    return {
        "mode": mode,
        "state_weights": state_weights,
        "ordered_states": ordered_states,
        "keep_hold": bool(discrete_cfg.get("keep_hold", True)),
        "quantize_fallback": str(discrete_cfg.get("quantize_fallback", "direction_aware")).strip().lower(),
        "prompt_name": str(discrete_cfg.get("prompt", "decision_agent_discrete_target_state_v1.txt")).strip(),
    }


def _resolve_decision_prompt_name(cfg: Dict | None, decision_space_cfg: Dict[str, object]) -> str:
    if decision_space_cfg.get("mode") == "discrete_target_state":
        return str(decision_space_cfg.get("prompt_name") or "decision_agent_discrete_target_state_v1.txt")
    return str(
        (cfg or {}).get("agents", {}).get("dual_agent", {}).get("decision_agent", {}).get("prompt", "decision_agent_v1.txt")
    )


def _append_active_decision_space_note(system_prompt: str, decision_space_cfg: Dict[str, object]) -> str:
    if decision_space_cfg.get("mode") != "discrete_target_state":
        return system_prompt

    ordered_states = decision_space_cfg.get("ordered_states") or []
    lines = [
        "",
        "<ACTIVE DECISION SPACE>",
        "You are operating in GLOBAL decision mode: discrete_target_state.",
        "You MUST output decisions using the `target_state` field.",
        "You MUST NOT output `target_cash_amount` or any free-form allocation value.",
        "Allowed target_state values:",
    ]
    for state_name, weight in ordered_states:
        lines.append(f'- "{state_name}": target portfolio weight {float(weight):.0%}')
    lines.append('- "hold": keep the current position unchanged')
    lines.append("</ACTIVE DECISION SPACE>")
    return system_prompt.rstrip() + "\n\n" + "\n".join(lines)


def _append_portfolio_constraint_note(system_prompt: str, cfg: Dict | None) -> str:
    portfolio_cfg = (cfg or {}).get("portfolio", {}) or {}
    backtest_cfg = (cfg or {}).get("backtest", {}) or {}

    min_cash_ratio = max(0.0, float(portfolio_cfg.get("min_cash_ratio", 0.0) or 0.0))
    raw_max_positions = backtest_cfg.get("max_positions", 999999)
    try:
        max_positions = int(raw_max_positions)
    except Exception:
        max_positions = 999999

    lines = [
        "",
        "<ACTIVE PORTFOLIO CONSTRAINTS>",
        f"- Maintain at least {min_cash_ratio:.0%} cash after rebalancing.",
    ]
    if max_positions < 999999:
        lines.append(f"- Target no more than {max_positions} active positions.")
    lines.append("- When budget is tight, keep lower-priority names at flat instead of spreading exposure too thin.")
    lines.append("- Prioritize higher-confidence names before lower-confidence names.")
    lines.append("</ACTIVE PORTFOLIO CONSTRAINTS>")
    return system_prompt.rstrip() + "\n\n" + "\n".join(lines)


def _coerce_reasons(raw_reasons: object) -> List[str]:
    if isinstance(raw_reasons, list):
        cleaned = [str(item) for item in raw_reasons if str(item).strip()]
        return cleaned or ["No specific reason"]
    if raw_reasons is None:
        return ["No specific reason"]
    text = str(raw_reasons).strip()
    return [text] if text else ["No specific reason"]


def _coerce_confidence(raw_confidence: object) -> float:
    try:
        return max(0.0, min(1.0, float(raw_confidence)))
    except Exception:
        return 0.5


def _derive_action_from_target_cash(target_cash_amount: float, current_position_value: float, total_assets: float) -> str:
    tolerance = max(float(total_assets) * 0.001, abs(float(current_position_value)) * 0.01, 50.0)
    if abs(float(target_cash_amount) - float(current_position_value)) <= tolerance:
        return "hold"
    if float(target_cash_amount) <= tolerance:
        return "close"
    return "increase" if float(target_cash_amount) > float(current_position_value) else "decrease"


def _quantize_target_state_from_legacy(
    action: str,
    desired_weight: Optional[float],
    current_weight: float,
    ordered_states: List[tuple[str, float]],
) -> str:
    if not ordered_states:
        return "hold"

    action = str(action).strip().lower()
    eps = 1e-9

    if action == "hold":
        return "hold"
    if action == "close":
        return "flat"

    if action == "increase":
        higher_states = [(name, weight) for name, weight in ordered_states if weight > current_weight + eps]
        if not higher_states:
            return ordered_states[-1][0]
        if desired_weight is not None:
            for name, weight in higher_states:
                if weight >= desired_weight - eps:
                    return name
            return higher_states[-1][0]
        return higher_states[0][0]

    if action == "decrease":
        lower_states = [(name, weight) for name, weight in ordered_states if weight < current_weight - eps]
        if not lower_states:
            return "flat"
        if desired_weight is not None:
            chosen = None
            for name, weight in lower_states:
                if weight <= desired_weight + eps:
                    chosen = name
            if chosen is not None:
                return chosen
            return lower_states[0][0]
        return lower_states[-1][0]

    if desired_weight is None:
        return "hold"
    return min(ordered_states, key=lambda item: abs(item[1] - desired_weight))[0]


def _normalize_symbol_decision(
    symbol: str,
    symbol_decision: Dict,
    symbol_features: Dict,
    total_assets: float,
    decision_space_cfg: Dict[str, object],
) -> Dict[str, object]:
    current_position_value = float((symbol_features.get("position_state") or {}).get("current_position_value", 0.0))
    reasons = _coerce_reasons(symbol_decision.get("reasons"))
    confidence = _coerce_confidence(symbol_decision.get("confidence", 0.5))

    if decision_space_cfg.get("mode") != "discrete_target_state":
        action = str(symbol_decision.get("action", "hold")).lower().strip()
        if action not in {"increase", "hold", "decrease", "close"}:
            raise ValueError(f"{symbol}: unsupported action '{action}'")
        if action == "hold" and "target_cash_amount" not in symbol_decision:
            target_cash_amount = current_position_value
        else:
            target_cash_amount = float(symbol_decision.get("target_cash_amount", current_position_value if action == "hold" else 0.0))
        target_cash_amount = max(0.0, target_cash_amount)
        return {
            "action": action,
            "target_cash_amount": target_cash_amount,
            "cash_change": target_cash_amount - current_position_value,
            "target_state": str(symbol_decision.get("target_state")).strip().lower() if symbol_decision.get("target_state") is not None else None,
            "reasons": reasons,
            "confidence": confidence,
        }

    state_weights = decision_space_cfg.get("state_weights") or {}
    ordered_states = decision_space_cfg.get("ordered_states") or []
    target_state_raw = symbol_decision.get("target_state")

    if target_state_raw is not None:
        target_state = str(target_state_raw).strip().lower()
        if target_state == "hold":
            target_cash_amount = current_position_value
        elif target_state in state_weights:
            target_cash_amount = max(0.0, float(total_assets) * float(state_weights[target_state]))
        else:
            raise ValueError(f"{symbol}: unsupported target_state '{target_state}'")
    else:
        if "action" not in symbol_decision and "target_cash_amount" not in symbol_decision:
            raise ValueError(f"{symbol}: missing target_state in discrete_target_state mode")
        legacy_action = str(symbol_decision.get("action", "hold")).strip().lower()
        raw_target_cash = symbol_decision.get("target_cash_amount")
        desired_cash = None
        if raw_target_cash not in (None, ""):
            desired_cash = max(0.0, float(raw_target_cash))

        current_weight = current_position_value / float(total_assets) if float(total_assets) > 0 else 0.0
        desired_weight = desired_cash / float(total_assets) if desired_cash is not None and float(total_assets) > 0 else None
        target_state = _quantize_target_state_from_legacy(
            legacy_action,
            desired_weight,
            current_weight,
            ordered_states,
        )
        if target_state == "hold":
            target_cash_amount = current_position_value
        else:
            target_cash_amount = max(0.0, float(total_assets) * float(state_weights.get(target_state, 0.0)))

    action = _derive_action_from_target_cash(target_cash_amount, current_position_value, total_assets)
    return {
        "action": action,
        "target_cash_amount": max(0.0, float(target_cash_amount)),
        "cash_change": float(target_cash_amount) - current_position_value,
        "target_state": target_state,
        "reasons": reasons,
        "confidence": confidence,
    }


def _build_hold_decision(current_position_value: float, reason: str, decision_space_cfg: Dict[str, object]) -> Dict[str, object]:
    decision = {
        "action": "hold",
        "target_cash_amount": float(current_position_value),
        "cash_change": 0.0,
        "reasons": [reason],
        "confidence": 0.5,
        "timestamp": datetime.now().isoformat(),
    }
    if decision_space_cfg.get("mode") == "discrete_target_state":
        decision["target_state"] = "hold"
    return decision


def _decision_cash_tolerance(total_assets: float, reference_cash: float = 0.0) -> float:
    return max(float(total_assets) * 0.001, abs(float(reference_cash)) * 0.01, 50.0)


def _allocator_priority_score(decision: Dict[str, object], current_position_value: float, total_assets: float) -> float:
    desired_cash = max(0.0, float(decision.get("target_cash_amount", 0.0) or 0.0))
    desired_weight = desired_cash / float(total_assets) if float(total_assets) > 0 else 0.0
    current_weight = float(current_position_value) / float(total_assets) if float(total_assets) > 0 else 0.0
    confidence = _coerce_confidence(decision.get("confidence", 0.5))
    existing_bonus = 0.015 if current_position_value > _decision_cash_tolerance(total_assets, current_position_value) else 0.0
    persistence_bonus = min(current_weight, desired_weight) * 0.10
    return desired_weight + (0.05 * confidence) + existing_bonus + persistence_bonus


def _build_allocator_levels(
    decision: Dict[str, object],
    current_position_value: float,
    total_assets: float,
    ordered_states: List[tuple[str, float]],
) -> List[tuple[str, float]]:
    tolerance = _decision_cash_tolerance(total_assets, current_position_value)
    requested_state = str(decision.get("target_state", "") or "").strip().lower()
    desired_cash = max(0.0, float(decision.get("target_cash_amount", current_position_value) or 0.0))

    levels: List[tuple[str, float]] = []

    def _append_level(level_name: str, level_cash: float) -> None:
        normalized_cash = max(0.0, float(level_cash))
        for _, existing_cash in levels:
            if abs(existing_cash - normalized_cash) <= tolerance:
                return
        levels.append((str(level_name).strip().lower(), normalized_cash))

    if requested_state == "hold":
        _append_level("hold", current_position_value)
        for state_name, weight in sorted(ordered_states, key=lambda item: item[1], reverse=True):
            state_cash = float(total_assets) * float(weight)
            if state_cash < float(current_position_value) - tolerance:
                _append_level(str(state_name), state_cash)
    else:
        _append_level(requested_state or "hold", desired_cash)
        for state_name, weight in sorted(ordered_states, key=lambda item: item[1], reverse=True):
            state_cash = float(total_assets) * float(weight)
            if state_cash < float(desired_cash) - tolerance:
                _append_level(str(state_name), state_cash)

    _append_level("flat", 0.0)
    return levels or [("flat", 0.0)]


def _apply_allocator_level(
    decision: Dict[str, object],
    level_name: str,
    target_cash_amount: float,
    current_position_value: float,
    total_assets: float,
) -> None:
    normalized_state = str(level_name).strip().lower() or "flat"
    normalized_cash = max(0.0, float(target_cash_amount))
    decision["target_state"] = normalized_state
    decision["target_cash_amount"] = normalized_cash
    decision["cash_change"] = normalized_cash - float(current_position_value)
    decision["action"] = _derive_action_from_target_cash(normalized_cash, current_position_value, total_assets)


def _enforce_discrete_allocator_constraints(
    normalized_decisions: Dict[str, Dict[str, object]],
    symbols: Dict[str, Dict],
    total_assets: float,
    min_cash_ratio: float,
    max_positions: int,
    decision_space_cfg: Dict[str, object],
) -> tuple[Dict[str, Dict[str, object]], Dict[str, object]]:
    if decision_space_cfg.get("mode") != "discrete_target_state" or not normalized_decisions:
        return normalized_decisions, {}

    ordered_states = list(decision_space_cfg.get("ordered_states") or [])
    tolerance = _decision_cash_tolerance(total_assets, 0.0)
    safe_max_positions = max(0, int(max_positions)) if max_positions < 999999 else 999999
    max_invested = max(0.0, float(total_assets) * (1.0 - max(0.0, float(min_cash_ratio))))

    level_options: Dict[str, List[tuple[str, float]]] = {}
    level_indices: Dict[str, int] = {}
    priority_scores: Dict[str, float] = {}
    current_position_values: Dict[str, float] = {}
    original_targets: Dict[str, tuple[str, float]] = {}

    for symbol, decision in normalized_decisions.items():
        current_position_value = float((symbols.get(symbol, {}).get("features", {}).get("position_state") or {}).get("current_position_value", 0.0))
        current_position_values[symbol] = current_position_value
        level_options[symbol] = _build_allocator_levels(decision, current_position_value, total_assets, ordered_states)
        level_indices[symbol] = 0
        priority_scores[symbol] = _allocator_priority_score(decision, current_position_value, total_assets)
        original_targets[symbol] = (
            str(decision.get("target_state", "hold") or "hold").strip().lower(),
            max(0.0, float(decision.get("target_cash_amount", current_position_value) or 0.0)),
        )

    def _current_target_cash(symbol: str) -> float:
        _, level_cash = level_options[symbol][level_indices[symbol]]
        return float(level_cash)

    def _current_target_state(symbol: str) -> str:
        level_name, _ = level_options[symbol][level_indices[symbol]]
        return str(level_name)

    def _active_symbols() -> List[str]:
        return [symbol for symbol in normalized_decisions.keys() if _current_target_cash(symbol) > tolerance]

    def _invested_total() -> float:
        return sum(_current_target_cash(symbol) for symbol in normalized_decisions.keys())

    adjustment_notes: List[str] = []

    if safe_max_positions < 999999:
        active_symbols = _active_symbols()
        if len(active_symbols) > safe_max_positions:
            symbols_to_close = sorted(
                active_symbols,
                key=lambda symbol: (priority_scores.get(symbol, 0.0), original_targets[symbol][1], symbol),
            )[: len(active_symbols) - safe_max_positions]
            for symbol in symbols_to_close:
                level_indices[symbol] = len(level_options[symbol]) - 1
                adjustment_notes.append(f"{symbol}: forced to flat due to max_positions={safe_max_positions}")

    while _invested_total() > max_invested + tolerance:
        reducible_symbols = [
            symbol
            for symbol in normalized_decisions.keys()
            if level_indices[symbol] + 1 < len(level_options[symbol])
        ]
        if not reducible_symbols:
            break

        symbol_to_reduce = min(
            reducible_symbols,
            key=lambda symbol: (
                priority_scores.get(symbol, 0.0),
                _current_target_cash(symbol),
                symbol,
            ),
        )
        previous_state = _current_target_state(symbol_to_reduce)
        level_indices[symbol_to_reduce] += 1
        adjustment_notes.append(
            f"{symbol_to_reduce}: reduced from {previous_state} to "
            f"{level_options[symbol_to_reduce][level_indices[symbol_to_reduce]][0]} for budget"
        )

    allocator_adjustments = 0
    for symbol, decision in normalized_decisions.items():
        current_position_value = current_position_values[symbol]
        original_state, original_cash = original_targets[symbol]
        final_state, final_cash = level_options[symbol][level_indices[symbol]]
        _apply_allocator_level(decision, final_state, final_cash, current_position_value, total_assets)

        if abs(float(final_cash) - float(original_cash)) > tolerance or final_state != original_state:
            allocator_adjustments += 1
            reasons = _coerce_reasons(decision.get("reasons"))
            reasons.append(
                f"Allocator adjusted target from {original_state} to {final_state} to satisfy portfolio cash and concentration limits"
            )
            decision["reasons"] = reasons

    final_invested = _invested_total()
    final_cash = max(0.0, float(total_assets) - float(final_invested))
    return normalized_decisions, {
        "adjustments": allocator_adjustments,
        "active_positions": len(_active_symbols()),
        "final_cash": final_cash,
        "final_cash_ratio": (final_cash / float(total_assets)) if float(total_assets) > 0 else 0.0,
        "notes": adjustment_notes[-10:],
    }


def _filter_hallucination_decisions(decisions_data: dict, valid_symbols: set) -> dict:
    """
    Filter out hallucinated decisions, keeping only actual input stock symbols
    
    Args:
        decisions_data: Decision data dictionary returned by LLM
        valid_symbols: Set of valid stock symbols actually input
        
    Returns:
        Filtered decision data dictionary
    """
    if not isinstance(decisions_data, dict):
        return decisions_data
    
    filtered_decisions = {}
    hallucinated_symbols = []
    
    for symbol, decision in decisions_data.items():
        if symbol in valid_symbols:
            filtered_decisions[symbol] = decision
        else:
            hallucinated_symbols.append(symbol)
    
    # Log filtered hallucinated decisions
    if hallucinated_symbols:
        logger.warning(f"[HALLUCINATION_FILTER] Filtered hallucinated decision symbols: {hallucinated_symbols}")
        logger.info(f"[FILTER_STATS] Valid decisions: {len(filtered_decisions)}, Filtered decisions: {len(hallucinated_symbols)}")
    
    return filtered_decisions


def _validate_decision_logic(action: str, target_cash_amount: float, current_position_value: float) -> bool:
    """
    Validate whether decision logic is reasonable
    
    Args:
        action: Decision action ("increase", "decrease", "hold", "close")
        target_cash_amount: Target cash amount
        current_position_value: Current position value
        
    Returns:
        bool: True if logic is reasonable, False if logic is unreasonable
    """
    try:
        action = str(action).lower().strip()
        target_cash_amount = float(target_cash_amount)
        current_position_value = float(current_position_value)
        
        # Increase operation: target amount should be greater than current position value
        if action == "increase":
            if target_cash_amount <= current_position_value:
                logger.warning(f"[VALIDATION_ERROR] Increase operation unreasonable: target_cash_amount({target_cash_amount:.2f}) <= current_position_value({current_position_value:.2f})")
                return False
        
        # Decrease operation: target amount should be less than current position value
        elif action == "decrease":
            if target_cash_amount >= current_position_value:
                logger.warning(f"[VALIDATION_ERROR] Decrease operation unreasonable: target_cash_amount({target_cash_amount:.2f}) >= current_position_value({current_position_value:.2f})")
                return False
        
        # Close operation: target amount should be 0 or close to 0
        elif action == "close":
            if target_cash_amount > 0.01:  # Allow small margin of error
                logger.warning(f"[VALIDATION_ERROR] Close operation unreasonable: target_cash_amount({target_cash_amount:.2f}) > 0")
                return False
        
        # Hold operation: target amount should equal current position value (allow small fluctuations)
        elif action == "hold":
            # For hold operation, allow certain tolerance range
            tolerance = max(current_position_value * 0.01, 100.0)  # 1% or 100 unit tolerance
            if abs(target_cash_amount - current_position_value) > tolerance:
                logger.warning(f"[VALIDATION_WARNING] Hold operation has significant deviation: target_cash_amount({target_cash_amount:.2f}) vs current_position_value({current_position_value:.2f}), difference: {abs(target_cash_amount - current_position_value):.2f}")
                # For hold operation, only warning, don't return False
        
        logger.info(f"[VALIDATION_OK] {action} operation validation passed: target_cash_amount({target_cash_amount:.2f}) vs current_position_value({current_position_value:.2f})")
        return True
        
    except Exception as e:
        logger.error(f"[VALIDATION_ERROR] Error occurred during validation: {e}")
        return False


def decide_batch_dual_agent(features_list: List[Dict], cfg: Dict | None = None, enable_llm: bool = True, 
                           bars_data: Dict[str, Dict] = None, 
                           run_id: Optional[str] = None, previous_decisions: Optional[Dict] = None, 
                           decision_history: Optional[Dict[str, List[Dict]]] = None, ctx: Dict = None, 
                           rejected_orders: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """
    Dual agent batch decision making. Input is features list, returns {symbol: decision_output_dict}.
    
    This function implements the dual-agent architecture:
    1. Step 1: Fundamental Filter Agent - determines which stocks need fundamental analysis
    2. Step 2: Enhanced Feature Construction - builds features with/without fundamental data based on filtering
    3. Step 3: Decision Agent - makes final trading decisions
    
    Args:
        features_list: Input features list
        cfg: Configuration dictionary containing llm sub-configuration
        enable_llm: Whether to enable LLM, if False then fallback to neutral decisions
        bars_data: Raw historical data dictionary {symbol: {"bars_day": df}} for feature construction
        run_id: Backtest run ID for organizing LLM cache directory
        previous_decisions: Previous decision results for backward compatibility
        decision_history: Long-term historical decision records
        ctx: Context dictionary containing portfolio information
        rejected_orders: List of rejected order information for retry logic
        
    Returns:
        Dictionary {symbol: decision_dict, "__meta__": meta_dict}
    """
    
    results: Dict[str, Dict] = {}
    meta_agg: Dict[str, object] = {"calls": 0, "cache_hits": 0, "parse_errors": 0, "latency_ms_sum": 0, 
                                  "tokens_prompt": 0, "tokens_completion": 0, "prompt_version": None}
    decision_space_cfg = _get_decision_space_config(cfg)
    
    # If LLM not enabled, directly fallback to hold
    if not enable_llm:
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            current_position_value = float((item.get("features", {}).get("position_state") or {}).get("current_position_value", 0.0))
            hold_decision = _build_hold_decision(
                current_position_value,
                f"LLM not enabled, {symbol} maintains current position",
                decision_space_cfg,
            )
            hold_decision.update({
                "analysis_excerpt": "",
                "tech_score": 0.5,
                "sent_score": 0.0,
                "event_risk": "normal"
            })
            results[symbol] = round_numbers_in_obj(hold_decision, 2)
        results["__meta__"] = meta_agg
        return results
    
    logger.info(f"🚀 [DUAL_AGENT] Starting dual-agent decision process for {len(features_list)} stocks")
    
    try:
        # Step 1: Fundamental Filter Agent - determines which stocks need fundamental analysis
        logger.info(f"📊 [DUAL_AGENT] Step 1: Calling fundamental filter agent")
        filter_result = filter_stocks_needing_fundamental(
            features_list=features_list,
            cfg=cfg,
            enable_llm=enable_llm,
            run_id=run_id,
            ctx=ctx,
            previous_decisions=previous_decisions,
            decision_history=decision_history
        )
        
        stocks_need_fundamental = filter_result.get("stocks_need_fundamental", [])
        reasoning = filter_result.get("reasoning", {})
        
        logger.info(f"✅ [DUAL_AGENT] Filter completed: {len(stocks_need_fundamental)}/{len(features_list)} stocks need fundamental analysis")
        logger.info(f"📋 [DUAL_AGENT] Stocks needing fundamental: {stocks_need_fundamental}")
        
        # Step 2: Enhanced Feature Construction - build features with/without fundamental data
        logger.info(f"🔧 [DUAL_AGENT] Step 2: Building enhanced features based on filtering results")
        enhanced_features_list = []
        
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            features = item.get("features", {})
            
            # Conditionally rebuild features based on filter results
            enhanced_features = None
            rebuild_success = False
            
            # Check if bars_data is available for rebuilding
            if bars_data and symbol in bars_data:
                try:
                    original_data = bars_data.get(symbol, {})
                    
                    # Validate required data components
                    required_keys = ["bars_day", "snapshot", "position_state"]
                    missing_keys = [key for key in required_keys if key not in original_data]
                    
                    if missing_keys:
                        logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: Missing data components for rebuild: {missing_keys}")
                    
                    # Determine whether to include fundamental data based on filter results
                    exclude_fundamental = symbol not in stocks_need_fundamental
                    
                    # For stocks needing fundamental data, preserve existing news data from original features
                    # to avoid losing news information during feature rebuilding
                    original_news_events = features.get("news_events", {}).get("top_k_events", [])
                    original_image_inputs = features.get("news_events", {}).get("image_inputs", [])
                    preserved_news_items = []
                    image_lookup = {}

                    for entry in original_image_inputs:
                        if isinstance(entry, dict):
                            event_index = entry.get("event_index")
                            if isinstance(event_index, int):
                                image_lookup[event_index] = entry

                    if original_news_events and original_news_events != ["No news data available"]:
                        # Convert existing news events back to news_items format for rebuild
                        for idx, event in enumerate(original_news_events, start=1):
                            if isinstance(event, dict):
                                preserved_news_items.append(event)
                            elif isinstance(event, str):
                                rebuilt_item = {"title": event, "description": ""}
                                image_entry = image_lookup.get(idx)
                                if image_entry:
                                    rebuilt_item["image"] = image_entry.get("image_url", "")
                                preserved_news_items.append(rebuilt_item)

                    # Prefer raw news items because they preserve image metadata and source fields.
                    raw_news_items = original_data.get("news_items", [])
                    news_items_for_rebuild = raw_news_items or preserved_news_items
                    
                    # Check configuration for include_current_price setting
                    include_current_price = (cfg or {}).get("features", {}).get("include_current_price", False)
                    
                    # Attempt to rebuild features with appropriate fundamental data inclusion
                    rebuilt_features = build_features_for_prompt(
                        bars_day=original_data.get("bars_day"), 
                        snapshot=original_data.get("snapshot", {}),
                        news_items=news_items_for_rebuild,
                        position_state=original_data.get("position_state", {}),
                        details=original_data.get("details", {}),
                        config=cfg or {},
                        include_price=include_current_price,  # Use configuration setting
                        exclude_fundamental=exclude_fundamental
                    )
                    
                    # Extract only the features part to avoid double nesting
                    # rebuilt_features has structure {"symbol": "...", "features": {...}}
                    # We only need the "features" part here
                    enhanced_features = rebuilt_features.get("features", {})
                    
                    rebuild_success = True
                    
                    if symbol in stocks_need_fundamental:
                        logger.debug(f"📊 [DUAL_AGENT] {symbol}: Successfully rebuilt features WITH fundamental data")
                    else:
                        logger.debug(f"🎯 [DUAL_AGENT] {symbol}: Successfully rebuilt features WITHOUT fundamental data")
                        
                except Exception as e:
                    logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: Failed to rebuild features: {e}")
                    enhanced_features = None
            else:
                logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: bars_data not available for feature rebuild")
            
            # Fallback: use original features if rebuild failed or data unavailable
            if not rebuild_success or enhanced_features is None:
                enhanced_features = features.copy()
                logger.info(f"🔄 [DUAL_AGENT] {symbol}: Using original features as fallback (may lack fundamental data)")
            
            # Add filter reasoning to the enhanced features
            enhanced_features["filter_reasoning"] = reasoning.get(symbol, "No reasoning provided")
            
            enhanced_item = {
                "symbol": symbol,
                "features": enhanced_features
            }
            enhanced_features_list.append(enhanced_item)
            
        # Calculate statistics for monitoring
        stocks_with_fundamental = len(stocks_need_fundamental)
        stocks_without_fundamental = len(enhanced_features_list) - stocks_with_fundamental
        
        logger.info(f"✅ [DUAL_AGENT] Enhanced features built for {len(enhanced_features_list)} stocks:")
        logger.info(f"   📊 {stocks_with_fundamental} stocks WITH fundamental data: {list(stocks_need_fundamental)}")
        logger.info(f"   🎯 {stocks_without_fundamental} stocks WITHOUT fundamental data")
        
        # Log feature optimization for monitoring
        if stocks_with_fundamental > 0:
            logger.info(f"🔧 [DUAL_AGENT] Feature enhancement: Added fundamental data for {stocks_with_fundamental} stocks requiring deeper analysis")
        if stocks_without_fundamental > 0:
            logger.info(f"🔧 [DUAL_AGENT] Feature optimization: Excluded fundamental data for {stocks_without_fundamental} stocks to reduce noise")
        
        # Step 3: Decision Agent - makes final trading decisions using enhanced features
        logger.info(f"🎯 [DUAL_AGENT] Step 3: Calling decision agent with enhanced features")
        
        # Use the decision agent prompt from config
        prompt_name = _resolve_decision_prompt_name(cfg, decision_space_cfg)
        system_prompt = _load_prompt(prompt_name)
        system_prompt = _append_active_decision_space_note(system_prompt, decision_space_cfg)
        system_prompt = _append_portfolio_constraint_note(system_prompt, cfg)
        prompt_version = _prompt_version(prompt_name)
        if decision_space_cfg.get("mode") == "discrete_target_state":
            prompt_version = f"{prompt_version}[discrete_target_state]"
        meta_agg["prompt_version"] = prompt_version
        
        # Get LLM configuration for decision agent
        # Use the already selected llm config (processed by --llm-profile in run_backtest.py)
        llm_cfg_raw = (cfg or {}).get("llm", {})
        
        # If no llm config found, this is an error - don't fallback to defaults
        if not llm_cfg_raw:
            logger.error("❌ No LLM configuration found! Please specify --llm-profile parameter.")
            raise ValueError("No LLM configuration found. Use --llm-profile parameter to specify configuration.")
        
        # Get dual agent decision configuration
        dual_agent_cfg = (cfg or {}).get("agents", {}).get("dual_agent", {})
        decision_cfg = dual_agent_cfg.get("decision_agent", {})
        
        # Read global cache.mode configuration
        cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower()

        llm_cfg = LLMConfig(
            provider=str(llm_cfg_raw.get("provider", "openai-compatible")),
            base_url=str(llm_cfg_raw.get("base_url", "https://api.openai.com/v1")),
            # Use dedicated decision_agent model, fallback to llm_profile model, then other fallbacks
            model=str(decision_cfg.get("model") or llm_cfg_raw.get("decision_agent_model") or llm_cfg_raw.get("model") or llm_cfg_raw.get("single_agent_model") or llm_cfg_raw.get("analyzer_model", "gpt-4o-mini")),
            temperature=float(decision_cfg.get("temperature", 0.7)),
            max_tokens=int(decision_cfg.get("max_tokens", 8000)),
            seed=llm_cfg_raw.get("seed"),
            timeout_sec=float(llm_cfg_raw.get("timeout_sec", 60)),
            max_retries=int(llm_cfg_raw.get("retry", {}).get("max_retries", 3)),
            backoff_factor=float(llm_cfg_raw.get("retry", {}).get("backoff_factor", 0.5)),
            cache_enabled=bool(llm_cfg_raw.get("cache", {}).get("enabled", True)),
            cache_ttl_hours=int(llm_cfg_raw.get("cache", {}).get("ttl_hours", 24)),
            budget_prompt_tokens=int(llm_cfg_raw.get("budget", {}).get("max_prompt_tokens", 200_000)),
            budget_completion_tokens=int(llm_cfg_raw.get("budget", {}).get("max_completion_tokens", 200_000)),
            auth_required=llm_cfg_raw.get("auth_required"),
            api_key_env=str(llm_cfg_raw.get("api_key_env", "OPENAI_API_KEY")),
            supports_image_input=llm_cfg_raw.get("supports_image_input"),
            max_input_images=int(llm_cfg_raw.get("max_input_images", 8)),
        )

        # Refine LLM cache read/write switches based on cache.mode
        if cache_mode == "off":
            llm_cfg.cache_read_enabled = False
            llm_cfg.cache_write_enabled = False
        elif cache_mode == "llm_write_only":
            llm_cfg.cache_read_enabled = False
            llm_cfg.cache_write_enabled = True
        elif cache_mode == "full":
            # If read cache is not available now, set read to False; keep True for future enablement
            llm_cfg.cache_read_enabled = True
            llm_cfg.cache_write_enabled = True
        else:
            # Unknown value: fall back to profile defaults
            llm_cfg.cache_read_enabled = None
            llm_cfg.cache_write_enabled = None
        
        client = LLMClient()
        
        return _decide_batch_portfolio_dual_agent(
            enhanced_features_list,
            llm_cfg,
            system_prompt,
            client,
            meta_agg,
            cfg,
            bars_data,
            run_id,
            previous_decisions,
            decision_history,
            ctx,
            rejected_orders,
        )
    
    except Exception as e:
        logger.error(f"❌ [DUAL_AGENT] Error during dual-agent processing: {e}")
        logger.exception("Detailed error:")
        
        # Fallback to hold decisions for all stocks
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            current_position_value = float((item.get("features", {}).get("position_state") or {}).get("current_position_value", 0.0))
            hold_decision = _build_hold_decision(
                current_position_value,
                f"Dual-agent error ({str(e)[:50]}), {symbol} maintains current position",
                decision_space_cfg,
            )
            hold_decision.update({
                "analysis_excerpt": "",
                "tech_score": 0.5,
                "sent_score": 0.0,
                "event_risk": "normal"
            })
            results[symbol] = round_numbers_in_obj(hold_decision, 2)
        results["__meta__"] = meta_agg
        return results


def _decide_batch_portfolio_dual_agent(features_list: List[Dict], llm_cfg: LLMConfig, system_prompt: str,
                                      client: LLMClient, meta_agg: Dict, cfg: Dict, bars_data: Dict, 
                                      run_id: Optional[str], previous_decisions: Optional[Dict] = None, 
                                      decision_history: Optional[Dict[str, List[Dict]]] = None, ctx: Dict = None, 
                                      rejected_orders: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """Dual-agent batch portfolio decision making with comprehensive retry mechanism"""
    results = {}
    decision_space_cfg = _get_decision_space_config(cfg)
    
    # Build input format conforming to prompt template
    symbols = {}
    total_current_position = 0.0
    
    for item in features_list:
        symbol = item.get("symbol", "UNKNOWN")
        features = item.get("features", {})
        
        # Accumulate current total position value
        current_pos_value = features.get("position_state", {}).get("current_position_value", 0.0)
        total_current_position += current_pos_value
        
        # Build symbols format conforming to template (enhanced features include filter_reasoning)
        symbols[symbol] = {
            "features": features
        }
    
    # Build portfolio info (similar to single agent)
    portfolio_cfg = cfg.get("portfolio", {}) if cfg else {}
    
    if ctx and "portfolio" in ctx:
        current_cash = float(ctx["portfolio"].cash)
        total_assets = current_cash + total_current_position
        available_cash = current_cash
        available_cash_ratio = current_cash / total_assets if total_assets > 0 else 0.0
        remaining_cash_ratio = available_cash_ratio
    else:
        total_assets = portfolio_cfg.get("total_cash", 100000)  # Keep consistent with fundamental_filter_agent
        available_cash = total_assets - total_current_position
        remaining_cash_ratio = available_cash / total_assets if total_assets > 0 else 0.0
        available_cash_ratio = remaining_cash_ratio
    
    # Get portfolio-wide constraint requirements
    min_cash_ratio = portfolio_cfg.get("min_cash_ratio", 0.0)
    try:
        max_positions = int(((cfg or {}).get("backtest", {}) or {}).get("max_positions", 999999))
    except Exception:
        max_positions = 999999
    
    # Build historical decision records
    if decision_history:
        logger.info(f"[DEBUG] Dual agent decision: Using long-term historical records, containing history of {len(decision_history)} symbols")
        history = decision_history
    else:
        logger.info(f"[DEBUG] Dual agent decision: Building historical records from previous_decisions, previous_decisions={'available' if previous_decisions else 'none'}")
        # Build current_features for historical record correction
        current_features = {}
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            features = item.get("features", {})
            current_features[symbol] = features
        
        history = _build_history_from_previous_decisions(previous_decisions, current_features)
        logger.info(f"[DEBUG] Dual agent decision: Historical record construction completed, containing history of {len(history)} symbols")
    
    # Build complete input data
    portfolio_input = {
        "portfolio_info": {
            "total_assets": total_assets,
            "available_cash": available_cash,
            "position_value": total_current_position,
        },
        "symbols": symbols,
        "history": history
    }
    
    # Build base user prompt
    base_user_prompt = json.dumps(round_numbers_in_obj(portfolio_input, 2), ensure_ascii=False)
    
    # Try to extract trading date with enhanced fallback
    trade_date = None
    try:
        if features_list and len(features_list) > 0:
            # Try multiple sources for date extraction
            for item in features_list:
                features = item.get("features", {})
                market_data = features.get("market_data", {})
                
                # Method 1: Direct date field
                if "date" in market_data:
                    trade_date = market_data["date"]
                    break
                    
                # Method 2: Timestamp field
                elif "timestamp" in market_data:
                    timestamp = market_data["timestamp"]
                    if isinstance(timestamp, str):
                        try:
                            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                            trade_date = dt.strftime("%Y-%m-%d")
                            break
                        except:
                            pass
            
            # Method 3: Try to extract from context if available
            if not trade_date and ctx:
                if "date" in ctx:
                    ctx_date = ctx["date"]
                    if hasattr(ctx_date, 'strftime'):
                        trade_date = ctx_date.strftime("%Y-%m-%d")
                    elif isinstance(ctx_date, str):
                        trade_date = ctx_date
                        
            # Method 4: Fallback to current date for consistency
            if not trade_date:
                trade_date = datetime.now().strftime("%Y-%m-%d")
                logger.warning(f"[DUAL_AGENT_DECISION] No date found in features, using current date: {trade_date}")
                
    except Exception as e:
        # Final fallback to current date
        trade_date = datetime.now().strftime("%Y-%m-%d")
        logger.warning(f"[DUAL_AGENT_DECISION] Error extracting date: {e}, using current date: {trade_date}")
    
    # Get unified retry configuration
    retry_cfg = cfg.get("agents", {}).get("retry", {}) if cfg else {}
    max_unified_retries = int(retry_cfg.get("max_attempts", 3))
    
    # Check if order rejection info is included and determine starting retry count
    order_rejection_info = []
    current_retry_attempt = 0
    engine_retry_count = 0  # Track engine-level retries separately
    
    if rejected_orders:
        # Create a lookup for rejected orders by symbol
        rejected_by_symbol = {order.get("symbol"): order for order in rejected_orders}
        
        for symbol in symbols.keys():
            rejection_info = rejected_by_symbol.get(symbol)
            
            if rejection_info:
                rejection_reason = rejection_info.get("reason", "Order rejected")
                rejection_context = rejection_info.get("context", {})
                
                # Track engine retry count from rejected orders
                engine_retry_count = max(engine_retry_count, rejection_info.get("retry_count", 0))
                
                # Check if this is a portfolio-wide rebalancing issue
                is_portfolio_rebalance = rejection_context.get("portfolio_rebalance_needed", False)
                
                if is_portfolio_rebalance:
                    # Portfolio-wide cash constraint issue
                    total_required = rejection_context.get("total_cash_required_all_orders", 0)
                    available_cash = rejection_context.get("available_cash", 0)
                    cash_shortfall = rejection_context.get("cash_shortfall", 0)
                    suggestion = rejection_context.get("suggestion", "")
                    
                    rejection_prompt = f"\\n\\n🚨 CRITICAL: PORTFOLIO-WIDE CASH CONSTRAINT VIOLATION\\n"
                    rejection_prompt += f"❌ Previous attempt failed due to insufficient total cash for all positions\\n"
                    rejection_prompt += f"📊 Financial Summary:\\n"
                    rejection_prompt += f"   • Available Cash: ${available_cash:,.2f}\\n"
                    rejection_prompt += f"   • Total Required: ${total_required:,.2f}\\n"
                    rejection_prompt += f"   • Cash Shortfall: ${cash_shortfall:,.2f}\\n"
                    rejection_prompt += f"\\n💡 ACTION REQUIRED: {suggestion}\\n"
                    rejection_prompt += f"\\n🔄 You MUST rebalance the ENTIRE portfolio to fit within the available cash budget.\\n"
                    rejection_prompt += f"Consider reducing all position sizes proportionally or selecting fewer stocks.\\n"
                    
                    # Add this global message only once (for the first rejected symbol)
                    if len(order_rejection_info) == 0:
                        order_rejection_info.append(rejection_prompt)
                else:
                    # Individual order rejection (legacy logic)
                    rejection_prompt = f"\\n\\n❌ IMPORTANT: Previous order for {symbol} was rejected.\\n"
                    rejection_prompt += f"Rejection reason: {rejection_reason}\\n"
                    
                    if rejection_context:
                        rejection_prompt += f"Additional context: {rejection_context}\\n"
                    
                    rejection_prompt += f"Please provide a corrected decision for {symbol} that addresses this rejection.\\n"
                    order_rejection_info.append(rejection_prompt)
                
                # Set current retry attempt to engine retry count for rejected orders
                current_retry_attempt = engine_retry_count
    
    # Unified retry loop - comprehensive validation and retry mechanism
    retry_count = current_retry_attempt
    data = None
    decisions_data = None
    
    # Check if this is an engine-level retry (rejected orders)
    is_engine_retry = rejected_orders is not None and len(rejected_orders) > 0
    if is_engine_retry:
        logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Engine-level retry detected ({len(rejected_orders)} rejected orders), engine_retry_count={engine_retry_count}")
    else:
        logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Starting unified retry loop: current_retry={retry_count}, max_total_retries={max_unified_retries}")
    
    # Global retry limit: total retries (engine + LLM) cannot exceed max_attempts
    while True:
        total_retry_attempt = engine_retry_count + retry_count
        
        if total_retry_attempt >= max_unified_retries:
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Global retry limit reached: engine_retries={engine_retry_count} + llm_retries={retry_count} = {total_retry_attempt} >= {max_unified_retries}")
            break
        # Build user prompt for this attempt
        if order_rejection_info:
            rejection_prompt = "\\n\\n" + "\\n".join(order_rejection_info)
            user_prompt = base_user_prompt + rejection_prompt
            logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Including order rejection prompts for {len(order_rejection_info)} stocks")
        else:
            user_prompt = base_user_prompt
        
        # Add any additional retry notes from previous validation failures
        if retry_count > current_retry_attempt and "retry_notes" in locals():
            user_prompt += "\\n\\n" + retry_notes
            logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Including retry notes from previous validation failures")
        
        # Calculate total retry attempt (engine retries + LLM retries)
        total_retry_attempt = engine_retry_count + retry_count
        
        # Log retry breakdown for debugging
        if total_retry_attempt > 0:
            logger.info(f"[RETRY_BREAKDOWN] Engine retries: {engine_retry_count}, LLM retries: {retry_count}, Total: {total_retry_attempt}")
        
        # Call LLM with complete retry attempt info
        data, meta = client.generate_json("decision_agent", llm_cfg, system_prompt, user_prompt,
                                         trade_date=trade_date, run_id=run_id, retry_attempt=total_retry_attempt)
        
        meta_agg["calls"] = int(meta_agg["calls"]) + 1
        meta_agg["cache_hits"] = int(meta_agg["cache_hits"]) + (1 if meta.get("cached") else 0)
        meta_agg["latency_ms_sum"] = int(meta_agg["latency_ms_sum"]) + int(meta.get("latency_ms", 0))
        usage = meta.get("usage", {})
        meta_agg["tokens_prompt"] = int(meta_agg["tokens_prompt"]) + int(usage.get("prompt_tokens", 0))
        meta_agg["tokens_completion"] = int(meta_agg["tokens_completion"]) + int(usage.get("completion_tokens", 0))
        
        # Parse batch decision results
        if not data or not isinstance(data, dict):
            meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
            
            # Check if this is a truncation issue (finish_reason: length)
            is_truncated = False
            if hasattr(meta, 'get') and meta.get('raw_response', {}).get('choices', []):
                finish_reason = meta['raw_response']['choices'][0].get('finish_reason')
                if finish_reason == 'length':
                    is_truncated = True
                    logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Response was truncated due to token limit")
            
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: LLM returned invalid data format (truncated: {is_truncated})")
            
            # Check if we can continue retrying (global limit)
            next_total_attempt = engine_retry_count + retry_count + 1
            if next_total_attempt < max_unified_retries:
                # For truncated responses, add instruction for more concise output
                if is_truncated:
                    logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Adding concise output instruction for truncation retry")
                    
                    # Add instruction for more concise output
                    if "user_prompt" in locals():
                        user_prompt += "\n\nIMPORTANT: Due to previous response truncation, please provide a more concise analysis while maintaining all required JSON decision fields."
                
                retry_count += 1
                continue
            else:
                # Final attempt failed, fallback to hold decisions
                logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to invalid data format (engine: {engine_retry_count}, llm: {retry_count})")
                for symbol in symbols.keys():
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    hold_decision = _build_hold_decision(
                        current_position_value,
                        f"Dual-agent retry failed: invalid data format after {max_unified_retries} attempts",
                        decision_space_cfg,
                    )
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
                results["__meta__"] = meta_agg
                return results
        
        # Process decisions
        decisions_data = data.get("decisions", data)  # Handle both formats
        
        # Additional data validation and cleanup
        if not isinstance(decisions_data, dict):
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Unable to extract valid decision data from LLM response")
            meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
            
            # Check if this is a truncation issue and try to extract partial decisions
            partial_decisions = None
            if hasattr(meta, 'get') and meta.get('raw_response', {}).get('choices', []):
                finish_reason = meta['raw_response']['choices'][0].get('finish_reason')
                if finish_reason == 'length':
                    logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempting to extract partial decisions from truncated response")
                    # Try to extract partial decisions using enhanced JSON parsing
                    try:
                        from stockbench.llm.llm_client import LLMClient
                        temp_client = LLMClient()
                        raw_content = meta.get('raw_response', {}).get('choices', [{}])[0].get('message', {}).get('content', '')
                        if raw_content:
                            partial_data = temp_client._extract_json_with_improved_logic(raw_content)
                            if partial_data and isinstance(partial_data, dict) and "decisions" in partial_data:
                                partial_decisions = partial_data.get("decisions")
                                logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Successfully extracted {len(partial_decisions)} partial decisions")
                    except Exception as e:
                        logger.debug(f"[DUAL_AGENT_UNIFIED_RETRY] Failed to extract partial decisions: {e}")
            
            if partial_decisions and isinstance(partial_decisions, dict) and len(partial_decisions) > 0:
                # Use partial decisions if we got some
                decisions_data = partial_decisions
                logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Using {len(partial_decisions)} partial decisions from truncated response")
            else:
                # Check if we can continue retrying (global limit)
                next_total_attempt = engine_retry_count + retry_count + 1
                if next_total_attempt < max_unified_retries:
                    retry_count += 1
                    continue
                else:
                    # Final attempt failed, fallback to hold decisions
                    logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to unparseable data (engine: {engine_retry_count}, llm: {retry_count})")
                for symbol in symbols.keys():
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    hold_decision = _build_hold_decision(
                        current_position_value,
                        f"Dual-agent retry failed: unparseable data after {max_unified_retries} attempts",
                        decision_space_cfg,
                    )
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
                results["__meta__"] = meta_agg
                return results
        
        # Filter hallucinated decisions
        decisions_data = _filter_hallucination_decisions(decisions_data, set(symbols.keys()))
        
        # Comprehensive validation: logic validation and fund constraints
        logic_validation_failed = False
        cash_shortage_detected = False
        cash_ratio_violation = False
        invalid_decisions = []
        normalized_decisions: Dict[str, Dict[str, object]] = {}
        
        # Validate logic and normalize decisions first
        for symbol in symbols.keys():
            symbol_decision = decisions_data.get(symbol)
            
            if isinstance(symbol_decision, dict):
                try:
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    normalized_decision = _normalize_symbol_decision(
                        symbol,
                        symbol_decision,
                        symbols[symbol]["features"],
                        total_assets,
                        decision_space_cfg,
                    )
                    normalized_decisions[symbol] = normalized_decision
                    action = str(normalized_decision.get("action", "hold")).lower().strip()
                    target_cash_amount = float(normalized_decision.get("target_cash_amount", current_position_value))
                    
                    # Validate decision logic using the same function as single agent
                    if not _validate_decision_logic(action, target_cash_amount, current_position_value):
                        logic_validation_failed = True
                        invalid_decisions.append({
                            "symbol": symbol,
                            "action": action,
                            "target_cash_amount": target_cash_amount,
                            "current_position_value": current_position_value,
                            "target_state": normalized_decision.get("target_state"),
                        })
                        logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: {symbol} {action} operation logic unreasonable")
                        
                except (ValueError, TypeError) as e:
                    logic_validation_failed = True
                    invalid_decisions.append({
                        "symbol": symbol,
                        "error": str(e)
                    })
                    logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: {symbol} decision parsing failed: {e}")

        allocator_meta: Dict[str, object] = {}
        if not logic_validation_failed and decision_space_cfg.get("mode") == "discrete_target_state":
            normalized_decisions, allocator_meta = _enforce_discrete_allocator_constraints(
                normalized_decisions,
                symbols,
                total_assets,
                min_cash_ratio,
                max_positions,
                decision_space_cfg,
            )
            if allocator_meta.get("adjustments"):
                logger.info(
                    f"[ALLOCATOR] Applied {allocator_meta.get('adjustments')} adjustments, "
                    f"active_positions={allocator_meta.get('active_positions')}, "
                    f"final_cash_ratio={float(allocator_meta.get('final_cash_ratio', 0.0)):.3f}"
                )
                for note in allocator_meta.get("notes", []):
                    logger.info(f"[ALLOCATOR] {note}")

        # Calculate predicted cash usage and validate constraints after allocation
        predicted_cash_usage = 0.0
        predicted_final_position_value = 0.0
        for symbol, symbol_data in symbols.items():
            current_position_value = float(symbol_data["features"].get("position_state", {}).get("current_position_value", 0.0))
            normalized_decision = normalized_decisions.get(symbol)
            target_cash_amount = current_position_value
            if isinstance(normalized_decision, dict):
                try:
                    target_cash_amount = max(0.0, float(normalized_decision.get("target_cash_amount", current_position_value)))
                except Exception:
                    target_cash_amount = current_position_value
            predicted_final_position_value += target_cash_amount

        for symbol, normalized_decision in normalized_decisions.items():
            try:
                cash_change = float(normalized_decision.get("cash_change", 0.0))
                if cash_change > 0:
                    predicted_cash_usage += cash_change
            except Exception:
                pass
        
        # Check fund constraints
        available_cash_after = total_assets - predicted_final_position_value
        predicted_remaining_ratio = available_cash_after / total_assets if total_assets > 0 else 0.0
        
        if available_cash_after < 0:
            cash_shortage_detected = True
        
        if predicted_remaining_ratio < min_cash_ratio:
            cash_ratio_violation = True
        
        # If validation passed, process and return results
        if not logic_validation_failed and not cash_shortage_detected and not cash_ratio_violation:
            logger.info(f"✅ [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: All validations passed, processing results")
            if allocator_meta:
                meta_agg["allocator_adjustments"] = int(allocator_meta.get("adjustments", 0))
                meta_agg["allocator_active_positions"] = int(allocator_meta.get("active_positions", 0))
                meta_agg["allocator_final_cash_ratio"] = float(allocator_meta.get("final_cash_ratio", predicted_remaining_ratio))
            
            # Process each decision
            for symbol, symbol_data in symbols.items():
                current_position_value = symbol_data["features"].get("position_state", {}).get("current_position_value", 0.0)
                
                # Get decision for this symbol
                symbol_decision = normalized_decisions.get(symbol)
                
                if isinstance(symbol_decision, dict):
                    try:
                        normalized_result = {
                            "action": str(symbol_decision.get("action", "hold")).lower(),
                            "target_cash_amount": max(0.0, float(symbol_decision.get("target_cash_amount", current_position_value))),
                            "cash_change": float(symbol_decision.get("cash_change", 0.0)),
                            "reasons": _coerce_reasons(symbol_decision.get("reasons")),
                            "confidence": _coerce_confidence(symbol_decision.get("confidence", 0.5)),
                            "timestamp": datetime.now().isoformat(),
                        }
                        if symbol_decision.get("target_state") is not None:
                            normalized_result["target_state"] = str(symbol_decision.get("target_state"))
                        results[symbol] = round_numbers_in_obj(normalized_result, 2)
                        
                    except Exception as e:
                        # Parsing failed, use hold decision
                        meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
                        hold_decision = _build_hold_decision(
                            current_position_value,
                            f"Dual-agent decision parsing error: {str(e)[:50]}",
                            decision_space_cfg,
                        )
                        results[symbol] = round_numbers_in_obj(hold_decision, 2)
                else:
                    # No decision for this symbol, use hold
                    hold_decision = _build_hold_decision(
                        current_position_value,
                        "No decision provided by dual-agent",
                        decision_space_cfg,
                    )
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
            
            results["__meta__"] = meta_agg
            return results
        
        # Validation failed, prepare for retry
        if logic_validation_failed:
            logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Decision logic validation failed for {len(invalid_decisions)} decisions")
        if cash_shortage_detected:
            logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Insufficient available_cash: Expected remaining cash {available_cash_after:.2f} < 0")
        if cash_ratio_violation:
            logger.warning(f"⚠️ [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Expected remaining cash ratio {predicted_remaining_ratio:.3f} below minimum requirement {min_cash_ratio:.3f}")
        
        # Check if we can continue retrying (global limit)  
        next_total_attempt = engine_retry_count + retry_count + 1
        if next_total_attempt < max_unified_retries:
            logger.info(f"🔄 [DUAL_AGENT_UNIFIED_RETRY] Preparing retry {next_total_attempt + 1}/{max_unified_retries} (engine: {engine_retry_count}, llm: {retry_count + 1})")
            
            # Generate different retry prompts based on violation type
            retry_notes_list = []
            
            if logic_validation_failed:
                logic_error_details = []
                for invalid in invalid_decisions:
                    if "error" not in invalid:
                        if invalid["action"] == "increase":
                            logic_error_details.append(f"{invalid['symbol']}: increase operation requires target_cash_amount > current_position_value, but you set {invalid['target_cash_amount']:.0f} <= {invalid['current_position_value']:.0f}")
                        elif invalid["action"] == "decrease":
                            logic_error_details.append(f"{invalid['symbol']}: decrease operation requires target_cash_amount < current_position_value, but you set {invalid['target_cash_amount']:.0f} >= {invalid['current_position_value']:.0f}")
                        elif invalid["action"] == "close":
                            logic_error_details.append(f"{invalid['symbol']}: close operation requires target_cash_amount = 0, but you set {invalid['target_cash_amount']:.0f}")
                    else:
                        logic_error_details.append(f"{invalid['symbol']}: parsing error - {invalid['error']}")
                
                retry_notes_list.append(f"❌ DECISION LOGIC ERRORS: The following decisions have unreasonable logic:\\n" + "\\n".join(logic_error_details) + "\\nPlease correct these logical inconsistencies.")
            
            if cash_shortage_detected:
                retry_notes_list.append(f"💰 INSUFFICIENT FUNDS: Total predicted cash usage {predicted_cash_usage:.2f} exceeds available cash {available_cash:.2f}. Please reduce purchase amounts or choose different stocks.")
            
            if cash_ratio_violation:
                retry_notes_list.append(f"⚖️ CASH RATIO VIOLATION: Predicted remaining cash ratio {predicted_remaining_ratio:.3f} is below minimum requirement {min_cash_ratio:.3f}. Please maintain higher cash reserves.")
            
            retry_notes = "\\n\\n" + "\\n\\n".join(retry_notes_list)
            retry_count += 1
            continue
        else:
            # Final attempt failed, fallback to hold decisions
            logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to validation errors (engine: {engine_retry_count}, llm: {retry_count})")
            for symbol in symbols.keys():
                current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                hold_decision = _build_hold_decision(
                    current_position_value,
                    f"Dual-agent validation failed after {max_unified_retries} attempts",
                    decision_space_cfg,
                )
                results[symbol] = round_numbers_in_obj(hold_decision, 2)
            results["__meta__"] = meta_agg
            return results
    
    # Should not reach here, but fallback to hold decisions just in case
    logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] Unexpected exit from retry loop, using hold decisions")
    for symbol in symbols.keys():
        current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
        hold_decision = _build_hold_decision(
            current_position_value,
            "Dual-agent unexpected error, maintaining current position",
            decision_space_cfg,
        )
        results[symbol] = round_numbers_in_obj(hold_decision, 2)
    results["__meta__"] = meta_agg
    return results


def _build_history_from_previous_decisions(previous_decisions: Optional[Dict] = None, current_features: Optional[Dict] = None) -> Dict[str, List[Dict]]:
    """Build historical records from previous decision results (same as single agent)"""
    history = {}
    
    logger.info(f"[DEBUG] Building historical decision records: previous_decisions={'Yes' if previous_decisions else 'No'}")
    
    if not previous_decisions:
        logger.info(f"[DEBUG] No historical decision records, returning empty history")
        return history
    
    try:
        decisions = {k: v for k, v in previous_decisions.items() if k != "__meta__"}
        logger.info(f"[DEBUG] Extracted historical decisions for {len(decisions)} symbols")
        
        history_date = None
        if "__meta__" in previous_decisions:
            meta = previous_decisions["__meta__"]
            if isinstance(meta, dict) and "date" in meta:
                history_date = meta["date"]
                logger.info(f"[DEBUG] Historical decision date: {history_date}")
            else:
                logger.info(f"[DEBUG] No valid historical decision date found")
        else:
            logger.info(f"[DEBUG] No meta information found")
        
        for symbol, decision in decisions.items():
            if not isinstance(decision, dict):
                logger.info(f"[DEBUG] Skipping invalid decision record: {symbol}")
                continue
                
            # Fix historical record target_cash_amount logic
            action = decision.get("action", "hold")
            cash_change = decision.get("cash_change", 0.0)
            
            # For hold operations, if target_cash_amount is 0, try to get actual position value from current features
            target_cash_amount = decision.get("target_cash_amount", 0.0)
            if action == "hold" and target_cash_amount == 0.0 and cash_change == 0.0:
                # Try to get current position value from current_features
                if current_features and symbol in current_features:
                    current_pos = current_features[symbol].get("position_state", {}).get("current_position_value", 0.0)
                    if current_pos > 0:
                        target_cash_amount = current_pos
                        logger.debug(f"[DUAL_AGENT] Corrected Hold operation history record {symbol}: target_cash_amount corrected from 0.0 to {target_cash_amount}")
                    else:
                        logger.debug(f"[DUAL_AGENT] Hold operation history record {symbol}: current position is 0, keep target_cash_amount=0")
                else:
                    logger.debug(f"[DUAL_AGENT] Hold operation history record {symbol}: cannot get current position, keep target_cash_amount=0")
            
            history_record = {
                "date": history_date,
                "action": action,
                "target_state": decision.get("target_state"),
                "cash_change": cash_change,
                "target_cash_amount": target_cash_amount,
                "reasons": decision.get("reasons", []),
                "confidence": decision.get("confidence", 0.5)
            }
            
            history[symbol] = [history_record]
            logger.info(f"[DEBUG] Built historical record for {symbol}: action={history_record['action']}, cash_change={history_record['cash_change']}, target_cash_amount={history_record['target_cash_amount']}")
        
        logger.info(f"[DEBUG] Successfully built historical records for {len(history)} symbols")
            
    except Exception as e:
        logger.error(f"Failed to build historical records: {e}")
    
    return history
