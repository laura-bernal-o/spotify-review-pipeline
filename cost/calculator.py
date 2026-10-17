"""100-review cost and runtime calculator (COST_CALCULATOR.md).

DEFAULT = OFFLINE REPLAY: recomputes every cost from saved usage (pilot_calls.jsonl)
x editable rates (rates.csv) and projects full-run scenarios from assumptions.json.
No API key, no network, no model calls.

    python -m cost.calculator                     # offline replay -> cost/report.md, cost/usage.csv
    python -m cost.calculator --rates my.csv      # replay with edited prices
    python -m cost.calculator pilot               # shows what the paid pilot would do (no calls)
    python -m cost.calculator pilot --execute     # PAID: real cold + warm run on cost_100.csv

Billing formula per mutually exclusive item: item_cost = billed_units x price_per_unit,
with per-1M-token prices divided by 1,000,000 first. Jev bills input only (output $0);
Claude output tokens already include thinking, so nothing is added twice.
"""
import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COST = ROOT / "cost"
PILOT_CSV = ROOT / "data/raw/cost_100.csv"
MANIFEST = ROOT / "data/raw/manifest.json"
PILOT_SHA = None  # read from manifest at runtime
ROLES = ("enrich", "verify", "group", "memo")


# ------------------------------------------------------------------ loading
def load_rates(path):
    rates = {}
    with Path(path).open(newline="") as f:
        for r in csv.DictReader(f):
            per = 1e6 if r["unit"].strip() == "1M tokens" else 1.0
            rates[(r["model"], r["item"])] = {"per_unit": float(r["price_usd_per_unit"]) / per,
                                             "price": float(r["price_usd_per_unit"]), "unit": r["unit"],
                                             "source": r["source"], "checked": r["checked"]}
    return rates


def load_calls(path):
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def call_cost(call, rates):
    """Returns (cost, missing_usage). Failed calls with unknown usage cost 0 but are flagged."""
    if call.get("input_tokens") is None:
        return 0.0, True
    rin = rates[(call["model"], "input")]["per_unit"]
    rout = rates[(call["model"], "output")]["per_unit"]
    return call["input_tokens"] * rin + (call.get("output_tokens") or 0) * rout, False


# ------------------------------------------------------------------ replay
def summarize(calls, rates, meta):
    by = defaultdict(lambda: {"requests": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                              "missing_usage": 0, "review_ids": 0, "models": set(), "call_seconds": 0.0})
    for c in calls:
        s = by[(c["pilot_run"], c["role"])]
        s["requests"] += 1
        s["failed"] += c["outcome"] != "succeeded"
        s["input_tokens"] += c.get("input_tokens") or 0
        s["output_tokens"] += c.get("output_tokens") or 0
        cost, missing = call_cost(c, rates)
        s["cost_usd"] += cost
        s["missing_usage"] += missing
        s["review_ids"] += len(c["review_ids"]) if c["outcome"] == "succeeded" else 0
        s["models"].add(c["model"])
        s["call_seconds"] += c.get("duration_s") or 0
    out = {}
    for run in ("cold", "warm"):
        stages = {}
        for role in ROLES:
            s = by.get((run, role))
            stages[role] = ({**s, "models": sorted(s["models"]), "cost_usd": round(s["cost_usd"], 8)} if s else
                            {"requests": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                             "missing_usage": 0, "review_ids": 0, "models": [], "call_seconds": 0.0})
        api = sum(v["cost_usd"] for v in stages.values())
        m = meta[run]
        completed = m["completed_records"]
        out[run] = {"stages": stages, "api_cost_usd": round(api, 8), "wall_seconds": m["wall_seconds"],
                    "completed_records": completed, "failed_records": m["failed_records"],
                    "cost_per_1000_inputs_usd": round(api / m["input_rows"] * 1000, 8),
                    "cost_per_completed_record_usd": round(api / completed, 10) if completed else None,
                    "throughput_records_per_s": round(completed / m["wall_seconds"], 3) if m["wall_seconds"] else None,
                    "enrich_cache_hits": m.get("enrich_cache_hits"), "claude_cache_hits": m.get("claude_cache_hits")}
    return out


