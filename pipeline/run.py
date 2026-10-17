"""Orchestrator: runs every stage on an input CSV without chat pasting.

    prepare -> enrich (Jev) -> verify (Claude) -> group (code + Claude names)
            -> rank (code) -> memo (Claude) -> export grading/

Code decides the order, owns record accounting, budgets and validation; models
only answer bounded language questions. Without --execute every model stage is
a dry run that prints its estimate, so nothing is spent by accident.

Usage:
    python -m pipeline.run data/raw/cost_100.csv --db data/work/pilot.db --scope all --out outputs/pilot
    python -m pipeline.run data/raw/cost_100.csv --db data/work/pilot.db --scope all --out outputs/pilot --execute
    python -m pipeline.run --rank-only --db data/work/state.db --out outputs/final   # no model calls
"""
import argparse
import json
import signal
import time
from pathlib import Path

from pipeline import config, enrich, export, group, labels, memo, prepare, rank, verify
from pipeline.common import ROOT, write_json


def log(path, event):
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time(), 3), **event}) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv_path", nargs="?")
    p.add_argument("--db", required=True)
    p.add_argument("--scope", default=str(config.FINAL_SCOPE))
    p.add_argument("--out", required=True)
    p.add_argument("--execute", action="store_true", help="allow paid model calls")
    p.add_argument("--skip", default="", help="comma list of stages to skip: verify,group_names,memo")
    p.add_argument("--rank-only", action="store_true", help="recompute ranking from saved outputs (no models)")
    p.add_argument("--grading", default=None, help="write grading/ export here")
    p.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    a = p.parse_args()

    # Graceful stop on Ctrl-C *and* on SIGTERM (and even when launched as a background job, where SIGINT
    # is inherited as ignored): both raise KeyboardInterrupt, so in-flight results are saved before exit.
    def _stop(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.rank_only:
        rows = rank.rank(a.db, out)
        print(f"Re-ranked {len(rows)} issues from saved membership + records; wrote {out / 'ranking.csv'}")
        return

    stem = str(Path(a.db).with_suffix(""))
    calls = stem + "_calls.jsonl"
    run_log = out / "run_log.jsonl"
    skip = set(filter(None, a.skip.split(",")))
    scope = a.scope if a.scope == "all" else int(a.scope)
    summary = {"run_id": a.run_id, "input": a.csv_path, "db": a.db, "scope": a.scope, "executed": a.execute,
               "label_config": labels.label_config(), "jev_model": config.JEV_MODEL,
               "claude_models": config.CLAUDE_MODEL_BY_ROLE, "claude_effort": config.CLAUDE_EFFORT,
               "spend_caps_usd": config.SPEND_CAP_USD, "run_cap_usd": config.RUN_CAP_USD,
               "workers": config.WORKERS, "stages": {}}
    t0 = time.time()

    def stage(name, fn):
        s = time.time()
        log(run_log, {"run_id": a.run_id, "stage": name, "event": "start"})
        result = fn()
        summary["stages"][name] = {"seconds": round(time.time() - s, 3), "result": result}
        log(run_log, {"run_id": a.run_id, "stage": name, "event": "end", "seconds": round(time.time() - s, 3),
                      "result": result})
        return result

    if not Path(a.db).exists():
        if not a.csv_path:
            raise SystemExit("csv_path is required for a new database")
        stage("prepare", lambda: prepare.prepare(a.csv_path, a.db, ROOT / "data/raw/golden_50_to_label.csv",
                                                 stem + "_ingestion_report.json")["counts"])
    e = stage("enrich", lambda: enrich.run(a.db, scope, a.execute, a.run_id, calls))
    if a.execute and e.get("stopped"):
        print("Enrichment stopped early; downstream stages skipped. Re-run the same command to resume.")
        write_json(out / "run_summary.json", summary)
        return
    if "verify" not in skip:
        stage("verify", lambda: verify.run(a.db, a.execute, a.run_id, calls, out / "verify_report.json"))
    stage("group", lambda: group.run(a.db, a.execute and "group_names" not in skip, a.run_id, calls, out))
    stage("rank", lambda: len(rank.rank(a.db, out)))
    if "memo" not in skip:
        stage("memo", lambda: memo.run(a.db, a.execute, a.run_id, calls, out))
    if a.grading:
        stage("export", lambda: export.export(a.db, out, a.grading))

    summary["wall_seconds"] = round(time.time() - t0, 3)
    write_json(out / "run_summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "stages"}, indent=2))
    print(f"Summary: {out / 'run_summary.json'}")


if __name__ == "__main__":
    main()
