"""Schema-aware SQL completion engine."""

from dataclasses import dataclass
from enum import StrEnum

from labrat.db.catalog import Catalog


class CompletionKind(StrEnum):
    TABLE = "table"
    COLUMN = "column"
    SCHEMA = "schema"
    KEYWORD = "keyword"


@dataclass
class Completion:
    """A single completion candidate."""

    label: str
    kind: CompletionKind
    detail: str
    score: int


_KEYWORDS = [
    "SELECT",
    "FROM",
    "WHERE",
    "JOIN",
    "LEFT JOIN",
    "RIGHT JOIN",
    "INNER JOIN",
    "OUTER JOIN",
    "ON",
    "GROUP BY",
    "ORDER BY",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "AS",
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "DISTINCT",
    "ALL",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "WITH",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
]

_CONTEXT_KEYWORDS = {"FROM", "JOIN", "SELECT", "WHERE"}


class SQLCompleter:
    """Returns ranked completions for a SQL string at a given cursor offset.

    Ranking: exact match (300) > prefix match (200) > substring match (100).
    Minimum prefix length: 2 characters (except after dot notation).
    """

    def __init__(self, catalog: Catalog | None = None) -> None:
        self._catalog = catalog

    def set_catalog(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def complete(self, sql: str, cursor_offset: int) -> list[Completion]:
        """Return sorted completion candidates for the cursor position."""
        prefix = self._extract_prefix(sql, cursor_offset)
        dot_pos = prefix.rfind(".")

        if dot_pos >= 0:
            # After a dot: complete columns of the named table
            table_name = prefix[:dot_pos]
            col_prefix = prefix[dot_pos + 1 :]
            results = self._column_completions_for(table_name, col_prefix)
        elif len(prefix) < 2:
            return []
        else:
            context = self._detect_context(sql, cursor_offset)
            results = self._context_completions(prefix, context)

        return sorted(results, key=lambda c: -c.score)

    # ── prefix / context extraction ───────────────────────────────────────────

    @staticmethod
    def _extract_prefix(sql: str, cursor_offset: int) -> str:
        """Return the identifier fragment ending at cursor_offset."""
        text = sql[:cursor_offset]
        i = len(text) - 1
        while i >= 0 and (text[i].isalnum() or text[i] in "_."):
            i -= 1
        return text[i + 1 :]

    @staticmethod
    def _detect_context(sql: str, cursor_offset: int) -> str:
        """Return the last SQL keyword that establishes context for completions."""
        upper = sql[:cursor_offset].upper()
        best_kw = ""
        best_pos = -1
        for kw in _CONTEXT_KEYWORDS:
            idx = upper.rfind(kw + " ")
            if idx > best_pos:
                best_pos = idx
                best_kw = kw
        return best_kw

    # ── candidate generation ──────────────────────────────────────────────────

    def _context_completions(self, prefix: str, context: str) -> list[Completion]:
        results: list[Completion] = []
        if context in ("FROM", "JOIN"):
            results.extend(self._table_completions(prefix))
        else:
            results.extend(self._table_completions(prefix))
            results.extend(self._all_column_completions(prefix))
            results.extend(self._keyword_completions(prefix))
        return results

    def _table_completions(self, prefix: str) -> list[Completion]:
        if self._catalog is None:
            return []
        out: list[Completion] = []
        for schema in self._catalog.schemas:
            for table in schema.tables:
                score = _score(table.name, prefix)
                if score:
                    out.append(
                        Completion(
                            label=table.name,
                            kind=CompletionKind.TABLE,
                            detail=f"table in {schema.name}",
                            score=score,
                        )
                    )
        return out

    def _column_completions_for(self, table_name: str, col_prefix: str) -> list[Completion]:
        if self._catalog is None:
            return []
        table = self._catalog.find_table(table_name)
        if table is None:
            return []
        out: list[Completion] = []
        for col in table.columns:
            score = _score(col.name, col_prefix) if col_prefix else 1
            if score or not col_prefix:
                out.append(
                    Completion(
                        label=col.name,
                        kind=CompletionKind.COLUMN,
                        detail=col.data_type,
                        score=score if col_prefix else 50,
                    )
                )
        return out

    def _all_column_completions(self, prefix: str) -> list[Completion]:
        if self._catalog is None:
            return []
        out: list[Completion] = []
        for schema in self._catalog.schemas:
            for table in schema.tables:
                for col in table.columns:
                    score = _score(col.name, prefix)
                    if score:
                        out.append(
                            Completion(
                                label=col.name,
                                kind=CompletionKind.COLUMN,
                                detail=f"{col.data_type} in {table.name}",
                                score=score,
                            )
                        )
        return out

    def _keyword_completions(self, prefix: str) -> list[Completion]:
        out: list[Completion] = []
        for kw in _KEYWORDS:
            score = _score(kw, prefix)
            if score:
                out.append(
                    Completion(
                        label=kw,
                        kind=CompletionKind.KEYWORD,
                        detail="keyword",
                        score=score,
                    )
                )
        return out


def _score(candidate: str, prefix: str) -> int:
    """Rank a candidate against a prefix. 0 = no match."""
    if not prefix:
        return 0
    lower = candidate.lower()
    pfx = prefix.lower()
    if lower == pfx:
        return 300
    if lower.startswith(pfx):
        return 200
    if pfx in lower:
        return 100
    return 0
