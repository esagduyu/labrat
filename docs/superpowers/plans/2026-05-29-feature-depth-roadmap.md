# LabRat Feature Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn LabRat from a broad alpha scaffold into a deeply wired terminal data agent where safety, context, memory, catalog, audit, threads, findings, providers, and evals all participate in one end-to-end product path.

**Architecture:** Build depth by routing every user-facing execution path through shared services, then layering session prompt composition, provider selection, catalog/context loading, memory/validation application, audit logging, thread versioning, and findings on top of those services. Each milestone must ship a vertical slice with tests that prove the feature is active in the TUI or agent loop, not merely present as an isolated module.

**Tech Stack:** Python 3.12, `uv`, Textual, Pydantic v2, SQLGlot, Polars, JSONL local storage, keyring, pytest, pytest-textual-snapshot, ruff, pyright.

---

## Why This Plan Exists

The current codebase has good architectural instincts and many useful subsystem modules, but too many features are wired only partially:

- `run_sql` has safety/history, while editor execution and chart execution bypass that policy.
- memory, context bundles, catalog metadata, and validation rules exist, but are not included in the session prompt.
- provider abstractions exist, but the TUI and validations hardcode `ClaudeCodeProvider`.
- audit logs are modeled and tested, but production paths do not write audit events.
- threads and versions exist, but query execution does not append real versions.
- findings pin thread ids as version ids and do not retain result artifacts.
- default profile selection and catalog onboarding collect data but do not persist/use it fully.

The guiding rule for this plan: **a feature is done only when it is exercised from the main product path and protected by a regression test that would fail if the wiring disappeared.**

## Milestone Order

Milestones are intentionally sequenced. Do not skip ahead:

1. Feature depth contract and baseline tests
2. Central execution service and safety policy
3. Provider/profile configuration and default profile persistence
4. Session prompt composition with context, catalog, memory, and validations
5. Catalog onboarding and dbt/context integration
6. Memory and validation application loop
7. Audit, history, thread versions, and findings artifacts
8. Multi-schema/dialect hardening
9. TUI depth QA and snapshot stabilization
10. End-to-end depth eval suite and release gate

Each milestone should be merged only after its acceptance gate passes.

## Global Development Rules

- Keep existing user changes. Before editing, run `git status --short`.
- Use TDD for behavior changes: failing test, minimal implementation, passing test.
- Prefer small services with explicit inputs over hidden globals.
- Avoid adding another feature surface until the existing surface is wired end to end.
- Use `uv run ruff format .`, then `uv run ruff check .`, then `uv run pyright`, then targeted tests before each commit.
- Run `uv run pytest -q tests/unit tests/integration` before every milestone commit.
- Run full `uv run pytest -q` at milestone boundaries. If Textual snapshots fail, inspect and either update snapshots intentionally or fix rendering.

---

## Shared File Map

These are the main files this plan introduces or changes.

### New Core Services

- `src/labrat/sql/execution.py`  
  Owns mutation detection, auto-limit, dry-run/explain option, execution, result summarization, history writes, audit writes, and optional result artifact persistence.

- `src/labrat/agent/providers/factory.py`  
  Builds `ModelProvider` instances from profile/session provider settings.

- `src/labrat/agent/prompt_context.py`  
  Builds the full system prompt from base prompt, dialect prompt, context bundle, catalog metadata, memories, validations, and session instructions.

- `src/labrat/profile/settings.py`  
  Persists app-level profile settings such as default profile name.

- `src/labrat/results/store.py`  
  Writes query result artifacts as Parquet plus small JSON metadata, keyed by version id or execution id.

- `src/labrat/runtime/session.py`  
  Coordinates current profile, thread id, current version id, provider, prompt, execution service, audit log, and stores.

- `src/labrat/sql/identifiers.py`  
  Parses schema-qualified identifiers and provides dialect-aware quoting helpers used by tools/adapters.

- `src/labrat/eval/suites/depth_smoke.py`  
  Runs fast end-to-end product-depth checks against fixture data, seeded history, memory, dbt catalog, and validations.

### Existing Files To Wire

- `src/labrat/app.py`
- `src/labrat/cli.py`
- `src/labrat/screens/main.py`
- `src/labrat/screens/onboarding.py`
- `src/labrat/agent/loop.py`
- `src/labrat/agent/prompts/__init__.py`
- `src/labrat/agent/tools/run_sql.py`
- `src/labrat/agent/tools/create_chart.py`
- `src/labrat/agent/tools/explain_sql.py`
- `src/labrat/agent/tools/sample_rows.py`
- `src/labrat/agent/tools/describe_table.py`
- `src/labrat/agent/tools/column_stats.py`
- `src/labrat/agent/tools/recall_memories.py`
- `src/labrat/profile/model.py`
- `src/labrat/profile/manager.py`
- `src/labrat/profile/storage.py`
- `src/labrat/thread/manager.py`
- `src/labrat/thread/findings.py`
- `src/labrat/audit/log.py`
- `src/labrat/history/log.py`
- `src/labrat/catalog/manager.py`
- `src/labrat/catalog/dbt/loader.py`
- `src/labrat/context_engine/bundle.py`
- `src/labrat/validations/checker.py`
- `src/labrat/validations/store.py`

---

## Milestone 1: Feature Depth Contract And Baseline Tests

**Goal:** Define measurable feature-depth criteria and add tests that expose the current partial wiring.

**Files:**
- Create: `docs/feature_depth_contract.md`
- Create: `tests/unit/test_feature_depth_contract.py`
- Create: `tests/unit/test_current_depth_regressions.py`

### Task 1.1: Document The Product-Depth Contract

- [ ] **Step 1: Create `docs/feature_depth_contract.md`**

Write this document:

```markdown
# LabRat Feature Depth Contract

LabRat features count as complete only when they satisfy all four layers:

1. **Model layer:** typed data model or service interface exists.
2. **Runtime layer:** the main TUI or agent loop calls the feature without test-only shortcuts.
3. **Persistence/provenance layer:** state is saved in the correct local store and avoids secrets/result-row leakage.
4. **Regression layer:** at least one test fails if the runtime wiring is removed.

## Required Vertical Behaviors

| Behavior | Runtime Proof | Persistence Proof | Regression Proof |
|---|---|---|---|
| Safe SQL execution | Agent, editor, and chart execution use the same execution service | query history and audit receive events | mutation and auto-limit tests cover all execution paths |
| Provider selection | active profile selects provider/model | profile/default settings persist | provider factory tests and main-screen wiring tests |
| Prompt context | system prompt includes dialect, context bundle, catalog, memories, validations | no extra persistence beyond source stores | prompt builder tests |
| Catalog context | onboarding dbt path affects schema/context prompt | profile stores catalog source | dbt fixture integration test |
| Memory | active profile memories are recalled and surfaced | memory application count increments | profile-scoped memory tool test |
| Validations | successful query runs validation checks automatically or via required agent tool flow | validation result is surfaced/audited | fake validation checker test |
| Audit | user prompt, tool calls, SQL, findings, exports are event-sourced | JSONL session log | audit integration test |
| Threads/findings | successful execution creates a version; pinning uses version id and result ref | versions, findings, result artifacts persist | thread/finding integration test |
| Multi-schema | schema-qualified names work in tools and completions | no persistence required | duplicate table-name test |
| TUI quality | snapshots are stable or intentionally updated | snapshot artifacts live in tests | full pytest gate |
```

