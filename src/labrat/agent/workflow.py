"""Data-analysis workflow SOP + inspectable run-state (FEATURE_ROADMAP #30).

The procedural half of the article's two-layer skill pattern: a canonical, ordered
senior-analyst SOP the agent walks and tracks. Pure data + rendering — no I/O, no LLM.
"""

from __future__ import annotations

from pydantic import BaseModel

_REPAIR_ATTEMPT_CAP = 3


class WorkflowStep(BaseModel):
    key: str
    label: str


DATA_ANALYSIS_WORKFLOW: list[WorkflowStep] = [
    WorkflowStep(key="clarify", label="Clarify the question (+ decompose multi-part questions)"),
    WorkflowStep(key="consult_scent", label="Consult reference docs (search_reference_docs)"),
    WorkflowStep(
        key="ground",
        label="Ground in the schema (profile_dataset / link_schema / column values)",
    ),
    WorkflowStep(key="plan", label="State a numbered plan"),
    WorkflowStep(key="query", label="Execute one step at a time, reading each result"),
    WorkflowStep(key="repair", label="On a SQL error, use the diagnostics to fix and retry"),
    WorkflowStep(key="verify_joins", label="Verify joins (verify_join) before trusting them"),
    WorkflowStep(key="verify_answer", label="Verify the answer addresses the question"),
    WorkflowStep(key="review", label="(opt-in) adversarial review"),
]

STEP_KEYS: tuple[str, ...] = tuple(s.key for s in DATA_ANALYSIS_WORKFLOW)

_STATUS_GLYPH = {"pending": " ", "doing": "~", "done": "x"}


class WorkflowState(BaseModel):
    statuses: dict[str, str] = {}
    notes: dict[str, str] = {}
    repair_attempts: int = 0

    @classmethod
    def new(cls) -> WorkflowState:
        return cls(statuses={s.key: "pending" for s in DATA_ANALYSIS_WORKFLOW})

    def mark(self, key: str, status: str, note: str | None = None) -> None:
        if key not in STEP_KEYS:
            raise ValueError(f"unknown workflow step: {key!r}")
        self.statuses[key] = status
        if note is not None:
            self.notes[key] = note

    def note_repair_failure(self) -> int:
        self.repair_attempts += 1
        return self.repair_attempts

    def render(self) -> str:
        lines: list[str] = []
        for step in DATA_ANALYSIS_WORKFLOW:
            glyph = _STATUS_GLYPH.get(self.statuses.get(step.key, "pending"), " ")
            line = f"[{glyph}] {step.key} — {step.label}"
            note = self.notes.get(step.key)
            if note:
                line += f"  ({note})"
            if step.key == "repair" and self.repair_attempts >= _REPAIR_ATTEMPT_CAP:
                line += f"  (!) {self.repair_attempts} failed attempts — rethink the approach"
            lines.append(line)
        return "\n".join(lines)
