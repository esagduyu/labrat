"""Thin orchestration over the tested M5 harvest helpers (Task 8).

Pure, unit-testable glue for the TUI's harvest-review flow. Keeps all
non-trivial logic in ``labrat.maze.harvest`` (Tasks 5-6, already tested) —
this module just sequences the calls and gates when harvesting is allowed
to run at all.

Imports of the maze/memory modules are done lazily inside functions to
avoid pulling their (heavier) dependency chains into every ``screens/``
import — this module will be imported by the TUI app shell once the
harvest action is wired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labrat.maze.document import Section
    from labrat.maze.store import MazeStore
    from labrat.memory.model import Memory


def review_corrections(
    memories: list[Memory],
    *,
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, list[Section]]:
    """Cluster correction memories and draft Scent sections for human review.

    Pure pipeline over the already-tested helpers: clusters -> drafted
    sections, ready to hand to an approval UI. Nothing here writes to the
    MazeStore — that only happens once a human approves specific sections
    via ``apply_approved_sections``. Returned dict is keyed by cluster key
    (see ``draft_harvested_sections``); use ``domain_for_cluster`` to map a
    key to a Scent domain.
    """
    from labrat.maze.harvest import cluster_corrections, draft_harvested_sections

    return draft_harvested_sections(
        cluster_corrections(memories), generated_at=generated_at, model_id=model_id
    )


def domain_for_cluster(key: str) -> str:
    """Map a cluster key to a Scent domain doc name (``__global__`` → ``general``)."""
    return "general" if key == "__global__" else key


def filter_unpromoted_decisions(memories: list[Memory], store: MazeStore) -> list[Memory]:
    """Drop decision memories already promoted into their target domain's Decisions section.

    For each decision memory, the target domain is
    ``domain_for_cluster(m.table_scope or "__global__")``. Loads that domain's
    PROJECT-layer doc (never the merged view — mirrors ``apply_approved_sections``'s
    layer discipline), collects the bullets of any section headed "Decisions"
    (body split on newlines, leading ``"- "`` stripped), and drops a decision
    whose ``text.strip()`` matches an existing bullet. Order is preserved.
    """
    domain_bullets: dict[str, set[str]] = {}
    survivors: list[Memory] = []
    for m in memories:
        domain = domain_for_cluster(m.table_scope or "__global__")
        if domain not in domain_bullets:
            bullets: set[str] = set()
            doc = store.load_domain(domain, scope="project")
            if doc is not None:
                for s in doc.sections:
                    if s.heading.strip() != "Decisions":
                        continue
                    for line in s.body.split("\n"):
                        line = line.strip()
                        if line.startswith("- "):
                            line = line[2:]
                        if line:
                            bullets.add(line.strip())
            domain_bullets[domain] = bullets
        if m.text.strip() not in domain_bullets[domain]:
            survivors.append(m)
    return survivors


def review_decisions(
    memories: list[Memory],
    store: MazeStore,
    *,
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, list[Section]]:
    """Filter already-promoted decisions, cluster the rest, and draft Decisions sections."""
    from labrat.maze.harvest import cluster_decisions, draft_decision_sections

    return draft_decision_sections(
        cluster_decisions(filter_unpromoted_decisions(memories, store)),
        generated_at=generated_at,
        model_id=model_id,
    )


def merge_drafts(
    a: dict[str, list[Section]], b: dict[str, list[Section]]
) -> dict[str, list[Section]]:
    """Concat drafted sections per domain: all domains from both, ``a``'s sections then ``b``'s."""
    merged: dict[str, list[Section]] = {}
    for domain in dict.fromkeys([*a, *b]):
        merged[domain] = [*a.get(domain, []), *b.get(domain, [])]
    return merged


def harvesting_enabled(is_interactive: bool, profile_opt_in: bool) -> bool:
    """Decide whether a ``SessionHarvester`` should run with ``enabled=True``.

    Harvesting must be off unless BOTH hold: there is a genuine interactive
    TUI session (never on benchmark/headless/no-TTY paths) AND the profile
    has opted in. Callers must pass this into ``SessionHarvester(enabled=...)``;
    the harvester itself now defaults to disabled.
    """
    return is_interactive and profile_opt_in
