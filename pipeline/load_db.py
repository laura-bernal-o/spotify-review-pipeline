"""Load saved pipeline outputs into the dashboard database (Postgres/Neon). No model calls.

Reads the local SQLite state + outputs/<run>/ (ranking.csv, issues.json, membership.csv,
claims.csv, recommendations.json, memo.md, verify_report.json) and replaces the
dashboard tables in one transaction. Connection string comes from DATABASE_URL in .env.

Usage: python -m pipeline.load_db --db data/work/state.db --out outputs/final
"""
import argparse
import csv
import json
import os
from pathlib import Path

from pipeline.common import ROOT, connect

TABLES = ["recommendations", "claims", "reviews", "issues", "overall_metrics", "run_info", "monthly"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    from dotenv import load_dotenv
    import psycopg
    load_dotenv()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set in .env")
    out = Path(a.out)
    lite = connect(a.db)
    ranking = list(csv.DictReader((out / "ranking.csv").open(newline="")))
    issues = json.loads((out / "issues.json").read_text())["issues"]
    membership = {r["review_id"]: r["issue_id"] for r in csv.DictReader((out / "membership.csv").open(newline=""))}
    claims = list(csv.DictReader((out / "claims.csv").open(newline="")))
    recs = json.loads((out / "recommendations.json").read_text())
    memo_input = json.loads((out / "memo_input.json").read_text())
    verify_rep = json.loads((out / "verify_report.json").read_text()) if (out / "verify_report.json").exists() else {}
    summary = json.loads((out / "run_summary.json").read_text()) if (out / "run_summary.json").exists() else {}

    with psycopg.connect(url) as pg, pg.cursor() as cur:
        cur.execute((ROOT / "dashboard/schema.sql").read_text())
        cur.execute("TRUNCATE " + ", ".join(TABLES))
        info = {
            "memo_markdown": (out / "memo.md").read_text(),
            "recommended_area": recs["recommended_area"],
            "memo_model": recs.get("model"), "memo_prompt_version": recs.get("prompt_version"),
            "cited_review_ids": recs.get("cited_review_ids", []),
            "verify": {k: verify_rep.get(k) for k in ("model", "sample_size", "topic_agreement", "intent_agreement",
                                                       "severity_exact_agreement", "severity_mae")},
            "run": {k: summary.get(k) for k in ("run_id", "label_config", "jev_model", "claude_models", "scope")},
            "source_sha256": lite.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()[0],
        }
        for k, v in info.items():
            cur.execute("INSERT INTO run_info VALUES (%s, %s)", (k, json.dumps(v)))
        for k, v in memo_input["overall_metrics"].items():
            cur.execute("INSERT INTO overall_metrics VALUES (%s, %s)", (k, str(v)))
        for r in ranking:
            meta = issues[r["issue_id"]]
            cur.execute("INSERT INTO issues VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (r["issue_id"], int(r["rank"]), meta["name"], meta.get("description", ""),
                         r["issue_id"].split(".")[0], int(r["complaint_count"]), int(r["severity_sum"]),
                         r["mean_severity"], int(r["priority_score"])))
        rows = lite.execute(
            "SELECT r.review_id, r.review_text, r.review_rating, r.review_timestamp, r.app_version, s.topic, s.intent, "
            "s.severity, s.sentiment, s.evidence_quote, s.needs_review, s.cache_source_id, s.label_config "
            "FROM reviews r JOIN results s USING(review_id) WHERE r.status='completed'")
        with cur.copy("COPY reviews FROM STDIN") as cp:
            for row in rows:
                rating = int(row[2]) if str(row[2]).isdigit() else None
                cp.write_row([row[0], row[1], rating, row[3], row[4], row[5], row[6], row[7], row[8], row[9],
                              bool(row[10]), row[11], row[12], membership.get(row[0])])
        for c in claims:
            cur.execute("INSERT INTO claims VALUES (%s,%s,%s,%s)", (c["claim_id"], c["issue_id"], c["metric"], c["value"]))
        for i, r in enumerate(recs["recommendations"], 1):
            cur.execute("INSERT INTO recommendations VALUES (%s,%s,%s,%s)",
                        (i, r["title"], r["rationale"], json.dumps(r["claim_ids"])))
        for month, n, comp in lite.execute(
                "SELECT substr(r.review_timestamp,1,7), COUNT(*), SUM(s.intent IN ('complaint','cancellation')) "
                "FROM reviews r JOIN results s USING(review_id) WHERE r.status='completed' GROUP BY 1 ORDER BY 1"):
            cur.execute("INSERT INTO monthly VALUES (%s,%s,%s)", (month, n, comp))
        pg.commit()
        counts = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    print("Loaded:", counts)


if __name__ == "__main__":
    main()
