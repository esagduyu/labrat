import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.mcp.policy import (
    MAX_IDENTIFIER_CHARS_CEILING,
    MAX_MONGO_DEPTH_CEILING,
    MAX_MONGO_FILTER_BYTES_CEILING,
    MAX_OUTPUT_CHARS_CEILING,
    MAX_ROWS_CEILING,
    MAX_SAMPLE_ROWS_CEILING,
    MAX_SQL_CHARS_CEILING,
    MAX_TABLES_CEILING,
    McpPolicy,
    PolicyDenied,
    PolicyLoadError,
    PolicySession,
    canonical_policy_bytes,
    load_policy_from_env,
    policy_digest,
)


def _policy_data() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_manifest_sha256": "a" * 64,
        "task_id": "task-42",
        "trial_num": 1,
        "attempt_num": 0,
        "primary_database": "main",
        "allowed_tools": ["list_tables", "run_sql"],
        "source_grants": [
            {
                "alias": "main",
                "db_type": "duckdb",
                "relations": [
                    {
                        "database": "main",
                        "schema_name": "analytics",
                        "table": "orders",
                        "columns": ["order_id", "amount"],
                    }
                ],
            }
        ],
        "mongo_grants": [
            {
                "alias": "events",
                "database": "app",
                "collection": "clicks",
                "target_table": "clicks_materialized",
                "primary_database": "main",
                "max_rows": 500,
            }
        ],
        "limits": {
            "max_rows": 1000,
            "max_sample_rows": 100,
            "max_tables": 50,
            "max_output_chars": 20000,
            "max_sql_chars": 4000,
            "max_identifier_chars": 128,
            "max_mongo_depth": 8,
            "max_mongo_filter_bytes": 4096,
        },
        "cartographer_enabled": False,
        "builder_sha256": "b" * 64,
        "digest": "d" * 64,
    }


def _policy() -> McpPolicy:
    return McpPolicy.model_validate(_policy_data())


def _signed_policy() -> McpPolicy:
    policy = _policy()
    return policy.model_copy(update={"digest": policy_digest(policy)})


def _write_policy(path: Path, data: dict[str, object] | None = None) -> None:
    payload = _signed_policy().model_dump(mode="json") if data is None else data
    path.write_text(json.dumps(payload), encoding="utf-8")


class _Input(BaseModel):
    value: str = ""


class _Tool(Tool[_Input]):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} description"

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        return {"value": args.value}


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_Tool(name))
    return registry


def test_canonical_policy_bytes_are_exact_sorted_ascii_json_without_digest() -> None:
    expected = (
        b'{"allowed_tools":["list_tables","run_sql"],"attempt_num":0,'
        + b'"builder_sha256":"'
        + b"b" * 64
        + b'","cartographer_enabled":false,"limits":{"max_identifier_chars":128,'
        + b'"max_mongo_depth":8,"max_mongo_filter_bytes":4096,"max_output_chars":20000,'
        + b'"max_rows":1000,"max_sample_rows":100,"max_sql_chars":4000,"max_tables":50},'
        + b'"mongo_grants":[{"alias":"events","collection":"clicks","database":"app",'
        + b'"max_rows":500,"primary_database":"main","target_table":"clicks_materialized"}],'
        + b'"primary_database":"main","run_manifest_sha256":"'
        + b"a" * 64
        + b'","schema_version":1,"source_grants":[{"alias":"main","db_type":"duckdb",'
        + b'"relations":[{"columns":["order_id","amount"],"database":"main",'
        + b'"schema_name":"analytics","table":"orders"}]}],"task_id":"task-42","trial_num":1}'
    )

    assert canonical_policy_bytes(_policy()) == expected


def test_policy_digest_hashes_only_the_exclude_digest_canonical_bytes() -> None:
    policy = _policy()
    expected = hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()

    assert policy_digest(policy) == expected
    assert b'"digest":"' + b"d" * 64 + b'"' in canonical_policy_bytes(policy, include_digest=True)


def test_policy_models_are_frozen_and_collection_fields_are_tuples() -> None:
    policy = _policy()

    assert isinstance(policy.allowed_tools, tuple)
    assert isinstance(policy.source_grants, tuple)
    assert isinstance(policy.source_grants[0].relations, tuple)
    assert isinstance(policy.source_grants[0].relations[0].columns, tuple)
    with pytest.raises(ValidationError):
        policy.task_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trial_num", -1),
        ("attempt_num", -1),
        ("task_id", ""),
        ("task_id", "../escape"),
        ("primary_database", "bad/database"),
        ("run_manifest_sha256", "A" * 64),
        ("run_manifest_sha256", "a" * 63),
        ("builder_sha256", "not-a-sha"),
        ("digest", "g" * 64),
    ],
)
def test_policy_rejects_invalid_top_level_values(field: str, value: object) -> None:
    data = _policy_data()
    data[field] = value

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


