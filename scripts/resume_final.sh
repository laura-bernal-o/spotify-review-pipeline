#!/bin/bash
# Phase 2 of the interruption evidence: resume the final run with unchanged settings, then run
# verify/group/rank/memo, export grading/, and save checkpoint_after.
set -u
cd "$(dirname "$0")/.."
LOG=grading/recording/02_resume.log
stamp() { perl -MPOSIX -ne '$|=1; print strftime("%Y-%m-%d %H:%M:%S ", localtime), $_'; }
CMD="python -m pipeline.run data/raw/spotify_reviews_18months.csv --db data/work/state.db --scope 100000 --out outputs/final --execute --run-id final-resume --grading grading"
echo "\$ $CMD" | stamp > "$LOG"
.venv/bin/python -u -m pipeline.run data/raw/spotify_reviews_18months.csv --db data/work/state.db --scope 100000 \
  --out outputs/final --execute --run-id final-resume --grading grading 2>&1 | stamp >> "$LOG"
echo "### exit code ${PIPESTATUS[0]}" | stamp >> "$LOG"
.venv/bin/python -c "from pipeline import export; n = export.snapshot('data/work/state.db', 'grading/checkpoint_after.json'); print(f'checkpoint_after.json saved: {n} completed IDs')" | stamp >> "$LOG"
sqlite3 data/work/state.db "SELECT 'calls logged: ' || COUNT(*) || ' | spend \$' || ROUND(SUM(cost_usd),4) FROM calls;" | stamp >> "$LOG"
