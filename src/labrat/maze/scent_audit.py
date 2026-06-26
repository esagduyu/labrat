"""Contamination audit for Scent docs and benchmark trial output.

Single source of truth for the answer-key / gold-answer / external-dataset
substring patterns. Used at two seams:
  - generate_scent freeze-time: audit_scent_doc(doc) guards LLM-authored semantics
    before a doc is frozen/consumed (fail-loud via ScentContaminationError).
  - DAB trial scoring: detect_contamination(trial_text) withdraws leaked trials.
"""

from __future__ import annotations

from labrat.maze.document import ScentDoc, render_document

# Tags checked in order; answer-key access is the more severe signal. These mark
# answer-key leakage (validate.py / ground_truth.csv, or NL gold-answer assertions)
# or external labelled datasets (HuggingFace load_dataset). See DAB PR #54.
CONTAMINATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("validate.py", "answer_key"),
    ("ground_truth", "answer_key"),
    ("ground truth", "answer_key"),
    ("ground-truth", "answer_key"),
    ("answer key", "answer_key"),
    ("gold answer", "answer_key"),
    ("load_dataset", "external_dataset"),
    ("huggingface", "external_dataset"),
    ("fancyzhx/ag_news", "external_dataset"),
)


class ScentContaminationError(RuntimeError):
    """Raised when an authored Scent doc contains answer-shaped content."""


def detect_contamination(text: str) -> str | None:
    """Return a contamination tag ('answer_key' / 'external_dataset') if the text
    shows answer-key or external-label leakage; otherwise None. Case-insensitive."""
    low = text.lower()
    for needle, tag in CONTAMINATION_PATTERNS:
        if needle in low:
            return tag
    return None


def audit_scent_doc(doc: ScentDoc) -> str | None:
    """Return a contamination tag if a rendered Scent doc contains answer-shaped
    content; otherwise None. Run before freezing/consuming an LLM-authored doc."""
    return detect_contamination(render_document(doc))
