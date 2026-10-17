"""Offline end-to-end test with FAKE model responses (no API calls, no keys used).

Exercises prepare -> enrich (with interruption + resume) -> verify -> group -> rank
-> memo check -> export on a temporary database built from checkpoint_500.csv.
Everything here is synthetic and is written only under a temp directory.

Run: python -m tests.test_pipeline_offline
"""
import csv
import gzip
import json
import random
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import claude_client, enrich, export, group, labels, memo, prepare, rank, verify  # noqa: E402
from pipeline.common import connect  # noqa: E402


# ---------------- fakes -------------------------------------------------------
class Ans:
    def __init__(self, choice, criteria):
        self.type, self.choice = "choice", choice
        self.probabilities = {k: (0.9 if k == choice else 0.1 / (len(criteria) - 1)) for k in criteria}
        self.confidence = 0.9


def fake_classify(client, text):
    rnd = random.Random(text)
    answers = {n: Ans(rnd.choice(list(q["criteria"])), q["criteria"]) for n, q in labels.QUESTIONS.items()}
    if "FAIL_ONCE" in text and not getattr(fake_classify, "failed", False):
        fake_classify.failed = True
        answers["topic"].choice = "not-a-topic"   # invalid output -> should retry once
    return types.SimpleNamespace(model=enrich.config.JEV_MODEL, answers=answers,
                                 usage=types.SimpleNamespace(input_tokens=700, output_tokens=80))


class FakeTypeSafe:
    def __init__(self, *a, **k):
        pass


def fake_call_json(ledger, role, system, user, schema, review_ids, validate, phase="initial", label_config=None):
    import time as _t
    if role == "verify":
        items = json.loads(user.split("\n", 1)[1])
        data = {"labels": [{"review_id": x["review_id"], "topic": "playback", "intent": "complaint", "severity": 3}
                           for x in items]}
    elif role == "group":
        pack = json.loads(user.split("\n", 1)[1])
        data = {"issues": [{"issue_id": i, "name": f"Name {i}", "description": "d"} for i in pack]}
    else:
        inp = json.loads(user)
        top = inp["ranking_top"][0]
        ev = next(iter(inp["evidence_pack"].values()))
        data = {"recommended_area": "playback",
                "memo_markdown": f"## Recommendation\nFocus on {top['issue_id']} ({{C1}}); "
                                 f"{{M:classified_in_scope}} reviews classified. See {ev[0]['review_id']}.",
                "claims": [{"claim_id": "C1", "issue_id": top["issue_id"], "metric": "priority_score"}],
                "cited_review_ids": [ev[0]["review_id"]],
                "dashboard_recommendations": [{"title": "t", "rationale": "r", "claim_ids": ["C1"]}]}
    validate(data)
    ledger.record(provider="claude", role=role, model="claude-haiku-4-5", phase=phase, outcome="succeeded",
                  attempt=1, review_ids=review_ids, label_config=label_config, input_tokens=1000,
                  output_tokens=200, started_at=_t.time(), reserved=0)
    return data, "fake-req"


