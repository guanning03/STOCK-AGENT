#!/usr/bin/env bash
set -euo pipefail

# ── Activate conda environment ──
eval "$(conda shell.bash hook)"
conda activate stockagent

# ── Load API key ──
cd "$(dirname "$0")/../../stockbench"
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# ── Run backtest: 2025-03-05 to 2025-03-15 using DeepSeek via OpenRouter ──
echo "=========================================="
echo " Backtest: 2025-03-05 → 2025-03-15"
echo " Model: deepseek/deepseek-chat-v3-0324"
echo " Provider: OpenRouter"
echo " Mode: offline_only (local data)"
echo "=========================================="

python run_direct.py 2025-03-05 2025-03-15 openrouter