@pytest.mark.parametrize(
    ("field", "ceiling"),
    [
        ("max_rows", MAX_ROWS_CEILING),
        ("max_sample_rows", MAX_SAMPLE_ROWS_CEILING),
        ("max_tables", MAX_TABLES_CEILING),
        ("max_output_chars", MAX_OUTPUT_CHARS_CEILING),
        ("max_sql_chars", MAX_SQL_CHARS_CEILING),
        ("max_identifier_chars", MAX_IDENTIFIER_CHARS_CEILING),
        ("max_mongo_depth", MAX_MONGO_DEPTH_CEILING),
        ("max_mongo_filter_bytes", MAX_MONGO_FILTER_BYTES_CEILING),
    ],
)
def test_policy_limit_accepts_exact_hard_ceiling(field: str, ceiling: int) -> None:
    data = _policy_data()
    limits = deepcopy(data["limits"])
    assert isinstance(limits, dict)
    limits[field] = ceiling
    data["limits"] = limits

    McpPolicy.model_validate(data)


@pytest.mark.parametrize(
    ("field", "ceiling"),
    [
        ("max_rows", MAX_ROWS_CEILING),
        ("max_sample_rows", MAX_SAMPLE_ROWS_CEILING),
        ("max_tables", MAX_TABLES_CEILING),
        ("max_output_chars", MAX_OUTPUT_CHARS_CEILING),
        ("max_sql_chars", MAX_SQL_CHARS_CEILING),
        ("max_identifier_chars", MAX_IDENTIFIER_CHARS_CEILING),
        ("max_mongo_depth", MAX_MONGO_DEPTH_CEILING),
        ("max_mongo_filter_bytes", MAX_MONGO_FILTER_BYTES_CEILING),
    ],
)
@pytest.mark.parametrize("invalid_value", [0, -1, None])
def test_policy_limits_must_be_positive_and_bounded(
    field: str, ceiling: int, invalid_value: int | None
) -> None:
    data = _policy_data()
    limits = deepcopy(data["limits"])
    assert isinstance(limits, dict)
    limits[field] = ceiling + 1 if invalid_value is None else invalid_value
    data["limits"] = limits

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("allowed_tools", 0), "bad tool"),
        (("source_grants", 0, "alias"), "bad/alias"),
        (("source_grants", 0, "relations", 0, "database"), ""),
        (("source_grants", 0, "relations", 0, "schema_name"), "bad.schema"),
        (("source_grants", 0, "relations", 0, "table"), "bad-table"),
        (("source_grants", 0, "relations", 0, "columns", 0), "bad column"),
        (("mongo_grants", 0, "database"), "../app"),
        (("mongo_grants", 0, "collection"), "bad collection"),
        (("mongo_grants", 0, "target_table"), ""),
    ],
)
def test_policy_rejects_unsafe_identifiers(path: tuple[object, ...], value: object) -> None:
    data = deepcopy(_policy_data())
    target: object = data
    for part in path[:-1]:
        assert isinstance(target, (dict, list))
        target = target[part]  # type: ignore[index]
    assert isinstance(target, (dict, list))
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_policy_rejects_unknown_fields_at_every_schema_level() -> None:
    data = deepcopy(_policy_data())
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)

    data = deepcopy(_policy_data())
    source_grants = data["source_grants"]
    assert isinstance(source_grants, list)
    source_grants[0]["unexpected"] = True  # type: ignore[index]
    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_policy_rejects_duplicate_allowed_tools() -> None:
    data = _policy_data()
    data["allowed_tools"] = ["run_sql", "run_sql"]

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_policy_rejects_duplicate_source_grant_aliases() -> None:
    data = deepcopy(_policy_data())
    source_grants = data["source_grants"]
    assert isinstance(source_grants, list)
    source_grants.append(deepcopy(source_grants[0]))

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_policy_rejects_duplicate_mongo_grant_identities() -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    mongo_grants.append(deepcopy(mongo_grants[0]))

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_mongo_alias_can_grant_multiple_collections_for_one_source_pair() -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    second = deepcopy(mongo_grants[0])
    second["collection"] = "views"
    second["target_table"] = "views_materialized"
    mongo_grants.append(second)

    McpPolicy.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("database", "other_app"), ("primary_database", "other_primary")],
)
def test_mongo_alias_must_resolve_to_one_consistent_source_pair(field: str, value: str) -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    second = deepcopy(mongo_grants[0])
    second["collection"] = "views"
    second["target_table"] = "views_materialized"
    second[field] = value
    mongo_grants.append(second)

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_mongo_grants_reject_duplicate_primary_target_destinations() -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    second = deepcopy(mongo_grants[0])
    second["alias"] = "other_events"
    second["database"] = "other_app"
    second["collection"] = "views"
    mongo_grants.append(second)

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_source_and_mongo_alias_namespaces_may_intentionally_overlap() -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    mongo_grants[0]["alias"] = "main"

    McpPolicy.model_validate(data)