def main():
    enrich.classify = fake_classify
    import typesafe_sdk
    typesafe_sdk.TypeSafeClient = FakeTypeSafe
    claude_client.call_json = fake_call_json
    verify.call_json = fake_call_json

    tmp = Path(tempfile.mkdtemp(prefix="a5test-"))
    src = tmp / "input.csv"
    rows = list(csv.DictReader((ROOT / "data/raw/checkpoint_500.csv").open(encoding="utf-8", newline="")))
    rows[3]["review_text"] = rows[2]["review_text"]          # planted exact duplicate
    rows[5]["review_text"] = "FAIL_ONCE " + rows[5]["review_text"]
    rows[7]["review_text"] = "   "                           # planted empty text
    with src.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    db, out, calls = tmp / "t.db", tmp / "out", tmp / "calls.jsonl"
    out.mkdir()

    rep = prepare.prepare(src, db, ROOT / "data/raw/golden_50_to_label.csv", tmp / "ing.json")
    assert rep["counts"]["records"] == 500 and rep["counts"]["empty_review_text"] == 1
    expected_distinct = len({r["review_text"] for r in rows if r["review_text"].strip()})
    assert rep["counts"]["distinct_nonempty_texts"] == expected_distinct

    dry = enrich.run(db, "all", False, "r0", calls)
    assert dry["executed"] is False and dry["calls"] == expected_distinct
    assert connect(db).execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0, "dry run must not label"

    first = enrich.run(db, "all", True, "r1", calls, stop_after=120)             # interruption
    export.snapshot(db, tmp / "checkpoint_before.json")
    before = set(json.loads((tmp / "checkpoint_before.json").read_text())["completed_ids"])
    second = enrich.run(db, "all", True, "r2", calls)                            # resume
    export.snapshot(db, tmp / "checkpoint_after.json")
    after = set(json.loads((tmp / "checkpoint_after.json").read_text())["completed_ids"])
    conn = connect(db)
    resent = conn.execute("SELECT review_ids FROM calls WHERE run_id='r2' AND role='enrich'").fetchall()
    resent_ids = {i for (r,) in resent for i in json.loads(r)}
    assert first["phase"] == "initial" and second["phase"] == "resume"
    assert before < after and not (resent_ids & before), "resume must not re-send completed IDs"
    assert len(after) == 499, len(after)
    dup = conn.execute("SELECT cache_source_id FROM results WHERE review_id=?", (rows[3]["review_id"],)).fetchone()[0]
    assert dup == rows[2]["review_id"], "exact duplicate must reuse with direct cache_source_id"
    fails = conn.execute("SELECT COUNT(*) FROM calls WHERE outcome='failed'").fetchone()[0]
    assert fails == 1, "invalid output should be logged once and retried"
    for rid, text, quote in conn.execute("SELECT r.review_id, r.review_text, s.evidence_quote "
                                         "FROM results s JOIN reviews r USING(review_id)"):
        assert quote in text, rid

    v = verify.run(db, True, "r3", calls, out / "verify_report.json")
    assert v["sample_size"] >= 20
    g = group.run(db, True, "r3", calls, out)
    r1 = rank.rank(db, out)
    r2 = rank.rank(db, out)
    assert r1 == r2 and all(int(x["priority_score"]) == int(x["severity_sum"]) for x in r1)
    assert r1 == sorted(r1, key=lambda x: (-x["priority_score"], x["issue_id"]))
    m = memo.run(db, True, "r3", calls, out)
    assert m["claims"] == 1
    _, ranking, _, evidence, overall = memo.load_inputs(db, out)
    memo_text = (out / "memo.md").read_text()
    assert f"{r1[0]['priority_score']} [C1]" in memo_text, "code must fill the exact saved value"
    bad = {"claims": [{"claim_id": "C1", "issue_id": "usability", "metric": "priority_score"}],
           "cited_review_ids": ["nope"], "dashboard_recommendations": [],
           "memo_markdown": "Usability scores 3.33 {C2} {M:made_up}, see 45c561b9; this will reduce churn. "
                            "No revenue is claimed."}
    probs = " | ".join(memo.check(bad, ranking, evidence, overall))
    for expect in ("issue_id 'usability' is not in ranking_top", "cited review nope", "{C2} has no claim",
                   "unknown overall metric", "typed number/ID '3.33'", "'45c561b9", "reduce churn", "rank-1 issue"):
        assert expect in probs, (expect, probs)
    assert "revenue" not in probs, "a disclaimer must not be flagged"

    e = export.export(db, out, tmp / "grading")
    recs = [json.loads(l) for l in gzip.open(tmp / "grading/records.jsonl.gz", "rt")]
    assert len(recs) == 500 and len({r["review_id"] for r in recs}) == 500
    assert sum(r["status"] == "quarantined" for r in recs) == 1
    print("ALL OFFLINE CHECKS PASSED")
    print(json.dumps({"tmp": str(tmp), "enrich_first": first, "enrich_resume": second, "groups": g,
                      "top_issue": r1[0], "export": e["records"]}, indent=1, default=str))


if __name__ == "__main__":
    main()
