"""Shared text handling for memories: content-word tokens and similarity."""

from __future__ import annotations

import re

STEM_LENGTH = 6
MIN_TOKEN = 3

STOPWORDS = frozenset("""
the and for are but not you all any can had her was one our out has have this that with from they
will would there their what when which while into than then them these those been being over such
only also just more most other some very about after before again here where your yours each
use used using never always should must make sure does doing done like
che per con non una uno gli le lo la il di da del della dei delle nel nella sono come anche
piu più tutto tutti sempre mai questo questa quello quella essere fare viene ogni dove quando
""".split())

_TOKEN = re.compile(r"[a-zà-ù0-9_]+")


def stem(token: str) -> str:
    # Crude prefix stemming: "refunds"/"refund", "rimborsi"/"rimborso" meet at six letters.
    return token[:STEM_LENGTH]


def tokens(text: str) -> set[str]:
    """Stemmed content words; identifiers and path parts count as words."""
    return {stem(w) for w in _content_words(text)}


_FACT_PATTERNS = (
    re.compile(r"`([^`\n]+)`"),                                           # code spans
    re.compile(r"\b\d+(?:[.,]\d+)?%?"),                                    # numbers, versions, percentages
    re.compile(r"\b[A-Za-z_][\w-]*(?:[./][\w-]+)+/?"),                     # paths, dotted names, files
    re.compile(r"\b[a-z0-9]+(?:_[a-z0-9]+)+\b"),                           # snake_case
    re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+)+\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b"),  # camelCase, PascalCase
    re.compile(r"\b[A-Z][A-Z0-9]{2,}\b"),                                  # SEC003, AWS, HTTP
)


def facts(text: str) -> set[str]:
    """Precise details a summary must not drop: numbers, identifiers, paths, code spans."""
    found = set()
    for pattern in _FACT_PATTERNS:
        for match in pattern.finditer(text):
            value = (match.group(1) if match.groups() else match.group(0)).strip().strip(".,;:").lower()
            if value:
                found.add(value)
    return found


def lost_details(source: str, rewrite: str, words: bool = True) -> list[str]:
    """What `rewrite` dropped from `source`: facts always, and content words when `words` is set."""
    target = rewrite.lower()
    lost = {f for f in facts(source) if f not in target}
    if words:
        original: dict[str, str] = {}
        for w in _content_words(source):
            original.setdefault(stem(w), w)
        lost |= {original[s] for s in tokens(source) - tokens(rewrite)}
    return sorted(lost, key=lambda x: (len(x), x))


def _content_words(text: str) -> list[str]:
    words = _TOKEN.findall(text.lower().replace("/", " ").replace(".", " "))
    return [w for w in words if len(w) >= MIN_TOKEN and w not in STOPWORDS and not w.isdigit()]


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of content words, 0..1."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