- [ ] **Step 2: No test is required for this doc-only commit**

Run:

```bash
uv run ruff format docs/feature_depth_contract.md
```

Expected: command exits successfully or reports no Python files formatted.

- [ ] **Step 3: Commit**

```bash
git add docs/feature_depth_contract.md
git commit -m "docs: define LabRat feature depth contract"
```

### Task 1.2: Add Regression Tests For Known Partial Wiring

These tests should initially fail. They are the baseline that later milestones turn green.

- [ ] **Step 1: Create `tests/unit/test_current_depth_regressions.py`**

Use this test content:

```python
"""Regression tests for product-depth wiring.

These tests encode behavior that must remain true once the feature-depth work lands.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.recall_memories import RecallMemoriesTool
from labrat.memory.model import Memory, MemoryKind, MemoryScope
from labrat.memory.store import MemoryStore


def _make_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders (id INTEGER, amount DOUBLE);")
    con.execute("INSERT INTO orders VALUES (1, 10.0), (2, 20.0);")
    con.close()


def test_recall_memories_uses_tool_context_profile_name(
    tmp_path: Path, monkeypatch
) -> None:
    import labrat.agent.tools.recall_memories as recall_mod

    store = MemoryStore(memory_dir=tmp_path)
    store.append(
        Memory(
            profile="dev",
            scope=MemoryScope.global_,
            kind=MemoryKind.edit_derived,
            text="dev memory",
        )
    )
    store.append(
        Memory(
            profile="default",
            scope=MemoryScope.global_,
            kind=MemoryKind.edit_derived,
            text="default memory",
        )
    )
    monkeypatch.setattr(recall_mod, "_memory_store", store)

    tool = RecallMemoriesTool()
    ctx = ToolContext(connection=object(), catalog=object(), profile_name="dev")
    result = asyncio.run(
        tool.execute(ctx, tool.input_model(context="revenue by orders", tables=["orders"]))
    )

    texts = [m["text"] for m in result["memories"]]
    assert texts == ["dev memory"]
```

- [ ] **Step 2: Run the new test and confirm it fails**

```bash
uv run pytest tests/unit/test_current_depth_regressions.py::test_recall_memories_uses_tool_context_profile_name -q
```

Expected: FAIL showing that `default memory` is returned instead of `dev memory`.

- [ ] **Step 3: Commit the failing test only if the team accepts red baseline commits**

If red baseline commits are not acceptable on the branch, keep this test unstaged and land it in Milestone 6 with the implementation. The preferred path for agentic execution is to include each red test in the same task as its green implementation.

---

## Milestone 2: Central Execution Service And Safety Policy

**Goal:** Every path that executes SQL goes through one service, so safety, auto-limit, history, audit, result refs, and validation hooks cannot drift.

**Files:**
- Create: `src/labrat/sql/execution.py`
- Create: `tests/unit/test_sql_execution_service.py`
- Modify: `src/labrat/agent/tools/run_sql.py`
- Modify: `src/labrat/agent/tools/create_chart.py`
- Modify: `src/labrat/screens/main.py`
- Modify: `tests/unit/test_sql_execution_tools.py`
- Modify: `tests/widgets/test_results_table.py`

### Target API

`src/labrat/sql/execution.py` should expose:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import polars as pl
from pydantic import BaseModel, ConfigDict

ExecutionSource = Literal["agent", "editor", "chart", "eval"]


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    profile_name: str
    thread_id: str = "unknown"
    version_id: str = "unknown"
    source: ExecutionSource
    auto_limit: int = 1000
    allow_mutation: bool = False
    write_history: bool = True
    write_audit: bool = True


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    requested_sql: str
    executed_sql: str
    refused: bool = False
    error: str | None = None
    columns: list[str] = []
    rows: list[list[str]] = []
    row_count: int = 0
    elapsed_ms: float = 0.0
    result_ref: str | None = None

    def summary_for_prompt(self, max_rows: int = 5) -> str:
        preview = self.rows[:max_rows]
        return (
            f"ok={self.ok} rows={self.row_count} columns={self.columns} "
            f"preview={preview} error={self.error!r}"
        )


@dataclass
class SqlExecutionService:
    history_log: object | None = None
    audit_log: object | None = None
    result_store: object | None = None

    def execute(self, connection: object, request: ExecutionRequest) -> ExecutionResult:
        ...
```

### Task 2.1: Write Service Tests

- [ ] **Step 1: Add tests to `tests/unit/test_sql_execution_service.py`**

Include these tests:

```python
from __future__ import annotations

from pathlib import Path

import duckdb

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.sql.execution import ExecutionRequest, SqlExecutionService


def _db(path: Path, read_only: bool = True) -> DuckDBConnection:
    if not path.exists():
        con = duckdb.connect(str(path))
        con.execute("CREATE TABLE orders (id INTEGER, amount DOUBLE);")
        con.execute("INSERT INTO orders VALUES (1, 10.0), (2, 20.0), (3, 30.0);")
        con.close()
    conn = DuckDBConnection(path, read_only=read_only)
    conn.connect()
    return conn


def test_service_applies_limit(tmp_path: Path) -> None:
    conn = _db(tmp_path / "test.duckdb")
    service = SqlExecutionService()
    result = service.execute(
        conn,
        ExecutionRequest(sql="SELECT * FROM orders", profile_name="dev", source="editor", auto_limit=2),
    )
    conn.disconnect()

    assert result.ok is True
    assert result.row_count == 2
    assert result.executed_sql.endswith("LIMIT 2")


def test_service_refuses_mutation_even_when_connection_is_writable(tmp_path: Path) -> None:
    conn = _db(tmp_path / "test.duckdb", read_only=False)
    service = SqlExecutionService()
    result = service.execute(
        conn,
        ExecutionRequest(sql="DROP TABLE orders", profile_name="dev", source="agent"),
    )

    still_exists = conn.execute("SELECT COUNT(*) AS n FROM orders")
    conn.disconnect()

    assert result.ok is False
    assert result.refused is True
    assert still_exists.item(0, "n") == 3


