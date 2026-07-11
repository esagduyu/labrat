"""Versioned, transport-neutral tool profiles for DAB benchmark tasks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Never, cast

from labrat.agent.tools.base import Tool, ToolRegistry

ToolProfileName = Literal["dab-core-v1", "legacy-full-20260710"]


class _FrozenDict(dict[str, Any]):
    """JSON-compatible dict that rejects every normal mutation API."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("canonical schemas are immutable")

    def __setitem__(self, key: str, value: Any) -> Never:
        _ = (key, value)
        self._immutable()

    def __delitem__(self, key: str) -> Never:
        _ = key
        self._immutable()

    def __ior__(self, value: Any) -> Never:
        _ = value
        self._immutable()

    def clear(self) -> Never:
        self._immutable()

    def pop(self, key: str, default: Any = None) -> Never:
        _ = (key, default)
        self._immutable()

    def popitem(self) -> Never:
        self._immutable()

    def setdefault(self, key: str, default: Any = None) -> Never:
        _ = (key, default)
        self._immutable()

    def update(self, *args: Any, **kwargs: Any) -> Never:
        _ = (args, kwargs)
        self._immutable()


class _FrozenList(list[Any]):
    """JSON-compatible list that rejects every normal mutation API."""

    @staticmethod
    def _immutable() -> Never:
        raise TypeError("canonical schemas are immutable")

    def __setitem__(self, index: Any, value: Any) -> Never:
        _ = (index, value)
        self._immutable()

    def __delitem__(self, index: Any) -> Never:
        _ = index
        self._immutable()

    def __iadd__(self, values: Any) -> Never:
        _ = values
        self._immutable()

    def __imul__(self, value: Any) -> Never:
        _ = value
        self._immutable()

    def append(self, value: Any) -> Never:
        _ = value
        self._immutable()

    def clear(self) -> Never:
        self._immutable()

    def extend(self, values: Any) -> Never:
        _ = values
        self._immutable()

    def insert(self, index: Any, value: Any) -> Never:
        _ = (index, value)
        self._immutable()

    def pop(self, index: Any = -1) -> Never:
        _ = index
        self._immutable()

    def remove(self, value: Any) -> Never:
        _ = value
        self._immutable()

    def reverse(self) -> Never:
        self._immutable()

    def sort(self, *args: Any, **kwargs: Any) -> Never:
        _ = (args, kwargs)
        self._immutable()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, _FrozenDict | _FrozenList):
        return value
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return _FrozenDict((key, _freeze_json(nested)) for key, nested in mapping.items())
    if isinstance(value, list | tuple):
        sequence = cast(list[Any] | tuple[Any, ...], value)
        return _FrozenList(_freeze_json(nested) for nested in sequence)
    return value


@dataclass(frozen=True)
class ResolvedToolProfile:
    name: ToolProfileName
    tools: tuple[str, ...]
    canonical_schemas: tuple[dict[str, Any], ...]
    schema_sha256: str

    def __post_init__(self) -> None:
        frozen_schemas = tuple(_freeze_json(schema) for schema in self.canonical_schemas)
        object.__setattr__(
            self,
            "canonical_schemas",
            cast(tuple[dict[str, Any], ...], frozen_schemas),
        )


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
    payload = json.dumps(
        canonical_schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
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


def _validate_resolved_profile(profile: ResolvedToolProfile) -> None:
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

    if len(profile.canonical_schemas) != len(profile.tools):
        raise ValueError(f"Tool profile {profile.name!r} disagrees with its canonical schemas")

    canonical_names: list[str] = []
    for index, canonical in enumerate(profile.canonical_schemas):
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
        computed_hash = _schema_sha256(profile.canonical_schemas)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Tool profile {profile.name!r} canonical schemas are not valid JSON"
        ) from exc
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
    _validate_resolved_profile(profile)
    by_name = _index_requested_tools(registry, profile.tools, profile.name)
    filtered = ToolRegistry()
    for tool_name, canonical in zip(profile.tools, profile.canonical_schemas, strict=True):
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
    _validate_resolved_profile(profile)
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
