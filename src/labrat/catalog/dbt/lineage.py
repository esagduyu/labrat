"""Lineage graph queries over dbt CatalogEntry objects (M30)."""

from __future__ import annotations

from labrat.catalog.base import CatalogEntry


class DbtLineage:
    """Navigate the DAG of dbt model dependencies."""

    def __init__(self, entries: dict[str, CatalogEntry]) -> None:
        self._entries = entries

    def upstream(self, table: str, depth: int = 1) -> list[str]:
        """Return all models upstream of *table* up to *depth* hops."""
        return self._walk(table, direction="upstream", depth=depth)

    def downstream(self, table: str, depth: int = 1) -> list[str]:
        """Return all models downstream of *table* up to *depth* hops."""
        return self._walk(table, direction="downstream", depth=depth)

    def lineage_graph(self, table: str, depth: int = 2) -> dict[str, list[str]]:
        """Return a subgraph dict {node: [neighbours]} centred on *table*."""
        visited: set[str] = set()
        self._collect(table, "upstream", depth, visited)
        self._collect(table, "downstream", depth, visited)
        visited.add(table)
        graph: dict[str, list[str]] = {}
        for node in visited:
            entry = self._entries.get(node)
            if entry is None:
                continue
            neighbours = list(entry.upstream) + list(entry.downstream)
            graph[node] = [n for n in neighbours if n in visited]
        return graph

    # ── private ───────────────────────────────────────────────────────────────

    def _walk(self, table: str, direction: str, depth: int) -> list[str]:
        visited: set[str] = set()
        self._collect(table, direction, depth, visited)
        return list(visited)

    def _collect(self, table: str, direction: str, depth: int, visited: set[str]) -> None:
        if depth <= 0:
            return
        entry = self._entries.get(table)
        if entry is None:
            return
        neighbours = entry.upstream if direction == "upstream" else entry.downstream
        for neighbour in neighbours:
            if neighbour not in visited:
                visited.add(neighbour)
                self._collect(neighbour, direction, depth - 1, visited)
