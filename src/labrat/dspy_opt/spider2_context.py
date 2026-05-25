"""Runtime context shared by all Spider2-DBT agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labrat.dspy_opt.snapshot import ReferenceSnapshot


@dataclass
class Spider2Context:
    """Mutable state threaded through every tool call in a Spider2 agent run."""

    project_dir: Path
    db_path: Path | None  # DuckDB file; may not exist until dbt run succeeds
    pre_existing_files: frozenset[str]  # relative paths that must NOT be overwritten
    instruction: str
    target_file: str

    # Mutable state
    dbt_dirty: bool = True  # True when files changed since last successful run_dbt
    submitted: bool = False  # True after submit tool is called
    submit_description: str = ""
    last_dbt_returncode: int | None = None

    # Phase 3: snapshot + verifier
    snapshot: ReferenceSnapshot | None = None
    eval_tables: list[str] = field(default_factory=list)


def build_pre_existing(project_dir: Path) -> frozenset[str]:
    """Scan the project and record all pre-existing SQL model files."""
    models_dir = project_dir / "models"
    if not models_dir.exists():
        return frozenset()
    return frozenset(str(f.relative_to(project_dir)) for f in models_dir.rglob("*.sql"))
