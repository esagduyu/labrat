"""Read-only Analyst mode (Unit A): ToolContext.read_only + Tool.is_mutating + dispatch gate."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from pydantic import BaseModel

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool, _is_write_for_readonly
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


class _NoopInput(BaseModel):
    value: str = "x"


class _ReaderTool(Tool[_NoopInput]):
    @property
    def name(self) -> str:
        return "reader"

    @property
    def description(self) -> str:
        return "A read-only tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "read-ok"


class _WriterTool(Tool[_NoopInput]):
    mutating = True

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "A structurally mutating tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "wrote"


def test_tool_context_read_only_defaults_false() -> None:
    assert ToolContext().read_only is False


def test_tool_context_read_only_flag_set() -> None:
    assert ToolContext(read_only=True).read_only is True


def test_default_is_mutating_false() -> None:
    assert _ReaderTool().is_mutating(_NoopInput()) is False


def test_class_attr_mutating_true() -> None:
    assert _WriterTool().is_mutating(_NoopInput()) is True


async def test_dispatch_blocks_mutating_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext(read_only=True))
    assert res.ok is False
    assert res.value is None
    assert res.error == "blocked: read-only Analyst mode"


async def test_dispatch_allows_reader_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_ReaderTool())
    res = await reg.dispatch("reader", {}, ToolContext(read_only=True))
    assert res.ok is True
    assert res.value == "read-ok"


async def test_dispatch_allows_mutating_tool_when_not_read_only() -> None:
    # Regression: read_only defaults False → zero behavior change for all callers.
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext())
    assert res.ok is True
    assert res.value == "wrote"


async def test_attach_database_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "attach_database",
        {"path": "/tmp/x.sqlite", "alias": "ext", "db_type": "sqlite"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_load_file_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "load_file",
        {"path": "/tmp/x.csv", "table_name": "t"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_load_mongo_collection_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "load_mongo_collection",
        {"database": "articles_db", "collection": "articles", "target_table": "articles"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t(a INT)",
        "CREATE OR REPLACE VIEW v AS SELECT 1",
        "ALTER TABLE t ADD COLUMN b INT",
        "TRUNCATE t",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1",
        "GRANT SELECT ON t TO bob",
        "ATTACH 'x.db' AS aux",
        "DETACH aux",
        "COPY t TO 'out.csv'",
        "SET threads = 4",
        "SELECT 1; DROP TABLE t",  # stacked write in position 2
        "EXPLAIN ANALYZE INSERT INTO t VALUES (1)",  # actually executes on DuckDB/Postgres
        "EXPLAIN ANALYZE DELETE FROM t",
        "EXPLAIN ANALYZE UPDATE t SET a = 1",
        "explain analyze insert into t values (1)",  # case-insensitive
        "EXPLAIN ANALYZE SELECT 1",  # EXPLAIN ANALYZE always executes; over-blocking is fine
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",  # CTE-embedded write
        "EXPLAIN (ANALYZE) DELETE FROM t",  # parenthesized options
        "EXPLAIN (ANALYZE, BUFFERS) DELETE FROM t",  # ANALYZE first among options
        "EXPLAIN (BUFFERS, ANALYZE) DELETE FROM t",  # ANALYZE not first among options
        "EXPLAIN (ANALYZE true) DELETE FROM t",  # ANALYZE with explicit value
        "EXPLAIN ANALYSE DELETE FROM t",  # British spelling, bare
        "EXPLAIN (ANALYSE) INSERT INTO t VALUES (1)",  # British spelling, parenthesized
        "explain (analyze) delete from t",  # case-insensitive, parenthesized
        "EXPLAIN (Buffers, Analyze) DELETE FROM t",  # mixed case, ANALYZE not first
        "EXPLAIN /*c*/ ANALYZE INSERT INTO t VALUES (1)",  # leading block comment obfuscation
        "EXPLAIN /* multi word */ ANALYZE DELETE FROM t",  # leading block comment, multi-word
        "EXPLAIN --x\nANALYZE INSERT INTO t VALUES (1)",  # leading line comment obfuscation
        "EXPLAIN /*c*/ (ANALYZE) DELETE FROM t",  # leading comment before parenthesized options
        "SELECT * INTO t2 FROM t",  # SELECT INTO is a write (creates a table), no force
        "WITH c AS (SELECT 1 AS x) SELECT * INTO t2 FROM c",  # WITH-wrapped SELECT INTO
    ],
)
def test_write_statements_classified_mutating(sql: str) -> None:
    assert _is_write_for_readonly(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT a FROM t WHERE b > 2",
        "WITH q AS (SELECT 1 AS x) SELECT * FROM q",
        "SELECT 1 UNION SELECT 2",
        "EXPLAIN SELECT 1",
        "DESCRIBE t",
        "SHOW TABLES",
        "PRAGMA database_list",
        "EXPLAIN INSERT INTO t VALUES (1)",  # bare EXPLAIN doesn't execute — stays safe
        "EXPLAIN SELECT analyze_flag FROM t",  # false-positive probe: word in payload, not options
        "EXPLAIN /*c*/ SELECT * FROM t",  # leading comment before a non-ANALYZE payload
        "SELECT * FROM t",  # plain select, no INTO
        "SELECT id FROM t WHERE id IN (SELECT id FROM u)",  # subquery read, no INTO
    ],
)
def test_read_statements_classified_safe(sql: str) -> None:
    assert _is_write_for_readonly(sql) is False


@pytest.mark.parametrize("sql", ["SELEC nope FROM", "EXPORT DATABASE 'd'", ""])
def test_unparseable_sql_fail_closed(sql: str) -> None:
    assert _is_write_for_readonly(sql) is True


def test_run_sql_is_mutating_uses_query_classification() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="SELECT 1")) is False
    assert tool.is_mutating(tool.input_model(query="DROP TABLE t")) is True


def test_run_sql_is_mutating_explain_analyze_write_blocked() -> None:
    tool = RunSqlTool()
    assert (
        tool.is_mutating(tool.input_model(query="EXPLAIN ANALYZE INSERT INTO t VALUES (1)")) is True
    )
    assert tool.is_mutating(tool.input_model(query="EXPLAIN ANALYZE DELETE FROM t")) is True


def test_run_sql_is_mutating_bare_explain_still_safe() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="EXPLAIN SELECT * FROM t")) is False


def test_run_sql_is_mutating_explain_paren_analyze_blocked() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="EXPLAIN (ANALYZE) DELETE FROM t")) is True
    assert (
        tool.is_mutating(tool.input_model(query="EXPLAIN (BUFFERS, ANALYZE) DELETE FROM t")) is True
    )


def test_run_sql_is_mutating_explain_analyse_british_blocked() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="EXPLAIN ANALYSE DELETE FROM t")) is True
    assert (
        tool.is_mutating(tool.input_model(query="EXPLAIN (ANALYSE) INSERT INTO t VALUES (1)"))
        is True
    )


def test_run_sql_is_mutating_explain_false_positive_probe_safe() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="EXPLAIN SELECT analyze_flag FROM t")) is False


def test_run_sql_is_mutating_cte_embedded_write_blocked() -> None:
    tool = RunSqlTool()
    query = "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d"
    assert tool.is_mutating(tool.input_model(query=query)) is True


@pytest.fixture()
def ro_sql_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolContext]:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    p = str(tmp_path / "ro.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE items(id INTEGER, label VARCHAR)")
    raw.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    yield ToolContext(connection=conn, read_only=True)
    conn.disconnect()


async def test_run_sql_select_passes_under_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch("run_sql", {"query": "SELECT * FROM items"}, ro_sql_ctx)
    assert res.ok is True
    assert res.value.row_count == 2  # type: ignore[union-attr]


async def test_run_sql_insert_blocked_under_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch("run_sql", {"query": "INSERT INTO items VALUES (3, 'c')"}, ro_sql_ctx)
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_force_does_not_bypass_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch("run_sql", {"query": "DROP TABLE items", "force": True}, ro_sql_ctx)
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_explain_analyze_insert_blocked_under_read_only(
    ro_sql_ctx: ToolContext,
) -> None:
    # Critical fail-open bypass: EXPLAIN ANALYZE <write> parses to the same generic
    # exp.Command as bare EXPLAIN but actually executes the wrapped statement.
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "run_sql",
        {"query": "EXPLAIN ANALYZE INSERT INTO items VALUES (3, 'c')", "force": True},
        ro_sql_ctx,
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_explain_analyze_delete_blocked_under_read_only(
    ro_sql_ctx: ToolContext,
) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "run_sql",
        {"query": "EXPLAIN ANALYZE DELETE FROM items", "force": True},
        ro_sql_ctx,
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_explain_paren_analyze_delete_blocked_under_read_only(
    ro_sql_ctx: ToolContext,
) -> None:
    # Same bypass class, parenthesized-options form: EXPLAIN (ANALYZE) DELETE FROM t
    # also executes the wrapped statement on DuckDB/Postgres.
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "run_sql",
        {"query": "EXPLAIN (ANALYZE) DELETE FROM items", "force": True},
        ro_sql_ctx,
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_bare_explain_select_allowed_under_read_only(
    ro_sql_ctx: ToolContext,
) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch("run_sql", {"query": "EXPLAIN SELECT * FROM items"}, ro_sql_ctx)
    assert res.ok is True


async def test_run_sql_mutation_refusal_unchanged_when_not_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: without read_only, an INSERT is still handled by run_sql's own
    # (pre-existing) mutation refusal — dispatch ok=True, tool output refused=True.
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    p = str(tmp_path / "rw.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE items(id INTEGER)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        reg = build_data_tools_registry()
        res = await reg.dispatch(
            "run_sql", {"query": "INSERT INTO items VALUES (1)"}, ToolContext(connection=conn)
        )
    finally:
        conn.disconnect()
    assert res.ok is True
    assert res.value.refused is True  # type: ignore[union-attr]
