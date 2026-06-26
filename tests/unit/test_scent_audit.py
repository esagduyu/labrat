"""Shared Scent contamination audit (FEATURE: LLM-semantic Scent / T1c)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import audit_scent_doc, detect_contamination


def _doc(body: str) -> ScentDoc:
    return ScentDoc(
        domain="d",
        sections=[Section(heading="Gotchas", body=body, source="draft")],
    )


def test_clean_doc_passes() -> None:
    assert (
        audit_scent_doc(_doc("Dates come in 3 mixed formats; filter with LIKE '%2018%'.")) is None
    )


def test_answer_key_phrase_flagged() -> None:
    assert audit_scent_doc(_doc("the ground truth answer is 2020")) == "answer_key"


def test_external_dataset_flagged() -> None:
    assert (
        audit_scent_doc(_doc("pull labels via load_dataset from huggingface")) == "external_dataset"
    )


def test_detect_contamination_text() -> None:
    assert detect_contamination("matches the gold answer") == "answer_key"
    assert detect_contamination("clean analytical text") is None


def test_suite_detector_uses_shared_patterns() -> None:
    # the DAB suite must reuse the same detector (one pattern list)
    from labrat.eval.benchmarks.dab.suite import _detect_contamination

    assert _detect_contamination("read ground_truth.csv") == "answer_key"
    assert _detect_contamination("clean") is None
