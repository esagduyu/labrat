"""Versioned, transport-neutral tool profiles for DAB benchmark tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from labrat.agent.tools.base import Tool, ToolRegistry

ToolProfileName = Literal["dab-core-v1", "legacy-full-20260710"]


def _serialize_canonical_schemas(
    canonical_schemas: tuple[dict[str, Any], ...],
) -> str:
    return json.dumps(
        canonical_schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


@dataclass(frozen=True, init=False)
class ResolvedToolProfile:
    name: ToolProfileName
    tools: tuple[str, ...]
    schema_sha256: str
    _canonical_json: str = field(init=False, repr=False)

    def __init__(
        self,
        name: ToolProfileName,
        tools: tuple[str, ...],
        canonical_schemas: tuple[dict[str, Any], ...],
        schema_sha256: str,
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "tools", tuple(tools))
        object.__setattr__(self, "schema_sha256", schema_sha256)
        object.__setattr__(self, "_canonical_json", _serialize_canonical_schemas(canonical_schemas))

    def _decode_canonical_schemas(self) -> tuple[dict[str, Any], ...]:
        decoded: object = json.loads(self._canonical_json)
        if not isinstance(decoded, list):
            raise ValueError("Canonical schema state must be a JSON array")
        decoded_list = cast(list[Any], decoded)
        return cast(tuple[dict[str, Any], ...], tuple(decoded_list))

    @property
    def canonical_schemas(self) -> tuple[dict[str, Any], ...]:
        """Return a detached, mutable view of the authoritative canonical JSON."""
        return self._decode_canonical_schemas()


@dataclass(frozen=True)
class TaskToolContract:
    task_id: str
    profile_name: ToolProfileName
    tools: tuple[str, ...]
    schema_sha256: str


_DAB_CORE_V1 = (
    "profile_dataset",
    "list_tables",
    "describe_table",
    "search_columns",
    "link_schema",
    "sample_rows",
    "column_stats",
    "run_sql",
    "explain_sql",
    "check_sql",
    "explain_lineage",
    "verify_join",
    "workflow",
)

_LEGACY_FULL_20260710 = (
    "profile_dataset",
    "list_tables",
    "describe_table",
    "search_columns",
    "link_schema",
    "sample_rows",
    "column_stats",
    "run_sql",
    "explain_sql",
    "check_sql",
    "explain_lineage",
    "verify_join",
    "attach_database",
    "load_file",
    "load_mongo_collection",
    "search_reference_docs",
    "search_trails",
    "workflow",
    "llm_extract",
    "llm_classify",
    "run_program",
    "dispatch_subagent",
)

_DAB_CORE_V1_VARIANTS = (
    _DAB_CORE_V1,
    (*_DAB_CORE_V1, "search_reference_docs"),
    (*_DAB_CORE_V1, "load_mongo_collection"),
    (*_DAB_CORE_V1, "search_reference_docs", "load_mongo_collection"),
)


def _schema_sha256(canonical_schemas: tuple[dict[str, Any], ...]) -> str:
    payload = _serialize_canonical_schemas(canonical_schemas)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _first_duplicate(values: tuple[str, ...]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _canonical_entry(value: object, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"Canonical schema entry {index} must contain exactly name and input_schema"
        )
    mapping = cast(dict[str, Any], value)
    if set(mapping) != {"name", "input_schema"}:
        raise ValueError(
            f"Canonical schema entry {index} must contain exactly name and input_schema"
        )
    return mapping


def _index_requested_tools(
    registry: ToolRegistry,
    requested_tools: tuple[str, ...],
    profile_name: str,
) -> dict[str, Tool[Any]]:
    requested_names = set(requested_tools)
    by_name: dict[str, Tool[Any]] = {}
    for tool in registry.tools:
        if tool.name not in requested_names:
            continue
        if tool.name in by_name:
            raise ValueError(
                f"Tool profile {profile_name!r} has duplicate requested tool: {tool.name}"
            )
        by_name[tool.name] = tool

    missing = [tool_name for tool_name in requested_tools if tool_name not in by_name]
    if missing:
        raise ValueError(
            f"Tool profile {profile_name!r} is missing requested tool(s): {', '.join(missing)}"
        )
    return by_name


def _validate_profile_integrity(profile: ResolvedToolProfile) -> None:
    duplicate_tool = _first_duplicate(profile.tools)
    if duplicate_tool is not None:
        raise ValueError(
            f"Tool profile {profile.name!r} has duplicate requested tool: {duplicate_tool}"
        )

    if profile.name == "dab-core-v1":
        if profile.tools not in _DAB_CORE_V1_VARIANTS:
            raise ValueError(f"Resolved tool profile {profile.name!r} disagrees with its name")
    elif profile.name == "legacy-full-20260710":
        if profile.tools != _LEGACY_FULL_20260710:
            raise ValueError(f"Resolved tool profile {profile.name!r} disagrees with its name")
    else:
        raise ValueError(f"Unknown tool profile: {profile.name!r}")

    canonical_schemas = profile._decode_canonical_schemas()  # pyright: ignore[reportPrivateUsage]
    if len(canonical_schemas) != len(profile.tools):
        raise ValueError(f"Tool profile {profile.name!r} disagrees with its canonical schemas")

    canonical_names: list[str] = []
    for index, canonical in enumerate(canonical_schemas):
        canonical = _canonical_entry(canonical, index)
        canonical_name = canonical["name"]
        if not isinstance(canonical_name, str) or not canonical_name:
            raise ValueError(f"Canonical schema entry {index} name must be a nonempty string")
        if not isinstance(canonical["input_schema"], dict):
            raise ValueError(f"Canonical schema entry {index} input_schema must be a JSON object")
        canonical_names.append(canonical_name)

    duplicate_schema = _first_duplicate(tuple(canonical_names))
    if duplicate_schema is not None:
        raise ValueError(
            f"Tool profile {profile.name!r} has duplicate canonical schema: {duplicate_schema}"
        )

    for index, (canonical_name, tool_name) in enumerate(
        zip(canonical_names, profile.tools, strict=True)
    ):
        if canonical_name != tool_name:
            raise ValueError(
                f"Canonical schema entry {index} name {canonical_name!r} does not match "
                f"requested tool {tool_name!r}"
            )

    try:
        normalized_json = _serialize_canonical_schemas(canonical_schemas)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Tool profile {profile.name!r} canonical schemas are not valid JSON"
        ) from exc
    if normalized_json != profile._canonical_json:  # pyright: ignore[reportPrivateUsage]
        raise ValueError(f"Tool profile {profile.name!r} canonical schemas are not normalized")
    computed_hash = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
    if computed_hash != profile.schema_sha256:
        raise ValueError(
            f"Tool profile {profile.name!r} schema hash disagrees with canonical schemas"
        )


def resolve_tool_profile(
    name: ToolProfileName,
    registry: ToolRegistry,
    *,
    cartographer: bool = False,
    mongo: bool = False,
) -> ResolvedToolProfile:
    if name == "dab-core-v1":
        conditional_tools = (("search_reference_docs",) if cartographer else ()) + (
            ("load_mongo_collection",) if mongo else ()
        )
        requested_tools = _DAB_CORE_V1 + conditional_tools
    elif name == "legacy-full-20260710":
        if cartographer or mongo:
            raise ValueError(f"Tool profile {name!r} does not accept conditional flags")
        requested_tools = _LEGACY_FULL_20260710
    else:
        raise ValueError(f"Unknown tool profile: {name!r}")

    by_name = _index_requested_tools(registry, requested_tools, name)
    canonical_schemas = tuple(
        {
            "name": tool_name,
            "input_schema": by_name[tool_name].input_model.model_json_schema(),
        }
        for tool_name in requested_tools
    )
    return ResolvedToolProfile(
        name=name,
        tools=requested_tools,
        canonical_schemas=canonical_schemas,
        schema_sha256=_schema_sha256(canonical_schemas),
    )


def filter_registry(
    registry: ToolRegistry,
    profile: ResolvedToolProfile,
) -> ToolRegistry:
    _validate_profile_integrity(profile)
    by_name = _index_requested_tools(registry, profile.tools, profile.name)
    canonical_schemas = profile._decode_canonical_schemas()  # pyright: ignore[reportPrivateUsage]
    filtered = ToolRegistry()
    for tool_name, canonical in zip(profile.tools, canonical_schemas, strict=True):
        tool = by_name[tool_name]
        if (
            canonical.get("name") != tool_name
            or canonical.get("input_schema") != tool.input_model.model_json_schema()
        ):
            raise ValueError(
                f"Registry disagrees with tool profile {profile.name!r} for {tool_name}"
            )
        filtered.register(tool)
    return filtered


def resolve_task_tool_contract(
    task_id: str,
    profile: ResolvedToolProfile,
    *,
    cartographer: bool,
    mongo: bool,
) -> TaskToolContract:
    _validate_profile_integrity(profile)
    if profile.name == "dab-core-v1":
        requested_conditional = (("search_reference_docs",) if cartographer else ()) + (
            ("load_mongo_collection",) if mongo else ()
        )
        if profile.tools != _DAB_CORE_V1 + requested_conditional:
            raise ValueError(
                f"Task conditional flags do not match resolved profile {profile.name!r}"
            )
    elif profile.name == "legacy-full-20260710":
        if cartographer or mongo:
            raise ValueError(f"Tool profile {profile.name!r} does not accept conditional flags")
        if profile.tools != _LEGACY_FULL_20260710:
            raise ValueError(f"Resolved tool profile {profile.name!r} disagrees with its name")
    else:
        raise ValueError(f"Unknown tool profile: {profile.name!r}")

    return TaskToolContract(
        task_id=task_id,
        profile_name=profile.name,
        tools=profile.tools,
        schema_sha256=profile.schema_sha256,
    )