def test_service_allows_mutation_only_when_explicitly_allowed(tmp_path: Path) -> None:
    conn = _db(tmp_path / "test.duckdb", read_only=False)
    service = SqlExecutionService()
    result = service.execute(
        conn,
        ExecutionRequest(
            sql="CREATE TABLE scratch_depth_check (id INTEGER)",
            profile_name="dev",
            source="editor",
            allow_mutation=True,
        ),
    )
    check = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'scratch_depth_check'"
    )
    conn.disconnect()

    assert result.ok is True
    assert check.height == 1
```

- [ ] **Step 2: Run tests and confirm import failure**

```bash
uv run pytest tests/unit/test_sql_execution_service.py -q
```

Expected: FAIL because `labrat.sql.execution` does not exist.

### Task 2.2: Implement `SqlExecutionService`

- [ ] **Step 1: Create `src/labrat/sql/execution.py`**

Implement the target API using the existing helpers from `labrat.agent.tools.run_sql` at first. Move `_is_mutation`, `_has_limit`, `_apply_limit`, and `_log` into `sql/execution.py`, then update imports in `run_sql.py`.

Execution rules:

- if mutation and `allow_mutation=False`, do not call `connection.execute`;
- if not mutation and no `LIMIT`, append `LIMIT auto_limit`;
- if execution succeeds, convert result rows to `list[list[str]]`;
- if execution fails, return `ok=False` with `error=str(exc)`;
- when `history_log` is provided and `write_history=True`, append `QueryEvent`;
- when `audit_log` is provided and `write_audit=True`, log `SqlExecuted`;
- if `result_store` is provided and execution succeeds, persist the DataFrame and set `result_ref`.

- [ ] **Step 2: Run service tests**

```bash
uv run pytest tests/unit/test_sql_execution_service.py -q
```

Expected: PASS.

### Task 2.3: Refactor Agent `run_sql` To Use The Service

- [ ] **Step 1: Update `RunSqlTool`**

`RunSqlTool.execute()` should build an `ExecutionRequest(source="agent")` and call `SqlExecutionService.execute()`.

Preserve the existing output model fields so current agent tests keep passing.

- [ ] **Step 2: Run existing SQL tool tests**

```bash
uv run pytest tests/unit/test_sql_execution_tools.py -q
```

Expected: PASS.

### Task 2.4: Wire Editor Execution Through The Service

- [ ] **Step 1: Update `MainScreen._execute_sql`**

Replace direct `self._connection.execute(sql)` with `SqlExecutionService.execute(...)`.

Set a local `allow_mutation` flag inside `MainScreen`: initialize it to `False`; set it to `True` only inside the confirmation callback that handles a user-approved mutation. Then build:

```python
ExecutionRequest(
    sql=sql,
    profile_name=self._profile,
    thread_id=self._current_thread_id or "unknown",
    version_id=self._current_version_id or "unknown",
    source="editor",
    allow_mutation=allow_mutation,
)
```

If no mutation confirmation happened, `allow_mutation=False`.

- [ ] **Step 2: Add a widget/unit test**

Add a test that monkeypatches `SqlExecutionService.execute` and proves `_execute_sql` uses it. Put the test in `tests/tui/test_main_screen.py` or a new focused file `tests/unit/test_main_screen_execution.py` if Textual workers make the snapshot file awkward.

- [ ] **Step 3: Run targeted tests**

```bash
uv run pytest tests/unit/test_main_screen_execution.py tests/tui/test_main_screen.py -q
```

Expected: PASS, except snapshot tests may require intentional update in Milestone 9.

### Task 2.5: Wire Chart Execution Through The Service

- [ ] **Step 1: Update `CreateChartTool`**

The tool must call `SqlExecutionService.execute()` with `source="chart"` and `allow_mutation=False`.

If `ExecutionResult.ok` is false, return:

```python
{"ok": False, "error": result.error or "query refused", "refused": result.refused}
```

- [ ] **Step 2: Add chart safety test**

Add to `tests/unit/test_chart.py` or create `tests/unit/test_create_chart_tool.py`:

```python
async def test_create_chart_refuses_mutation_on_writable_connection(tmp_path: Path) -> None:
    ...
    result = await tool.execute(ctx, tool.input_model(query="DROP TABLE orders", chart_type="bar", x="id", y="amount"))
    assert result["ok"] is False
    assert result["refused"] is True
    assert conn.execute("SELECT COUNT(*) AS n FROM orders").item(0, "n") == 3
```

- [ ] **Step 3: Run chart tests**

```bash
uv run pytest tests/unit/test_chart.py tests/unit/test_create_chart_tool.py -q
```

Expected: PASS.

### Milestone 2 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_sql_execution_service.py tests/unit/test_sql_execution_tools.py tests/unit/test_create_chart_tool.py
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/sql/execution.py src/labrat/agent/tools/run_sql.py src/labrat/agent/tools/create_chart.py src/labrat/screens/main.py tests
git commit -m "feat: centralize SQL execution safety and history"
```

---

## Milestone 3: Provider/Profile Configuration And Default Profile Persistence

**Goal:** The active profile controls provider/model selection, and `labrat conn set-default` actually affects startup.

**Files:**
- Create: `src/labrat/agent/providers/factory.py`
- Create: `src/labrat/profile/settings.py`
- Create: `tests/unit/test_provider_factory.py`
- Create: `tests/unit/test_profile_settings.py`
- Modify: `src/labrat/profile/model.py`
- Modify: `src/labrat/profile/manager.py`
- Modify: `src/labrat/cli.py`
- Modify: `src/labrat/app.py`
- Modify: `src/labrat/screens/main.py`
- Modify: `tests/unit/test_profile.py`
- Modify: `tests/unit/test_cli_conn.py`

### Task 3.1: Extend Profile Model With Provider Settings

- [ ] **Step 1: Add provider fields to `Profile`**

Add:

```python
ProviderKind = Literal["claude_code", "anthropic", "openai_compatible"]

provider: ProviderKind = "claude_code"
model: str = "claude-sonnet-4-6"
base_url: str | None = None
```

Keep `api_key` out of the profile JSON; use keyring secrets for provider credentials.

- [ ] **Step 2: Add tests**

In `tests/unit/test_profile.py`, assert defaults:

```python
def test_profile_provider_defaults() -> None:
    p = make_profile("dev", "duckdb", path=":memory:")
    assert p.provider == "claude_code"
    assert p.model == "claude-sonnet-4-6"
    assert p.base_url is None
```

- [ ] **Step 3: Run**

```bash
uv run pytest tests/unit/test_profile.py -q
```

Expected: PASS.

### Task 3.2: Add Provider Factory

- [ ] **Step 1: Create `src/labrat/agent/providers/factory.py`**

Expose:

