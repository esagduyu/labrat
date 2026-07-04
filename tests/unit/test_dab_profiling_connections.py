"""C1: build_profiling_connections profiles attached SQLite/Postgres for the cartographer."""

from __future__ import annotations

import logging
import sqlite3
from typing import cast

from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import AttachSpec, build_profiling_connections


def _sqlite_clinical(path: str) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE clinical_info(icd_o_3_histology TEXT, histological_type TEXT)")
    for code, name in [
        ("9400/3", "Astrocytoma"),
        ("9401/3", "Astrocytoma"),
        ("9450/3", "Oligodendroglioma"),
        ("9382/3", "Oligoastrocytoma"),
    ]:
        c.execute("INSERT INTO clinical_info VALUES (?,?)", (code, name))
    c.commit()
    c.close()


def test_profiles_attached_sqlite(tmp_path) -> None:
    spath = str(tmp_path / "sec.sqlite")
    _sqlite_clinical(spath)
    conns, cats = build_profiling_connections(
        [AttachSpec(alias="sec", path=spath, db_type="sqlite")]
    )
    try:
        assert set(conns) == {"sec"}
        cat = cast(Catalog, cats["sec"])
        names = [t.name for s in cat.schemas for t in s.tables]
        assert "clinical_info" in names
        # USE <alias> applied -> bare-name query resolves against the attached catalog
        conn = cast(DuckDBConnection, conns["sec"])
        assert conn.execute("SELECT COUNT(*) FROM clinical_info").item() == 4
    finally:
        for c in conns.values():
            cast(DuckDBConnection, c).disconnect()


def test_skips_bad_attach(tmp_path, caplog) -> None:
    spec = AttachSpec(alias="bad", path="/nonexistent_dir_xyz/nope.sqlite", db_type="sqlite")
    with caplog.at_level(logging.WARNING):
        conns, cats = build_profiling_connections([spec])
    assert conns == {}
    assert cats == {}
    assert any("bad" in r.getMessage() for r in caplog.records)  # warned, no exception
