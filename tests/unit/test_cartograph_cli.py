"""The cartograph CLI builds docs into the store without a model (#26b)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

_CLI = Path("scripts/cartograph.py")


def _load_cli():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("cartograph", _CLI)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_cli_writes_doc_without_model(tmp_path: Path, ecommerce_db: Path) -> None:
    cli = _load_cli()
    out = tmp_path / "labrat_maze" / "scent"
    args = SimpleNamespace(
        connections=json.dumps({"shop": {"db_type": "duckdb", "db_path": str(ecommerce_db)}}),
        primary=None,
        out=str(out),
        with_semantics=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        table_budget=40,
        distinct_cap=25,
    )
    rc = await cli._run(args)
    assert rc == 0
    written = out / "shop.md"
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "## Key Tables" in text
    assert "**Source:** verified" in text