```python
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.providers.openai_compatible import OpenAICompatibleProvider
from labrat.agent.providers.base import ModelProvider
from labrat.profile.model import Profile
from labrat.profile import storage


def provider_for_profile(profile: Profile) -> ModelProvider:
    if profile.provider == "claude_code":
        return ClaudeCodeProvider(model=profile.model)
    if profile.provider == "anthropic":
        return AnthropicProvider(model=profile.model)
    if profile.provider == "openai_compatible":
        api_key = storage.load_secret(f"{profile.name}.provider_api_key")
        return OpenAICompatibleProvider(
            model=profile.model,
            base_url=profile.base_url,
            api_key=api_key,
        )
    raise ValueError(f"Unsupported provider: {profile.provider}")
```

If keyring naming needs a cleaner helper, add it to `profile/storage.py` in the same task.

- [ ] **Step 2: Add tests**

`tests/unit/test_provider_factory.py` should assert:

- `claude_code` returns `ClaudeCodeProvider`;
- `anthropic` returns `AnthropicProvider`;
- `openai_compatible` passes model/base_url/api_key.

Use monkeypatch for `storage.load_secret`.

- [ ] **Step 3: Run**

```bash
uv run pytest tests/unit/test_provider_factory.py -q
```

Expected: PASS.

### Task 3.3: Persist Default Profile

- [ ] **Step 1: Create `src/labrat/profile/settings.py`**

Implement:

```python
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_DATA_DIR = Path.home() / ".local" / "share" / "labrat"
_SETTINGS_FILE = _DATA_DIR / "settings.json"


class ProfileSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    default_profile: str | None = None


def load_settings(path: Path | None = None) -> ProfileSettings:
    p = path or _SETTINGS_FILE
    if not p.exists():
        return ProfileSettings()
    return ProfileSettings.model_validate(json.loads(p.read_text()))


def save_settings(settings: ProfileSettings, path: Path | None = None) -> None:
    p = path or _SETTINGS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(settings.model_dump(mode="json"), indent=2))
```

- [ ] **Step 2: Add tests**

`tests/unit/test_profile_settings.py`:

```python
def test_default_profile_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(ProfileSettings(default_profile="prod"), path)
    assert load_settings(path).default_profile == "prod"
```

- [ ] **Step 3: Wire CLI**

`conn set-default` should call `save_settings(ProfileSettings(default_profile=name))` after verifying the profile exists.

- [ ] **Step 4: Wire app startup**

`LabRatApp.on_mount()` should:

1. load settings;
2. if default exists and profile exists, choose it;
3. otherwise choose first sorted profile;
4. if no profiles, launch onboarding.

- [ ] **Step 5: Run**

```bash
uv run pytest tests/unit/test_profile_settings.py tests/unit/test_cli_conn.py tests/unit/test_profile.py -q
```

Expected: PASS.

### Task 3.4: Use Provider Factory In Main Screen

- [ ] **Step 1: Pass `Profile` object or provider config into `MainScreen`**

`LabRatApp._launch_main()` currently passes only profile name and dialect. It should either pass the full `Profile` or a small immutable `AgentSessionConfig`. Prefer passing `Profile` because profile settings are already typed.

- [ ] **Step 2: Replace `ClaudeCodeProvider()` in `MainScreen.on_mount()`**

Use:

```python
from labrat.agent.providers.factory import provider_for_profile
provider = provider_for_profile(self._profile_obj)
```

For screens launched without a real profile, keep Claude Code as the fallback.

- [ ] **Step 3: Add test**

Test that a profile with `provider="openai_compatible"` causes the factory to be called. Monkeypatch `provider_for_profile`.

### Milestone 3 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_provider_factory.py tests/unit/test_profile_settings.py tests/unit/test_profile.py tests/unit/test_cli_conn.py
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/agent/providers/factory.py src/labrat/profile src/labrat/cli.py src/labrat/app.py src/labrat/screens/main.py tests
git commit -m "feat: wire provider and default profile settings"
```

---

## Milestone 4: Full Session Prompt Composition

**Goal:** The agent receives the product’s differentiating context at session start: dialect, personal context, catalog metadata, relevant memories, active validations, and session instructions.

**Files:**
- Create: `src/labrat/agent/prompt_context.py`
- Create: `tests/unit/test_prompt_context.py`
- Modify: `src/labrat/agent/prompts/__init__.py`
- Modify: `src/labrat/screens/main.py`
- Modify: `tests/unit/test_system_prompts.py`

### Task 4.1: Build Prompt Context Renderer

- [ ] **Step 1: Create `src/labrat/agent/prompt_context.py`**

Expose:

```python
from __future__ import annotations

from dataclasses import dataclass

from labrat.context_engine.bundle import ContextBundle
from labrat.memory.model import Memory
from labrat.validations.model import ValidationRule


@dataclass(frozen=True)
class PromptContext:
    context_bundle: ContextBundle | None = None
    memories: list[Memory] | None = None
    validation_rules: list[ValidationRule] | None = None
    catalog_snippet: str = ""
    session_instructions: str = ""


def render_prompt_context(ctx: PromptContext) -> str:
    sections: list[str] = []
    if ctx.context_bundle is not None:
        sections.append(ctx.context_bundle.to_prompt_snippet())
    if ctx.catalog_snippet:
        sections.append("## Catalog-provided context\n\n" + ctx.catalog_snippet.strip())
    if ctx.memories:
        lines = ["## Things learned from this user's corrections"]
        for memory in ctx.memories:
            lines.append(f"- ({memory.scope.value}/{memory.kind.value}) {memory.text}")
        sections.append("\n".join(lines))
    if ctx.validation_rules:
        lines = ["## Validation rules you must check"]
        for rule in ctx.validation_rules:
            lines.append(f"- [{rule.severity.value}] {rule.natural_language_rule}")
        sections.append("\n".join(lines))
    if ctx.session_instructions.strip():
        sections.append("## Session instructions\n\n" + ctx.session_instructions.strip())
    return "\n\n".join(s for s in sections if s.strip())
```

- [ ] **Step 2: Add tests**

`tests/unit/test_prompt_context.py` should assert that all sections render and that an empty context renders an empty string.

- [ ] **Step 3: Run**

```bash
uv run pytest tests/unit/test_prompt_context.py -q
```

Expected: PASS.

### Task 4.2: Extend `build_system_prompt`

- [ ] **Step 1: Modify signature**

Change:

```python
def build_system_prompt(dialect: str) -> str:
```

to:

```python
def build_system_prompt(dialect: str, prompt_context: PromptContext | None = None) -> str:
```

Append `render_prompt_context(prompt_context)` after the dialect prompt when present.

- [ ] **Step 2: Preserve existing behavior**

Existing calls with only `dialect` must produce the same base+dialect prompt.

- [ ] **Step 3: Add tests**

In `tests/unit/test_system_prompts.py`, assert that a memory and validation text appear in the built prompt when supplied.

### Task 4.3: Wire Prompt Context Into `MainScreen`

- [ ] **Step 1: Load runtime context in `MainScreen.on_mount()`**

Before creating `AgentLoop`, load:

- `ContextBundle.load(self._profile)`;
- top relevant memories from `MemoryStore().read_profile(self._profile)`;
- active validation rules from `ValidationRuleStore().active_rules(self._profile)`;
- catalog snippet from the catalog integration introduced in Milestone 5.

For Milestone 4, catalog snippet can be empty. Milestone 5 fills it.

- [ ] **Step 2: Create `PromptContext` and pass system prompt explicitly**

Build:

```python
system = build_system_prompt(self._dialect, prompt_context)
loop = AgentLoop(provider=provider, registry=registry, ctx=ctx, system=system, dialect=self._dialect)
```

- [ ] **Step 3: Add unit test**

Use a fake `ContextBundle`, fake `MemoryStore`, and fake `ValidationRuleStore` to prove `AgentLoop` receives prompt text containing those entries.

### Milestone 4 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_prompt_context.py tests/unit/test_system_prompts.py tests/unit/test_agent_loop.py
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/agent/prompt_context.py src/labrat/agent/prompts/__init__.py src/labrat/screens/main.py tests
git commit -m "feat: compose session prompt with user context"
```

