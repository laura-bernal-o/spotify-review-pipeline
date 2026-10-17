#!/bin/bash
# Interruption/resume evidence for the final run (paid). Phase 1: start the run, send a real SIGINT
# (same as Ctrl-C) after $1 seconds, save checkpoint_before. Phase 2 (resume) is a separate command.
set -u
cd "$(dirname "$0")/.."
WAIT=${1:-180}
LOG=grading/recording/01_initial_then_interrupt.log
stamp() { perl -MPOSIX -ne '$|=1; print strftime("%Y-%m-%d %H:%M:%S ", localtime), $_'; }
echo "\$ python -m pipeline.run data/raw/spotify_reviews_18months.csv --db data/work/state.db --scope 100000 --out outputs/final --execute --run-id final-initial" | stamp > "$LOG"
.venv/bin/python -u -m pipeline.run data/raw/spotify_reviews_18months.csv --db data/work/state.db --scope 100000 \
  --out outputs/final --execute --run-id final-initial > >(stamp >> "$LOG") 2>&1 &
PID=$!
sleep "$WAIT"
echo "### sending SIGINT (Ctrl-C) to pid $PID" | stamp >> "$LOG"
kill -INT "$PID"
wait "$PID"
echo "### process exited with code $?" | stamp >> "$LOG"
sleep 1
.venv/bin/python -c "from pipeline import export; n = export.snapshot('data/work/state.db', 'grading/checkpoint_before.json'); print(f'checkpoint_before.json saved: {n} completed IDs')" | stamp >> "$LOG"
sqlite3 data/work/state.db "SELECT 'calls logged: ' || COUNT(*) || ' | spend $' || ROUND(SUM(cost_usd),4) FROM calls;" | stamp >> "$LOG"
