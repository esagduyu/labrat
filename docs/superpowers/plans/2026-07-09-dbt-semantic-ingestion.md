# T1b — dbt Semantic-Layer Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest dbt `semantic_models`/`metrics` from `manifest.json` into `semantic_layer`-tagged, `schema_hash`-stamped project-layer Scent docs (first-connect + drift-offer), stamp `schema_hash` on Cartographer sections, and thereby activate the read-model-v2 freshness path end to end.

**Architecture:** Pure parser (`catalog/dbt/semantic.py`) → pure section builder + manifest-fingerprint helpers + write controller (`maze/semantic_ingest.py`, replace-semantics + audit-fail-loud + `.manifest_fingerprint` sidecar) → config plumbing (`Profile.dbt_project_path`, onboarding persistence, Settings row) → `MainScreen` worker after the M2 scent worker → Cartographer stamps `schema_hash` via one `model_copy` loop in `generate_scent` (builders untouched).

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`catalog/`, `maze/` strict; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-09-dbt-semantic-ingestion-design.md` — read before starting.

## Global Constraints

- Branch: `feat/dbt-semantic-ingestion` off master.
- Every ingested section passes `audit_scent_doc` fail-loud BEFORE write; never catch-and-continue around the audit.
- Re-ingest replaces ONLY `source=="semantic_layer"` sections; all other sections preserved byte-for-byte (test-pinned).
- Deterministic: no LLM, no clock anywhere in this plan; identical manifest+catalog → identical bytes. `generated_at` is NEVER stamped.
- Benchmark isolation: ingestion reachable only via `Profile.dbt_project_path` (TUI) or explicit controller call; nothing under `eval/`/`mcp/` touches these seams.
- Fail-open UI (worker try/except → warning toast), fail-loud writes.
- `Profile` change legacy-safe (defaulted `None`); `make_profile`/onboarding wiring must not alter existing call sites' behavior.
- The Meta-render invariant holds: sections with NO meta fields render NO `**Meta:**` line (`tests/unit/test_maze_document.py:106` must keep passing).
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known local env flake: `tests/tui/test_app_renders.py::test_app_renders` (fails on unmodified master, CI-skipped) — never a regression signal; restore `snapshot_report.html` via `git checkout` if regenerated.

---

## File Structure

- Create: `src/labrat/catalog/dbt/semantic.py`, `src/labrat/maze/semantic_ingest.py`, `tests/fixtures/sample_dbt_project/manifest_semantic.json`.
- Modify: `src/labrat/profile/model.py`, `src/labrat/profile/manager.py` (`make_profile`), `src/labrat/app.py` (`_save_onboarding_result`), `src/labrat/screens/settings.py`, `src/labrat/screens/main.py`, `src/labrat/maze/cartographer.py` (`generate_scent` only), `src/labrat/screens/help.py`, `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_dbt_semantic_parser.py`, `tests/unit/test_semantic_ingest.py`, `tests/unit/test_profile_dbt_path.py`, `tests/tui/test_settings_screen.py` (extend), `tests/tui/test_main_screen_semantic.py`, `tests/unit/test_cartographer_stamping.py`, `tests/unit/test_semantic_footer_e2e.py`.

---

### Task 1: Fixture + parser (`catalog/dbt/semantic.py`)

**Files:**
- Create: `tests/fixtures/sample_dbt_project/manifest_semantic.json`, `src/labrat/catalog/dbt/semantic.py`
- Test: `tests/unit/test_dbt_semantic_parser.py`

**Interfaces:**
- Produces (Tasks 2–3 consume): `EntityDef(name, type)`, `DimensionDef(name, type, description)`, `MeasureDef(name, agg, description, expr)`, `SemanticModelDef(name, table, description, entities, dimensions, measures)`, `MetricDef(name, type, label, description, measure_refs)`, `SemanticArtifacts(models: list[SemanticModelDef], metrics: list[MetricDef], warnings: list[str])`, `parse_semantic_manifest(manifest: dict[str, Any]) -> SemanticArtifacts`.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/sample_dbt_project/manifest_semantic.json` — match the existing fixture's metadata conventions (`dbt_version: "1.8.0"`, manifest v11 schema URL, `generated_at: "2024-01-01T00:00:00.000000Z"`, `invocation_id: "test-invocation-id"`), with top-level `nodes: {}`, `sources: {}` plus:

```json
{
  "metadata": {
    "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v11/manifest.json",
    "dbt_version": "1.8.0",
    "generated_at": "2024-01-01T00:00:00.000000Z",
    "invocation_id": "test-invocation-id",
    "env": {}
  },
  "nodes": {},
  "sources": {},
  "semantic_models": {
    "semantic_model.jaffle.orders": {
      "name": "orders",
      "description": "One row per order.",
      "node_relation": {"alias": "orders", "schema_name": "main", "relation_name": "\"dev\".\"main\".\"orders\""},
      "entities": [{"name": "order_id", "type": "primary"}, {"name": "customer_id", "type": "foreign"}],
      "dimensions": [{"name": "status", "type": "categorical", "description": "Order lifecycle state."},
                      {"name": "created_at", "type": "time", "description": ""}],
      "measures": [{"name": "order_total", "agg": "sum", "description": "Order revenue.", "expr": "total_amount"},
                    {"name": "order_count", "agg": "count", "description": "", "expr": "1"}]
    },
    "semantic_model.jaffle.customers": {
      "name": "customers",
      "description": "One row per customer.",
      "node_relation": {"alias": "customers", "schema_name": "main", "relation_name": "\"dev\".\"main\".\"customers\""},
      "entities": [{"name": "customer_id", "type": "primary"}],
      "dimensions": [{"name": "region", "type": "categorical", "description": "Sales region."}],
      "measures": [{"name": "customer_count", "agg": "count_distinct", "description": "", "expr": "customer_id"}]
    },
    "semantic_model.jaffle.broken": {"name": "broken"}
  },
  "metrics": {
    "metric.jaffle.revenue": {
      "name": "revenue", "type": "simple", "label": "Revenue",
      "description": "Total completed order revenue.",
      "type_params": {"measure": {"name": "order_total"}}
    },
    "metric.jaffle.revenue_per_customer": {
      "name": "revenue_per_customer", "type": "ratio", "label": "Revenue per Customer",
      "description": "Revenue divided by distinct customers.",
      "type_params": {"numerator": {"name": "revenue"}, "denominator": {"name": "customer_count"}}
    },
    "metric.jaffle.broken_metric": {"name": "broken_metric"}
  }
}
```