---

## Milestone 5: Catalog Onboarding And dbt/Context Integration

**Goal:** A dbt catalog selected during onboarding affects schema context, prompt context, and agent behavior.

**Files:**
- Modify: `src/labrat/profile/model.py`
- Modify: `src/labrat/screens/onboarding.py`
- Modify: `src/labrat/app.py`
- Modify: `src/labrat/catalog/manager.py`
- Modify: `src/labrat/catalog/dbt/loader.py`
- Modify: `src/labrat/agent/prompt_context.py`
- Create: `tests/unit/test_catalog_profile_wiring.py`
- Create: `tests/integration/test_dbt_catalog_prompt_context.py`

### Task 5.1: Persist Catalog Sources On Profiles

- [ ] **Step 1: Add model**

In `profile/model.py`:

```python
CatalogSourceType = Literal["dbt"]


class CatalogSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: CatalogSourceType
    path: str


class Profile(BaseModel):
    ...
    catalog_sources: list[CatalogSource] = []
```

- [ ] **Step 2: Update `make_profile`**

Add `catalog_sources: list[CatalogSource] | None = None` and pass `catalog_sources or []`.

- [ ] **Step 3: Wire onboarding save**

In `LabRatApp._save_onboarding_result()`, if `result.catalog_type == "dbt"` and `result.catalog_path` is set, pass:

```python
catalog_sources=[CatalogSource(type="dbt", path=result.catalog_path)]
```

- [ ] **Step 4: Add tests**

`tests/unit/test_catalog_profile_wiring.py`:

```python
def test_make_profile_persists_catalog_source() -> None:
    p = make_profile(
        "dev",
        "duckdb",
        path=":memory:",
        catalog_sources=[CatalogSource(type="dbt", path="/tmp/dbt")],
    )
    assert p.catalog_sources[0].type == "dbt"
    assert p.catalog_sources[0].path == "/tmp/dbt"
```

### Task 5.2: Render dbt Catalog Snippet For Prompt

- [ ] **Step 1: Add renderer to `catalog/manager.py`**

Add:

```python
def render_catalog_prompt(entries: dict[str, CatalogEntry], limit: int = 20) -> str:
    lines: list[str] = []
    for name, entry in list(entries.items())[:limit]:
        lines.append(f"### {entry.schema_name}.{name}")
        if entry.description:
            lines.append(entry.description)
        if entry.tags:
            lines.append(f"Tags: {', '.join(entry.tags)}")
        if entry.upstream:
            lines.append(f"Upstream: {', '.join(entry.upstream)}")
        if entry.downstream:
            lines.append(f"Downstream: {', '.join(entry.downstream)}")
        for col in entry.columns.values():
            marker = " [PII]" if col.is_pii else ""
            desc = f" — {col.description}" if col.description else ""
            dtype = f" ({col.data_type})" if col.data_type else ""
            lines.append(f"- {col.name}{dtype}{marker}{desc}")
        lines.append("")
    return "\n".join(lines).strip()
```

- [ ] **Step 2: Load sources in app startup**

When a profile has `CatalogSource(type="dbt")`, load it with `DbtLoader(Path(source.path)).load()`, render snippet, and pass into `PromptContext.catalog_snippet`.

- [ ] **Step 3: Add integration test using fixture dbt project**

Use `tests/fixtures/sample_dbt_project`. Assert prompt contains known model/column descriptions from `schema.yml` or `manifest.json`.

Run:

```bash
uv run pytest tests/integration/test_dbt_catalog_prompt_context.py -q
```

Expected: PASS.

### Task 5.3: Surface Catalog Metadata In Schema Browser

- [ ] **Step 1: Extend `Catalog` table comments from dbt entries**

When merging dbt metadata, set `Table.comment` from `CatalogEntry.description` when the entry has a non-empty description. Set `Column.comment` from `ColumnEntry.description` when the column entry has a non-empty description.

- [ ] **Step 2: Update schema tree labels**

If a table has a comment, render the table as:

```text
fct_orders  - Canonical orders table
```

Keep column labels compact.

- [ ] **Step 3: Add schema tree unit test**

Assert table label includes the catalog description for a fixture catalog.

### Milestone 5 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_catalog_profile_wiring.py tests/integration/test_dbt_catalog_prompt_context.py tests/unit/test_catalog
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/profile src/labrat/screens/onboarding.py src/labrat/app.py src/labrat/catalog src/labrat/agent/prompt_context.py tests
git commit -m "feat: wire dbt catalog into profile and prompts"
```

---

## Milestone 6: Memory And Validation Application Loop

**Goal:** Memories and validation rules affect behavior without relying on the model to discover dormant modules.

**Files:**
- Modify: `src/labrat/agent/tools/recall_memories.py`
- Modify: `src/labrat/memory/store.py`
- Create: `src/labrat/memory/coordinator.py`
- Create: `src/labrat/validations/service.py`
- Modify: `src/labrat/agent/tools/run_validations.py`
- Modify: `src/labrat/sql/execution.py`
- Modify: `src/labrat/screens/main.py`
- Create: `tests/unit/test_memory_coordinator.py`
- Create: `tests/unit/test_validation_service.py`
- Modify: `tests/unit/test_current_depth_regressions.py`

### Task 6.1: Fix Profile-Scoped Memory Recall

- [ ] **Step 1: Update `RecallMemoriesTool`**

Change:

```python
profile: str = getattr(ctx, "profile", "default")
```

to:

```python
profile: str = ctx.profile_name
```

Add optional constructor injection:

```python
def __init__(self, memory_store: MemoryStore | None = None) -> None:
    self._memory_store = memory_store or _memory_store
