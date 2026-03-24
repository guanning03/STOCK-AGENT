#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ -f /home/azanette/miniconda3/etc/profile.d/conda.sh ]]; then
  # shellcheck disable=SC1091
  source /home/azanette/miniconda3/etc/profile.d/conda.sh
else
  export PATH="/home/azanette/miniconda3/bin:$PATH"
fi

cd "${ROOT_DIR}"
conda run --no-capture-output -n stockagent \
  python "${ROOT_DIR}/scripts/05_baseline_report/analyze.py" "$@"
