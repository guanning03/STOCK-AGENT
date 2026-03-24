#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="${ROOT_DIR}/scripts/05_baseline_report/logs"
mkdir -p "${LOG_DIR}"

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
LLM_MODEL="${LLM_MODEL:-deepseek-v3.1}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
WANDB_PROJECT="${WANDB_PROJECT:-stock-agent-baseline-report-refresh}"
WANDB_ENTITY="${WANDB_ENTITY:-}"
RUN_TAG="${RUN_TAG:-}"

safe_model="${LLM_MODEL//\//_}"
safe_model="${safe_model//./_}"
ts="$(date -u +%Y%m%d_%H%M%S_%N)"
suffix="${RUN_TAG:+_${RUN_TAG}}"
launcher_log="${LOG_DIR}/${safe_model}${suffix}_launcher_${ts}.log"
main_log="${LOG_DIR}/${safe_model}${suffix}_${ts}.log"

inner_script="$(mktemp)"
cat > "${inner_script}" <<EOF
set -euo pipefail
source /home/azanette/miniconda3/etc/profile.d/conda.sh
conda activate stockagent
cd "${ROOT_DIR}"
export WANDB_PROJECT="${WANDB_PROJECT}"
export WANDB_ENTITY="${WANDB_ENTITY}"
python -u "${ROOT_DIR}/scripts/05_baseline_report/run_backtest.py" \
  "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${LLM_MODEL}" "${NEWS_ENABLED}" \
  >> "${main_log}" 2>&1
EOF

nohup bash "${inner_script}" > "${launcher_log}" 2>&1 < /dev/null &
pid=$!
disown "${pid}" 2>/dev/null || true

echo "PID=${pid}"
echo "LAUNCHER_LOG=${launcher_log}"
echo "MAIN_LOG=${main_log}"