def project(summary, assumptions, rates, meta):
    cold = summary["cold"]["stages"]
    enr = cold["enrich"]
    ok_enrich = enr["requests"] - enr["failed"]
    if not ok_enrich:
        raise SystemExit("No successful enrichment calls in the pilot; cannot project.")
    model_e = enr["models"][0]
    tok_in_per_call = enr["input_tokens"] / enr["requests"]   # includes failed attempts' usage
    tok_out_per_call = enr["output_tokens"] / enr["requests"]
    measured_retry = enr["requests"] / ok_enrich - 1
    sec_per_enrich_call = enr["call_seconds"] / enr["requests"]
    ver = cold["verify"]
    ver_per_review = {"in": ver["input_tokens"] / ver["review_ids"], "out": ver["output_tokens"] / ver["review_ids"],
                      "s": ver["call_seconds"] / ver["review_ids"]} if ver["review_ids"] else None
    fixed = {r: cold[r] for r in ("group", "memo")}
    r = lambda m, item: rates[(m, item)]["per_unit"]  # noqa: E731

    results = []
    for tname, t in assumptions["targets"].items():
        for reuse in (True, False):
            n_calls = t["distinct_texts"] if reuse else t["classified"]
            for sname, sc in assumptions["scenarios"].items():
                retry = measured_retry + sc.get("retry_rate_add", 0)
                calls = n_calls * (1 + retry)
                e_cost = calls * (tok_in_per_call * r(model_e, "input") + tok_out_per_call * r(model_e, "output"))
                e_time = calls * sec_per_enrich_call / assumptions["max_workers"] / sc["throughput_multiplier"]
                v_cost = v_time = 0.0
                if ver_per_review:
                    m = ver["models"][0]
                    nv = n_calls * assumptions["verify_fraction"] * sc["verify_multiplier"]
                    v_cost = nv * (ver_per_review["in"] * r(m, "input") + ver_per_review["out"] * r(m, "output"))
                    v_time = nv * ver_per_review["s"] / sc["throughput_multiplier"]
                f_cost = f_time = 0.0
                for st in fixed.values():
                    if st["requests"]:
                        m = st["models"][0]
                        f_cost += (st["input_tokens"] * r(m, "input") + st["output_tokens"] * r(m, "output")) \
                            * sc["fixed_stage_multiplier"]
                        f_time += st["call_seconds"] * sc["fixed_stage_multiplier"]
                total = e_cost + v_cost + f_cost
                results.append({"target": tname, "exact_text_reuse": reuse, "scenario": sname,
                                "enrich_calls": round(calls), "enrich_usd": round(e_cost, 4),
                                "verify_usd": round(v_cost, 4), "group_memo_usd": round(f_cost, 4),
                                "total_api_usd": round(total, 4),
                                "est_hours": round((e_time + v_time + f_time) / 3600, 2),
                                "over_budget": total > assumptions["spending_limit_usd"]})
    basis = {"enrich_input_tokens_per_request": round(tok_in_per_call, 1),
             "enrich_output_tokens_per_request": round(tok_out_per_call, 1),
             "measured_enrich_retry_rate": round(measured_retry, 4),
             "enrich_seconds_per_request": round(sec_per_enrich_call, 3), "verify_per_review": ver_per_review}
    return basis, results


def write_usage_csv(calls, rates, path):
    with Path(path).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pilot_run", "request_id", "role", "model", "outcome", "item", "billed_units", "unit",
                    "price_usd_per_unit", "item_cost_usd"])
        for c in calls:
            for item, key in (("input", "input_tokens"), ("output", "output_tokens")):
                units = c.get(key)
                rt = rates[(c["model"], item)]
                w.writerow([c["pilot_run"], c["request_id"], c["role"], c["model"], c["outcome"], item,
                            "" if units is None else units, "tokens", rt["per_unit"],
                            "" if units is None else round(units * rt["per_unit"], 10)])


