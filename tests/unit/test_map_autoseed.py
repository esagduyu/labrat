"""dbt-structure auto-seed drafts kind=map skeletons per domain folder."""

import json
from pathlib import Path

from labrat.maze.map import draft_maps_from_dbt, scent_members


def _manifest(tmp_path: Path) -> Path:
    nodes = {
        "model.acme.mrr": {
            "resource_type": "model",
            "name": "mrr",
            "alias": "mrr",
            "fqn": ["acme", "marts", "finance", "mrr"],
        },
        "model.acme.invoices": {
            "resource_type": "model",
            "name": "invoices",
            "alias": "invoices",
            "fqn": ["acme", "marts", "finance", "invoices"],
        },
        "model.acme.events": {
            "resource_type": "model",
            "name": "events",
            "alias": "events",
            "fqn": ["acme", "marts", "product", "events"],
        },
        "model.acme.stg_x": {
            "resource_type": "model",
            "name": "stg_x",
            "alias": "stg_x",
            "fqn": ["acme", "staging", "stg_x"],
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    return p


def test_autoseed_groups_by_folder(tmp_path: Path) -> None:
    mp = _manifest(tmp_path)
    maps = draft_maps_from_dbt(
        mp,
        existing_scent_domains={"mrr", "invoices", "events"},
        generated_at="2026-07-10T00:00:00Z",
    )
    assert set(maps) == {"finance", "product"}  # staging excluded
    assert set(scent_members(maps["finance"])) == {"mrr", "invoices"}
    assert scent_members(maps["product"]) == ["events"]
    assert all(m.kind == "map" for m in maps.values())
    assert all(s.source == "draft" for m in maps.values() for s in m.sections)


def test_autoseed_only_points_at_existing_scent(tmp_path: Path) -> None:
    mp = _manifest(tmp_path)
    maps = draft_maps_from_dbt(
        mp,
        existing_scent_domains={"mrr"},  # invoices scent not generated yet
        generated_at="2026-07-10T00:00:00Z",
    )
    assert scent_members(maps["finance"]) == ["mrr"]  # invoices dropped (no scent)
    assert "product" not in maps  # events dropped -> group has zero members


def test_autoseed_skips_intermediate_and_base_folders(tmp_path: Path) -> None:
    nodes = {
        "model.acme.int_x": {
            "resource_type": "model",
            "name": "int_x",
            "alias": "int_x",
            "fqn": ["acme", "intermediate", "int_x"],
        },
        "model.acme.base_x": {
            "resource_type": "model",
            "name": "base_x",
            "alias": "base_x",
            "fqn": ["acme", "base", "base_x"],
        },
        "model.acme.int2_x": {
            "resource_type": "model",
            "name": "int2_x",
            "alias": "int2_x",
            "fqn": ["acme", "int", "int2_x"],
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    maps = draft_maps_from_dbt(
        p,
        existing_scent_domains={"int_x", "base_x", "int2_x"},
        generated_at="2026-07-10T00:00:00Z",
    )
    assert maps == {}


def test_autoseed_skips_non_model_resource_types(tmp_path: Path) -> None:
    nodes = {
        "seed.acme.raw_x": {
            "resource_type": "seed",
            "name": "raw_x",
            "alias": "raw_x",
            "fqn": ["acme", "marts", "finance", "raw_x"],
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    maps = draft_maps_from_dbt(
        p,
        existing_scent_domains={"raw_x"},
        generated_at="2026-07-10T00:00:00Z",
    )
    assert maps == {}


def test_autoseed_falls_back_to_name_when_no_alias(tmp_path: Path) -> None:
    nodes = {
        "model.acme.mrr": {
            "resource_type": "model",
            "name": "mrr",
            "fqn": ["acme", "marts", "finance", "mrr"],
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    maps = draft_maps_from_dbt(
        p,
        existing_scent_domains={"mrr"},
        generated_at="2026-07-10T00:00:00Z",
    )
    assert scent_members(maps["finance"]) == ["mrr"]


def test_autoseed_skips_nodes_with_too_few_fqn_segments(tmp_path: Path) -> None:
    nodes = {
        "model.acme.root_model": {
            "resource_type": "model",
            "name": "root_model",
            "alias": "root_model",
            "fqn": ["root_model"],
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    maps = draft_maps_from_dbt(
        p,
        existing_scent_domains={"root_model"},
        generated_at="2026-07-10T00:00:00Z",
    )
    assert maps == {}