(The `broken` entries exercise the warnings path: `semantic_model.jaffle.broken` has no `node_relation`/lists; `broken_metric` has no `type`/`type_params`.)

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_dbt_semantic_parser.py
"""parse_semantic_manifest: tolerant extraction of semantic_models + metrics."""

import json
from pathlib import Path

from labrat.catalog.dbt.semantic import parse_semantic_manifest

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _manifest() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_parses_models_and_metrics() -> None:
    art = parse_semantic_manifest(_manifest())
    names = {m.name for m in art.models}
    assert {"orders", "customers"} <= names
    orders = next(m for m in art.models if m.name == "orders")
    assert orders.table == "orders"
    assert [e.name for e in orders.entities] == ["order_id", "customer_id"]
    assert [d.name for d in orders.dimensions] == ["status", "created_at"]
    assert [me.name for me in orders.measures] == ["order_total", "order_count"]
    assert orders.measures[0].agg == "sum" and orders.measures[0].expr == "total_amount"
    metrics = {m.name: m for m in art.metrics}
    assert metrics["revenue"].type == "simple"
    assert metrics["revenue"].measure_refs == ["order_total"]
    assert metrics["revenue_per_customer"].type == "ratio"
    assert set(metrics["revenue_per_customer"].measure_refs) == {"revenue", "customer_count"}


def test_malformed_entries_become_warnings_not_errors() -> None:
    art = parse_semantic_manifest(_manifest())
    # "broken" model lacks node_relation → skipped-with-warning OR parsed with
    # name-fallback table; either way NO exception and a warning mentioning it.
    assert any("broken" in w for w in art.warnings)


def test_missing_keys_yield_empty_artifacts() -> None:
    art = parse_semantic_manifest({"metadata": {}, "nodes": {}})
    assert art.models == [] and art.metrics == [] and art.warnings == []


def test_never_raises_on_garbage_shapes() -> None:
    art = parse_semantic_manifest(
        {"semantic_models": {"x": None, "y": 3, "z": {"entities": "nope"}},
         "metrics": {"a": [], "b": {"type_params": 7}}}
    )
    assert isinstance(art.warnings, list) and len(art.warnings) >= 3