def fmt(x, nd=4, money=True):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"${x:,.{nd}f}" if money else f"{x:,.{nd}f}"
    return str(x)


def write_report(path, meta, summary, basis, proj, assumptions, rates, rates_path):
    L = ["# 100-review pilot: measured cost and runtime", "",
         f"Input `{meta['input']}` · SHA-256 `{meta['input_sha256']}` · {meta['input_rows']} IDs · "
         f"{meta['unique_texts']} unique texts · workers {meta['workers']} · rates file `{rates_path}`", "",
         "All numbers below are recomputed offline from `pilot_calls.jsonl` × `rates.csv`. "
         "Measured results never change when projection assumptions change.", "",
         "## Measured: cold vs warm", "",
         "| | Cold run (empty cache) | Warm run (saved results) |", "|---|---|---|"]
    c, w = summary["cold"], summary["warm"]
    for label, key in [("Completed records", "completed_records"), ("Failed records", "failed_records"),
                       ("Wall-clock seconds (end to end)", "wall_seconds"), ("API cost", "api_cost_usd"),
                       ("Cost per 1,000 input rows", "cost_per_1000_inputs_usd"),
                       ("Cost per completed record", "cost_per_completed_record_usd"),
                       ("Throughput (records/s)", "throughput_records_per_s"),
                       ("Enrichment result-cache hits", "enrich_cache_hits"),
                       ("Claude result-cache hits", "claude_cache_hits")]:
        money = "cost" in key
        nd = 8 if money else 3
        L.append(f"| {label} | {fmt(c[key], nd, money)} | {fmt(w[key], nd, money)} |")
    L += ["", "## Measured by stage", "",
          "| Run | Stage | Model | Requests | Failed | Input tok | Output tok | Missing usage | Call seconds | Cost |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for run in ("cold", "warm"):
        for role, s in summary[run]["stages"].items():
            L.append(f"| {run} | {role} | {', '.join(s['models']) or '—'} | {s['requests']} | {s['failed']} | "
                     f"{s['input_tokens']:,} | {s['output_tokens']:,} | {s['missing_usage']} | "
                     f"{s['call_seconds']:.2f} | {fmt(s['cost_usd'], 6)} |")
    L += ["", "Stage settings: " + json.dumps(meta["settings"]), "",
          "Local compute: **unknown** — " + assumptions["local_compute"]["note"], "",
          "## Rates used", "", "| Model | Item | Price | Unit | Source | Checked |", "|---|---|---|---|---|---|"]
    for (m, item), rt in sorted(rates.items()):
        L.append(f"| {m} | {item} | ${rt['price']} | per {rt['unit']} | {rt['source']} | {rt['checked']} |")
    L += ["", "## Projections (estimates, not measurements)", "",
          f"Basis from the cold pilot: `{json.dumps(basis)}`", "",
          f"Spending limit **${assumptions['spending_limit_usd']:.2f}** · max workers {assumptions['max_workers']} · "
          f"output-token caps {json.dumps(assumptions['output_token_cap'])} · max fallback fraction "
          f"{assumptions['fallback_fraction_max']} ({assumptions['fallback_note']})", "",
          "| Target | Exact-text reuse | Scenario | Enrich calls | Enrich | Verify | Group+memo (once) | Total API | Est. hours | Budget |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for p in proj:
        L.append(f"| {p['target']} | {'yes' if p['exact_text_reuse'] else 'no'} | {p['scenario']} | "
                 f"{p['enrich_calls']:,} | ${p['enrich_usd']:.2f} | ${p['verify_usd']:.2f} | "
                 f"${p['group_memo_usd']:.2f} | **${p['total_api_usd']:.2f}** | {p['est_hours']} | "
                 f"{'⚠️ OVER' if p['over_budget'] else 'ok'} |")
    L += ["", "Group and memo are fixed overhead counted once per run (never multiplied by review count). "
          "The conservative case adds retries, doubles the verification sample, triples fixed-stage cost and halves throughput.",
          "", "## Replay", "", "```", "python -m cost.calculator            # offline, no API key", "```"]
    Path(path).write_text("\n".join(L) + "\n")


def replay(dir_, rates_path, assumptions_path):
    dir_ = Path(dir_)
    calls_path, meta_path = dir_ / "pilot_calls.jsonl", dir_ / "pilot_meta.json"
    if not calls_path.exists() or not meta_path.exists():
        print("No pilot measurements yet. Run the paid pilot first:  python -m cost.calculator pilot --execute")
        return None
    rates, calls = load_rates(rates_path), load_calls(calls_path)
    meta, assumptions = json.loads(meta_path.read_text()), json.loads(Path(assumptions_path).read_text())
    summary = summarize(calls, rates, meta)
    basis, proj = project(summary, assumptions, rates, meta)
    write_usage_csv(calls, rates, dir_ / "usage.csv")
    write_report(dir_ / "report.md", meta, summary, basis, proj, assumptions, rates, rates_path)
    result = {"cold_api_usd": summary["cold"]["api_cost_usd"], "warm_api_usd": summary["warm"]["api_cost_usd"],
              "warm_enrich_requests": summary["warm"]["stages"]["enrich"]["requests"],
              "projections": proj}
    print(f"Cold ${summary['cold']['api_cost_usd']:.6f} in {summary['cold']['wall_seconds']}s | "
          f"warm ${summary['warm']['api_cost_usd']:.6f} in {summary['warm']['wall_seconds']}s | "
          f"report: {dir_ / 'report.md'}")
    for p in proj:
        if p["target"] == "declared_scope_100k" and p["exact_text_reuse"]:
            print(f"  100k {p['scenario']}: ${p['total_api_usd']:.2f}, ~{p['est_hours']} h"
                  + ("  ⚠️ OVER BUDGET" if p["over_budget"] else ""))
    return result


# ------------------------------------------------------------------ paid pilot
def file_sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pilot(execute, dir_):
    sys.path.insert(0, str(ROOT))
    from pipeline import config
    expected = json.loads(MANIFEST.read_text())["files"]["cost_100.csv"]["sha256"]
    actual = file_sha(PILOT_CSV)
    if actual != expected:
        raise SystemExit(f"cost_100.csv checksum mismatch: {actual} != manifest {expected}")
    run_dir = Path(dir_) / "pilot_run"
    db = run_dir / "pilot.db"
    base = [sys.executable, "-m", "pipeline.run", str(PILOT_CSV), "--db", str(db), "--scope", "all"]
    print(f"Pilot input verified: cost_100.csv sha256 {actual[:12]}… (matches manifest)")
    print(f"Plan: cold run (empty cache, {config.WORKERS} worker) then warm run (same settings), all stages.")
    print(f"Models: enrich {config.JEV_MODEL}; Claude {config.CLAUDE_MODEL_BY_ROLE} (effort {config.CLAUDE_EFFORT})")
    if not execute:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="pilot-dryrun-"))   # never touches the real pilot database
        dry = [sys.executable, "-m", "pipeline.run", str(PILOT_CSV), "--db", str(tmp / "dry.db"), "--scope", "all"]
        subprocess.run(dry + ["--out", str(tmp / "out")], cwd=ROOT, check=True)
        print("\nDry run only. Add --execute to make the paid pilot calls.")
        return
    if db.exists():
        raise SystemExit(f"{db} exists — a pilot was already run. Move it aside deliberately to re-run cold.")
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"input": "data/raw/cost_100.csv", "input_sha256": actual, "input_rows": 100, "workers": config.WORKERS,
            "settings": {"jev_model": config.JEV_MODEL, "claude_models": config.CLAUDE_MODEL_BY_ROLE,
                         "claude_effort": config.CLAUDE_EFFORT, "enrich_batch_size": 1,
                         "verify_batch_size": config.VERIFY_BATCH, "verify_sample": "20 (minimum) of 100",
                         "max_tokens": config.CLAUDE_MAX_TOKENS}}
    for phase in ("cold", "warm"):
        t = time.time()
        subprocess.run(base + ["--out", str(run_dir / f"out_{phase}"), "--execute", "--run-id", f"pilot-{phase}"],
                       cwd=ROOT, check=True)
        meta[phase] = {"wall_seconds": round(time.time() - t, 3)}
    finalize(run_dir, db, meta, Path(dir_))


