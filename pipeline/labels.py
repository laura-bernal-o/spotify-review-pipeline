"""Shared label definitions and the Jev questions used by the enrich role.

Wording follows GRADING_CONTRACT.md (common labels, severity scale, intent
precedence) but is kept short because Jev bills input tokens on every call.
Bump LABEL_VERSION whenever any wording changes: cached results are only
reused under the identical label_config.
"""
from pipeline.common import INTENTS, TOPICS

LABEL_VERSION = "labels-v1-short"
JEV_MODEL = "jev-1.13.0"  # pinned; checked against response.model at runtime

TOPIC = {
    "instructions": "Main topic. Pick the most severe specific problem (first mentioned if tied); "
                    "for praise, the first praised feature. Paid plan mentioned alone is not billing.",
    "criteria": {
        "access": "login, signup, password, account access",
        "usability": "navigation, controls, layout, queue/playlist management, ad interruptions",
        "playback": "won't play, crashes, lag, connection errors, audio quality, battery/data use",
        "downloads": "downloading, offline listening, saved music disappearing",
        "catalog": "missing songs/artists, search, recommendations, lyrics",
        "billing": "price, charges, subscription, paywalls, premium-only features",
        "support": "contacting customer support or its response",
        "other": "general praise/criticism, unrelated, or no specific topic",
    },
}

INTENT = {
    "instructions": "Reviewer intent. If several apply, choose the one listed first.",
    "criteria": {
        "cancellation": "leaving, uninstalling, cancelling, or threatening to",
        "complaint": "negative experience, incl. mixed reviews or 'bad app'",
        "request": "wants a change, no failure reported",
        "praise": "positive",
        "unclear": "meaningless, unrelated, or a bare boycott slogan",
    },
}

SEVERITY = {
    "instructions": "Impact actually reported. Anger, stars or quitting alone don't raise it.",
    "criteria": {
        "1": "no problem: praise, neutral, unclear, or feature request",
        "2": "dislike, generic criticism, minor annoyance",
        "3": "a function degraded or restricted, still partly usable",
        "4": "a core task blocked, e.g. can't log in or play music",
        "5": "explicit serious financial, privacy or data harm",
    },
}

assert tuple(TOPIC["criteria"]) == TOPICS
assert tuple(INTENT["criteria"]) == INTENTS

QUESTIONS = {"topic": TOPIC, "intent": INTENT, "severity": SEVERITY}


def label_config(questions=("topic", "intent", "severity")):
    return f"{JEV_MODEL}+{LABEL_VERSION}+schema-a5-v1+q={','.join(questions)}"


def question_chars(names=("topic", "intent", "severity")):
    """Characters of question text sent per call (for offline token estimates)."""
    total = 0
    for n in names:
        q = QUESTIONS[n]
        total += len(n) + len(q["instructions"]) + sum(len(k) + len(v) for k, v in q["criteria"].items())
    return total


# Sentiment is derived in code (no extra Jev question, so no extra tokens):
# sentiment = P(praise) - P(complaint) - P(cancellation), clipped to [-1, 1].
# Declared method; evaluated against human labels with a +/-0.5 tolerance.
def sentiment_from_intent(probs):
    s = probs.get("praise", 0) - probs.get("complaint", 0) - probs.get("cancellation", 0)
    return round(max(-1.0, min(1.0, s)), 3)