```

- [ ] **Step 3: Run tests to verify they fail** — `uv run pytest tests/unit/test_dbt_semantic_parser.py -v` → `ModuleNotFoundError: labrat.catalog.dbt.semantic`.

- [ ] **Step 4: Implement**

```python
# src/labrat/catalog/dbt/semantic.py
"""Parse dbt semantic-layer artifacts (manifest.json semantic_models + metrics).

Pure + tolerant: malformed entries are skipped with a human-readable warning,
never an exception (T1b spec 3.1). dbt >= 1.6 compiles semantic models and
metrics into manifest.json top-level keys — NOT dbt_project.yml.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EntityDef(BaseModel):
    name: str
    type: str = ""


class DimensionDef(BaseModel):
    name: str
    type: str = ""
    description: str = ""


class MeasureDef(BaseModel):
    name: str
    agg: str = ""
    description: str = ""
    expr: str = ""


class SemanticModelDef(BaseModel):
    name: str
    table: str
    description: str = ""
    entities: list[EntityDef] = []
    dimensions: list[DimensionDef] = []
    measures: list[MeasureDef] = []


class MetricDef(BaseModel):
    name: str
    type: str  # normalized: simple | ratio | derived | other
    label: str = ""
    description: str = ""
    measure_refs: list[str] = []


class SemanticArtifacts(BaseModel):
    models: list[SemanticModelDef] = []
    metrics: list[MetricDef] = []
    warnings: list[str] = []


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else ""


def _sub_defs(d: dict[str, Any], key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    raw = d.get(key)
    if not isinstance(raw, list):
        return out
    for item in raw:
        entry = _as_dict(item)
        if entry is None or not isinstance(entry.get("name"), str):
            continue
        out.append({f: _str(entry, f) for f in fields})
    return out


def _table_for(entry: dict[str, Any], name: str) -> str:
    rel = _as_dict(entry.get("node_relation")) or {}
    alias = rel.get("alias")
    if isinstance(alias, str) and alias:
        return alias
    relname = rel.get("relation_name")
    if isinstance(relname, str) and relname:
        return relname.replace('"', "").split(".")[-1]
    return name


def _measure_refs(entry: dict[str, Any]) -> list[str]:
    params = _as_dict(entry.get("type_params")) or {}
    refs: list[str] = []
    for key in ("measure", "numerator", "denominator"):
        ref = _as_dict(params.get(key))
        if ref and isinstance(ref.get("name"), str):
            refs.append(ref["name"])
    inputs = params.get("metrics")
    if isinstance(inputs, list):
        for item in inputs:
            entry_d = _as_dict(item)
            if entry_d and isinstance(entry_d.get("name"), str):
                refs.append(entry_d["name"])
            elif isinstance(item, str):
                refs.append(item)
    return refs


_KNOWN_TYPES = {"simple", "ratio", "derived"}


def parse_semantic_manifest(manifest: dict[str, Any]) -> SemanticArtifacts:
    """Extract semantic models + metrics; skip malformed entries with warnings."""
    art = SemanticArtifacts()
    for uid, raw in (_as_dict(manifest.get("semantic_models")) or {}).items():
        entry = _as_dict(raw)
        if entry is None or not isinstance(entry.get("name"), str):
            art.warnings.append(f"skipped malformed semantic model {uid!r}")
            continue
        name = entry["name"]
        if _as_dict(entry.get("node_relation")) is None:
            art.warnings.append(f"semantic model {name!r} ({uid}) has no node_relation")
        art.models.append(
            SemanticModelDef(
                name=name,
                table=_table_for(entry, name),
                description=_str(entry, "description"),
                entities=[EntityDef(**e) for e in _sub_defs(entry, "entities", ("name", "type"))],
                dimensions=[
                    DimensionDef(**d)
                    for d in _sub_defs(entry, "dimensions", ("name", "type", "description"))
                ],
                measures=[
                    MeasureDef(**m)
                    for m in _sub_defs(entry, "measures", ("name", "agg", "description", "expr"))
                ],
            )
        )
    for uid, raw in (_as_dict(manifest.get("metrics")) or {}).items():
        entry = _as_dict(raw)
        if entry is None or not isinstance(entry.get("name"), str):
            art.warnings.append(f"skipped malformed metric {uid!r}")
            continue
        mtype = _str(entry, "type").lower()
        if mtype not in _KNOWN_TYPES:
            if not mtype:
                art.warnings.append(f"metric {entry['name']!r} ({uid}) has no type")
            mtype = mtype if mtype in _KNOWN_TYPES else "other"
        art.metrics.append(
            MetricDef(
                name=entry["name"],
                type=mtype,
                label=_str(entry, "label"),
                description=_str(entry, "description"),
                measure_refs=_measure_refs(entry),
            )
        )
    art.models.sort(key=lambda m: m.name)
    art.metrics.sort(key=lambda m: m.name)
    return art
```

Note the ordering: models/metrics sorted by name at the end (spec 3.2 determinism). Adjust the `test_never_raises_on_garbage_shapes` expected warning count against the real behavior if your implementation legitimately produces a different ≥3 count — the contract is "no exception + one warning per skipped entry."

- [ ] **Step 5: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_dbt_semantic_parser.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/catalog/dbt/semantic.py tests/unit/test_dbt_semantic_parser.py tests/fixtures/sample_dbt_project/manifest_semantic.json
git commit -m "feat(catalog): parse_semantic_manifest — tolerant dbt semantic_models/metrics parser"
```

---

### Task 2: Section builder + manifest fingerprint (`maze/semantic_ingest.py`, pure half)

**Files:**
- Create: `src/labrat/maze/semantic_ingest.py`
- Test: `tests/unit/test_semantic_ingest.py` (builder + fingerprint parts)

**Interfaces:**
- Consumes: Task 1 models; `Section` (`labrat.maze.document`).
- Produces (Task 3 consumes): `build_semantic_sections(artifacts: SemanticArtifacts, schema_hash: str | None) -> dict[str, list[Section]]`; `semantic_fingerprint(manifest: dict[str, Any]) -> str`; `read_manifest_fingerprint(scent_dir: Path) -> str | None`; `write_manifest_fingerprint(scent_dir: Path, fingerprint: str) -> None` (sidecar file `.manifest_fingerprint`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_semantic_ingest.py
"""build_semantic_sections routing/determinism + manifest fingerprint sidecar."""

import json
from pathlib import Path

from labrat.catalog.dbt.semantic import parse_semantic_manifest
from labrat.maze.semantic_ingest import (
    build_semantic_sections,
    read_manifest_fingerprint,
    semantic_fingerprint,
    write_manifest_fingerprint,
)

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _artifacts():
    return parse_semantic_manifest(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def test_models_route_to_table_domains_with_stamp() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash="fp123")
    assert {"orders", "customers"} <= set(drafts)
    orders_secs = drafts["orders"]
    model_sec = next(s for s in orders_secs if s.heading == "Semantic Model: orders")
    assert model_sec.source == "semantic_layer"
    assert model_sec.schema_hash == "fp123"
    assert model_sec.generated_at is None                      # no clock, ever
    assert "order_total" in model_sec.body and "sum" in model_sec.body


def test_simple_metric_routes_to_owner_domain() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash=None)
    headings = [s.heading for s in drafts["orders"]]
    assert "Metric: Revenue" in headings                        # owner of order_total
    revenue = next(s for s in drafts["orders"] if s.heading == "Metric: Revenue")
    assert revenue.schema_hash is None                          # honest unknown


def test_ratio_metric_routes_to_metrics_domain() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash=None)
    assert "metrics" in drafts
    assert any(s.heading == "Metric: Revenue per Customer" for s in drafts["metrics"])


def test_deterministic_bytes() -> None:
    a = build_semantic_sections(_artifacts(), schema_hash="fp")
    b = build_semantic_sections(_artifacts(), schema_hash="fp")
    assert {k: [s.model_dump() for s in v] for k, v in a.items()} == {
        k: [s.model_dump() for s in v] for k, v in b.items()
    }


def test_manifest_fingerprint_tracks_semantic_subset_only() -> None:
    manifest = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    base = semantic_fingerprint(manifest)
    manifest["nodes"] = {"model.x": {"anything": 1}}            # model churn: no drift
    assert semantic_fingerprint(manifest) == base
    manifest["metrics"]["metric.jaffle.revenue"]["description"] = "changed"
    assert semantic_fingerprint(manifest) != base               # semantic change: drift


def test_sidecar_round_trip(tmp_path: Path) -> None:
    assert read_manifest_fingerprint(tmp_path) is None
    write_manifest_fingerprint(tmp_path, "abc")
    assert read_manifest_fingerprint(tmp_path) == "abc"
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/unit/test_semantic_ingest.py -v` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement** (module docstring: "dbt semantic-layer → Scent ingestion (T1b). Pure builder + fingerprint here; the write controller (Task 3) lives below them in this same module.")

```python
# src/labrat/maze/semantic_ingest.py  (pure half)
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from labrat.catalog.dbt.semantic import MetricDef, SemanticArtifacts, SemanticModelDef
from labrat.maze.document import Section

_MANIFEST_FINGERPRINT_FILE = ".manifest_fingerprint"
_METRICS_DOMAIN = "metrics"


def _model_body(m: SemanticModelDef) -> str:
    lines: list[str] = []
    if m.description:
        lines.append(m.description)
    for e in m.entities:
        lines.append(f"- entity `{e.name}` ({e.type})" if e.type else f"- entity `{e.name}`")
    for d in m.dimensions:
        desc = f" — {d.description}" if d.description else ""
        lines.append(f"- dimension `{d.name}` ({d.type}){desc}")
    for me in m.measures:
        expr = f" = `{me.expr}`" if me.expr else ""
        desc = f" — {me.description}" if me.description else ""
        lines.append(f"- measure `{me.name}` ({me.agg}){expr}{desc}")
    return "\n".join(lines)


def _metric_body(mt: MetricDef, owners: dict[str, str]) -> str:
    lines: list[str] = []
    if mt.description:
        lines.append(mt.description)
    lines.append(f"- type: {mt.type}")
    for ref in mt.measure_refs:
        owner = owners.get(ref)
        lines.append(f"- uses `{ref}`" + (f" (from `{owner}`)" if owner else ""))
    return "\n".join(lines)


def build_semantic_sections(
    artifacts: SemanticArtifacts, schema_hash: str | None
) -> dict[str, list[Section]]:
    """Route semantic models to their table domains; metrics to owner-or-'metrics'."""
    owners: dict[str, str] = {}  # measure name -> semantic model TABLE (domain)
    for m in artifacts.models:
        for me in m.measures:
            owners.setdefault(me.name, m.table)

    out: dict[str, list[Section]] = {}

    def _add(domain: str, heading: str, body: str) -> None:
        out.setdefault(domain, []).append(
            Section(
                heading=heading, body=body, source="semantic_layer", schema_hash=schema_hash
            )
        )

    for m in artifacts.models:  # already name-sorted by the parser
        _add(m.table, f"Semantic Model: {m.name}", _model_body(m))
    for mt in artifacts.metrics:
        title = mt.label or mt.name
        if mt.type == "simple" and mt.measure_refs and mt.measure_refs[0] in owners:
            domain = owners[mt.measure_refs[0]]
        else:
            domain = _METRICS_DOMAIN
        _add(domain, f"Metric: {title}", _metric_body(mt, owners))
    return out


def semantic_fingerprint(manifest: dict[str, Any]) -> str:
    """sha256 over ONLY the semantic subset — model-body churn must not signal drift."""
    subset = {
        "semantic_models": manifest.get("semantic_models") or {},
        "metrics": manifest.get("metrics") or {},
    }
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_manifest_fingerprint(scent_dir: Path) -> str | None:
    path = scent_dir / _MANIFEST_FINGERPRINT_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_manifest_fingerprint(scent_dir: Path, fingerprint: str) -> None:
    scent_dir.mkdir(parents=True, exist_ok=True)
    (scent_dir / _MANIFEST_FINGERPRINT_FILE).write_text(fingerprint + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_semantic_ingest.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/semantic_ingest.py tests/unit/test_semantic_ingest.py
git commit -m "feat(maze): semantic section builder + manifest fingerprint sidecar"
```

---

### Task 3: Ingestion controller (`ingest_dbt_semantics`)

**Files:**
- Modify: `src/labrat/maze/semantic_ingest.py`
- Test: `tests/unit/test_semantic_ingest.py` (extend)

**Interfaces:**
- Consumes: Tasks 1–2; `MazeStore.load_domain(domain, scope="project")`/`write_doc` (RMv2); `audit_scent_doc`/`ScentContaminationError` (`labrat.maze.scent_audit`); `fingerprint_from_catalog` (`labrat.maze.staleness`); `Catalog` (`labrat.db.catalog`); `user_scent_dir` NOT used — the sidecar lives in the PROJECT scent dir: `store` has no public dir accessor, so the controller takes `project_scent_dir: Path` explicitly.
- Produces (Task 6 consumes): `IngestOutcome(domains: tuple[str, ...], sections_written: int, warnings: tuple[str, ...], skipped: bool, drifted: bool)`; `ingest_dbt_semantics(*, manifest_path: Path, catalog: Catalog | None, store: MazeStore, project_scent_dir: Path, force: bool = False) -> IngestOutcome` — `force=False` + unchanged fingerprint → `skipped=True, drifted=False` (no writes); sidecar absent → first contact, ingest; fingerprint differs + `force=False` → `skipped=True, drifted=True` (the TUI offers re-ingest); `force=True` → always ingest.

- [ ] **Step 1: Write the failing tests** (append)

```python
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.scent_audit import ScentContaminationError
from labrat.maze.semantic_ingest import ingest_dbt_semantics
from labrat.maze.store import MazeStore
import pytest


def _store(tmp_path: Path) -> tuple[MazeStore, Path]:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    return store, tmp_path / "proj" / "labrat_maze" / "scent"


def test_first_contact_ingests_and_stamps_sidecar(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is False and out.sections_written >= 4
    assert {"orders", "customers", "metrics"} <= set(out.domains)
    assert read_manifest_fingerprint(scent_dir) is not None
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    assert any(s.source == "semantic_layer" for s in doc.sections)


def test_unchanged_manifest_skips(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is True and out.drifted is False


def test_drift_detected_and_force_replaces_only_semantic_sections(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    # A harvested section lands in the same doc between ingests.
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    doc.sections.append(
        Section(heading="Gotchas", body="- keep me", source="harvested")
    )
    store.write_doc(doc)
    write_manifest_fingerprint(scent_dir, "stale")               # simulate drift
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is True and out.drifted is True           # offer, don't force
    out2 = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store,
        project_scent_dir=scent_dir, force=True,
    )
    assert out2.skipped is False
    doc2 = store.load_domain("orders", scope="project")
    harvested = [s for s in doc2.sections if s.source == "harvested"]
    assert [s.body for s in harvested] == ["- keep me"]          # preserved byte-for-byte
    semantic_after = [s for s in doc2.sections if s.source == "semantic_layer"]
    semantic_first = [s for s in doc.sections if s.source == "semantic_layer"]
    assert len(semantic_after) == len(semantic_first)            # replaced, not doubled


def test_missing_manifest_skips_with_warning(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    out = ingest_dbt_semantics(
        manifest_path=tmp_path / "nope" / "manifest.json",
        catalog=None, store=store, project_scent_dir=scent_dir,
    )
    assert out.skipped is True and out.warnings


def test_contaminated_description_fails_loud_writes_nothing(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    bad = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    bad["semantic_models"]["semantic_model.jaffle.orders"]["description"] = (
        "see ground_truth.csv for the answers"
    )
    bad_path = tmp_path / "manifest.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ScentContaminationError):
        ingest_dbt_semantics(
            manifest_path=bad_path, catalog=None, store=store, project_scent_dir=scent_dir
        )
    assert store.load_domain("orders", scope="project") is None  # nothing written


def test_catalog_stamps_real_fingerprint(tmp_path: Path) -> None:
    from labrat.maze.staleness import fingerprint_from_catalog

    cat = Catalog(
        database_name="db",
        schemas=[Schema(name="main", tables=[
            Table(name="orders", schema_name="main",
                  columns=[Column(name="id", data_type="INTEGER", nullable=False)])
        ])],
    )
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=cat, store=store, project_scent_dir=scent_dir
    )
    doc = store.load_domain("orders", scope="project")
    sem = next(s for s in doc.sections if s.source == "semantic_layer")
    assert sem.schema_hash == fingerprint_from_catalog(cat)
```

(Catalog fixture fields follow `tests/unit/test_staleness_catalog.py` conventions — verify against it before writing.)

- [ ] **Step 2: Run to verify FAIL** — `ImportError: ingest_dbt_semantics`.

- [ ] **Step 3: Implement** (append to `semantic_ingest.py`)

```python
from pydantic import BaseModel  # add to imports; plus:
from labrat.catalog.dbt.semantic import parse_semantic_manifest
from labrat.db.catalog import Catalog
from labrat.maze.document import ScentDoc
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc
from labrat.maze.staleness import fingerprint_from_catalog
from labrat.maze.store import MazeStore


class IngestOutcome(BaseModel):
    domains: tuple[str, ...] = ()
    sections_written: int = 0
    warnings: tuple[str, ...] = ()
    skipped: bool = False
    drifted: bool = False


def ingest_dbt_semantics(
    *,
    manifest_path: Path,
    catalog: Catalog | None,
    store: MazeStore,
    project_scent_dir: Path,
    force: bool = False,
) -> IngestOutcome:
    """Ingest semantic models/metrics into project-layer Scent (replace + audit).

    Fail-open at the controller level for missing/invalid manifests (skipped +
    warning); fail-LOUD (ScentContaminationError) once content reaches the
    write path — never catch the audit.
    """
    try:
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return IngestOutcome(skipped=True, warnings=(f"manifest unreadable: {exc}",))

    fingerprint = semantic_fingerprint(manifest)
    stored = read_manifest_fingerprint(project_scent_dir)
    if stored is not None and not force:
        if stored == fingerprint:
            return IngestOutcome(skipped=True)
        return IngestOutcome(skipped=True, drifted=True)

    artifacts = parse_semantic_manifest(manifest)
    if not artifacts.models and not artifacts.metrics:
        return IngestOutcome(skipped=True, warnings=tuple(artifacts.warnings))

    schema_hash = fingerprint_from_catalog(catalog) if catalog is not None else None
    drafts = build_semantic_sections(artifacts, schema_hash)

    written = 0
    for domain in sorted(drafts):
        doc = store.load_domain(domain, scope="project") or ScentDoc(domain=domain)
        doc.sections = [s for s in doc.sections if s.source != "semantic_layer"]
        doc.sections.extend(drafts[domain])
        tag = audit_scent_doc(doc)
        if tag:
            raise ScentContaminationError(
                f"semantic ingestion for {domain!r} tripped contamination guard: {tag}"
            )
        store.write_doc(doc)
        written += len(drafts[domain])

    write_manifest_fingerprint(project_scent_dir, fingerprint)
    return IngestOutcome(
        domains=tuple(sorted(drafts)),
        sections_written=written,
        warnings=tuple(artifacts.warnings),
    )
```

Note on the contamination test's "nothing written" assertion: `sorted(drafts)` puts `customers` before `orders`, so a contaminated `orders` doc CAN follow a written `customers` doc (per-doc audit contract, same as harvest). The test contaminates `orders` and asserts only `orders` unwritten — verify the fixture ordering makes the assertion correct as written; if your sort order writes `customers` first, that is fine and expected (assert on `orders` only, as shown). Also: the sidecar is written ONLY after all domains succeed — a mid-loop audit raise leaves no fingerprint, so the next attempt re-ingests (crash-safe).

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_semantic_ingest.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/semantic_ingest.py tests/unit/test_semantic_ingest.py
git commit -m "feat(maze): ingest_dbt_semantics — replace-semantics, audit fail-loud, drift sidecar"
```

---

### Task 4: `Profile.dbt_project_path` + onboarding persistence + Settings row

**Files:**
- Modify: `src/labrat/profile/model.py`, `src/labrat/profile/manager.py` (`make_profile`), `src/labrat/app.py` (`_save_onboarding_result`), `src/labrat/screens/settings.py`
- Test: `tests/unit/test_profile_dbt_path.py` (create), `tests/tui/test_settings_screen.py` (extend)

**Interfaces:**
- Produces: `Profile.dbt_project_path: str | None = None` (declared after `verify_enabled`, model.py:40); `make_profile(..., dbt_project_path: str | None = None)`; onboarding persists `result.catalog_path` into it when `result.catalog_type == "dbt"`; SettingsScreen row `#dbt-path-input` (empty → `None`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_profile_dbt_path.py
"""Profile.dbt_project_path: legacy-safe field + make_profile passthrough."""

from labrat.profile.manager import make_profile
from labrat.profile.model import Profile


def test_field_defaults_none_and_legacy_validates() -> None:
    assert Profile(name="p", dialect="duckdb").dbt_project_path is None
    legacy = {"name": "old", "dialect": "duckdb", "path": "/tmp/x.duckdb"}
    assert Profile.model_validate(legacy).dbt_project_path is None


def test_make_profile_passthrough() -> None:
    p = make_profile(name="p", dialect="duckdb", dbt_project_path="/repo/dbt")
    assert p.dbt_project_path == "/repo/dbt"
```

Extend `tests/tui/test_settings_screen.py` (match its existing `_Host`/manager pattern — read the file first):

```python
async def test_dbt_path_round_trips(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    mgr.add(Profile(name="p1", dialect="duckdb"))
    host = _Host(SettingsScreen(mgr.get("p1"), manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#dbt-path-input", Input).value = "/repo/dbt"
        await pilot.click("#save-btn")
        await pilot.pause()
    assert mgr.get("p1").dbt_project_path == "/repo/dbt"
```

(Add the `Input` import if the file lacks it.) Onboarding persistence is covered in Step 3's app.py change and pinned by an assertion added to whichever existing onboarding-save test exists — check `grep -rn "_save_onboarding_result\|OnboardingResult" tests/` and extend the closest test with `catalog_type="dbt", catalog_path="/repo/dbt"` → `profile.dbt_project_path == "/repo/dbt"`; if no such test exists, add a minimal direct test calling `_save_onboarding_result` with a stub result (keep it unit-level: construct `OnboardingResult` directly).

- [ ] **Step 2: Run to verify FAIL** — `AttributeError`/`TypeError` on the new names.

- [ ] **Step 3: Implement**

(a) `model.py` — after `verify_enabled` (line 40): `dbt_project_path: str | None = None` (with a one-line comment: `# dbt project root (T1b semantic ingestion); None = no dbt project`).
(b) `manager.py::make_profile` — add kw-only `dbt_project_path: str | None = None`, pass through to the `Profile(...)` construction.
(c) `app.py::_save_onboarding_result` (app.py:104-125) — add to the `make_profile(...)` call:

```python
    dbt_project_path=(
        result.catalog_path if result.catalog_type == "dbt" and result.catalog_path else None
    ),
```

(d) `settings.py` — after the Model row (settings.py:64-70 pattern):

```python
            with Horizontal(classes="row"):
                yield Label("dbt project")
                yield Input(
                    value=self._profile.dbt_project_path or "",
                    placeholder="path to dbt project root (optional)",
                    id="dbt-path-input",
                )
```

and in `action_save`'s update dict: `"dbt_project_path": (self.query_one("#dbt-path-input", Input).value.strip() or None),`.

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_profile_dbt_path.py tests/tui/test_settings_screen.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/profile/model.py src/labrat/profile/manager.py src/labrat/app.py src/labrat/screens/settings.py tests/unit/test_profile_dbt_path.py tests/tui/test_settings_screen.py
git commit -m "feat(profile): dbt_project_path — field, onboarding persistence, settings row"
```

(Include any onboarding-test file you extended in the `git add`.)

---

### Task 5: Cartographer stamps `schema_hash`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`generate_scent` only — builders untouched)
- Test: `tests/unit/test_cartographer_stamping.py` (create)

**Interfaces:**
- Consumes: `fingerprint_from_catalog` (`labrat.maze.staleness`); `generate_scent`'s per-domain loop (cartographer.py:641, sections assembled :665-682, ScentDoc at :683-689).
- Produces: every section in every `generate_scent`-emitted doc carries `schema_hash` = that domain's catalog fingerprint; `generated_at` stays `None`. Deterministic (pure function of the catalog).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_stamping.py
"""Cartographer sections carry schema_hash (freshness activation, T1b D3)."""

from pathlib import Path

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent
from labrat.maze.staleness import fingerprint_from_catalog


async def test_all_sections_stamped_no_clock(ecommerce_db: Path) -> None:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    docs = await generate_scent(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main"
    )
    expected = fingerprint_from_catalog(catalog)
    assert docs
    for doc in docs:
        for s in doc.sections:
            assert s.schema_hash == expected
            assert s.generated_at is None and s.model_id is None and s.git_sha is None


async def test_stamping_is_deterministic(ecommerce_db: Path) -> None:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    kwargs = dict(connections={"main": conn}, catalogs={"main": catalog}, primary="main")
    a = await generate_scent(**kwargs)
    b = await generate_scent(**kwargs)
    from labrat.maze.document import render_document

    assert [render_document(d) for d in a] == [render_document(d) for d in b]
```

(Verify `generate_scent` is async and its exact invocation against cartographer.py:623-634 — adapt the call if `primary` handling differs; do not guess.)

- [ ] **Step 2: Run to verify FAIL** — sections have `schema_hash is None`.

- [ ] **Step 3: Implement**

In `generate_scent`, inside the per-domain loop, after the full `sections` list is assembled (after the view-lineage append at :680-682) and BEFORE the `ScentDoc(...)` construction at :683:

```python
        fp = fingerprint_from_catalog(cast(Catalog, catalogs[name]))
        sections = [s.model_copy(update={"schema_hash": fp}) for s in sections]
```

(`fingerprint_from_catalog` import at module top; `cast`/`Catalog` already imported per the :680 call site.) Builders stay pure and untouched; the semantics pass (:690-710) runs AFTER — check whether `merge_sections`/`prune_unsupported` construct NEW sections that would miss the stamp: if the `with_semantics` path adds sections after this point, move the stamp loop to AFTER the semantics merge so every section in the final doc is stamped (note which placement you chose and why in your report).

- [ ] **Step 4: Run the full maze suite** — `uv run pytest tests/unit -k "cartographer or maze_document or first_connect or scent" -v`. Expected: `test_legacy_doc_round_trips_byte_identical` still passes (meta-less sections still render no Meta line — the stamp only touches generate_scent output, not the document model defaults). Any cartographer test asserting exact rendered output must be updated to expect the `**Meta:** schema_hash=…` line — list every such change in your report; heading/source/body-substring assertions are unaffected (Meta renders as its own line, outside `.body`).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_stamping.py
git commit -m "feat(maze): cartographer stamps schema_hash on all sections (freshness activation)"
```

(Add any updated golden-test files to the `git add`.)

---

### Task 6: MainScreen ingest worker + drift offer + help

**Files:**
- Modify: `src/labrat/screens/main.py`, `src/labrat/screens/help.py`
- Test: `tests/tui/test_main_screen_semantic.py` (create)

**Interfaces:**
- Consumes: `ingest_dbt_semantics`/`IngestOutcome` (Task 3); `Profile.dbt_project_path` (Task 4); M2's `_run_scent_prepass` block (main.py:383-413, called at :372) and `ConfirmScreen`.
- Produces: constructor param `dbt_manifest_override: Path | None = None`; `_run_semantic_ingest()` worker (`@work(exclusive=True, group="semantic")`); `action_reingest_semantics` (binding `f9`, confirm-gated force re-ingest); help row.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_main_screen_semantic.py
"""First-connect dbt semantic ingestion wiring on MainScreen."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, tmp_path: Path, *, dbt: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="semprof", dialect="duckdb",
        catalog=conn.introspect_catalog(), connection=conn,
        profile_obj=Profile(
            name="semprof", dialect="duckdb", path=str(ecommerce_db),
            dbt_project_path="/configured" if dbt else None,
        ),
        scent_dir=tmp_path / "scent",
        dbt_manifest_override=_FIXTURE if dbt else None,
        project_root_override=tmp_path / "proj",
    )


