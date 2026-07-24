import json
from pathlib import Path
from typing import Any

from labrat.mcp.server import _build_context_from_env, _log_tool_call


def test_build_context_from_env_allows_in_memory_primary(monkeypatch: Any) -> None:
    """A :memory: primary (the DAB federation workspace) must come up writable —
    DuckDB cannot even open :memory: read-only, and the agent ATTACHes / loads
    Mongo into it. Regression for the sandbox smoke that exposed this."""
    monkeypatch.setenv(
        "LABRAT_MCP_CONNECTIONS", '{"__federation": {"db_type": "duckdb", "db_path": ":memory:"}}'
    )
    monkeypatch.setenv("LABRAT_MCP_PRIMARY", "__federation")
    ctx, live = _build_context_from_env()
    try:
        assert ctx.primary == "__federation"
        # Writable check: CREATE would raise on a read-only connection.
        ctx.connections["__federation"]._conn.execute("CREATE TABLE t(x INTEGER)")  # type: ignore[attr-defined]
    finally:
        for c in live:
            c.disconnect()


def test_log_tool_call_writes_jsonl_line(tmp_path: Path) -> None:
    _log_tool_call(
        str(tmp_path),
        name="run_sql",
        arguments={"sql": "SELECT 1"},
        ok=True,
        output="[[1]]",
        latency_ms=12.5,
    )
    log_file = tmp_path / "mcp_tool_calls.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "run_sql"
    assert rec["input"] == {"sql": "SELECT 1"}
    assert rec["ok"] is True
    assert rec["output"] == "[[1]]"
    assert rec["latency_ms"] == 12.5


def test_log_tool_call_appends(tmp_path: Path) -> None:
    for i in range(3):
        _log_tool_call(
            str(tmp_path), name="list_tables", arguments={}, ok=True, output=str(i), latency_ms=1.0
        )
    lines = (tmp_path / "mcp_tool_calls.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_log_tool_call_noop_without_dir(tmp_path: Path) -> None:
    # No directory configured → silent no-op, no file, no exception.
    _log_tool_call(None, name="run_sql", arguments={}, ok=True, output="x", latency_ms=1.0)
    assert list(tmp_path.iterdir()) == []


def test_build_context_profiles_path(tmp_path: Path, monkeypatch: Any) -> None:
    import duckdb

    from labrat.profile.manager import ProfileManager, make_profile

    db = tmp_path / "s.duckdb"
    duckdb.connect(str(db)).close()
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    mgr.add(make_profile(name="served", dialect="duckdb", path=str(db)))
    # Route the module-level default manager at the config seam:
    import labrat.mcp.config as mcp_config

    monkeypatch.setattr(mcp_config, "ProfileManager", lambda: mgr)
    monkeypatch.setenv("LABRAT_MCP_PROFILES", "served")
    monkeypatch.delenv("LABRAT_MCP_CONNECTIONS", raising=False)

    from labrat.mcp.server import _build_context_from_env

    ctx, live = _build_context_from_env()
    assert ctx.profile_name == "served" and ctx.read_only is True
    assert set(ctx.connections) == {"served"} and len(live) == 1
    for conn in live:
        conn.disconnect()


def test_build_context_reads_llm_classify_backend_from_env(monkeypatch: Any) -> None:
    """The claude-mcp path must be able to route llm_classify to local-embed —
    the only classification backend that runs without an llm_fn over MCP."""
    monkeypatch.setenv(
        "LABRAT_MCP_CONNECTIONS", '{"__federation": {"db_type": "duckdb", "db_path": ":memory:"}}'
    )
    monkeypatch.setenv("LABRAT_MCP_PRIMARY", "__federation")
    monkeypatch.setenv("LABRAT_MCP_LLM_CLASSIFY_BACKEND", "local-embed")
    ctx, live = _build_context_from_env()
    try:
        assert ctx.llm_classify_backend == "local-embed"
    finally:
        for c in live:
            c.disconnect()


def test_build_context_llm_classify_backend_defaults_to_llm(monkeypatch: Any) -> None:
    """Unset env → unchanged default 'llm' (byte-identical to prior behavior)."""
    monkeypatch.setenv(
        "LABRAT_MCP_CONNECTIONS", '{"__federation": {"db_type": "duckdb", "db_path": ":memory:"}}'
    )
    monkeypatch.setenv("LABRAT_MCP_PRIMARY", "__federation")
    monkeypatch.delenv("LABRAT_MCP_LLM_CLASSIFY_BACKEND", raising=False)
    ctx, live = _build_context_from_env()
    try:
        assert ctx.llm_classify_backend == "llm"
    finally:
        for c in live:
            c.disconnect()
