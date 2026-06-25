"""_detect_infra_failure classifies server-side API outages as infra (not semantic fails).

A 2026-06-23 Claude outage surfaced 14 trials with "API Error: 500/529" final text that
were miscounted as semantic FAILs because the pattern list didn't cover API errors.
"""

from __future__ import annotations

import pytest

from labrat.eval.benchmarks.dab.suite import _detect_infra_failure


@pytest.mark.parametrize(
    "text",
    [
        "API Error: 500 Internal server error. This is a server-side issue, usually temporary",
        "API Error: 529 Overloaded. This is a server-side issue",
        "API Error: 502 Bad gateway",
        "API Error: 429 Too Many Requests",
        "The model is Overloaded right now",
    ],
)
def test_api_outage_text_is_infra(text: str) -> None:
    assert _detect_infra_failure(text) == "api_error"


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your Sonnet limit · resets 5am (America/Vancouver)",
        "You've hit your Opus limit · resets 5am",
        "You've hit your usage limit",
    ],
)
def test_model_rate_limit_text_is_infra(text: str) -> None:
    # The claude CLI surfaces a per-model/usage quota as plain text with exit 0, so
    # without this pattern it was miscounted as a semantic FAIL (score 0) — a 2026-06-25
    # ablation lost 3 of 4 arms this way. Must classify as infra so it's excluded/retried.
    assert _detect_infra_failure(text) == "rate_limit"


def test_existing_infra_tags_unchanged() -> None:
    assert _detect_infra_failure("You've hit your session limit") == "session_limit"
    assert _detect_infra_failure("[trial exceeded 1200s timeout]") == "timeout"


def test_normal_answer_is_not_infra() -> None:
    assert _detect_infra_failure("The answer is 42 unique tracks.") is None
    assert _detect_infra_failure("SELECT COUNT(*) returned 1530 rows.") is None