async def test_mount_ingests_when_dbt_configured(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, tmp_path, dbt=True)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        scent = tmp_path / "proj" / "labrat_maze" / "scent"
        assert (scent / "orders.md").exists()
        assert (scent / ".manifest_fingerprint").exists()
        text = (scent / "orders.md").read_text(encoding="utf-8")
        assert "**Source:** semantic_layer" in text
        assert "**Meta:** schema_hash=" in text                  # catalog stamped


async def test_no_dbt_path_no_ingest(ecommerce_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, tmp_path, dbt=False)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert not (tmp_path / "proj" / "labrat_maze").exists()
```

Note the extra constructor param this reveals: the controller writes to the PROJECT scent dir, which in production is `Path.cwd()/labrat_maze/scent` via `MazeStore.from_env` — tests must not write into the repo cwd, so the constructor also gains `project_root_override: Path | None = None`; production passes `None` → cwd behavior via `MazeStore.from_env(profile=...)`; tests get an isolated root via `MazeStore(project_root=override, home=..., profile=...)`. Implement accordingly (both overrides used ONLY when set; the store construction helper can be a small `_semantic_store()` method).

- [ ] **Step 2: Run to verify FAIL** — `TypeError: unexpected keyword argument 'dbt_manifest_override'`.

- [ ] **Step 3: Implement**

(a) Constructor: add `dbt_manifest_override: Path | None = None` and `project_root_override: Path | None = None`; store both; init `self._semantic_drifted = False`.
(b) In `on_mount`'s connected branch, immediately after `self._run_scent_prepass()` (main.py:372): `self._run_semantic_ingest()`.
(c) Worker + action (mirror the M2 worker's shape — imports and notify INSIDE the try, per the M3 fail-open lesson):

```python
    @work(exclusive=True, group="semantic")
    async def _run_semantic_ingest(self, *, force: bool = False) -> None:
        try:
            from pathlib import Path as _Path

            from labrat.maze.semantic_ingest import ingest_dbt_semantics
            from labrat.maze.store import MazeStore

            if self._profile_obj is None or not self._profile_obj.dbt_project_path:
                return
            if self._catalog is None:
                return
            manifest = self._dbt_manifest_override or (
                _Path(self._profile_obj.dbt_project_path) / "target" / "manifest.json"
            )
            if not manifest.is_file():
                self.notify(
                    "dbt manifest not found — run `dbt parse` in your project "
                    f"({manifest})", severity="warning", timeout=8,
                )
                return
            if self._project_root_override is not None:
                store = MazeStore(
                    project_root=self._project_root_override,
                    home=self._project_root_override / "home",
                    profile=self._profile_obj.name,
                )
                scent_dir = self._project_root_override / "labrat_maze" / "scent"
            else:
                store = MazeStore.from_env(profile=self._profile_obj.name)
                scent_dir = _Path.cwd() / "labrat_maze" / "scent"
            outcome = ingest_dbt_semantics(
                manifest_path=manifest, catalog=self._catalog,
                store=store, project_scent_dir=scent_dir, force=force,
            )
            self._semantic_drifted = outcome.drifted
            if outcome.drifted:
                self.notify(
                    "dbt semantic layer changed — press F9 to re-ingest",
                    severity="warning", timeout=8,
                )
            elif not outcome.skipped:
                self.notify(
                    f"semantic layer ingested · {outcome.sections_written} sections "
                    f"across {len(outcome.domains)} domains", timeout=4,
                )
            for w in outcome.warnings[:3]:
                self.notify(f"dbt ingest: {w}", severity="warning", timeout=6)
        except Exception as exc:  # fail-open: chat unaffected; audit error surfaces here too
            self.notify(f"Semantic ingestion failed: {exc}", severity="error", timeout=8)

    def action_reingest_semantics(self) -> None:
        from labrat.screens.confirm import ConfirmScreen

        if self._profile_obj is None or not self._profile_obj.dbt_project_path:
            self.notify("No dbt project configured (Ctrl+, → dbt project).",
                        severity="warning")
            return

        def _after(confirmed: bool | None) -> None:
            if confirmed:
                self._run_semantic_ingest(force=True)

        self.app.push_screen(
            ConfirmScreen(
                "[bold]Re-ingest dbt semantic layer?[/bold]\n\n"
                "Replaces semantic-layer sections in project Scent docs.\n"
                "[dim]Harvested and human sections are preserved.[/dim]"
            ),
            _after,
        )
