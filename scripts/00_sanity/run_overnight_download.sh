
#!/bin/bash
# ==============================================================================
# Overnight Data Pre-Download Script
# ==============================================================================
# Run this at night to pre-download all data for offline backtesting.
# Progress is saved automatically - you can interrupt (Ctrl+C) and resume.
#
# Usage:
#   bash scripts/00_sanity/run_overnight_download.sh            # Run all phases
#   bash scripts/00_sanity/run_overnight_download.sh --report   # Check current coverage
#   nohup bash scripts/00_sanity/run_overnight_download.sh &    # Run in background (detached)
# ==============================================================================

set -e

# Navigate to project
cd "$(dirname "$0")/../../stockbench"

# Load API keys from .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Log file with timestamp
LOG_DIR="../logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/download_$(date +%Y%m%d_%H%M%S).log"

echo "======================================"
echo " Overnight Data Pre-Download"
echo "======================================"
echo " Log file: $LOG_FILE"
echo " Start time: $(date)"
echo ""
echo " Target: 2024-12-01 ~ 2025-12-31"
echo " Symbols: DJIA 20 + SPY"
echo ""
echo " Free tier rate limits:"
echo "   Polygon: 5 req/min (12.5s delay)"
echo "   Finnhub: 60 req/min (1.5s delay)"
echo ""
echo " Estimated max runtime: ~20 hours"
echo "   (much less with existing cached data)"
echo ""
echo " Progress is auto-saved. Safe to Ctrl+C."
echo "======================================"
echo ""

# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate stockagent

# Run with all output tee'd to log file
python pre_download_data.py \
    --start 2024-12-01 \
    --end 2025-12-31 \
    "$@" \
    2>&1 | tee "$LOG_FILE"

echo ""
echo "Download completed at $(date)"
echo "Log saved to: $LOG_FILE"
