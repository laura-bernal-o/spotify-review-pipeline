"""Offline test of the cost calculator with a FAKE pilot (no API calls, no keys).

Runs the real stage code on cost_100.csv with fake Jev/Claude responses (the real
claude_client path, including its result cache), then checks the calculator rules
from COST_CALCULATOR.md:
  - warm run makes zero new enrichment and Claude calls;
  - doubling every rate doubles API spend and leaves time unchanged;
  - changing projected volume does not change measured pilot results.

Run: python -m tests.test_calculator_offline
"""
import csv
import json
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cost import calculator  # noqa: E402
from pipeline import claude_client, enrich, group, memo, prepare, rank, verify  # noqa: E402
from tests.test_pipeline_offline import FakeTypeSafe, fake_classify  # noqa: E402


class FakeMessages:
    n = 0

    def create(self, **params):
        FakeMessages.n += 1
        user = params["messages"][0]["content"]
        props = params["output_config"]["format"]["schema"]["properties"]
        if "labels" in props:
            items = json.loads(user.split("\n", 1)[1])
            data = {"labels": [{"review_id": x["review_id"], "topic": "playback", "intent": "complaint",
                                "severity": 3} for x in items]}
        elif "issues" in props:
            data = {"issues": [{"issue_id": i, "name": f"N {i}", "description": "d"}
                               for i in json.loads(user.split("\n", 1)[1])]}
        else:
            inp = json.loads(user)
            top = inp["ranking_top"][0]
            data = {"recommended_area": "playback", "memo_markdown": f"Focus {top['issue_id']} {{C1}}",
                    "claims": [{"claim_id": "C1", "issue_id": top["issue_id"], "metric": "complaint_count"}],
                    "cited_review_ids": [], "dashboard_recommendations": []}
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(input_tokens=len(user) // 3 + 400, output_tokens=150 + len(json.dumps(data)) // 3),
            stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=json.dumps(data))],
            _request_id=f"req_fake_{FakeMessages.n}")


def main():
    enrich.classify = fake_classify
    import typesafe_sdk
    typesafe_sdk.TypeSafeClient = FakeTypeSafe
    claude_client._client = types.SimpleNamespace(messages=FakeMessages())
    claude_client.config.CLAUDE_MODEL_BY_ROLE["memo"] = "claude-sonnet-5-5"

    tmp = Path(tempfile.mkdtemp(prefix="a5calc-"))
    run_dir, db = tmp / "pilot_run", tmp / "pilot_run/pilot.db"
    run_dir.mkdir()
    prepare.prepare(calculator.PILOT_CSV, db, ROOT / "data/raw/golden_50_to_label.csv", tmp / "ing.json")
    meta = {"input": "data/raw/cost_100.csv", "input_sha256": calculator.file_sha(calculator.PILOT_CSV),
            "input_rows": 100, "workers": 1, "settings": {"note": "FAKE offline test"}}
    for phase in ("cold", "warm"):
        t = time.time()
        rid, out, calls = f"pilot-{phase}", run_dir / f"out_{phase}", str(tmp / "calls.jsonl")
        out.mkdir()
        enrich.run(db, "all", True, rid, calls)
        verify.run(db, True, rid, calls, out / "verify_report.json")
        group.run(db, True, rid, calls, out)
        rank.rank(db, out)
        memo.run(db, True, rid, calls, out)
        meta[phase] = {"wall_seconds": round(time.time() - t, 3)}
    calculator.finalize(run_dir, db, meta, tmp)

    a = calculator.replay(tmp, ROOT / "cost/rates.csv", ROOT / "cost/assumptions.json")
    assert a["warm_enrich_requests"] == 0, "warm run must make zero enrichment calls"
    assert a["warm_api_usd"] == 0, "warm run must make zero paid calls"
    assert a["cold_api_usd"] > 0
    pm = json.loads((tmp / "pilot_meta.json").read_text())
    assert pm["warm"]["claude_cache_hits"] >= 2, pm["warm"]

    doubled = tmp / "rates_x2.csv"
    rows = list(csv.DictReader((ROOT / "cost/rates.csv").open(newline="")))
    for r in rows:
        r["price_usd_per_unit"] = str(float(r["price_usd_per_unit"]) * 2)
    with doubled.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    b = calculator.replay(tmp, doubled, ROOT / "cost/assumptions.json")
    assert abs(b["cold_api_usd"] - 2 * a["cold_api_usd"]) < 1e-12, (a["cold_api_usd"], b["cold_api_usd"])
    assert [p["est_hours"] for p in a["projections"]] == [p["est_hours"] for p in b["projections"]]

    big = json.loads((ROOT / "cost/assumptions.json").read_text())
    big["targets"]["full_corpus"]["distinct_texts"] *= 2
    (tmp / "a2.json").write_text(json.dumps(big))
    c = calculator.replay(tmp, ROOT / "cost/rates.csv", tmp / "a2.json")
    assert c["cold_api_usd"] == a["cold_api_usd"], "projection inputs must not change measured results"
    print("ALL CALCULATOR CHECKS PASSED")
    print("report:", tmp / "report.md")


if __name__ == "__main__":
    main()
