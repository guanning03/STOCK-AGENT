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
mkdir -p /home/azanette/code/STOCK-AGENT/scripts/02_discrete/logs

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
LLM_MODEL="${LLM_MODEL:-deepseek-v3}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
DECISION_SPACE_MODE="${DECISION_SPACE_MODE:-discrete_target_state}"

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
log="/home/azanette/code/STOCK-AGENT/scripts/02_discrete/logs/${safe_model}_${DECISION_SPACE_MODE}_${ts}.log"

: "${WANDB_API_KEY:?WANDB_API_KEY is required to run backtests with W&B logging}"
if [[ "${LLM_PROFILE}" == "qingyuntop" ]]; then
  : "${QINGYUNTOP_API_KEY:?QINGYUNTOP_API_KEY is required for LLM_PROFILE=qingyuntop}"
fi

echo "REQUESTED_MODEL=${LLM_MODEL}"
echo "RESOLVED_MODEL=${RESOLVED_MODEL}"
echo "LOG=$log"
echo "QINGYUNTOP_API_KEY=$([[ -n "${QINGYUNTOP_API_KEY:-}" ]] && echo present || echo missing)"
echo "WANDB_API_KEY=$([[ -n "${WANDB_API_KEY:-}" ]] && echo present || echo missing)"

DECISION_SPACE_MODE="${DECISION_SPACE_MODE}" \
conda run --no-capture-output -n stockagent \
  python -u run_direct.py "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${RESOLVED_MODEL}" "${NEWS_ENABLED}" "${DECISION_SPACE_MODE}" \
  > >(tee -a "$log") 2>&1
