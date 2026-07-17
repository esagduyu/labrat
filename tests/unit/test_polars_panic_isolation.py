"""Polars pyo3 PanicException isolation (out-of-range dates crash `iter_rows`).

A DuckDB result containing e.g. ``DATE '99999-01-01'`` makes polars'
``iter_rows()`` raise ``pyo3_runtime.PanicException`` — a **BaseException**
subclass that sails through every ``except Exception`` layer and killed a whole
DAB shard run (deps_dev_v1, 2026-07-16). These tests pin the containment: the
formatting seams convert the panic into a structured, repair-friendly error and
the registry dispatch has a last-resort backstop.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from polars.exceptions import PanicException
from pydantic import BaseModel

from labrat.agent.tools import run_sql as run_sql_mod
from labrat.agent.tools.base import DispatchResult, Tool, ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.sample_rows import SampleRowsTool
from labrat.db.duckdb_engine import DuckDBConnection

_PANIC_SQL = "SELECT DATE '99999-01-01' AS d"


@pytest.fixture()
def weird_db(tmp_path: Path) -> Path:
    import duckdb

    path = tmp_path / "weird.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE weird_dates(id INTEGER, d DATE)")
    con.execute("INSERT INTO weird_dates VALUES (1, DATE '99999-01-01')")
    con.close()
    return path


@pytest.fixture()
def ctx(weird_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolContext]:
    monkeypatch.setattr(run_sql_mod._history_log, "append", lambda event: None)
    conn = DuckDBConnection(weird_db, read_only=True)
    conn.connect()
    yield ToolContext(connection=conn, catalog=conn.introspect_catalog())
    conn.disconnect()


async def test_run_sql_out_of_range_date_returns_structured_error(ctx: ToolContext) -> None:
    tool = RunSqlTool()
    out = await tool.execute(ctx, tool.input_model(query=_PANIC_SQL))
    assert out.ok is False
    assert out.error_category == "result_conversion"
    assert out.hint is not None and "CAST" in out.hint


async def test_run_sql_panic_never_escapes_dispatch(ctx: ToolContext) -> None:
    registry = ToolRegistry()
    registry.register(RunSqlTool())
    result = await registry.dispatch("run_sql", {"query": _PANIC_SQL}, ctx)
    assert isinstance(result, DispatchResult)
    # the tool converts the panic into a structured payload; nothing escapes
    assert result.ok is True
    assert result.value is not None and result.value.ok is False


async def test_sample_rows_panic_degrades_to_dispatch_error(ctx: ToolContext) -> None:
    registry = ToolRegistry()
    registry.register(SampleRowsTool())
    result = await registry.dispatch("sample_rows", {"table": "weird_dates", "n": 5}, ctx)
    assert result.ok is False
    assert result.error is not None and "CAST" in result.error


async def test_profile_dataset_survives_pathological_table(ctx: ToolContext) -> None:
    from labrat.agent.tools.profile_dataset import ProfileDatasetTool

    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=3))
    table = next(t for t in out.tables if "weird_dates" in t.name)
    assert table.note is not None and "sampling failed" in table.note


async def test_dispatch_backstop_catches_raw_panic(ctx: ToolContext) -> None:
    class _Input(BaseModel):
        pass

    class _PanickyTool(Tool[_Input]):
        @property
        def name(self) -> str:
            return "panicky"

        @property
        def description(self) -> str:
            return "raises a pyo3 panic"

        @property
        def input_model(self) -> type[_Input]:
            return _Input

        async def execute(self, ctx: ToolContext, args: _Input) -> str:
            raise PanicException("called `Result::unwrap()` on an `Err` value")

    registry = ToolRegistry()
    registry.register(_PanickyTool())
    result = await registry.dispatch("panicky", {}, ctx)
    assert result.ok is False
    assert "unwrap" in (result.error or "")
