"""Deterministic extraction (no model): evidence quotes and entity terms.

evidence_quote is always an exact substring of the source text:
  - texts up to SHORT_LIMIT characters: the whole text;
  - longer texts: the first sentence that mentions a term for the predicted topic,
    else the first sentence.
entities are feature terms matched verbatim (case-insensitive) from FEATURE_TERMS.
"""
import re

SHORT_LIMIT = 200
MAX_QUOTE = 300

TOPIC_TERMS = {
    "access": ["log in", "login", "logged out", "log out", "sign in", "sign up", "password", "account", "facebook login"],
    "usability": ["ads", "ad ", "advert", "shuffle", "queue", "playlist", "button", "navigation", "layout",
                  "interface", "ui", "widget", "lock screen", "skip", "update"],
    "playback": ["crash", "stops playing", "stop playing", "won't play", "doesn't play", "not playing", "pause",
                 "lag", "buffer", "connection", "offline mode", "sound", "audio", "quality", "battery", "freez",
                 "bluetooth", "android auto", "glitch", "bug"],
    "downloads": ["download", "offline", "saved", "storage", "sd card"],
    "catalog": ["song", "artist", "album", "podcast", "search", "recommend", "lyrics", "discover", "algorithm",
                "music selection", "library"],
    "billing": ["premium", "price", "pay", "charge", "subscription", "refund", "money", "free trial", "card",
                "family plan", "student"],
    "support": ["support", "customer service", "help center", "contacted", "email", "chat"],
    "other": [],
}

FEATURE_TERMS = sorted({t.strip() for terms in TOPIC_TERMS.values() for t in terms if len(t.strip()) > 2}
                       | {"lyrics", "podcast", "shuffle", "premium", "ads", "dj", "wrapped", "car mode"})

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def evidence_quote(text, topic):
    if len(text) <= SHORT_LIMIT:
        return text
    sentences = [m.group(0) for m in _SENTENCE.finditer(text) if m.group(0).strip()]
    terms = TOPIC_TERMS.get(topic, [])
    chosen = next((s for s in sentences if any(t in s.lower() for t in terms)), None)
    chosen = (chosen or (sentences[0] if sentences else text)).strip()
    quote = chosen[:MAX_QUOTE]
    assert quote in text
    return quote


def entities(text):
    low = text.lower()
    return [t for t in FEATURE_TERMS if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low)][:8]
