"""Thin orchestration over the tested M5 harvest helpers (Task 8).

Pure, unit-testable glue for the TUI's harvest-review flow. Keeps all
non-trivial logic in ``labrat.maze.harvest`` (Tasks 5-6, already tested) —
this module just sequences the calls and gates when harvesting is allowed
to run at all.

Imports of the maze/memory modules are done lazily inside functions to
avoid pulling their (heavier) dependency chains into every ``screens/``
import — this module is imported eagerly by the TUI app shell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labrat.maze.document import Section
    from labrat.memory.model import Memory


def review_corrections(
    memories: list[Memory],
    *,
    generated_at: str,
    model_id: str | None = None,
) -> list[Section]:
    """Cluster correction memories and draft Scent sections for human review.

    Pure pipeline over the already-tested helpers: clusters -> drafted
    sections, ready to hand to an approval UI. Nothing here writes to the
    MazeStore — that only happens once a human approves specific sections
    via ``apply_approved_sections``.
    """
    from labrat.maze.harvest import cluster_corrections, draft_harvested_sections

    return draft_harvested_sections(
        cluster_corrections(memories), generated_at=generated_at, model_id=model_id
    )


def harvesting_enabled(is_interactive: bool, profile_opt_in: bool) -> bool:
    """Decide whether a ``SessionHarvester`` should run with ``enabled=True``.

    Harvesting must be off unless BOTH hold: there is a genuine interactive
    TUI session (never on benchmark/headless/no-TTY paths) AND the profile
    has opted in. This is the caller-side default ``SessionHarvester`` (Task
    4) deferred to its callers.
    """
    return is_interactive and profile_opt_in
