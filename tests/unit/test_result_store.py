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


def test_put_json_roundtrips_object(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    obj = {"database": "main", "tables": [{"name": "t", "row_count": 42}]}
    ref = store.put_json(obj)
    assert store.get(ref) == obj


def test_put_json_trace_roundtrips_as_jsonl(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    items = [{"step": 1, "tool": "run_sql"}, {"step": 2, "tool": "sample_rows"}]
    ref = store.put_json(items, kind="trace")
    assert store.get(ref) == items
    # trace files are JSONL on disk (one JSON object per line)
    jsonl_files = list(store.directory.glob("*.trace.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_put_json_trace_requires_list(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    with pytest.raises(TypeError, match="trace payload must be a list"):
        store.put_json({"not": "a list"}, kind="trace")


def test_mixed_kinds_resolve_independently(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    table_ref = store.put_table(df)
    json_ref = store.put_json({"k": "v"})
    trace_ref = store.put_json([{"i": 1}], kind="trace")
    assert isinstance(store.get(table_ref), pl.DataFrame)
    assert store.get(json_ref) == {"k": "v"}
    assert store.get(trace_ref) == [{"i": 1}]
    assert store.meta(json_ref) is None  # meta sidecar is table-only


def test_cap_bytes_strict_and_multibyte_safe() -> None:
    from labrat.results.store import cap_bytes

    assert cap_bytes("short", 100) == "short"
    capped = cap_bytes("é" * 100, 15)  # "é" is 2 bytes in UTF-8
    assert len(capped.encode("utf-8")) <= 15
    assert "�" not in capped  # no replacement chars from a split code point


def test_render_table_head_tsv() -> None:
    from labrat.results.store import render_table_head

    frame = pl.DataFrame({"a": [1, 2, 3], "b": ["x", None, "z"]})
    rendered = render_table_head(frame, 2)
    assert rendered.splitlines() == ["a\tb", "1\tx", "2\t"]


def test_preview_table_respects_row_and_byte_caps(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    big = pl.DataFrame({"n": list(range(1000)), "s": ["value"] * 1000})
    ref = store.put_table(big)

    by_rows = store.preview(ref, max_rows=5, max_bytes=100_000)
    assert len(by_rows.splitlines()) == 6  # header + 5 rows

    by_bytes = store.preview(ref, max_rows=1000, max_bytes=64)
    assert len(by_bytes.encode("utf-8")) <= 64


def test_preview_json_and_trace_respect_caps(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    json_ref = store.put_json({"blob": "y" * 500})
    assert len(store.preview(json_ref, max_rows=50, max_bytes=64).encode("utf-8")) <= 64

    trace_ref = store.put_json([{"i": i} for i in range(20)], kind="trace")
    trace_preview = store.preview(trace_ref, max_rows=3, max_bytes=100_000)
    assert len(trace_preview.splitlines()) == 3
    assert len(store.preview(trace_ref, max_rows=20, max_bytes=32).encode("utf-8")) <= 32


def test_session_path_traversal_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid session"):
        ResultStore(tmp_path, session="../evil")
    with pytest.raises(ValueError, match="invalid session"):
        ResultStore(tmp_path, session="foo/../../evil")
    with pytest.raises(ValueError, match="invalid session"):
        ResultStore(tmp_path, session="a/b")
    with pytest.raises(ValueError, match="invalid session"):
        ResultStore(tmp_path, session="a\\b")
    # session=None (auto-generated) still works
    store = ResultStore(tmp_path)
    assert store.directory == tmp_path / store.session


def test_construction_does_not_create_session_dir(tmp_path: Path) -> None:
    store = ResultStore(tmp_path, session="lazy")
    assert store.directory == tmp_path / "lazy"
    assert not store.directory.exists()  # lazy: no mkdir until first put_*


def test_first_put_table_creates_session_dir(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path, session="lazy2")
    assert not store.directory.exists()
    ref = store.put_table(df)
    assert store.directory.is_dir()
    assert store.get(ref).equals(df)


def test_first_put_json_creates_session_dir(tmp_path: Path) -> None:
    store = ResultStore(tmp_path, session="lazy3")
    assert not store.directory.exists()
    ref = store.put_json({"k": "v"})
    assert store.directory.is_dir()
    assert store.get(ref) == {"k": "v"}


def test_put_table_meta_builtin_fields_win_over_caller_meta(
    tmp_path: Path, df: pl.DataFrame
) -> None:
    store = ResultStore(tmp_path)
    ref = store.put_table(df, meta={"row_count": 999, "source": "x"})
    meta = store.meta(ref)
    assert meta is not None
    assert meta["row_count"] == df.height  # built-in wins over caller collision
    assert meta["source"] == "x"  # non-colliding caller key preserved
