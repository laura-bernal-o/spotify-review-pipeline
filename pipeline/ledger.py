"""Call log + spend ledger shared by every model role.

Every attempted model call (succeeded or failed) is appended to the `calls`
table and to calls.jsonl. Before each dispatch the caller reserves worst-case
cost; dispatch is refused when spent + reserved + next reservation > cap.
"""
import json
import threading
import time
import uuid
from pathlib import Path

from pipeline import config

CALLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    request_id    TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    role          TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    phase         TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    error         TEXT,
    attempt       INTEGER NOT NULL,
    review_ids    TEXT NOT NULL,
    label_config  TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL NOT NULL,
    started_at    REAL NOT NULL,
    duration_s    REAL NOT NULL
);
"""


class SpendCapReached(Exception):
    pass


def cost_usd(model, input_tokens, output_tokens):
    r = config.RATES[model]
    return (input_tokens or 0) * r["input"] / 1e6 + (output_tokens or 0) * r["output"] / 1e6


class Ledger:
    def __init__(self, conn, run_id, calls_jsonl: Path, run_cap=config.RUN_CAP_USD):
        self.conn, self.run_id, self.calls_jsonl = conn, run_id, Path(calls_jsonl)
        self.calls_jsonl.parent.mkdir(parents=True, exist_ok=True)
        conn.executescript(CALLS_SCHEMA)
        self.lock = threading.Lock()
        self.reserved = {"jev": 0.0, "claude": 0.0}
        self.run_cap = run_cap
        self.run_spent = 0.0
        self.cache_hits = {}

    def spent(self, provider):
        return self.conn.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE provider=?",
                                 (provider,)).fetchone()[0]

    def reserve(self, provider, amount):
        with self.lock:
            spent = self.spent(provider)
            cap = config.SPEND_CAP_USD[provider]
            if spent + self.reserved[provider] + amount > cap:
                raise SpendCapReached(f"{provider}: spent ${spent:.4f} + reserved ${self.reserved[provider]:.4f} "
                                      f"+ next ${amount:.4f} would exceed cap ${cap:.2f}")
            if self.run_spent + sum(self.reserved.values()) + amount > self.run_cap:
                raise SpendCapReached(f"run cap ${self.run_cap:.2f} reached (run spent ${self.run_spent:.4f})")
            self.reserved[provider] += amount

    def record(self, *, provider, role, model, phase, outcome, attempt, review_ids, label_config,
               input_tokens, output_tokens, started_at, reserved, error=None, request_id=None):
        cost = cost_usd(model, input_tokens, output_tokens) if input_tokens is not None else 0.0
        row = {
            "request_id": request_id or f"local-{uuid.uuid4().hex[:16]}",
            "run_id": self.run_id, "role": role, "provider": provider, "model": model, "phase": phase,
            "outcome": outcome, "error": error, "attempt": attempt, "review_ids": review_ids,
            "label_config": label_config, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_usd": round(cost, 10), "started_at": started_at, "duration_s": round(time.time() - started_at, 4),
        }
        with self.lock:
            self.reserved[provider] = max(0.0, self.reserved[provider] - reserved)
            self.run_spent += cost
            self.conn.execute(
                "INSERT INTO calls VALUES (:request_id,:run_id,:role,:provider,:model,:phase,:outcome,:error,"
                ":attempt,:review_ids,:label_config,:input_tokens,:output_tokens,:cost_usd,:started_at,:duration_s)",
                {**row, "review_ids": json.dumps(review_ids)})
            self.conn.commit()   # call evidence is durable immediately, even if the run crashes
            with self.calls_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
