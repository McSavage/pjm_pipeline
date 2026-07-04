#!/usr/bin/env bash
# weekly_update.sh — pull the past week's PJM data into the local database.
#
# Intended to run every Monday (cron, systemd timer, etc.). Runs the four
# incremental ingest scripts in sequence — they share one rate-limited API
# key, so they must not run in parallel.
#
#   0 6 * * 1  /home/daniel/projects/pjm_pipeline/weekly_update.sh >> /home/daniel/projects/pjm_pipeline/logs/weekly_update.log 2>&1

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
source .venv/bin/activate

echo "=== Weekly PJM update: $(date -Iseconds) ==="

status=0

run_step() {
    echo "--- $* ---"
    if ! python "$@"; then
        echo "!!! FAILED: $* !!!"
        status=1
    fi
}

run_step ingest_lmp.py --feed both --incremental
run_step ingest_load.py --incremental
run_step ingest_gen.py --incremental
run_step ingest_gen_capacity.py --incremental

echo "=== Done: $(date -Iseconds) (exit $status) ==="
exit $status
