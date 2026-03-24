#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_SCRIPT="${ROOT_DIR}/scripts/05_baseline_report/run.sh"
LOG_DIR="${ROOT_DIR}/scripts/05_baseline_report/logs"
PID_DIR="${ROOT_DIR}/scripts/05_baseline_report/pids"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
WANDB_PROJECT="${WANDB_PROJECT:-stock-agent-baseline-report}"
MODELS_CSV="${MODELS_CSV:-deepseek-v3.1,gpt-4o-mini,gemini-3-flash-preview-nothinking}"
IFS=',' read -r -a MODELS <<< "${MODELS_CSV}"

for model in "${MODELS[@]}"; do
  safe_model="${model//[^A-Za-z0-9]/_}"
  pid_file="${PID_DIR}/${safe_model}.pid"
  launcher_log="${LOG_DIR}/${safe_model}_launcher.log"

  if [[ -f "${pid_file}" ]]; then
    existing_pid="$(cat "${pid_file}")"
    if ps -p "${existing_pid}" >/dev/null 2>&1; then
      echo "[SKIP] ${model} is already running with pid ${existing_pid}"
      continue
    fi
  fi

  nohup env \
    START_DATE="${START_DATE}" \
    END_DATE="${END_DATE}" \
    LLM_PROFILE="${LLM_PROFILE}" \
    LLM_MODEL="${model}" \
    NEWS_ENABLED="${NEWS_ENABLED}" \
    WANDB_PROJECT="${WANDB_PROJECT}" \
    "${RUN_SCRIPT}" > "${launcher_log}" 2>&1 &

  pid=$!
  echo "${pid}" > "${pid_file}"
  echo "[START] ${model} pid=${pid} launcher_log=${launcher_log}"
done