```

- [ ] **Step 2: Run regression test**

```bash
uv run pytest tests/unit/test_current_depth_regressions.py::test_recall_memories_uses_tool_context_profile_name -q
```

Expected: PASS.

### Task 6.2: Track Memory Applications

- [ ] **Step 1: Create `src/labrat/memory/coordinator.py`**

Expose:

```python
class MemoryCoordinator:
    def __init__(self, store: MemoryStore | None = None) -> None: ...
    def relevant_for_prompt(self, profile: str, tables: list[str], query: str, limit: int = 5) -> list[Memory]: ...
    def mark_applied(self, profile: str, memories: list[Memory]) -> None: ...
```

`mark_applied` should call `MemoryStore.increment_applied`.

- [ ] **Step 2: Use coordinator in prompt assembly**

When `MainScreen` builds `PromptContext`, use `MemoryCoordinator.relevant_for_prompt(...)` and call `mark_applied` only after the agent actually executes a query or explicitly invokes `recall_memories`.

- [ ] **Step 3: Add tests**

`tests/unit/test_memory_coordinator.py` should verify profile scoping, relevance ordering, and application count increment.

### Task 6.3: Add Validation Service

- [ ] **Step 1: Create `src/labrat/validations/service.py`**

Expose:

```python
class ValidationService:
    def __init__(self, store: ValidationRuleStore | None = None, checker: ValidationChecker | None = None) -> None: ...
    async def check_execution(self, profile: str, sql: str, result_summary: str) -> list[ValidationResult]: ...
```

Use `ValidationRuleStore.active_rules(profile)`. If no active rules exist, return an empty list.

- [ ] **Step 2: Update `RunValidationsTool`**

Use `ValidationService` instead of constructing store/checker inline. Keep fake checker injection easy for tests.

- [ ] **Step 3: Wire successful editor/agent query execution to validations**

After `SqlExecutionService.execute()` succeeds, call validation service with `ExecutionResult.summary_for_prompt()`. Surface warnings/blocks in:

- chat/tool result for agent path;
- results pane/chart log for editor path;
- audit log after Milestone 7.

- [ ] **Step 4: Add tests**

`tests/unit/test_validation_service.py` should use a fake checker that returns pass/warn/block and assert blocked count is surfaced.

### Milestone 6 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_current_depth_regressions.py tests/unit/test_memory_coordinator.py tests/unit/test_validation_service.py tests/unit/test_validations
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/memory src/labrat/validations src/labrat/agent/tools/recall_memories.py src/labrat/agent/tools/run_validations.py src/labrat/sql/execution.py src/labrat/screens/main.py tests
git commit -m "feat: apply profile memories and validations in runtime"
```

---

## Milestone 7: Audit, History, Thread Versions, And Findings Artifacts

**Goal:** Every meaningful interaction has provenance. Successful SQL execution creates a version; pinning creates a finding tied to a version and result artifact.

**Files:**
- Create: `src/labrat/results/__init__.py`
- Create: `src/labrat/results/store.py`
- Create: `src/labrat/runtime/__init__.py`
- Create: `src/labrat/runtime/session.py`
- Modify: `src/labrat/sql/execution.py`
- Modify: `src/labrat/screens/main.py`
- Modify: `src/labrat/widgets/chat_panel.py`
- Modify: `src/labrat/thread/model.py`
- Modify: `src/labrat/thread/manager.py`
- Modify: `src/labrat/thread/findings.py`
- Modify: `src/labrat/audit/events.py`
- Modify: `src/labrat/audit/export.py`
- Create: `tests/unit/test_result_store.py`
- Create: `tests/unit/test_runtime_session.py`
- Create: `tests/unit/test_findings_artifacts.py`

### Task 7.1: Add Result Artifact Store

- [ ] **Step 1: Create `src/labrat/results/store.py`**

Expose:

```python
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
from pydantic import BaseModel, ConfigDict

_DEFAULT_RESULTS_DIR = Path.home() / ".local" / "share" / "labrat" / "results"


class ResultRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    parquet_path: str
    metadata_path: str
    row_count: int
    columns: list[str]


class ResultStore:
    def __init__(self, results_dir: Path | None = None) -> None:
        self._dir = results_dir or _DEFAULT_RESULTS_DIR

    def write(self, result_id: str, df: pl.DataFrame, *, sql: str, profile: str) -> ResultRef:
        self._dir.mkdir(parents=True, exist_ok=True)
        parquet = self._dir / f"{result_id}.parquet"
        meta = self._dir / f"{result_id}.json"
        df.write_parquet(parquet)
        metadata = {"id": result_id, "sql": sql, "profile": profile, "row_count": len(df), "columns": df.columns}
        meta.write_text(json.dumps(metadata, indent=2))
        return ResultRef(
            id=result_id,
            parquet_path=str(parquet),
            metadata_path=str(meta),
            row_count=len(df),
            columns=df.columns,
        )

    def read(self, ref: ResultRef | str) -> pl.DataFrame:
        path = ref.parquet_path if isinstance(ref, ResultRef) else ref
        return pl.read_parquet(path)
```

- [ ] **Step 2: Test round trip**

`tests/unit/test_result_store.py` should write a small DataFrame and read it back.

### Task 7.2: Persist Versions On Successful Execution

- [ ] **Step 1: Add current version tracking in `MainScreen`**

Add `self._current_version_id: str | None = None`.

- [ ] **Step 2: On successful execution, append version**

After agent/editor successful SQL execution:

```python
version = self._thread_manager.append_version(
    thread_id=self._current_thread_id or "unknown",
    sql=result.executed_sql,
    chat_history=self.query_one("#chat-content", ChatPanel).agent_history_for_version(),
    results_ref=result.result_ref,
)
self._current_version_id = version.id
```

Add `ChatPanel.agent_history_for_version()` returning a list of role/content dicts from its transcript or agent loop history.

- [ ] **Step 3: Add tests**

`tests/unit/test_runtime_session.py` or a focused main-screen test should assert successful execution creates one `Version` with a non-null `results_ref`.

### Task 7.3: Fix Finding Pinning

- [ ] **Step 1: Update `on_results_table_pin_requested`**

Refuse pinning if `self._current_version_id is None`.

Call:

```python
mgr.pin(
    version_id=self._current_version_id,
    question=self._last_question or "Pinned from results table",
    sql=self._last_sql,
    results_ref=self._last_result_ref,
    chart_spec=self._last_chart_spec,
    note="",
)
```

- [ ] **Step 2: Store last question/result/chart**

Track:

- `_last_question` when a chat message starts or manual execution runs;
- `_last_result_ref` from `ExecutionResult.result_ref`;
- `_last_chart_spec` from `CreateChartTool`.

- [ ] **Step 3: Add tests**

`tests/unit/test_findings_artifacts.py` should assert:

- finding version id equals a real `Version.id`;
- finding results_ref is not `None` after a successful query;
- export includes SQL and a result preview.

### Task 7.4: Wire Audit Events

- [ ] **Step 1: Create session audit log in runtime**

`MainScreen` should create one `AuditLog` per app session and pass it into services.

