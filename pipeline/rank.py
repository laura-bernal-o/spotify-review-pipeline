"""Stage 5 — Rank (code only, no model).

Recomputes the baseline ranking from saved membership.csv + saved severities:
  complaint_count = members, severity_sum = sum(severity),
  mean_severity = severity_sum / complaint_count (6 dp, half-up),
  priority_score = severity_sum (== count x mean, no rounding loss).
Sorted by priority_score desc, then issue_id asc.

Usage: python -m pipeline.rank --db data/work/state.db --out outputs
"""
import argparse
import csv
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pipeline.common import DB_PATH, ROOT, connect


def mean6(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def rank(db, out_dir):
    out_dir = Path(out_dir)
    conn = connect(db)
    sev = dict(conn.execute("SELECT review_id, severity FROM results"))
    count, total, seen = defaultdict(int), defaultdict(int), set()
    with (out_dir / "membership.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            pair = (row["issue_id"], row["review_id"])
            if pair in seen:
                raise ValueError(f"duplicate membership {pair}")
            seen.add(pair)
            count[row["issue_id"]] += 1
            total[row["issue_id"]] += sev[row["review_id"]]
    order = sorted(count, key=lambda i: (-total[i], i))
    rows = [{"rank": n, "issue_id": i, "complaint_count": count[i], "severity_sum": total[i],
             "mean_severity": mean6(total[i], count[i]), "priority_score": total[i]}
            for n, i in enumerate(order, 1)]
    with (out_dir / "ranking.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else
                           ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--out", default=str(ROOT / "outputs"))
    a = p.parse_args()
    rows = rank(a.db, a.out)
    for r in rows[:10]:
        print(r)


if __name__ == "__main__":
    main()
