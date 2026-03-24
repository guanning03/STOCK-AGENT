#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ -f /home/azanette/miniconda3/etc/profile.d/conda.sh ]]; then
  # shellcheck disable=SC1091
  source /home/azanette/miniconda3/etc/profile.d/conda.sh
else
  export PATH="/home/azanette/miniconda3/bin:$PATH"
fi

mkdir -p "${ROOT_DIR}/scripts/05_baseline_report/logs"

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
LLM_MODEL="${LLM_MODEL:-deepseek-v3.1}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
WANDB_PROJECT="${WANDB_PROJECT:-stock-agent-baseline-report}"
WANDB_ENTITY="${WANDB_ENTITY:-}"
NO_TEE_LOGGING="${NO_TEE_LOGGING:-false}"

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
log="${ROOT_DIR}/scripts/05_baseline_report/logs/${safe_model}_${ts}.log"

: "${WANDB_API_KEY:?WANDB_API_KEY is required to run backtests with W&B logging}"
if [[ "${LLM_PROFILE}" == "qingyuntop" ]]; then
  : "${QINGYUNTOP_API_KEY:?QINGYUNTOP_API_KEY is required for LLM_PROFILE=qingyuntop}"
fi

echo "START_DATE=${START_DATE}"
echo "END_DATE=${END_DATE}"
echo "LLM_PROFILE=${LLM_PROFILE}"
echo "REQUESTED_MODEL=${LLM_MODEL}"
echo "RESOLVED_MODEL=${RESOLVED_MODEL}"
echo "WANDB_PROJECT=${WANDB_PROJECT}"
echo "LOG=${log}"
echo "QINGYUNTOP_API_KEY=$([[ -n "${QINGYUNTOP_API_KEY:-}" ]] && echo present || echo missing)"
echo "WANDB_API_KEY=$([[ -n "${WANDB_API_KEY:-}" ]] && echo present || echo missing)"

cd "${ROOT_DIR}"
if [[ "${NO_TEE_LOGGING}" == "true" ]]; then
  WANDB_PROJECT="${WANDB_PROJECT}" \
  WANDB_ENTITY="${WANDB_ENTITY}" \
  conda run --no-capture-output -n stockagent \
    python -u "${ROOT_DIR}/scripts/05_baseline_report/run_backtest.py" \
    "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${RESOLVED_MODEL}" "${NEWS_ENABLED}" \
    >> "${log}" 2>&1
else
  WANDB_PROJECT="${WANDB_PROJECT}" \
  WANDB_ENTITY="${WANDB_ENTITY}" \
  conda run --no-capture-output -n stockagent \
    python -u "${ROOT_DIR}/scripts/05_baseline_report/run_backtest.py" \
    "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${RESOLVED_MODEL}" "${NEWS_ENABLED}" \
    > >(tee -a "${log}") 2>&1
fi
