"""Parse dbt semantic-layer artifacts (manifest.json semantic_models + metrics).

Pure + tolerant: malformed entries are skipped with a human-readable warning,
never an exception (T1b spec 3.1). dbt >= 1.6 compiles semantic models and
metrics into manifest.json top-level keys — NOT dbt_project.yml.
"""

from __future__ import annotations

from typing import Any, cast

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
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    return None


def _str(d: dict[str, Any], key: str) -> str:
    v = d.get(key)
    return v if isinstance(v, str) else ""


def _sub_defs(d: dict[str, Any], key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    raw = d.get(key)
    if not isinstance(raw, list):
        return out
    for item in cast(list[Any], raw):
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
        for item in cast(list[Any], inputs):
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