```

Design note the reviewer will check: the spec says fail-loud audit errors surface "as their own explicit toast" — here `ScentContaminationError` lands in the generic except with `severity="error"` and the exception message names the contamination guard; that satisfies the spec (loud + named), keep the single handler.
(d) BINDINGS: `Binding("f9", "reingest_semantics", "Re-ingest dbt", show=False)`.
(e) `help.py` Session section: `("F9", "Re-ingest dbt semantic layer into Scent docs"),`.

- [ ] **Step 4: Run tests** — `uv run pytest tests/tui/test_main_screen_semantic.py tests/tui -v` (new PASS; all existing TUI incl. M1/M2/M3 wiring + snapshots PASS unchanged).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/screens/main.py src/labrat/screens/help.py tests/tui/test_main_screen_semantic.py
git commit -m "feat(tui): first-connect dbt semantic ingestion + drift offer (f9)"
```

---

### Task 7: E2E footer proof + docs + finish

**Files:**
- Test: `tests/unit/test_semantic_footer_e2e.py` (create)
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: Write the E2E test (no TUI — tool + footer level)**

```python
# tests/unit/test_semantic_footer_e2e.py
"""End-to-end: ingest → retrieve → footer shows (semantic_layer·fresh) / ·stale."""

import json
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.semantic_ingest import ingest_dbt_semantics
from labrat.maze.store import MazeStore
from labrat.widgets.turn_provenance import TurnProvenance

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _catalog(cols: list[str]) -> Catalog:
    return Catalog(
        database_name="db",
        schemas=[Schema(name="main", tables=[
            Table(name="orders", schema_name="main",
                  columns=[Column(name=c, data_type="INTEGER", nullable=True) for c in cols])
        ])],
    )


@pytest.fixture
def ingested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Catalog:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    cat = _catalog(["id", "total_amount"])
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=cat, store=store,
        project_scent_dir=tmp_path / "labrat_maze" / "scent",
    )
    return cat


async def _footer(ctx: ToolContext) -> str:
    tool = SearchReferenceDocsTool()
    args = tool.input_model.model_validate({"question": "orders revenue measure"})
    out = await tool.execute(ctx, args)
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, out.model_dump_json())
    return prov.footer() or ""


async def test_fresh_semantic_footer(ingested: Catalog) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": ingested},
                      primary="main")
    footer = await _footer(ctx)
    assert "scent: orders (semantic_layer·fresh)" in footer


async def test_schema_drift_renders_stale(ingested: Catalog) -> None:
    drifted = _catalog(["id", "total_amount", "new_col"])       # schema changed
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": drifted},
                      primary="main")
    footer = await _footer(ctx)
    assert "scent: orders (semantic_layer·stale)" in footer
```

