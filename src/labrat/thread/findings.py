"""FindingsManager: pin/unpin/re-note/reorder findings (M18)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labrat.cheese.model import FindingProvenance
from labrat.thread.model import Finding

_DEFAULT_DIR = Path.home() / ".local" / "share" / "labrat"


class FindingsManager:
    """Manages pinned findings.

    Findings are stored as an ordered JSON list at
    ``<store_dir>/findings.json``.  Order is explicit and user-controlled
    via ``reorder()``.
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self._file = (store_dir or _DEFAULT_DIR) / "findings.json"

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> list[Finding]:
        if not self._file.exists():
            return []
        raw: list[dict[str, Any]] = json.loads(self._file.read_text())
        return [Finding.model_validate(d) for d in raw]

    def _save(self, findings: list[Finding]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps([f.model_dump(mode="json") for f in findings], indent=2))

    # ── public API ────────────────────────────────────────────────────────────

    def list_findings(self) -> list[Finding]:
        """Return all findings in their current order."""
        return self._load()

    def pin(
        self,
        *,
        version_id: str,
        question: str,
        sql: str,
        results_ref: str | None,
        chart_spec: dict[str, Any] | None,
        note: str = "",
        provenance: FindingProvenance | None = None,
    ) -> Finding:
        """Pin a new finding. Appended to the end of the list."""
        finding = Finding(
            id=str(uuid.uuid4()),
            version_id=version_id,
            question=question,
            sql=sql,
            results_ref=results_ref,
            chart_spec=chart_spec,
            note=note,
            pinned_at=datetime.now(tz=UTC),
            provenance=provenance,
        )
        findings = self._load()
        findings.append(finding)
        self._save(findings)
        return finding

    def unpin(self, finding_id: str) -> None:
        """Remove a finding by ID. No-op if the ID does not exist."""
        findings = self._load()
        findings = [f for f in findings if f.id != finding_id]
        self._save(findings)

    def update_note(self, finding_id: str, note: str) -> None:
        """Update the note on an existing finding."""
        findings = self._load()
        for i, f in enumerate(findings):
            if f.id == finding_id:
                findings[i] = f.model_copy(update={"note": note})
                break
        self._save(findings)

    def reorder(self, finding_id: str, new_index: int) -> None:
        """Move a finding to a new position (0-based) in the ordered list."""
        findings = self._load()
        target = next((f for f in findings if f.id == finding_id), None)
        if target is None:
            return
        findings = [f for f in findings if f.id != finding_id]
        new_index = max(0, min(new_index, len(findings)))
        findings.insert(new_index, target)
        self._save(findings)
