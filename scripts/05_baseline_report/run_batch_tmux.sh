#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_SCRIPT="${ROOT_DIR}/scripts/05_baseline_report/run.sh"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux is required but not installed."
  exit 1
fi

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"
WANDB_PROJECT="${WANDB_PROJECT:-stock-agent-baseline-report}"
SESSION_PREFIX="${SESSION_PREFIX:-baseline_report}"

MODELS=(
  "deepseek-v3.1"
  "gpt-4o-mini"
  "gemini-3-flash-preview-nothinking"
)

for model in "${MODELS[@]}"; do
  safe_model="${model//[^A-Za-z0-9]/_}"
  session_name="${SESSION_PREFIX}_${safe_model}"
  if tmux has-session -t "${session_name}" 2>/dev/null; then
    echo "[SKIP] Session already exists: ${session_name}"
    continue
  fi

  tmux new-session -d -s "${session_name}" \
    "cd '${ROOT_DIR}' && START_DATE='${START_DATE}' END_DATE='${END_DATE}' \
LLM_PROFILE='${LLM_PROFILE}' LLM_MODEL='${model}' NEWS_ENABLED='${NEWS_ENABLED}' \
WANDB_PROJECT='${WANDB_PROJECT}' '${RUN_SCRIPT}'"

  echo "[START] ${session_name}"
done

echo
echo "Active sessions:"
tmux ls | grep "${SESSION_PREFIX}" || true
