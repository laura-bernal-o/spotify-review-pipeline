"""Run settings, spend limits and dated price rates. Edit here, not in stage code."""
from pipeline.common import ROOT

# ---- Scope -------------------------------------------------------------------
# Final run classifies this many nonempty reviews (user decision 2026-10-06: 100k).
# Selection is deterministic: nonempty reviews ordered by SHA-256(SEED + ":" + review_id),
# the same uniform method manifest.json uses, so the golden 50 and the 10k analysis
# sample are always included. Everything else is quarantined as outside budget scope.
FINAL_SCOPE = 100_000
SCOPE_SEED = "berkeley-fall-2026-assignment-5-v1"
OUT_OF_SCOPE_REASON = "not_classified_outside_declared_budget_scope"

# ---- Spend controls ----------------------------------------------------------
SPEND_CAP_USD = {"jev": 5.00, "claude": 5.00}   # hard stop per provider, across all runs
RUN_CAP_USD = 5.00                               # hard stop for a single run
WORKERS = 1                                      # start at 1; raise only after measuring
COMMIT_EVERY = 50                                # save results after this many reviews
MAX_INVALID_RETRIES = 1                          # contract: retry invalid output once
MAX_ERROR_RETRIES = 3                            # bounded retries for API/network errors
BACKOFF_SECONDS = (1, 4, 10)

# ---- Models ------------------------------------------------------------------
JEV_MODEL = "jev-1.13.0"
CLAUDE_MODEL = "claude-haiku-4-5"  # user decision 2026-10-16: cheapest first; upgrade only if evals justify it
# Per-role overrides. Memo moved to Sonnet 5.5 (user decision 2026-10-16) after the Haiku memo on the
# 500 checkpoint passed code checks but mislabeled metrics and reasoned weakly; one call per run.
CLAUDE_MODEL_BY_ROLE = {"verify": CLAUDE_MODEL, "group": CLAUDE_MODEL, "memo": "claude-sonnet-5-5"}


def model_for(role):
    return CLAUDE_MODEL_BY_ROLE.get(role, CLAUDE_MODEL)
CLAUDE_EFFORT = "low"              # lowest effort that passes evals
CLAUDE_MAX_TOKENS = {"verify": 4000, "group": 4000, "memo": 8000}

VERIFY_SAMPLE_FRACTION = 0.01      # declared random sample re-labelled by the verifier
VERIFY_BATCH = 25                  # reviews per verifier request (<= 50)
VERIFY_SEED = "verify-v1"

# ---- Rates (USD) — edit when prices change; cost/rates.csv mirrors these ------
# Units: price per 1,000,000 tokens.
RATES = {
    "jev-1.13.0": {"input": 0.042, "output": 0.0,
                   "source": "https://docs.typesafe.ai/llms-full.txt (Structure Recovery cookbook: "
                             "'(0.042, 0.00) $ per 1M tokens'); https://typesafe.ai ('$42 per billion input tokens')",
                   "checked": "2026-10-06"},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00,
                         "source": "https://platform.claude.com/docs/en/about-claude/pricing", "checked": "2026-10-06"},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00,
                          "source": "https://platform.claude.com/docs/en/about-claude/pricing", "checked": "2026-10-06"},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00,
                        "source": "https://platform.claude.com/docs/en/about-claude/pricing", "checked": "2026-10-06"},
}

# Worst-case per-request reservation used by the spend ledger before dispatch.
RESERVE_TOKENS = {"jev": {"input": 2_000, "output": 200}}

STATE_DIR = ROOT / "data" / "work"
