"""Tests for the shared lexical helpers extracted from link_schema (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from labrat.maze._lexical import name_tokens, question_tokens, stem


def test_name_tokens_splits_identifiers_unfiltered() -> None:
    assert name_tokens("article_metadata") == ["article", "metadata"]
    # unfiltered: short tokens and stopwords are kept at this layer
    assert name_tokens("the id") == ["the", "id"]


def test_question_tokens_drops_stopwords_and_short_tokens() -> None:
    toks = question_tokens("How many orders did each customer place?")
    assert "orders" in toks
    assert "customer" in toks
    assert "many" not in toks  # stopword
    assert "how" not in toks  # stopword
    assert "place" in toks  # 5-char content word survives
    assert all(len(t) >= 3 for t in toks)  # short tokens filtered


def test_stem_strips_trailing_s_only_when_long_enough() -> None:
    assert stem("orders") == "order"
    assert stem("is") == "is"  # len <= 3 untouched