def test_policy_rejects_duplicate_relations_within_a_source_grant() -> None:
    data = deepcopy(_policy_data())
    source_grants = data["source_grants"]
    assert isinstance(source_grants, list)
    relations = source_grants[0]["relations"]  # type: ignore[index]
    relations.append(deepcopy(relations[0]))

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_policy_rejects_duplicate_columns_within_a_relation() -> None:
    data = deepcopy(_policy_data())
    source_grants = data["source_grants"]
    assert isinstance(source_grants, list)
    columns = source_grants[0]["relations"][0]["columns"]  # type: ignore[index]
    columns.append(columns[0])

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


@pytest.mark.parametrize("value", [0, MAX_ROWS_CEILING + 1])
def test_mongo_grant_max_rows_is_positive_and_bounded(value: int) -> None:
    data = deepcopy(_policy_data())
    mongo_grants = data["mongo_grants"]
    assert isinstance(mongo_grants, list)
    mongo_grants[0]["max_rows"] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        McpPolicy.model_validate(data)


def test_load_policy_absent_env_returns_none_without_file_access(monkeypatch: Any) -> None:
    def unexpected_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("policy loader touched the filesystem")

    monkeypatch.setattr(Path, "read_text", unexpected_read)

    assert load_policy_from_env({}) is None


def test_load_policy_returns_valid_policy_with_matching_digest(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _write_policy(path)

    loaded = load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})

    assert loaded == _signed_policy()


@pytest.mark.parametrize("configured_path", ["", "   "])
def test_load_policy_rejects_blank_configured_path(configured_path: str) -> None:
    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": configured_path})


def test_load_policy_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(tmp_path / "missing.json")})


def test_load_policy_rejects_non_file_path(tmp_path: Path) -> None:
    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(tmp_path)})


def test_load_policy_wraps_unreadable_file(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "policy.json"
    _write_policy(path)

    def deny_read(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("secret OS detail")

    monkeypatch.setattr(Path, "read_text", deny_read)

    with pytest.raises(PolicyLoadError, match="unable to read"):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


@pytest.mark.parametrize("contents", ["", "{not-json", "[]"])
def test_load_policy_rejects_malformed_json(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "policy.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_load_policy_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_bytes(b"\xff")

    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_load_policy_rejects_unknown_field(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    data = _signed_policy().model_dump(mode="json")
    data["unknown"] = True
    _write_policy(path, data)

    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_load_policy_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    data = _signed_policy().model_dump(mode="json")
    data["schema_version"] = 2
    _write_policy(path, data)

    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_load_policy_wraps_pydantic_validation_failure(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    data = _signed_policy().model_dump(mode="json")
    data["trial_num"] = -1
    _write_policy(path, data)

    with pytest.raises(PolicyLoadError):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_load_policy_rejects_digest_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    data = _signed_policy().model_dump(mode="json")
    data["digest"] = "0" * 64
    _write_policy(path, data)

    with pytest.raises(PolicyLoadError, match="digest mismatch"):
        load_policy_from_env({"LABRAT_MCP_POLICY_PATH": str(path)})


def test_policy_session_revalidates_allowed_tool_uniqueness_on_creation() -> None:
    bypassed = _signed_policy().model_copy(update={"allowed_tools": ("run_sql", "run_sql")})

    with pytest.raises(PolicyLoadError, match="allowed tools"):
        PolicySession(bypassed)


def test_visible_tools_returns_exact_policy_order_without_hidden_tools() -> None:
    registry = _registry("hidden", "run_sql", "list_tables")
    session = PolicySession(_signed_policy())

    visible = session.visible_tools(registry)

    assert [tool.name for tool in visible] == ["list_tables", "run_sql"]


def test_visible_tools_fails_closed_when_registry_is_missing_allowed_tool() -> None:
    registry = _registry("list_tables", "hidden")
    session = PolicySession(_signed_policy())

    with pytest.raises(PolicyDenied, match="unavailable"):
        session.visible_tools(registry)


def test_authorize_denies_direct_hidden_tool_call() -> None:
    session = PolicySession(_signed_policy())

    with pytest.raises(PolicyDenied):
        session.authorize("hidden", {}, ToolContext())


def test_authorize_allows_any_plain_arguments_for_allowed_tool_in_task_2() -> None:
    session = PolicySession(_signed_policy())

    session.authorize(
        "run_sql",
        {"sql": "DROP TABLE anything", "future_argument": object()},
        ToolContext(),
    )


@pytest.mark.parametrize("arguments", [None, [], "bad", {1: "non-string key"}])
def test_authorize_fails_closed_for_malformed_arguments(arguments: Any) -> None:
    session = PolicySession(_signed_policy())

    with pytest.raises(PolicyDenied, match="arguments"):
        session.authorize("run_sql", arguments, ToolContext())


def test_record_success_is_a_typed_noop_seam() -> None:
    session = PolicySession(_signed_policy())

    assert session.record_success("run_sql", {"sql": "SELECT 1"}) is None