Run: `uv run pytest tests/unit/test_semantic_footer_e2e.py -v`. Expected: PASS FIRST-RUN — every layer already shipped; this test PROVES the composition (the first live exercise of RMv2 freshness). If it fails, a real seam is broken — investigate the seam, never weaken the test.

- [ ] **Step 2: TESTING.md** — append after the M4 section:

```markdown
## T1b — dbt semantic ingestion (manual gate)

Setup: profile with `dbt_project_path` set (Ctrl+, → "dbt project"), pointing at a dbt project
whose `target/manifest.json` contains `semantic_models` (run `dbt parse` first).

1. Connect → "semantic layer ingested · N sections across M domains" toast; verify
   `./labrat_maze/scent/<table>.md` files carry `**Source:** semantic_layer` sections with
   `**Meta:** schema_hash=…`.
2. Reconnect → silent (fingerprint unchanged).
3. Edit a metric description in the dbt project, `dbt parse`, reconnect → drift warning toast;
   press F9 → confirm → sections replaced (old description gone), harvested/human sections in
   the same docs untouched.
4. Ask about a semantic-model table in chat → footer shows `scent: <table> (semantic_layer·fresh)`.
5. No dbt path configured → no toasts, no `metrics.md`, nothing ingested.
```

- [ ] **Step 3: decisions.md entry**

