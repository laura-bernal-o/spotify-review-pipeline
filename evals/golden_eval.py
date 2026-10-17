"""Golden-50 evaluation (no model calls): human labels vs saved pipeline predictions.

Human labels come from evals/golden_50_labeled.csv (exported from the labeling
workbook by scripts/export_golden.py). Predictions are read from a pipeline
database whose scope includes the golden IDs. Human labels are never model inputs.

Reports topic/intent agreement, severity exact agreement + MAE, sentiment MAE and
±0.5 tolerance, evidence-quote substring checks, needs_review as a prediction,
per-topic counts, a topic confusion table, ambiguous-case counts and every
disagreement.

Usage: python -m evals.golden_eval --db data/work/state.db
"""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from pipeline.common import ROOT, TOPICS, connect, write_json

HUMAN = ROOT / "evals/golden_50_labeled.csv"
SENTIMENT_TOLERANCE = 0.5   # predeclared


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(ROOT / "data/work/state.db"))
    p.add_argument("--out", default=str(ROOT / "evals/golden_results.json"))
    a = p.parse_args()
    if not HUMAN.exists():
        raise SystemExit("evals/golden_50_labeled.csv not found — label the workbook, then run "
                         "python scripts/export_golden.py")
    human = {r["review_id"]: r for r in csv.DictReader(HUMAN.open(newline="", encoding="utf-8"))}
    conn = connect(a.db)
    cols = ["review_id", "topic", "intent", "severity", "sentiment", "evidence_quote", "needs_review", "label_config"]
    pred = {r[0]: dict(zip(cols, r)) for r in conn.execute(
        f"SELECT {', '.join('s.' + c for c in cols)} FROM results s WHERE s.review_id IN "
        f"({','.join('?' * len(human))})", list(human))}
    texts = dict(conn.execute(f"SELECT review_id, review_text FROM reviews WHERE review_id IN "
                              f"({','.join('?' * len(human))})", list(human)).fetchall())

    n = len(human)
    missing = [rid for rid in human if rid not in pred]
    agree, conf, per_case = Counter(), Counter(), []
    sev_err = sent_err = 0.0
    nr_tp = nr_fp = nr_fn = 0
    for rid, h in human.items():
        q = pred.get(rid)
        case = {"review_id": rid, "text": texts.get(rid, "")[:300], "ambiguous": h["ambiguous"] == "true",
                "human": {k: h[k] for k in ("topic", "intent", "severity", "sentiment", "needs_review")}}
        if q is None:   # missing predictions count as incorrect
            case.update(prediction=None, passed=False)
            per_case.append(case)
            sev_err += 4
            continue
        t_ok, i_ok = q["topic"] == h["topic"], q["intent"] == h["intent"]
        s_ok = int(q["severity"]) == int(h["severity"])
        se = abs(int(q["severity"]) - int(h["severity"]))
        sne = abs(float(q["sentiment"]) - float(h["sentiment"]))
        quote_ok = q["evidence_quote"] in texts.get(rid, "")
        agree["topic"] += t_ok
        agree["intent"] += i_ok
        agree["severity"] += s_ok
        agree["sentiment_within_tol"] += sne <= SENTIMENT_TOLERANCE
        agree["quote_substring"] += quote_ok
        sev_err += se
        sent_err += sne
        conf[(h["topic"], q["topic"])] += 1
        hn, pn = h["needs_review"] == "true", bool(q["needs_review"])
        nr_tp += hn and pn
        nr_fp += pn and not hn
        nr_fn += hn and not pn
        case.update(prediction={k: q[k] for k in ("topic", "intent", "severity", "sentiment", "evidence_quote",
                                                   "needs_review", "label_config")},
                    fields={"topic": t_ok, "intent": i_ok, "severity_exact": s_ok, "severity_abs_err": se,
                            "sentiment_abs_err": round(sne, 3), "quote_is_substring": quote_ok},
                    passed=t_ok and i_ok and s_ok)
        per_case.append(case)

    result = {
        "n": n, "missing_predictions": len(missing), "ambiguous_cases": sum(c["ambiguous"] for c in per_case),
        "topic_agreement": round(agree["topic"] / n, 4), "intent_agreement": round(agree["intent"] / n, 4),
        "severity_exact_agreement": round(agree["severity"] / n, 4), "severity_mae": round(sev_err / n, 4),
        "sentiment_mae": round(sent_err / max(1, n - len(missing)), 4),
        "sentiment_within_tolerance": round(agree["sentiment_within_tol"] / n, 4),
        "sentiment_tolerance_declared": SENTIMENT_TOLERANCE,
        "evidence_quote_substring_rate": round(agree["quote_substring"] / n, 4),
        "needs_review_as_prediction": {"true_pos": nr_tp, "false_pos": nr_fp, "false_neg": nr_fn},
        "human_topic_counts": dict(Counter(h["topic"] for h in human.values())),
        "topic_confusion_human_to_pred": {f"{a_}->{b_}": c for (a_, b_), c in sorted(conf.items())},
        "label_configs": sorted({c["prediction"]["label_config"] for c in per_case if c["prediction"]}),
        "cases": per_case,
        "note": "50 cases are a small diagnostic sample, not a population accuracy estimate.",
    }
    write_json(a.out, result)
    with open(Path(a.out).with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "ambiguous", "human_topic", "pred_topic", "human_intent", "pred_intent",
                    "human_severity", "pred_severity", "passed"])
        for c in per_case:
            pr = c["prediction"] or {}
            w.writerow([c["review_id"], c["ambiguous"], c["human"]["topic"], pr.get("topic"), c["human"]["intent"],
                        pr.get("intent"), c["human"]["severity"], pr.get("severity"), c["passed"]])
    print(json.dumps({k: v for k, v in result.items() if k not in ("cases",)}, indent=1))
    print("Topic labels used:", ", ".join(TOPICS))


if __name__ == "__main__":
    main()
