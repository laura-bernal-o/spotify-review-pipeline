"""Stage 1 — Prepare (no model calls).

Reads any input CSV with the six source columns, preserves IDs and exact values,
computes source row hashes, profiles quality, groups exact-duplicate texts,
quarantines empty texts, flags golden-set IDs, and loads everything into the
local SQLite state database that later stages resume from.

Usage:
    python -m pipeline.prepare data/raw/spotify_reviews_18months.csv
    python -m pipeline.prepare data/raw/cost_100.csv --db data/work/pilot.db
"""
import argparse
import time
from collections import Counter
from pathlib import Path

from pipeline.common import DB_PATH, WORK, connect, csv_rows, file_sha, row_sha, text_sha, write_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    row_idx          INTEGER NOT NULL,          -- 0-based position in the source CSV
    review_id        TEXT PRIMARY KEY,
    source_sha256    TEXT NOT NULL,
    review_text      TEXT NOT NULL,
    review_rating    TEXT NOT NULL,
    review_likes     TEXT NOT NULL,
    app_version      TEXT NOT NULL,
    review_timestamp TEXT NOT NULL,
    text_sha256      TEXT NOT NULL,
    canonical_id     TEXT,                      -- first review_id with identical nonempty text
    is_golden        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL,             -- pending | completed | quarantined
    reason           TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_text ON reviews(text_sha256);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def load_golden_ids(path):
    if not path or not Path(path).exists():
        return set()
    return {row["review_id"] for row in csv_rows(path)}


def prepare(csv_path, db_path, golden_path, report_path):
    started = time.time()
    csv_path = Path(csv_path)
    golden_ids = load_golden_ids(golden_path)

    conn = connect(db_path)
    existing = conn.execute("SELECT name FROM sqlite_master WHERE name='reviews'").fetchone()
    if existing and conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]:
        raise SystemExit(f"{db_path} already has reviews; refusing to overwrite saved state. "
                         "Use a new --db path or delete the file deliberately.")
    conn.executescript(SCHEMA)

    counts = Counter()
    seen_ids = set()
    first_id_for_text = {}
    months, ratings = Counter(), Counter()
    first_ts = last_ts = None
    batch = []

    for idx, row in enumerate(csv_rows(csv_path)):
        rid = row["review_id"]
        counts["records"] += 1
        if rid in seen_ids:
            counts["duplicate_review_ids"] += 1
            continue  # cannot store twice; reported below
        seen_ids.add(rid)

        text = row["review_text"]
        empty = not text.strip()
        tsha = text_sha(text)
        if empty:
            counts["empty_review_text"] += 1
            status, reason, canonical_id = "quarantined", "empty_review_text", None
        else:
            status, reason = "pending", None
            canonical_id = first_id_for_text.setdefault(tsha, rid)
            if canonical_id != rid:
                counts["exact_duplicate_text_rows"] += 1
        if not row["app_version"].strip():
            counts["missing_app_version"] += 1
        if rid in golden_ids:
            counts["golden_found"] += 1

        ts = row["review_timestamp"]
        months[ts[:7]] += 1
        ratings[row["review_rating"]] += 1
        first_ts = min(first_ts, ts) if first_ts else ts
        last_ts = max(last_ts, ts) if last_ts else ts

        batch.append((idx, rid, row_sha(row), text, row["review_rating"], row["review_likes"],
                      row["app_version"], ts, tsha, canonical_id, int(rid in golden_ids), status, reason))
        if len(batch) >= 20000:
            conn.executemany("INSERT INTO reviews (row_idx, review_id, source_sha256, review_text, review_rating, "
                             "review_likes, app_version, review_timestamp, text_sha256, canonical_id, is_golden, "
                             "status, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("INSERT INTO reviews (row_idx, review_id, source_sha256, review_text, review_rating, "
                         "review_likes, app_version, review_timestamp, text_sha256, canonical_id, is_golden, "
                         "status, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        conn.commit()

    source_sha = file_sha(csv_path)
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)",
                     [("source_path", str(csv_path)), ("source_sha256", source_sha)])
    conn.commit()

    nonempty = counts["records"] - counts["duplicate_review_ids"] - counts["empty_review_text"]
    status_counts = dict(conn.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())
    report = {
        "source": {"path": str(csv_path), "bytes": csv_path.stat().st_size, "sha256": source_sha},
        "counts": {
            "records": counts["records"],
            "unique_review_ids": len(seen_ids),
            "duplicate_review_ids": counts["duplicate_review_ids"],
            "empty_review_text": counts["empty_review_text"],
            "nonempty_review_text": nonempty,
            "distinct_nonempty_texts": len(first_id_for_text),
            "exact_duplicate_text_rows": counts["exact_duplicate_text_rows"],
            "missing_app_version": counts["missing_app_version"],
            "golden_ids_in_file": counts["golden_found"],
        },
        "status_counts": status_counts,
        "quarantine_reasons": dict(conn.execute(
            "SELECT reason, COUNT(*) FROM reviews WHERE status='quarantined' GROUP BY reason").fetchall()),
        "first_review": first_ts,
        "last_review": last_ts,
        "reviews_by_month": dict(sorted(months.items())),
        "reviews_by_rating": dict(sorted(ratings.items())),
        "db_path": str(db_path),
        "runtime_seconds": round(time.time() - started, 2),
        "model_calls": 0,
    }
    write_json(report_path, report)
    conn.close()
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv_path")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--golden", default="data/raw/golden_50_to_label.csv")
    p.add_argument("--report", default=None, help="default: <db path without .db>_ingestion_report.json")
    a = p.parse_args()
    report_path = a.report or str(Path(a.db).with_suffix("")) + "_ingestion_report.json"
    r = prepare(a.csv_path, a.db, a.golden, report_path)
    c = r["counts"]
    print(f"Rows {c['records']:,} | empty {c['empty_review_text']} | nonempty {c['nonempty_review_text']:,} | "
          f"distinct texts {c['distinct_nonempty_texts']:,} | dup IDs {c['duplicate_review_ids']} | "
          f"missing app_version {c['missing_app_version']:,} | golden {c['golden_ids_in_file']} | "
          f"{r['runtime_seconds']}s, 0 model calls")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