```markdown
## 2026-07-09 — T1b: dbt semantic-layer ingestion + freshness activation

`parse_semantic_manifest` (catalog/dbt/semantic.py) reads manifest.json's `semantic_models`/
`metrics` (dbt ≥1.6 — the roadmap's dbt_project.yml pointer was stale); `ingest_dbt_semantics`
writes `semantic_layer`-tagged, `schema_hash`-stamped sections into project-layer Scent docs —
replace-not-append for semantic sections (source-of-truth derived), audited fail-loud, drift
via a `.manifest_fingerprint` sidecar (semantic subset only). Runs at first connect when
`Profile.dbt_project_path` is set (onboarding's collected path finally persists; Settings row
added); F9 re-ingests after drift. The Cartographer now stamps `schema_hash` on all sections
(deterministic, no clock — `generated_at` stays unset), activating read-model-v2's per-section
freshness: footers can now render `(semantic_layer·fresh)`/`·stale`. Benchmark paths cannot
reach any of this (no profile sets the path). Spec:
docs/superpowers/specs/2026-07-09-dbt-semantic-ingestion-design.md.
```

- [ ] **Step 4: Full gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_semantic_footer_e2e.py TESTING.md decisions.md
git commit -m "test+docs: T1b e2e freshness proof + manual gate + decisions entry"
```

- [ ] **Step 5: Manual spot-check** (controller, pty harness): configure `dbt_project_path` on the egetest profile pointing at a scratch dbt project built from the fixture manifest (copy `manifest_semantic.json` to `<scratch>/target/manifest.json` — table names already match the ecommerce fixture); connect → ingest toast → ask "what does the revenue metric mean?" → expect `search_reference_docs` trace + a `(semantic_layer` footer tier. Then restore the profile. Then superpowers:finishing-a-development-branch.
