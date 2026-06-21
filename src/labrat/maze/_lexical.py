"""Shared deterministic lexical helpers for grounding tools.

Tokenizer / stemmer / stopword set, extracted verbatim from link_schema so that
link_schema and search_reference_docs share one implementation. No LLM, no I/O.
"""

from __future__ import annotations

import re

STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "for",
    "to",
    "and",
    "or",
    "by",
    "with",
    "which",
    "what",
    "how",
    "many",
    "most",
    "all",
    "list",
    "show",
    "each",
    "per",
    "is",
    "are",
    "that",
    "this",
    "number",
    "count",
    "total",
    "average",
    "sum",
    "find",
    "give",
    "get",
    "from",
    "where",
    "group",
    "order",
    "have",
    "has",
    "did",
    "do",
    "does",
    "between",
    "over",
    "into",
    "their",
    "its",
    "was",
    "were",
    "any",
}


def name_tokens(s: str) -> list[str]:
    """Split an identifier into alphanumeric tokens (article_metadata → article, metadata)."""
    return re.findall(r"[a-z0-9]+", s.lower())


def question_tokens(s: str) -> list[str]:
    return [t for t in name_tokens(s) if len(t) >= 3 and t not in STOPWORDS]


def stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") else t
