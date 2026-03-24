#!/usr/bin/env bash
set -euo pipefail

# Initialize conda without relying on interactive shell startup files.
if [[ -f /home/azanette/miniconda3/etc/profile.d/conda.sh ]]; then
  # shellcheck disable=SC1091
  source /home/azanette/miniconda3/etc/profile.d/conda.sh
else
  export PATH="/home/azanette/miniconda3/bin:$PATH"
fi

cd /home/azanette/code/STOCK-AGENT/stockbench
mkdir -p /home/azanette/code/STOCK-AGENT/scripts/04_frequency/logs

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
LLM_MODEL="${LLM_MODEL:-deepseek-v3.1}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
DECISION_SPACE_MODE="${DECISION_SPACE_MODE:-continuous}"
TRADING_FREQUENCY="${TRADING_FREQUENCY:-every_2_trading_days}"
TOP_K_SHORTLIST_ENABLED="${TOP_K_SHORTLIST_ENABLED:-true}"
TOP_K_SHORTLIST_K="${TOP_K_SHORTLIST_K:-20}"
BACKTEST_MAX_POSITIONS="${BACKTEST_MAX_POSITIONS:-20}"

if [[ "${LLM_MODEL}" == "deepseek-v3" ]]; then
  RESOLVED_MODEL="deepseek-v3.1"
elif [[ "${LLM_MODEL}" == "gemini-3-flash-preview" ]]; then
  RESOLVED_MODEL="gemini-3-flash-preview-nothinking"
else
  RESOLVED_MODEL="${LLM_MODEL}"
fi

ts=$(date -u +%Y%m%d_%H%M%S)
safe_model="${RESOLVED_MODEL//\//_}"
safe_model="${safe_model//./_}"
safe_frequency="${TRADING_FREQUENCY//\//_}"
safe_frequency="${safe_frequency//./_}"
log="/home/azanette/code/STOCK-AGENT/scripts/04_frequency/logs/${safe_model}_${safe_frequency}_topk${TOP_K_SHORTLIST_K}_${DECISION_SPACE_MODE}_${ts}.log"

: "${WANDB_API_KEY:?WANDB_API_KEY is required to run backtests with W&B logging}"
if [[ "${LLM_PROFILE}" == "qingyuntop" ]]; then
  : "${QINGYUNTOP_API_KEY:?QINGYUNTOP_API_KEY is required for LLM_PROFILE=qingyuntop}"
fi

echo "START_DATE=${START_DATE}"
echo "END_DATE=${END_DATE}"
echo "LLM_PROFILE=${LLM_PROFILE}"
echo "REQUESTED_MODEL=${LLM_MODEL}"
echo "RESOLVED_MODEL=${RESOLVED_MODEL}"
echo "TRADING_FREQUENCY=${TRADING_FREQUENCY}"
echo "TOP_K_SHORTLIST_ENABLED=${TOP_K_SHORTLIST_ENABLED}"
echo "TOP_K_SHORTLIST_K=${TOP_K_SHORTLIST_K}"
echo "BACKTEST_MAX_POSITIONS=${BACKTEST_MAX_POSITIONS}"
echo "LOG=${log}"
echo "QINGYUNTOP_API_KEY=$([[ -n "${QINGYUNTOP_API_KEY:-}" ]] && echo present || echo missing)"
echo "WANDB_API_KEY=$([[ -n "${WANDB_API_KEY:-}" ]] && echo present || echo missing)"

TRADING_FREQUENCY="${TRADING_FREQUENCY}" \
TOP_K_SHORTLIST_ENABLED="${TOP_K_SHORTLIST_ENABLED}" \
TOP_K_SHORTLIST_K="${TOP_K_SHORTLIST_K}" \
BACKTEST_MAX_POSITIONS="${BACKTEST_MAX_POSITIONS}" \
DECISION_SPACE_MODE="${DECISION_SPACE_MODE}" \
conda run --no-capture-output -n stockagent \
  python -u run_direct.py "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${RESOLVED_MODEL}" "${NEWS_ENABLED}" "${DECISION_SPACE_MODE}" \
  > >(tee -a "$log") 2>&1
