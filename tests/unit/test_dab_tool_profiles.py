from __future__ import annotations

import copy
import hashlib
import json
import pickle
from typing import Any, cast

import pytest
from pydantic import BaseModel

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.eval.benchmarks.dab.tool_profiles import (
    ResolvedToolProfile,
    TaskToolContract,
    ToolProfileName,
    filter_registry,
    resolve_task_tool_contract,
    resolve_tool_profile,
)

CORE_TOOLS = (
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

LEGACY_TOOLS = (
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


class _WorkflowV2Input(BaseModel):
    revision: int


class _WorkflowV2Tool(Tool[_WorkflowV2Input]):
    @property
    def name(self) -> str:
        return "workflow"

    @property
    def description(self) -> str:
        return "Schema-changing workflow test double."

    @property
    def input_model(self) -> type[_WorkflowV2Input]:
        return _WorkflowV2Input

    async def execute(self, ctx: ToolContext, args: _WorkflowV2Input) -> dict[str, Any]:
        _ = ctx
        return args.model_dump()


class _RenamableWorkflowTool(_WorkflowV2Tool):
    def __init__(self) -> None:
        self.current_name = "workflow_alias"

    @property
    def name(self) -> str:
        return self.current_name


def _canonical_hash(schemas: tuple[dict[str, Any], ...]) -> str:
    serialized = json.dumps(
        schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _mutable_schemas(
    profile: ResolvedToolProfile,
) -> tuple[dict[str, Any], ...]:
    return cast(
        tuple[dict[str, Any], ...],
        tuple(json.loads(json.dumps(profile.canonical_schemas))),
    )


def _crafted_profile(
    source: ResolvedToolProfile,
    schemas: tuple[dict[str, Any], ...],
) -> ResolvedToolProfile:
    return ResolvedToolProfile(
        name=source.name,
        tools=source.tools,
        canonical_schemas=schemas,
        schema_sha256=_canonical_hash(schemas),
    )


def _first_nested_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        nested_values = value.values()
    elif isinstance(value, tuple):
        nested_values = value
    else:
        raise LookupError("No nested list in canonical schemas")
    for nested in nested_values:
        try:
            return _first_nested_list(nested)
        except LookupError:
            continue
    raise LookupError("No nested list in canonical schemas")


def _reverse_dict_insertion_order(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _reverse_dict_insertion_order(value[key]) for key in reversed(tuple(value))}
    if isinstance(value, list):
        return [_reverse_dict_insertion_order(nested) for nested in value]
    return value


def test_core_profile_has_exact_versioned_order() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())

    assert profile.name == "dab-core-v1"
    assert profile.tools == CORE_TOOLS
    assert tuple(schema["name"] for schema in profile.canonical_schemas) == CORE_TOOLS


def test_legacy_profile_has_exact_versioned_order() -> None:
    profile = resolve_tool_profile("legacy-full-20260710", build_data_tools_registry())

    assert profile.name == "legacy-full-20260710"
    assert profile.tools == LEGACY_TOOLS
    assert tuple(schema["name"] for schema in profile.canonical_schemas) == LEGACY_TOOLS


@pytest.mark.parametrize(
    ("cartographer", "mongo", "conditional_tools"),
    [
        (True, False, ("search_reference_docs",)),
        (False, True, ("load_mongo_collection",)),
        (True, True, ("search_reference_docs", "load_mongo_collection")),
    ],
)
def test_core_profile_appends_only_requested_conditional_tools_in_fixed_order(
    cartographer: bool,
    mongo: bool,
    conditional_tools: tuple[str, ...],
) -> None:
    profile = resolve_tool_profile(
        "dab-core-v1",
        build_data_tools_registry(),
        cartographer=cartographer,
        mongo=mongo,
    )

    assert profile.tools == CORE_TOOLS + conditional_tools


@pytest.mark.parametrize("flag", ["cartographer", "mongo"])
def test_legacy_profile_rejects_conditional_flags(flag: str) -> None:
    flags = {"cartographer": False, "mongo": False, flag: True}

    with pytest.raises(ValueError, match="does not accept conditional flags"):
        resolve_tool_profile(
            "legacy-full-20260710",
            build_data_tools_registry(),
            **flags,
        )


def test_unknown_profile_fails_closed_at_runtime() -> None:
    unknown = cast(ToolProfileName, "future-profile")

    with pytest.raises(ValueError, match="Unknown tool profile"):
        resolve_tool_profile(unknown, build_data_tools_registry())


def test_profile_resolution_fails_when_a_requested_tool_is_missing() -> None:
    incomplete = ToolRegistry()
    for tool in build_data_tools_registry().tools:
        if tool.name != "workflow":
            incomplete.register(tool)

    with pytest.raises(ValueError, match=r"missing requested tool.*workflow"):
        resolve_tool_profile("dab-core-v1", incomplete)


def test_profile_resolution_rejects_duplicate_requested_registry_names() -> None:
    registry = build_data_tools_registry()
    alias = _RenamableWorkflowTool()
    registry.register(alias)
    alias.current_name = "workflow"

    with pytest.raises(ValueError, match=r"duplicate requested tool.*workflow"):
        resolve_tool_profile("dab-core-v1", registry)


def test_schema_hash_is_exact_and_stable_across_registry_order_and_repeats() -> None:
    registry = build_data_tools_registry()
    reversed_registry = ToolRegistry()
    for tool in reversed(registry.tools):
        reversed_registry.register(tool)

    first = resolve_tool_profile("dab-core-v1", registry)
    repeated = resolve_tool_profile("dab-core-v1", registry)
    reordered = resolve_tool_profile("dab-core-v1", reversed_registry)
    serialized = json.dumps(
        first.canonical_schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    assert first.schema_sha256 == hashlib.sha256(serialized).hexdigest()
    assert repeated == first
    assert reordered == first


def test_schema_hash_changes_when_a_requested_tool_schema_changes() -> None:
    original = build_data_tools_registry()
    changed = build_data_tools_registry()
    changed.register(_WorkflowV2Tool())

    original_profile = resolve_tool_profile("dab-core-v1", original)
    changed_profile = resolve_tool_profile("dab-core-v1", changed)

    assert changed_profile.schema_sha256 != original_profile.schema_sha256


def test_schema_hash_sorts_nested_dict_keys_without_changing_list_order() -> None:
    source = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    mutable = _mutable_schemas(source)
    reordered = cast(
        tuple[dict[str, Any], ...],
        tuple(_reverse_dict_insertion_order(schema) for schema in mutable),
    )

    assert json.dumps(reordered) != json.dumps(mutable)
    assert _canonical_hash(reordered) == source.schema_sha256
    crafted = _crafted_profile(source, reordered)
    assert (
        resolve_task_tool_contract(
            "nested-order-task", crafted, cartographer=False, mongo=False
        ).schema_sha256
        == source.schema_sha256
    )


def test_filtered_registry_is_independent_exact_and_schema_equivalent() -> None:
    registry = build_data_tools_registry()
    original_names = tuple(tool.name for tool in registry.tools)
    profile = resolve_tool_profile("dab-core-v1", registry, cartographer=True, mongo=True)

    filtered = filter_registry(registry, profile)

    assert filtered is not registry
    assert tuple(tool.name for tool in filtered.tools) == profile.tools
    assert tuple(tool.name for tool in registry.tools) == original_names
    for tool, canonical in zip(filtered.tools, profile.canonical_schemas, strict=True):
        assert tool.name == canonical["name"]
        assert tool.input_model.model_json_schema() == canonical["input_schema"]


def test_filter_registry_rejects_registry_profile_schema_disagreement() -> None:
    original = build_data_tools_registry()
    profile = resolve_tool_profile("dab-core-v1", original)
    changed = build_data_tools_registry()
    changed.register(_WorkflowV2Tool())

    with pytest.raises(ValueError, match=r"disagrees.*workflow"):
        filter_registry(changed, profile)


def test_filter_registry_rejects_duplicate_requested_tool_names() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    duplicate = ResolvedToolProfile(
        name=profile.name,
        tools=(*profile.tools, profile.tools[-1]),
        canonical_schemas=(
            *profile.canonical_schemas,
            dict(profile.canonical_schemas[-1]),
        ),
        schema_sha256=profile.schema_sha256,
    )

    with pytest.raises(ValueError, match=r"duplicate requested tool.*workflow"):
        filter_registry(build_data_tools_registry(), duplicate)


def test_filter_registry_rejects_duplicate_canonical_schema_names() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    duplicate = ResolvedToolProfile(
        name=profile.name,
        tools=profile.tools,
        canonical_schemas=(
            *profile.canonical_schemas[:-1],
            dict(profile.canonical_schemas[0]),
        ),
        schema_sha256=profile.schema_sha256,
    )

    with pytest.raises(ValueError, match=r"duplicate canonical schema.*profile_dataset"):
        filter_registry(build_data_tools_registry(), duplicate)


def test_filter_registry_rejects_canonical_schema_hash_disagreement() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    tampered = ResolvedToolProfile(
        name=profile.name,
        tools=profile.tools,
        canonical_schemas=profile.canonical_schemas,
        schema_sha256="0" * 64,
    )

    with pytest.raises(ValueError, match="schema hash disagrees"):
        filter_registry(build_data_tools_registry(), tampered)


def test_filter_registry_rejects_profile_name_tool_list_disagreement() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    mislabeled = ResolvedToolProfile(
        name="legacy-full-20260710",
        tools=profile.tools,
        canonical_schemas=profile.canonical_schemas,
        schema_sha256=profile.schema_sha256,
    )

    with pytest.raises(ValueError, match="disagrees with its name"):
        filter_registry(build_data_tools_registry(), mislabeled)


@pytest.mark.parametrize(
    ("malformation", "message"),
    [
        ("missing_input_schema", "exactly name and input_schema"),
        ("extra_key", "exactly name and input_schema"),
        ("wrong_input_schema_type", "input_schema must be a JSON object"),
        ("empty_name", "name must be a nonempty string"),
        ("wrong_name_type", "name must be a nonempty string"),
        ("ordered_name_mismatch", "does not match requested tool"),
    ],
)
def test_consumers_reject_malformed_canonical_entries_even_with_matching_hash(
    malformation: str,
    message: str,
) -> None:
    registry = build_data_tools_registry()
    source = resolve_tool_profile("dab-core-v1", registry)
    schemas = list(_mutable_schemas(source))
    first = schemas[0]
    if malformation == "missing_input_schema":
        first.pop("input_schema")
    elif malformation == "extra_key":
        first["description"] = "not canonical"
    elif malformation == "wrong_input_schema_type":
        first["input_schema"] = []
    elif malformation == "empty_name":
        first["name"] = ""
    elif malformation == "wrong_name_type":
        first["name"] = 7
    else:
        first["name"] = "wrong_ordered_name"
    crafted_schemas = tuple(schemas)
    crafted = _crafted_profile(source, crafted_schemas)

    with pytest.raises(ValueError, match=message):
        filter_registry(registry, crafted)
    with pytest.raises(ValueError, match=message):
        resolve_task_tool_contract("malformed-task", crafted, cartographer=False, mongo=False)


def test_consumers_accept_a_valid_crafted_profile() -> None:
    registry = build_data_tools_registry()
    source = resolve_tool_profile("dab-core-v1", registry)
    crafted = _crafted_profile(source, _mutable_schemas(source))

    filtered = filter_registry(registry, crafted)
    contract = resolve_task_tool_contract("crafted-task", crafted, cartographer=False, mongo=False)

    assert tuple(tool.name for tool in filtered.tools) == source.tools
    assert contract.tools == source.tools
    assert contract.schema_sha256 == source.schema_sha256


def test_canonical_schema_top_level_mutation_is_detached() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    before = json.dumps(profile.canonical_schemas)
    view = profile.canonical_schemas

    view[0]["tampered"] = True

    assert view[0]["tampered"] is True
    assert json.dumps(profile.canonical_schemas) == before
    assert _canonical_hash(profile.canonical_schemas) == profile.schema_sha256


def test_canonical_schema_nested_dict_mutation_is_detached() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    before = json.dumps(profile.canonical_schemas)
    view = profile.canonical_schemas
    input_schema = view[0]["input_schema"]

    input_schema["tampered"] = True

    assert view[0]["input_schema"]["tampered"] is True
    assert json.dumps(profile.canonical_schemas) == before
    assert _canonical_hash(profile.canonical_schemas) == profile.schema_sha256


def test_canonical_schema_nested_list_mutation_is_detached() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    before = json.dumps(profile.canonical_schemas)
    view = profile.canonical_schemas
    nested_list = _first_nested_list(view)

    nested_list.append("tampered")

    assert "tampered" in nested_list
    assert json.dumps(profile.canonical_schemas) == before
    assert _canonical_hash(profile.canonical_schemas) == profile.schema_sha256


def test_bound_container_initializers_only_mutate_detached_views() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    before = json.dumps(profile.canonical_schemas)
    top_level_view = profile.canonical_schemas
    nested_list_view = profile.canonical_schemas
    nested_list = _first_nested_list(nested_list_view)

    top_level_view[0].__init__({"name": "detached", "input_schema": {}})
    nested_list.__init__(["detached"])

    assert top_level_view[0]["name"] == "detached"
    assert nested_list == ["detached"]
    assert json.dumps(profile.canonical_schemas) == before
    assert _canonical_hash(profile.canonical_schemas) == profile.schema_sha256


def test_profile_constructor_does_not_retain_the_callers_schema_view() -> None:
    source = resolve_tool_profile("dab-core-v1", build_data_tools_registry())
    caller_schemas = _mutable_schemas(source)
    profile = _crafted_profile(source, caller_schemas)
    before = json.dumps(profile.canonical_schemas)

    caller_schemas[0]["input_schema"]["tampered"] = True

    assert json.dumps(profile.canonical_schemas) == before
    assert _canonical_hash(profile.canonical_schemas) == profile.schema_sha256


def test_copy_deepcopy_and_pickle_round_trips_preserve_profile_integrity() -> None:
    registry = build_data_tools_registry()
    profile = resolve_tool_profile("dab-core-v1", registry)
    round_trips = (
        copy.copy(profile),
        copy.deepcopy(profile),
        pickle.loads(pickle.dumps(profile)),
    )

    for restored in round_trips:
        assert restored == profile
        assert hash(restored) == hash(profile)
        assert restored.canonical_schemas == profile.canonical_schemas
        filter_registry(registry, restored)
        resolve_task_tool_contract("round-trip-task", restored, cartographer=False, mongo=False)


def test_standard_json_dumps_supports_the_exact_canonical_options() -> None:
    profile = resolve_tool_profile("dab-core-v1", build_data_tools_registry())

    serialized = json.dumps(
        profile.canonical_schemas,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == profile.schema_sha256


def test_task_contract_copies_the_already_resolved_profile() -> None:
    profile = resolve_tool_profile(
        "dab-core-v1",
        build_data_tools_registry(),
        cartographer=True,
        mongo=True,
    )

    contract = resolve_task_tool_contract("task-17", profile, cartographer=True, mongo=True)

    assert contract == TaskToolContract(
        task_id="task-17",
        profile_name="dab-core-v1",
        tools=profile.tools,
        schema_sha256=profile.schema_sha256,
    )


@pytest.mark.parametrize(
    ("resolved_cartographer", "resolved_mongo", "cartographer", "mongo"),
    [
        (False, False, True, False),
        (True, False, False, False),
        (False, False, False, True),
        (False, True, False, False),
    ],
)
def test_task_contract_rejects_flags_that_do_not_match_resolved_core_profile(
    resolved_cartographer: bool,
    resolved_mongo: bool,
    cartographer: bool,
    mongo: bool,
) -> None:
    profile = resolve_tool_profile(
        "dab-core-v1",
        build_data_tools_registry(),
        cartographer=resolved_cartographer,
        mongo=resolved_mongo,
    )

    with pytest.raises(ValueError, match="flags do not match resolved profile"):
        resolve_task_tool_contract(
            "task-mismatch",
            profile,
            cartographer=cartographer,
            mongo=mongo,
        )


def test_task_contract_accepts_legacy_without_conditional_flags() -> None:
    profile = resolve_tool_profile("legacy-full-20260710", build_data_tools_registry())

    contract = resolve_task_tool_contract("legacy-task", profile, cartographer=False, mongo=False)

    assert contract.tools == LEGACY_TOOLS
    assert contract.schema_sha256 == profile.schema_sha256


@pytest.mark.parametrize(("cartographer", "mongo"), [(True, False), (False, True), (True, True)])
def test_task_contract_rejects_conditional_flags_for_legacy(
    cartographer: bool, mongo: bool
) -> None:
    profile = resolve_tool_profile("legacy-full-20260710", build_data_tools_registry())

    with pytest.raises(ValueError, match="does not accept conditional flags"):
        resolve_task_tool_contract(
            "legacy-task",
            profile,
            cartographer=cartographer,
            mongo=mongo,
        )
