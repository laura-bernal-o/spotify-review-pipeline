"""Write the standardized grading/ folder (GRADING_CONTRACT.md v1). No model calls.

records.jsonl has exactly one row per source ID: completed records with labels,
everything else quarantined with a reason (empty text, failed enrichment, or
outside the declared classification scope).
"""
import argparse
import gzip
import json
import shutil
from pathlib import Path

from pipeline import config
from pipeline.common import DB_PATH, ROOT, connect, write_json


def export(db, out_dir, grading_dir, ingestion_json=None):
    grading = Path(grading_dir)
    grading.mkdir(parents=True, exist_ok=True)
    out_dir = Path(out_dir)
    conn = connect(db)
    source_sha = conn.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    write_json(grading / "run.json", {"version": "a5-audit-v1", "analysis_count": total,
                                      "analysis_sha256": source_sha, "classification_input_fields": ["review_text"],
                                      "allow_multi_issue": False})
    if ingestion_json and Path(ingestion_json).resolve() != (grading / "ingestion.json").resolve():
        shutil.copy(ingestion_json, grading / "ingestion.json")

    counts = {"completed": 0, "quarantined": 0}
    with gzip.open(grading / "records.jsonl.gz", "wt", encoding="utf-8") as f:
        q = conn.execute(
            "SELECT r.review_id, r.source_sha256, r.status, r.reason, s.topic, s.intent, s.sentiment, s.severity, "
            "s.entities, s.evidence_quote, s.needs_review, s.label_config, s.cache_source_id "
            "FROM reviews r LEFT JOIN results s USING(review_id) ORDER BY r.row_idx")
        for rid, ssha, status, reason, topic, intent, sent, sev, ents, quote, nr, lc, cache in q:
            if status == "completed":
                rec = {"review_id": rid, "source_sha256": ssha, "status": "completed", "topic": topic,
                       "intent": intent, "sentiment": sent, "severity": sev, "entities": json.loads(ents),
                       "evidence_quote": quote, "needs_review": bool(nr), "label_config": lc}
                if cache:
                    rec["cache_source_id"] = cache
            else:
                rec = {"review_id": rid, "source_sha256": ssha, "status": "quarantined",
                       "reason": reason or config.OUT_OF_SCOPE_REASON}
            counts[rec["status"]] += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    for name in ("membership.csv", "ranking.csv", "claims.csv"):
        if (out_dir / name).exists():
            shutil.copy(out_dir / name, grading / name)

    with gzip.open(grading / "calls.jsonl.gz", "wt", encoding="utf-8") as f:
        has_calls = conn.execute("SELECT name FROM sqlite_master WHERE name='calls'").fetchone()
        for row in (conn.execute(
                "SELECT request_id, role, review_ids, model, phase, outcome, label_config, input_tokens, "
                "output_tokens FROM calls ORDER BY started_at") if has_calls else []):
            keys = ["request_id", "role", "review_ids", "model", "phase", "outcome", "label_config",
                    "input_tokens", "output_tokens"]
            rec = dict(zip(keys, row))
            rec["review_ids"] = json.loads(rec["review_ids"])
            f.write(json.dumps(rec) + "\n")
    return {"records": counts, "grading_dir": str(grading)}


def snapshot(db, path):
    """Save completed_ids for the interruption/resume evidence."""
    conn = connect(db)
    ids = [r[0] for r in conn.execute("SELECT review_id FROM reviews WHERE status='completed' ORDER BY row_idx")]
    write_json(path, {"completed_ids": ids})
    return len(ids)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--out", default=str(ROOT / "outputs"))
    p.add_argument("--grading", default=str(ROOT / "grading"))
    p.add_argument("--ingestion", default=str(ROOT / "grading" / "ingestion.json"))
    a = p.parse_args()
    print(export(a.db, a.out, a.grading, a.ingestion))


if __name__ == "__main__":
    main()