- [ ] **Step 2: Log events**

Log:

- `UserPrompt` when chat input submits;
- `AgentMessage` when final agent response is appended;
- `ToolCall` and `ToolResult` in registry dispatch wrapper;
- `SqlExecuted` in `SqlExecutionService`;
- `FindingPinned` in pin handler;
- `ExportRequested` in export screen.

- [ ] **Step 3: Add integration test**

Use temp audit directory and fake one chat/tool/query path. Assert JSONL contains ordered event types:

```python
["user_prompt", "tool_call", "tool_result", "sql_executed", "agent_message"]
```

### Milestone 7 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_result_store.py tests/unit/test_runtime_session.py tests/unit/test_findings_artifacts.py tests/unit/test_audit.py tests/unit/test_thread_manager.py
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/results src/labrat/runtime src/labrat/sql/execution.py src/labrat/screens/main.py src/labrat/widgets/chat_panel.py src/labrat/thread src/labrat/audit tests
git commit -m "feat: persist execution provenance and finding artifacts"
```

---

## Milestone 8: Multi-Schema And Dialect Hardening

**Goal:** Tools and completions handle schema-qualified names, duplicate table names, and dialect identifier quoting safely.

**Files:**
- Create: `src/labrat/sql/identifiers.py`
- Create: `tests/unit/test_sql_identifiers.py`
- Modify: `src/labrat/db/catalog.py`
- Modify: `src/labrat/agent/tools/describe_table.py`
- Modify: `src/labrat/agent/tools/sample_rows.py`
- Modify: `src/labrat/agent/tools/column_stats.py`
- Modify: `src/labrat/sql/completer.py`
- Modify: all warehouse adapters where `sample_table` and `column_stats` interpolate identifiers
- Create: `tests/unit/test_schema_qualified_tools.py`

### Task 8.1: Add Identifier Utilities

- [ ] **Step 1: Create `src/labrat/sql/identifiers.py`**

Expose:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableIdentifier:
    table: str
    schema: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.schema}.{self.table}" if self.schema else self.table


def parse_table_identifier(raw: str) -> TableIdentifier:
    parts = [p for p in raw.strip().split(".") if p]
    if len(parts) == 1:
        return TableIdentifier(table=parts[0])
    if len(parts) == 2:
        return TableIdentifier(schema=parts[0], table=parts[1])
    raise ValueError(f"Expected table or schema.table, got {raw!r}")


def quote_identifier(name: str, dialect: str) -> str:
    escaped = name.replace('"', '""')
    if dialect == "bigquery":
        return f"`{name.replace('`', '``')}`"
    return f'"{escaped}"'


def render_table_identifier(identifier: TableIdentifier, dialect: str) -> str:
    if identifier.schema:
        return f"{quote_identifier(identifier.schema, dialect)}.{quote_identifier(identifier.table, dialect)}"
    return quote_identifier(identifier.table, dialect)
```

- [ ] **Step 2: Add tests**

Assert parsing and rendering for `orders`, `main.orders`, and invalid `a.b.c`.

### Task 8.2: Support Schema-Qualified Catalog Lookup

- [ ] **Step 1: Update `Catalog.find_table`**

If `table_name` contains one dot and `schema_name is None`, parse it and search by schema/table.

- [ ] **Step 2: Add duplicate table test**

Catalog with `main.orders` and `analytics.orders`:

```python
assert catalog.find_table("main.orders").schema_name == "main"
assert catalog.find_table("analytics.orders").schema_name == "analytics"
assert catalog.find_table("orders") is not None
```

### Task 8.3: Update Tools

- [ ] **Step 1: `DescribeTableTool`**

After `Catalog.find_table` fix, `describe_table("main.orders")` should work.

- [ ] **Step 2: `SampleRowsTool` and `ColumnStatsTool`**

Pass parsed/rendered identifiers to adapters or update `Connection` interface to accept raw schema-qualified names. Keep the public tool input as string.

- [ ] **Step 3: Add tests**

`tests/unit/test_schema_qualified_tools.py` should cover:

- `describe_table(table="main.orders")`;
- `sample_rows(table="main.orders")`;
- `column_stats(table="main.orders", column="amount")`.

### Task 8.4: Improve Completer

- [ ] **Step 1: Complete columns for `schema.table.`**

`SQLCompleter._column_completions_for` should understand `main.orders`.

- [ ] **Step 2: Add tests**

In `tests/unit/test_sql/test_completer.py`, add:

```python
def test_schema_qualified_dot_notation_suggests_columns(catalog: Catalog) -> None:
    completer = SQLCompleter(catalog)
    sql = "SELECT main.orders."
    results = completer.complete(sql, len(sql))
    assert any(c.label == "amount" for c in results)
```

