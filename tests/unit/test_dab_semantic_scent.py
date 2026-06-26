"""DAB LLM-semantic Scent wiring (FEATURE: T1c)."""

from __future__ import annotations

from pathlib import Path

import labrat.eval.benchmarks.dab.suite as suite_mod
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import DabSuite, _run_cartographer


def _env(ecommerce_db: Path) -> DabTaskEnv:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    from labrat.agent.tools.base import ToolContext

    return DabTaskEnv(
        ctx=ToolContext(
            connections={"shop": conn},
            catalogs={"shop": Catalog(database_name="shop", schemas=[])},
            primary="shop",
        ),
        attachable=[],
    )


async def test_run_cartographer_threads_semantics(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def _cap(
        connections, catalogs, primary, scent_dir, *, with_semantics=False, llm_fn=None, **kw
    ):
        captured["with_semantics"] = with_semantics
        captured["has_llm"] = llm_fn is not None
        return []

    monkeypatch.setattr(suite_mod, "cartograph_prepass", _cap)

    async def _stub(prompt: str) -> str:
        return "## Gotchas\n- x"

    await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, with_semantics=True, llm_fn=_stub)
    assert captured["with_semantics"] is True
    assert captured["has_llm"] is True


def test_cartograph_llm_fn_routes_claude_code_on_mcp(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_build(name, model, *a, **k):
        captured["name"] = name
        captured["model"] = model

        class _P: ...

        return _P()

    monkeypatch.setattr(suite_mod, "build_provider", _fake_build, raising=False)
    monkeypatch.setattr(suite_mod, "provider_llm_fn", lambda p: lambda x: x, raising=False)

    suite = DabSuite(
        driver="claude-mcp",
        cartograph=True,
        cartograph_semantics=True,
        cartograph_semantics_model="claude-sonnet-4-6",
    )
    suite._cartograph_llm_fn()
    assert captured["name"] == "claude-code"  # Max-plan auth on the claude-mcp path
    assert captured["model"] == "claude-sonnet-4-6"


def test_cartograph_llm_fn_honors_provider_off_mcp(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_build(name, model, *a, **k):
        captured["name"] = name

        class _P: ...

        return _P()

    monkeypatch.setattr(suite_mod, "build_provider", _fake_build, raising=False)
    monkeypatch.setattr(suite_mod, "provider_llm_fn", lambda p: lambda x: x, raising=False)

    suite = DabSuite(
        driver="labrat-agent",
        cartograph=True,
        cartograph_semantics=True,
        cartograph_semantics_provider="anthropic",
    )
    suite._cartograph_llm_fn()
    assert captured["name"] == "anthropic"  # non-mcp path honors the configured provider


def test_eval_dab_threads_semantic_flags(monkeypatch, tmp_path: Path) -> None:
    import scripts.eval_dab as ed

    captured: dict[str, object] = {}

    class _FakeSuite:
        name = "dab"

        def __init__(self, **kw: object) -> None:
            captured.update(kw)

        def tasks(self):
            return []

        def write_submission(self, report, output_dir):
            pass

    async def _fake_interim(suite, n_trials, output_dir, task_filter):
        from labrat.eval.types import AggregateScore, BenchmarkReport

        return BenchmarkReport(
            benchmark="dab",
            run_id="test",
            score=AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0),
            trials=[],
            config={},
        )

    monkeypatch.setattr(ed, "DabSuite", _FakeSuite)
    monkeypatch.setattr(ed, "_run_interim", _fake_interim)
    ed.main(
        [
            "--driver",
            "claude-mcp",
            "--agent-cartograph",
            "--cartograph-semantics",
            "--cartograph-semantics-model",
            "claude-sonnet-4-6",
            "--cartograph-semantics-provider",
            "anthropic",
            "--datasets",
            "deps_dev_v1",
            "--output-dir",
            str(tmp_path / "r"),
        ]
    )
    assert captured.get("cartograph_semantics") is True
    assert captured.get("cartograph_semantics_model") == "claude-sonnet-4-6"
    assert captured.get("cartograph_semantics_provider") == "anthropic"


def test_semantics_without_cartograph_errors(monkeypatch, tmp_path: Path) -> None:
    import pytest

    import scripts.eval_dab as ed

    monkeypatch.setattr(ed, "DabSuite", object)  # must never be constructed
    with pytest.raises(SystemExit):
        ed.main(
            [
                "--driver",
                "claude-mcp",
                "--cartograph-semantics",  # no --agent-cartograph
                "--datasets",
                "deps_dev_v1",
                "--output-dir",
                str(tmp_path / "r"),
            ]
        )


def test_scent_dir_inherited_on_bare_resume(monkeypatch, tmp_path: Path) -> None:
    """A bare resume (no --cartograph-scent-dir) inherits the frozen dir from config.json."""
    import json

    import scripts.eval_dab as ed

    captured: dict[str, object] = {}

    class _FakeSuite:
        name = "dab"

        def __init__(self, **kw: object) -> None:
            captured.update(kw)

        def tasks(self):
            return []

        def write_submission(self, report, output_dir):
            pass

    async def _fake_interim(suite, n_trials, output_dir, task_filter):
        from labrat.eval.types import AggregateScore, BenchmarkReport

        return BenchmarkReport(
            benchmark="dab",
            run_id="test",
            score=AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0),
            trials=[],
            config={},
        )

    monkeypatch.setattr(ed, "DabSuite", _FakeSuite)
    monkeypatch.setattr(ed, "_run_interim", _fake_interim)

    frozen = tmp_path / "frozen"
    output_dir = tmp_path / "r"
    output_dir.mkdir(parents=True)
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "driver": "claude-mcp",
                "agent_cartograph": True,
                "cartograph_semantics": True,
                "cartograph_scent_dir": str(frozen),
            }
        )
    )

    ed.main(["--output-dir", str(output_dir)])  # no --cartograph-scent-dir
    assert captured.get("cartograph_scent_root") == frozen
