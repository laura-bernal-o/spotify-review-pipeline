"""Stage 2 — Enrich (role: enrich, model: Jev).

One Jev request per distinct pending text (1 review <= the 50-review limit), asking
topic, intent and severity together. Code validates every answer, derives sentiment,
extracts the evidence quote and entities, and saves results after every
COMMIT_EVERY texts. Exact-duplicate texts in scope reuse the saved result with a
direct cache_source_id. Completed IDs are never re-sent under the same label_config,
so an interrupted run resumes where it stopped.

Default is a dry run (estimate only, no calls). Paid execution needs --execute.

Usage:
    python -m pipeline.enrich --db data/work/pilot.db --scope all            # estimate
    python -m pipeline.enrich --db data/work/pilot.db --scope all --execute  # paid
"""
import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline import config, extract, labels
from pipeline.common import DB_PATH, connect
from pipeline.ledger import Ledger, SpendCapReached, cost_usd

NEEDS_REVIEW_CONFIDENCE = 0.5   # any of topic/intent/severity below this -> needs_review
EST_TOKENS_PER_CALL = 700       # pre-pilot estimate; replaced by measured average once calls exist

RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    review_id       TEXT PRIMARY KEY REFERENCES reviews(review_id),
    topic           TEXT NOT NULL,
    intent          TEXT NOT NULL,
    severity        INTEGER NOT NULL,
    sentiment       REAL NOT NULL,
    entities        TEXT NOT NULL,
    evidence_quote  TEXT NOT NULL,
    needs_review    INTEGER NOT NULL,
    label_config    TEXT NOT NULL,
    cache_source_id TEXT,
    probabilities   TEXT,
    request_id      TEXT,
    run_id          TEXT NOT NULL
);
"""


# ---------------------------------------------------------------- scope
def ensure_scope_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(reviews)")]
    if "in_scope" not in cols:
        conn.execute("ALTER TABLE reviews ADD COLUMN in_scope INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def set_scope(conn, scope):
    """Mark the declared classification scope. 'all' = every nonempty review."""
    ensure_scope_column(conn)
    prior = conn.execute("SELECT value FROM meta WHERE key='scope'").fetchone()
    if prior:
        if prior[0] != str(scope):
            raise SystemExit(f"This database was scoped as '{prior[0]}'; refusing to change to '{scope}'.")
        return
    ids = [r[0] for r in conn.execute("SELECT review_id FROM reviews WHERE status != 'quarantined'")]
    if scope != "all":
        ids.sort(key=lambda rid: hashlib.sha256(f"{config.SCOPE_SEED}:{rid}".encode()).hexdigest())
        ids = ids[: int(scope)]
    conn.executemany("UPDATE reviews SET in_scope=1 WHERE review_id=?", [(i,) for i in ids])
    conn.execute("INSERT INTO meta VALUES ('scope', ?)", (str(scope),))
    conn.commit()


# ---------------------------------------------------------------- work queue
def pending_work(conn, label_config):
    """Return [(rep_id, text, [alias_ids])] for in-scope pending texts.

    If a completed record with identical text and label_config exists, aliases are
    filled from it without a call (rep_id None)."""
    rows = conn.execute(
        "SELECT review_id, review_text, text_sha256 FROM reviews "
        "WHERE in_scope=1 AND status='pending' ORDER BY row_idx").fetchall()
    groups = {}
    for rid, text, tsha in rows:
        groups.setdefault(tsha, [text, []])[1].append(rid)
    done = dict(conn.execute(
        "SELECT r.text_sha256, s.review_id FROM results s JOIN reviews r USING(review_id) "
        "WHERE s.label_config=? AND s.cache_source_id IS NULL", (label_config,)).fetchall())
    work, reuse = [], []
    for tsha, (text, ids) in groups.items():
        if tsha in done:
            reuse.append((done[tsha], ids))
        else:
            work.append((ids[0], text, ids[1:]))
    return work, reuse


# ---------------------------------------------------------------- Jev call
def jev_questions():
    from typesafe_sdk import Choice
    return {name: Choice(instructions=q["instructions"], criteria=q["criteria"])
            for name, q in labels.QUESTIONS.items()}


def validate(response):
    """Map a Jev response into our schema or raise ValueError."""
    a = response.answers
    out = {}
    for name in ("topic", "intent", "severity"):
        if name not in a:
            raise ValueError(f"missing answer '{name}'")
        ans = a[name]
        allowed = set(labels.QUESTIONS[name]["criteria"])
        if getattr(ans, "type", None) != "choice" or ans.choice not in allowed:
            raise ValueError(f"invalid {name} answer: {ans!r}")
        out[name] = ans
    return out


def classify(client, text):
    return client.system_one(state=text, questions=jev_questions(), model=config.JEV_MODEL)


def call_with_retries(client, ledger, rep_id, text, label_config, phase):
    """Bounded attempts; every attempt is logged. Returns (answers, request_id) or (None, reason)."""
    reserve = cost_usd(config.JEV_MODEL, config.RESERVE_TOKENS["jev"]["input"], config.RESERVE_TOKENS["jev"]["output"])
    invalid_left, error_left, attempt = config.MAX_INVALID_RETRIES, config.MAX_ERROR_RETRIES, 0
    last_reason = "unknown"
    while True:
        attempt += 1
        ledger.reserve("jev", reserve)   # raises SpendCapReached before dispatch
        started = time.time()
        try:
            resp = classify(client, text)
        except Exception as e:  # network/API error: usage unknown, logged as failed
            last_reason = f"api_error:{type(e).__name__}"
            ledger.record(provider="jev", role="enrich", model=config.JEV_MODEL, phase=phase, outcome="failed",
                          attempt=attempt, review_ids=[rep_id], label_config=label_config, input_tokens=None,
                          output_tokens=None, started_at=started, reserved=reserve, error=f"{type(e).__name__}: {e}"[:300])
            if error_left <= 0 or type(e).__name__ in ("TypeSafeAuthenticationError", "TypeSafePermissionDeniedError"):
                return None, last_reason
            error_left -= 1
            time.sleep(config.BACKOFF_SECONDS[min(attempt - 1, len(config.BACKOFF_SECONDS) - 1)])
            continue
        usage = getattr(resp, "usage", None)
        in_tok, out_tok = getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)
        try:
            if resp.model != config.JEV_MODEL:
                raise ValueError(f"served model {resp.model} != pinned {config.JEV_MODEL}")
            answers = validate(resp)
        except ValueError as e:
            last_reason = f"invalid_output:{e}"[:200]
            ledger.record(provider="jev", role="enrich", model=config.JEV_MODEL, phase=phase, outcome="failed",
                          attempt=attempt, review_ids=[rep_id], label_config=label_config, input_tokens=in_tok,
                          output_tokens=out_tok, started_at=started, reserved=reserve, error=last_reason)
            if invalid_left <= 0:
                return None, last_reason
            invalid_left -= 1
            continue
        row = ledger.record(provider="jev", role="enrich", model=config.JEV_MODEL, phase=phase, outcome="succeeded",
                            attempt=attempt, review_ids=[rep_id], label_config=label_config, input_tokens=in_tok,
                            output_tokens=out_tok, started_at=started, reserved=reserve)
        return answers, row["request_id"]


def build_record(text, answers):
    topic, intent, sev = answers["topic"], answers["intent"], answers["severity"]
    probs = {"topic": topic.probabilities, "intent": intent.probabilities, "severity": sev.probabilities}
    low_conf = min(topic.confidence, intent.confidence, sev.confidence) < NEEDS_REVIEW_CONFIDENCE
    return {
        "topic": topic.choice, "intent": intent.choice, "severity": int(sev.choice),
        "sentiment": labels.sentiment_from_intent(intent.probabilities),
        "entities": json.dumps(extract.entities(text)),
        "evidence_quote": extract.evidence_quote(text, topic.choice),
        "needs_review": int(low_conf), "probabilities": json.dumps(probs),
    }


# ---------------------------------------------------------------- persistence
def save_completed(conn, rep_id, alias_ids, rec, label_config, request_id, run_id):
    rows = [(rep_id, None, request_id)] + [(a, rep_id, None) for a in alias_ids]
    for rid, src, req in rows:
        conn.execute(
            "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, rec["topic"], rec["intent"], rec["severity"], rec["sentiment"], rec["entities"],
             rec["evidence_quote"], rec["needs_review"], label_config, src, rec["probabilities"], req, run_id))
        conn.execute("UPDATE reviews SET status='completed', reason=NULL, attempts=attempts+? WHERE review_id=?",
                     (1 if src is None else 0, rid))


def save_reuse(conn, src_id, alias_ids, label_config, run_id):
    src = conn.execute("SELECT * FROM results WHERE review_id=?", (src_id,)).fetchone()
    cols = [d[0] for d in conn.execute("SELECT * FROM results LIMIT 0").description]
    rec = dict(zip(cols, src))
    for rid in alias_ids:
        conn.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, rec["topic"], rec["intent"], rec["severity"], rec["sentiment"], rec["entities"],
                      rec["evidence_quote"], rec["needs_review"], label_config, src_id, rec["probabilities"],
                      None, run_id))
        conn.execute("UPDATE reviews SET status='completed', reason=NULL WHERE review_id=?", (rid,))


def save_failed(conn, ids, reason):
    for rid in ids:
        conn.execute("UPDATE reviews SET status='quarantined', reason=?, attempts=attempts+1 WHERE review_id=?",
                     (reason, rid))


# ---------------------------------------------------------------- driver
def estimate(conn, work, label_config):
    measured = conn.execute(
        "SELECT AVG(input_tokens), COUNT(*) FROM calls WHERE role='enrich' AND outcome='succeeded' "
        "AND label_config=?", (label_config,)).fetchone() if conn.execute(
        "SELECT name FROM sqlite_master WHERE name='calls'").fetchone() else (None, 0)
    per_call = measured[0] or EST_TOKENS_PER_CALL
    basis = f"measured avg over {measured[1]} calls" if measured[0] else "pre-pilot estimate"
    tokens = per_call * len(work)
    return {"calls": len(work), "tokens_per_call": round(per_call, 1), "basis": basis,
            "est_input_tokens": int(tokens), "est_cost_usd": round(cost_usd(config.JEV_MODEL, tokens, 0), 4)}


def run(db, scope, execute, run_id, calls_jsonl, workers=config.WORKERS, stop_after=None):
    conn = connect(db)
    conn.executescript(RESULTS_SCHEMA)
    set_scope(conn, scope)
    label_config = labels.label_config()
    work, reuse = pending_work(conn, label_config)
    est = estimate(conn, work, label_config)
    in_scope = conn.execute("SELECT COUNT(*) FROM reviews WHERE in_scope=1").fetchone()[0]
    print(f"[enrich] in scope {in_scope:,} | pending distinct texts {len(work):,} | "
          f"reusable from saved results {sum(len(a) for _, a in reuse):,}")
    print(f"[enrich] estimate: {est['calls']:,} Jev calls x {est['tokens_per_call']} tokens "
          f"= ${est['est_cost_usd']:.4f} ({est['basis']})")

    # Free work first: exact-text reuse of already-saved results.
    for src_id, ids in reuse:
        save_reuse(conn, src_id, ids, label_config, run_id)
    conn.commit()
    if not execute:
        print("[enrich] dry run — no calls made. Re-run with --execute to spend.")
        return {"executed": False, **est}

    from dotenv import load_dotenv
    from typesafe_sdk import RetryPolicy, TypeSafeClient
    load_dotenv()
    client = TypeSafeClient(retry=RetryPolicy(max_retries=0), timeout=30.0)  # our loop owns retries + logging

    already = conn.execute("SELECT COUNT(*) FROM reviews WHERE in_scope=1 AND status='completed'").fetchone()[0]
    phase = "resume" if already else "initial"
    ledger = Ledger(conn, run_id, calls_jsonl)
    done = failed = 0
    started = time.time()
    if stop_after is not None:
        work = work[:stop_after]

    def task(item):
        rep_id, text, aliases = item
        answers, info = call_with_retries(client, ledger, rep_id, text, label_config, phase)
        return item, answers, info

    stopped = None
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i in range(0, len(work), config.COMMIT_EVERY):
                chunk = work[i:i + config.COMMIT_EVERY]
                for (rep_id, text, aliases), answers, info in pool.map(task, chunk):
                    if answers is None:
                        save_failed(conn, [rep_id, *aliases], info)
                        failed += 1
                    else:
                        save_completed(conn, rep_id, aliases, build_record(text, answers), label_config, info, run_id)
                        done += 1
                conn.commit()   # results saved after every chunk
                print(f"[enrich] saved {done:,} texts ok, {failed} failed, {time.time() - started:.1f}s, "
                      f"run spend ${ledger.run_spent:.5f}")
    except SpendCapReached as e:
        stopped = f"spend cap: {e}"
    except KeyboardInterrupt:
        stopped = "interrupted by user"
    finally:
        conn.commit()
    if stopped:
        print(f"[enrich] STOPPED ({stopped}). Saved work is kept; re-run to resume.")
    return {"executed": True, "phase": phase, "texts_completed": done, "texts_failed": failed,
            "stopped": stopped, "wall_seconds": round(time.time() - started, 2), "run_spend_usd": ledger.run_spent}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--scope", default=str(config.FINAL_SCOPE), help="'all' or a number of reviews")
    p.add_argument("--execute", action="store_true", help="make paid Jev calls (default: estimate only)")
    p.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    p.add_argument("--calls", default=None, help="calls.jsonl path (default: next to the db)")
    p.add_argument("--workers", type=int, default=config.WORKERS)
    p.add_argument("--stop-after", type=int, default=None, help="stop after N texts (interruption demo)")
    a = p.parse_args()
    from pathlib import Path
    calls = a.calls or str(Path(a.db).with_suffix("")) + "_calls.jsonl"
    scope = a.scope if a.scope == "all" else int(a.scope)
    print(json.dumps(run(a.db, scope, a.execute, a.run_id, calls, a.workers, a.stop_after), indent=2))


if __name__ == "__main__":
    main()
