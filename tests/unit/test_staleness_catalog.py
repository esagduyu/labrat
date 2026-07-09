"""Catalog-level fingerprint + sidecar file for TUI first-connect staleness."""

from pathlib import Path

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.staleness import (
    fingerprint_from_catalog,
    read_scent_fingerprint,
    schema_fingerprint,
    write_scent_fingerprint,
)
from labrat.maze.store import user_scent_dir


def _catalog(cols: list[str]) -> Catalog:
    return Catalog(
        database_name="testdb",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[Column(name=c, data_type="VARCHAR", nullable=True) for c in cols],
                    ),
                ],
            )
        ],
    )


def test_fingerprint_from_catalog_matches_dict_form() -> None:
    cat = _catalog(["id", "amount"])
    assert fingerprint_from_catalog(cat) == schema_fingerprint({"orders": ["id", "amount"]})


def test_fingerprint_changes_when_schema_changes() -> None:
    assert fingerprint_from_catalog(_catalog(["id"])) != fingerprint_from_catalog(
        _catalog(["id", "new_col"])
    )


def test_sidecar_round_trip(tmp_path: Path) -> None:
    assert read_scent_fingerprint(tmp_path) is None
    write_scent_fingerprint(tmp_path, "abc123")
    assert read_scent_fingerprint(tmp_path) == "abc123"


def test_user_scent_dir_matches_mazestore_user_layer(tmp_path: Path) -> None:
    # MUST equal MazeStore's user layer + "scent" kind dir — the retrieval seam.
    assert user_scent_dir("prof1", home=tmp_path) == (
        tmp_path / ".labrat" / "maze" / "prof1" / "scent"
    )
