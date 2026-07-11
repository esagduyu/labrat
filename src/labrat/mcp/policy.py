"""Fail-closed policy schema and authorization seam for the MCP server."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

MAX_ROWS_CEILING = 10_000
MAX_SAMPLE_ROWS_CEILING = 1_000
MAX_TABLES_CEILING = 1_000
MAX_OUTPUT_CHARS_CEILING = 2_000_000
MAX_SQL_CHARS_CEILING = 100_000
MAX_IDENTIFIER_CHARS_CEILING = 256
MAX_MONGO_DEPTH_CEILING = 16
MAX_MONGO_FILTER_BYTES_CEILING = 65_536

_SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_SAFE_TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

SafeIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_IDENTIFIER_CHARS_CEILING,
        pattern=_SAFE_IDENTIFIER_PATTERN,
    ),
]
SafeTaskId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=MAX_IDENTIFIER_CHARS_CEILING,
        pattern=_SAFE_TASK_ID_PATTERN,
    ),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]
PositiveRows = Annotated[int, Field(ge=1, le=MAX_ROWS_CEILING, strict=True)]


class PolicyLoadError(RuntimeError):
    """Raised when an explicitly configured MCP policy cannot be trusted."""


class PolicyDenied(RuntimeError):  # noqa: N818 - public contract uses this exact name
    """Raised when an MCP tool call is outside the active policy."""


class RelationGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: SafeIdentifier
    schema_name: SafeIdentifier | None
    table: SafeIdentifier
    columns: tuple[SafeIdentifier, ...]

    @model_validator(mode="after")
    def _columns_are_unique(self) -> Self:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("relation columns must be unique")
        return self


class SourceGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: SafeIdentifier
    db_type: Literal["duckdb", "sqlite", "postgres", "materialized_mongo"]
    relations: tuple[RelationGrant, ...]

    @model_validator(mode="after")
    def _relations_are_unique(self) -> Self:
        identities = [
            (relation.database, relation.schema_name, relation.table) for relation in self.relations
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("source grant relations must be unique")
        return self


class MongoGrant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: SafeIdentifier
    database: SafeIdentifier
    collection: SafeIdentifier
    target_table: SafeIdentifier
    primary_database: SafeIdentifier
    max_rows: PositiveRows


class PolicyLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_rows: Annotated[int, Field(ge=1, le=MAX_ROWS_CEILING, strict=True)]
    max_sample_rows: Annotated[int, Field(ge=1, le=MAX_SAMPLE_ROWS_CEILING, strict=True)]
    max_tables: Annotated[int, Field(ge=1, le=MAX_TABLES_CEILING, strict=True)]
    max_output_chars: Annotated[int, Field(ge=1, le=MAX_OUTPUT_CHARS_CEILING, strict=True)]
    max_sql_chars: Annotated[int, Field(ge=1, le=MAX_SQL_CHARS_CEILING, strict=True)]
    max_identifier_chars: Annotated[int, Field(ge=1, le=MAX_IDENTIFIER_CHARS_CEILING, strict=True)]
    max_mongo_depth: Annotated[int, Field(ge=1, le=MAX_MONGO_DEPTH_CEILING, strict=True)]
    max_mongo_filter_bytes: Annotated[
        int, Field(ge=1, le=MAX_MONGO_FILTER_BYTES_CEILING, strict=True)
    ]


class McpPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    run_manifest_sha256: Sha256Hex
    task_id: SafeTaskId
    trial_num: NonnegativeInt
    attempt_num: NonnegativeInt
    primary_database: SafeIdentifier
    allowed_tools: tuple[SafeIdentifier, ...]
    source_grants: tuple[SourceGrant, ...]
    mongo_grants: tuple[MongoGrant, ...]
    limits: PolicyLimits
    cartographer_enabled: bool
    builder_sha256: Sha256Hex
    digest: Sha256Hex

    @model_validator(mode="after")
    def _grant_identities_are_unique(self) -> Self:
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("allowed tools must be unique")

        source_aliases = [grant.alias for grant in self.source_grants]
        if len(set(source_aliases)) != len(source_aliases):
            raise ValueError("source grant aliases must be unique")

        mongo_identities = [
            (grant.alias, grant.database, grant.collection) for grant in self.mongo_grants
        ]
        if len(set(mongo_identities)) != len(mongo_identities):
            raise ValueError("Mongo grant identities must be unique")

        mongo_alias_sources: dict[str, tuple[str, str]] = {}
        for grant in self.mongo_grants:
            source_pair = (grant.database, grant.primary_database)
            prior = mongo_alias_sources.setdefault(grant.alias, source_pair)
            if prior != source_pair:
                raise ValueError("Mongo grant aliases must resolve to one source pair")

        mongo_targets = [
            (grant.primary_database, grant.target_table) for grant in self.mongo_grants
        ]
        if len(set(mongo_targets)) != len(mongo_targets):
            raise ValueError("Mongo grant targets must be unique")

        # Source and Mongo aliases are separate namespaces: a materialized_mongo
        # SourceGrant may intentionally pair with a MongoGrant under one alias.
        return self


def canonical_policy_bytes(policy: McpPolicy, *, include_digest: bool = False) -> bytes:
    """Serialize a policy to the exact canonical representation used for signing."""
    exclude = None if include_digest else {"digest"}
    payload = policy.model_dump(mode="json", exclude=exclude)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def policy_digest(policy: McpPolicy) -> str:
    """Return the SHA-256 digest of canonical policy bytes excluding ``digest``."""
    return hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()


def load_policy_from_env(env: Mapping[str, str]) -> McpPolicy | None:
    """Load and verify an explicitly configured policy, or preserve legacy mode.

    Absence of ``LABRAT_MCP_POLICY_PATH`` is the only condition that selects
    legacy behavior. Once the variable is present, every failure is fatal so a
    bad or tampered policy can never silently broaden access.
    """
    env_key = "LABRAT_MCP_POLICY_PATH"
    if env_key not in env:
        return None

    configured_path = env[env_key]
    if not configured_path.strip():
        raise PolicyLoadError("LABRAT_MCP_POLICY_PATH must name a policy file")

    path = Path(configured_path.strip())
    try:
        is_file = path.is_file()
    except OSError as exc:
        raise PolicyLoadError("unable to inspect MCP policy path") from exc
    if not is_file:
        raise PolicyLoadError("MCP policy path does not name a regular file")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyLoadError("unable to read MCP policy file") from exc
    except UnicodeError as exc:
        raise PolicyLoadError("MCP policy file is not valid UTF-8") from exc

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PolicyLoadError("MCP policy file is not valid JSON") from exc

    try:
        policy = McpPolicy.model_validate(payload)
    except ValidationError as exc:
        raise PolicyLoadError("MCP policy schema validation failed") from exc

    expected_digest = policy_digest(policy)
    if not hmac.compare_digest(policy.digest, expected_digest):
        raise PolicyLoadError("MCP policy digest mismatch")
    return policy


class PolicySession:
    """Runtime view of a verified policy used by the generic MCP server seam."""

    def __init__(self, policy: McpPolicy) -> None:
        if len(set(policy.allowed_tools)) != len(policy.allowed_tools):
            # Recheck at the trust boundary even if a caller bypassed normal
            # Pydantic validation with model_construct/model_copy.
            raise PolicyLoadError("MCP policy allowed tools must be unique")
        self.policy = policy
        self._allowed_tools = frozenset(policy.allowed_tools)

    def visible_tools(self, registry: ToolRegistry) -> list[Tool[Any]]:
        """Return only allowed tools, in policy order, failing on registry drift."""
        tools_by_name = {tool.name: tool for tool in registry.tools}
        missing = [name for name in self.policy.allowed_tools if name not in tools_by_name]
        if missing:
            raise PolicyDenied("MCP policy references an unavailable tool")
        return [tools_by_name[name] for name in self.policy.allowed_tools]

    def authorize(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> None:
        """Authorize tool membership; Task 3 adds argument semantics at this seam."""
        _ = ctx
        if name not in self._allowed_tools:
            raise PolicyDenied("MCP policy denied tool call")
        if not _is_plain_string_keyed_dict(arguments):
            raise PolicyDenied("MCP policy denied malformed tool arguments")

    def record_success(self, name: str, arguments: dict[str, Any]) -> None:
        """No-op seam reserved for Task 4 materialization-state tracking."""
        _ = name, arguments


def _is_plain_string_keyed_dict(value: object) -> bool:
    if type(value) is not dict:
        return False
    mapping = cast(dict[object, object], value)
    return all(isinstance(key, str) for key in mapping)