### Milestone 8 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_sql_identifiers.py tests/unit/test_schema_qualified_tools.py tests/unit/test_sql/test_completer.py tests/unit/test_schema_tools.py
uv run pytest -q tests/unit tests/integration
```

Expected: all pass.

Commit:

```bash
git add src/labrat/sql src/labrat/db src/labrat/agent/tools tests
git commit -m "feat: support schema-qualified tables across tools"
```

---

## Milestone 9: TUI Depth QA And Snapshot Stabilization

**Goal:** The UI reflects the deeper runtime state and the snapshot suite is intentionally green.

**Files:**
- Modify: `src/labrat/screens/main.py`
- Modify: `src/labrat/widgets/chat_panel.py`
- Modify: `src/labrat/widgets/results_table.py`
- Modify: `src/labrat/widgets/schema_tree.py`
- Modify: `tests/tui/*`
- Modify: `tests/widgets/__snapshots__/*`
- Modify: `tests/tui/__snapshots__/*`

### Task 9.1: Make TUI Tests Deterministic

- [ ] **Step 1: Isolate profile storage in TUI tests**

TUI snapshots should not depend on the developer’s real `~/.local/share/labrat`. Add monkeypatch fixtures in `tests/conftest.py` or per TUI test to use temp profile/settings/history directories.

- [ ] **Step 2: Stabilize app initial render**

`LabRatApp.compose()` currently yields a mascot before pushing screens. If this causes nondeterministic snapshots, replace with a deterministic loading/static root or make snapshots target `OnboardingScreen` and `MainScreen` directly.

- [ ] **Step 3: Run targeted snapshots**

```bash
uv run pytest -q tests/tui tests/widgets/test_query_editor.py tests/widgets/test_results_table.py tests/widgets/test_schema_tree.py
```

Expected: failures only where output intentionally changed.

### Task 9.2: Update Snapshots Intentionally

- [ ] **Step 1: Inspect `snapshot_report.html`**

Open the generated report and verify differences are expected from runtime wiring or deterministic storage changes.

- [ ] **Step 2: Update snapshots using the project’s snapshot workflow**

Use the workflow documented by `pytest-textual-snapshot` in this repo. Do not edit raw snapshots by hand unless the framework explicitly expects that.

- [ ] **Step 3: Re-run snapshots**

```bash
uv run pytest -q tests/tui tests/widgets
```

Expected: PASS.

### Task 9.3: Manual TUI QA

- [ ] **Step 1: Follow `TESTING.md` for core flows**

Run:

```bash
uv run labrat
```

Verify:

- default profile selection honors `conn set-default`;
- query execution produces history, audit event, version, result ref;
- pinning a finding uses the current version;
- schema browser shows dbt catalog descriptions if configured;
- memory/validation indicators appear in chat or result surface;
- chart generation refuses mutation statements.

- [ ] **Step 2: Record QA notes**

Append a dated section to `TESTING.md` with exact manual checks run and outcomes.

### Milestone 9 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

Expected: full suite passes. Snapshot report should not be modified after the final passing run.

Commit:

```bash
git add src/labrat/screens src/labrat/widgets tests TESTING.md
git commit -m "test: stabilize TUI snapshots for wired runtime"
```

---

## Milestone 10: End-To-End Depth Eval Suite And Release Gate

**Goal:** A fast eval proves the actual product loop uses history, memory, catalog, validations, and safety together.

**Files:**
- Create: `src/labrat/eval/suites/depth_smoke.py`
- Create: `tests/unit/test_depth_smoke_suite.py`
- Create: `tests/integration/test_depth_smoke_runtime.py`
- Modify: `src/labrat/eval/suites/__init__.py`
- Modify: `README.md`
- Modify: `CLAUDE.md`

### Task 10.1: Define Depth Smoke Cases

- [ ] **Step 1: Create `src/labrat/eval/suites/depth_smoke.py`**

Define cases:

1. **memory_applies_filter**  
   Seed memory: “Revenue queries should filter `status = 'completed'`.”  
   Ask: “show revenue by region.”  
   Assert final SQL includes completed status filter.

2. **catalog_prefers_canonical_model**  
   dbt catalog says `fct_orders` is canonical revenue model.  
   Ask: “show Q4 revenue.”  
   Assert final SQL uses `fct_orders`, not raw staging table.

3. **validation_blocks_bad_result**  
   Validation rule: “Revenue must not be negative.”  
   Fake result summary violates rule.  
   Assert block is surfaced.

4. **mutation_refused_all_paths**  
   Try mutation through agent tool, editor execution, and chart tool.  
   Assert table still exists.

5. **finding_has_provenance**  
   Run query, pin finding, export.  
   Assert finding has version id, result ref, SQL, and export includes them.

Use fake providers/checkers so this suite can run without LLM credentials.

- [ ] **Step 2: Add tests for case enumeration**

`tests/unit/test_depth_smoke_suite.py` should assert the five case ids above.

### Task 10.2: Add Integration Runtime Test

- [ ] **Step 1: Create `tests/integration/test_depth_smoke_runtime.py`**

Use:

- fixture DuckDB from `tests/fixtures/sample_dbs/ecommerce.duckdb`;
- fixture dbt project from `tests/fixtures/sample_dbt_project`;
- temp profile/history/memory/validation/result/audit stores;
- fake provider that calls the expected tools.

Assert each depth smoke case passes.

- [ ] **Step 2: Run**

```bash
uv run pytest -q tests/unit/test_depth_smoke_suite.py tests/integration/test_depth_smoke_runtime.py
```

Expected: PASS.

### Task 10.3: Document The New Definition Of Done

- [ ] **Step 1: Update `CLAUDE.md`**

Add a “Feature Depth Gate” section:

```markdown
## Feature Depth Gate

Before claiming a feature is complete, prove:
- it is called from the main TUI or agent loop;
- state persists in the right local store;
- audit/history/thread/finding behavior is correct where applicable;
- a regression test fails if runtime wiring is removed;
- `uv run pytest -q tests/integration/test_depth_smoke_runtime.py` passes when the feature touches context, memory, catalog, validations, execution, audit, threads, or findings.
```

- [ ] **Step 2: Update `README.md` status claims**

Change “feature-complete” language to a truthful statement after this work lands, for example:

```markdown
Status: v0 alpha, depth pass in progress. Core terminal agent loop, DuckDB flow, safety gates, context, catalog, memory, validations, audit, and findings are wired through the main product path and covered by depth-smoke tests.
```

If all milestones are complete and tests prove depth, the README can later say “feature-complete v0 alpha” again.

### Milestone 10 Acceptance Gate

Run:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q tests/unit/test_depth_smoke_suite.py tests/integration/test_depth_smoke_runtime.py
uv run pytest -q
```

Expected: full suite passes.

Commit:

```bash
git add src/labrat/eval/suites/depth_smoke.py tests README.md CLAUDE.md
git commit -m "test: add end-to-end feature depth smoke suite"
```

---

## Final Integration Checklist

After Milestone 10, run this from repo root:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
uv run pytest -q tests/integration/test_depth_smoke_runtime.py
```

Expected:

- ruff format changes no files;
- ruff check passes;
- pyright reports `0 errors, 0 warnings, 0 informations`;
- full pytest passes, including Textual snapshots;
- depth smoke runtime passes.

Manual QA:

```bash
uv run labrat conn add --name ecommerce-depth --dialect duckdb --path "$(pwd)/tests/fixtures/sample_dbs/ecommerce.duckdb"
uv run labrat conn set-default ecommerce-depth
uv run labrat
```

Verify:

- startup opens the default profile;
- schema browser is populated;
- agent prompt includes visible evidence from context/catalog/memory/validation in trace or debug logs;
- running a query creates history, audit, thread version, and result artifact;
- pinning creates a finding with real `version_id` and `results_ref`;
- export includes the pinned SQL and result preview;
- mutation attempts through editor, agent, and chart paths are refused unless explicitly confirmed for editor execution.

## Success Definition

This plan is complete when these statements are true:

- There is one SQL execution policy shared by agent, editor, and chart paths.
- The active profile controls provider/model and default startup profile.
- The agent prompt includes the user’s relevant context, catalog metadata, memories, and validation rules.
- Onboarding dbt catalog choices persist and influence the session.
- Memory recall is profile-scoped and application counts move when memories are used.
- Validation rules run in the runtime path and surface warnings/blocks.
- Audit log, query history, thread versions, results artifacts, findings, and exports form one provenance chain.
- Schema-qualified table names work across tools and completions.
- Textual snapshots are intentionally updated and green.
- A depth smoke eval fails if any of the above wiring is removed.

## Execution Handoff

Recommended execution mode later:

1. Use `superpowers:subagent-driven-development`.
2. Dispatch one subagent per milestone task group.
3. Review after every milestone before starting the next one.
4. Keep commits milestone-sized and green.

Do not treat this plan as permission to build new product breadth. New features should wait until the depth smoke suite protects the existing product surface.
