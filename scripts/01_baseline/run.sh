source ~/.bashrc
cd /home/azanette/code/STOCK-AGENT/stockbench
mkdir -p /home/azanette/code/STOCK-AGENT/scripts/01_baseline/logs

START_DATE="${START_DATE:-2025-03-01}"
END_DATE="${END_DATE:-2026-02-28}"
LLM_PROFILE="${LLM_PROFILE:-qingyuntop}"
LLM_MODEL="${LLM_MODEL:-deepseek-v3.1}"
NEWS_ENABLED="${NEWS_ENABLED:-true}"

ts=$(date -u +%Y%m%d_%H%M%S)
safe_model="${LLM_MODEL//\//_}"
safe_model="${safe_model//./_}"
log="/home/azanette/code/STOCK-AGENT/scripts/01_baseline/logs/${safe_model}_${ts}.log"

echo "LOG=$log"

conda run --no-capture-output -n stockagent \
  python -u run_direct.py "${START_DATE}" "${END_DATE}" "${LLM_PROFILE}" "${LLM_MODEL}" "${NEWS_ENABLED}" \
  > >(tee -a "$log") 2>&1