def finalize(run_dir, db, meta, dir_):
    """Copy pilot evidence (records, calls, counts) into cost/ in the required formats."""
    sys.path.insert(0, str(ROOT))
    from pipeline.common import connect
    conn = connect(db)
    meta["unique_texts"] = conn.execute("SELECT COUNT(DISTINCT text_sha256) FROM reviews").fetchone()[0]
    for phase in ("cold", "warm"):
        rid = f"pilot-{phase}"
        meta[phase]["input_rows"] = meta["input_rows"] = 100
        meta[phase]["completed_records"] = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE status='completed'").fetchone()[0]
        meta[phase]["failed_records"] = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE status='quarantined'").fetchone()[0]
        meta[phase]["enrich_cache_hits"] = 100 - conn.execute(
            "SELECT COUNT(*) FROM calls WHERE run_id=? AND role='enrich' AND outcome='succeeded'", (rid,)).fetchone()[0]
        has_hits = conn.execute("SELECT name FROM sqlite_master WHERE name='claude_cache_hits'").fetchone()
        meta[phase]["claude_cache_hits"] = conn.execute(
            "SELECT COUNT(*) FROM claude_cache_hits WHERE run_id=?", (rid,)).fetchone()[0] if has_hits else 0
    with (dir_ / "pilot_calls.jsonl").open("w") as f:
        for row in conn.execute("SELECT request_id, run_id, role, provider, model, phase, outcome, error, attempt, "
                                "review_ids, label_config, input_tokens, output_tokens, started_at, duration_s "
                                "FROM calls ORDER BY started_at"):
            keys = ["request_id", "run_id", "role", "provider", "model", "phase", "outcome", "error", "attempt",
                    "review_ids", "label_config", "input_tokens", "output_tokens", "started_at", "duration_s"]
            rec = dict(zip(keys, row))
            rec["review_ids"] = json.loads(rec["review_ids"])
            rec["pilot_run"] = "cold" if rec["run_id"] == "pilot-cold" else "warm"
            f.write(json.dumps(rec) + "\n")
    from pipeline import export
    export.export(db, run_dir / "out_cold", run_dir / "grading_tmp")
    import gzip
    with gzip.open(run_dir / "grading_tmp/records.jsonl.gz", "rt") as src, (dir_ / "pilot_records.jsonl").open("w") as dst:
        shutil.copyfileobj(src, dst)
    (dir_ / "pilot_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"Pilot evidence saved under {dir_}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", default="replay", choices=["replay", "pilot"])
    p.add_argument("--execute", action="store_true", help="pilot only: make the paid calls")
    p.add_argument("--dir", default=str(COST))
    p.add_argument("--rates", default=str(COST / "rates.csv"))
    p.add_argument("--assumptions", default=str(COST / "assumptions.json"))
    a = p.parse_args()
    if a.mode == "pilot":
        pilot(a.execute, a.dir)
    else:
        replay(a.dir, a.rates, a.assumptions)


if __name__ == "__main__":
    main()
