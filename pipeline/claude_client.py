"""Thin wrapper for Claude calls used by the verify, group and memo roles.

Every attempt goes through the shared Ledger (reservation before dispatch, usage
logged after). Structured outputs guarantee JSON; code still validates content.
"""
import hashlib
import json
import time

from pipeline import config
from pipeline.ledger import cost_usd

_client = None
CACHE_SCHEMA = ("CREATE TABLE IF NOT EXISTS claude_cache "
                "(key TEXT PRIMARY KEY, role TEXT, data TEXT NOT NULL, request_id TEXT)")


def client():
    global _client
    if _client is None:
        import anthropic
        from dotenv import load_dotenv
        load_dotenv()
        _client = anthropic.Anthropic(max_retries=0, timeout=120.0)  # our loop owns retries + logging
    return _client


def _params(role, system, user, schema):
    model = config.model_for(role)
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if not model.startswith("claude-haiku"):
        output_config["effort"] = config.CLAUDE_EFFORT   # Haiku 4.5 does not take effort
    return dict(model=model, max_tokens=config.CLAUDE_MAX_TOKENS[role], system=system,
                messages=[{"role": "user", "content": user}], output_config=output_config)


def call_json(ledger, role, system, user, schema, review_ids, validate, phase="initial", label_config=None):
    """Call Claude, parse JSON, run validate(data) (raise ValueError to reject).

    Returns (data, request_id). Invalid output is retried once; API errors use
    bounded backoff. Raises RuntimeError when attempts are exhausted."""
    import anthropic
    params = _params(role, system, user, schema)
    # Result cache: identical model + settings + prompt + input reuses the saved validated answer,
    # so a warm rerun makes no new Claude call. Cache hits are not logged as API calls.
    key = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    ledger.conn.execute(CACHE_SCHEMA)
    hit = ledger.conn.execute("SELECT data, request_id FROM claude_cache WHERE key=?", (key,)).fetchone()
    if hit:
        data = json.loads(hit[0])
        validate(data)
        ledger.cache_hits[role] = ledger.cache_hits.get(role, 0) + 1
        ledger.conn.execute("CREATE TABLE IF NOT EXISTS claude_cache_hits (run_id TEXT, role TEXT, key TEXT, t REAL)")
        ledger.conn.execute("INSERT INTO claude_cache_hits VALUES (?,?,?,?)", (ledger.run_id, role, key, time.time()))
        ledger.conn.commit()
        return data, hit[1]
    est_in = (len(system) + len(user)) // 3 + 50
    reserve = cost_usd(params["model"], est_in, params["max_tokens"])
    invalid_left, error_left, attempt = config.MAX_INVALID_RETRIES, config.MAX_ERROR_RETRIES, 0
    while True:
        attempt += 1
        ledger.reserve("claude", reserve)
        started = time.time()
        common = dict(provider="claude", role=role, model=params["model"], phase=phase, attempt=attempt,
                      review_ids=review_ids, label_config=label_config, started_at=started, reserved=reserve)
        try:
            msg = client().messages.create(**params)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            ledger.record(outcome="failed", input_tokens=None, output_tokens=None,
                          error=f"{type(e).__name__}: {e}"[:300], **common)
            if error_left <= 0:
                raise RuntimeError(f"{role}: API errors exhausted retries") from e
            error_left -= 1
            time.sleep(config.BACKOFF_SECONDS[min(attempt - 1, len(config.BACKOFF_SECONDS) - 1)])
            continue
        except anthropic.APIStatusError as e:   # 400/401/403/404: not retryable
            ledger.record(outcome="failed", input_tokens=None, output_tokens=None,
                          error=f"{type(e).__name__}: {e}"[:300], **common)
            raise RuntimeError(f"{role}: non-retryable API error {e.status_code}") from e
        u = msg.usage
        try:
            if msg.stop_reason != "end_turn":
                raise ValueError(f"stop_reason={msg.stop_reason}")
            text = next(b.text for b in msg.content if b.type == "text")
            data = json.loads(text)
            validate(data)
        except (ValueError, StopIteration, json.JSONDecodeError) as e:
            ledger.record(outcome="failed", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                          error=f"invalid_output: {e}"[:300], request_id=msg._request_id, **common)
            if invalid_left <= 0:
                raise RuntimeError(f"{role}: invalid output after retry: {e}") from e
            invalid_left -= 1
            continue
        ledger.record(outcome="succeeded", input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                      request_id=msg._request_id, **common)
        ledger.conn.execute("INSERT OR REPLACE INTO claude_cache VALUES (?,?,?,?)",
                            (key, role, json.dumps(data, ensure_ascii=False), msg._request_id))
        ledger.conn.commit()
        return data, msg._request_id
