"""ResultStore: addressable on-disk artifacts (tables→Parquet+meta, json, traces)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from labrat.results.store import ResultStore


@pytest.fixture()
def df() -> pl.DataFrame:
    return pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})


def test_put_table_roundtrips_dataframe(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    ref = store.put_table(df)
    assert ref.startswith("result://")
    out = store.get(ref)
    assert isinstance(out, pl.DataFrame)
    assert out.equals(df)


def test_put_table_writes_meta_sidecar(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    ref = store.put_table(df, meta={"tool": "run_sql"})
    meta = store.meta(ref)
    assert meta is not None
    assert meta["columns"] == ["id", "name"]
    assert meta["row_count"] == 3
    assert meta["tool"] == "run_sql"
    assert len(meta["dtypes"]) == 2


def test_refs_are_sequential_and_session_scoped(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path, session="sess1")
    ref_a = store.put_table(df)
    ref_b = store.put_table(df)
    assert ref_a == "result://sess1/0000"
    assert ref_b == "result://sess1/0001"
    assert store.session == "sess1"
    assert store.directory == tmp_path / "sess1"
    assert store.directory.is_dir()


def test_unknown_ref_raises_value_error(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path, session="sess1")
    store.put_table(df)
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("result://sess1/0099")
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("result://other/0000")
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("garbage")
