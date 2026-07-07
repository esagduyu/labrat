# TUI M1 — Chat Through the Real Agent Stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the TUI chat through the same agent wiring the benchmarks use — full tool registry, Context Ledger, injected `llm_fn`, optional verifier — via a shared session factory, plus the settings foundation (Profile fields + SettingsScreen) the later phases need.

**Architecture:** New `agent/session.py` exposes `resolve_provider(profile)` and `build_agent_session(...)`; `run_agent_task` is refactored to call the factory (behavior-preserving). `screens/main.py` builds its registry from `build_data_tools_registry(run_sql_tool=…)` + 5 TUI extras, a multi-DB `ToolContext` with `read_only`, and keeps the returned persistent `AgentLoop` across chat turns. `ChatPanel` drops its dispatch monkey-patch for the loop's first-class `on_tool_call`/`on_status` hooks.

**Tech Stack:** Python 3.12, Textual, Pydantic, pytest (`asyncio_mode = "auto"` — no decorators on async tests), ruff, pyright (strict on `src/labrat/` except `dspy_opt/` and `screens/`).

**Spec:** `docs/superpowers/specs/2026-07-06-tui-integration-design.md` (§3 Phase 0, §4 Phase 1). Read it before starting.

## Global Constraints

- Branch: `feat/tui-m1-agent-stack` off master.
- **`run_agent_task`'s public signature and behavior must not change** — the DAB `labrat-agent` driver and `scripts/run_task.py` depend on it. The existing tests in `tests/unit/test_agent_runner*.py` are the regression net and must pass unmodified.
- Model pinning: any provider construction must pass a model explicitly; the pinned default is `"claude-sonnet-4-6"`. Never let a CLI/provider default fall through.
- Verifier choice is the loop `LLMVerifier` (sufficiency judge). Do NOT import or wire `agent/verification/consensus.py` anywhere in this plan.
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- `screens/` is pyright-strict-exempt; `src/labrat/agent/`, `src/labrat/profile/` are NOT — new code there must be strict-clean.
- Tool `name`/`description`/`input_model` are `@property` methods, never class attributes.
- Tests must not depend on the gitignored `tests/fixtures/sample_dbs/ecommerce.duckdb`; use the `ecommerce_db` fixture from `tests/conftest.py`.

---

## File Structure

- Create: `src/labrat/agent/session.py` (provider resolution + session factory), `src/labrat/agent/prompts/tui_addendum.md`, `src/labrat/screens/settings.py`.
- Modify: `src/labrat/profile/model.py` (4 new fields), `src/labrat/profile/manager.py` (`update()`), `src/labrat/agent/runner.py` (delegate to factory), `src/labrat/agent/data_tools.py` (`run_sql_tool` param), `src/labrat/agent/prompts/__init__.py` (`build_tui_system_prompt`), `src/labrat/agent/tools/recall_memories.py` (profile bug), `src/labrat/app.py` (thread `Profile` through), `src/labrat/screens/main.py` (new wiring + settings binding), `src/labrat/widgets/chat_panel.py` (hooks), `src/labrat/screens/help.py` (new keybinding row), `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_profile_model_settings.py`, `tests/unit/test_profile_manager_update.py`, `tests/unit/test_agent_session.py`, `tests/unit/test_data_tools_registry.py`, `tests/unit/test_tui_prompt.py`, `tests/unit/test_recall_memories_profile.py`, `tests/tui/test_settings_screen.py`, `tests/tui/test_main_screen_agent_wiring.py`, `tests/widgets/test_chat_panel.py` (extend).

---

### Task 1: Profile settings fields

**Files:**
- Modify: `src/labrat/profile/model.py`
- Test: `tests/unit/test_profile_model_settings.py`

**Interfaces:**
- Produces: `Profile.agent_provider: Literal["auto","anthropic","claude-code","openai","codex"]` (default `"auto"`), `Profile.agent_model: str | None` (default `None`), `Profile.harvest_opt_in: bool` (default `False`), `Profile.verify_enabled: bool` (default `False`). Later tasks (3, 8, 10) and plans M3/M4 read these.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_profile_model_settings.py
"""Profile gains agent/harvest/verify settings fields (TUI M1)."""

from labrat.profile.model import Profile


def test_new_fields_have_safe_defaults() -> None:
    p = Profile(name="p1", dialect="duckdb")
    assert p.agent_provider == "auto"
    assert p.agent_model is None
    assert p.harvest_opt_in is False
    assert p.verify_enabled is False


def test_deserializes_legacy_profile_without_new_fields() -> None:
    # A profile serialized before these fields existed must still validate.
    legacy = {"name": "old", "dialect": "duckdb", "path": "/tmp/x.duckdb"}
    p = Profile.model_validate(legacy)
    assert p.agent_provider == "auto"
    assert p.harvest_opt_in is False


def test_fields_round_trip() -> None:
    p = Profile(
        name="p2",
        dialect="duckdb",
        agent_provider="anthropic",
        agent_model="claude-sonnet-4-6",
        harvest_opt_in=True,
        verify_enabled=True,
    )
    again = Profile.model_validate(p.model_dump())
    assert again.agent_provider == "anthropic"
    assert again.verify_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_profile_model_settings.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` (fields don't exist).

- [ ] **Step 3: Add the fields**

In `src/labrat/profile/model.py`, inside `class Profile`, after `description: str = ""`:

```python
    # Agent/TUI settings (all optional + defaulted: legacy serialized profiles
    # missing these keys validate cleanly).
    agent_provider: Literal["auto", "anthropic", "claude-code", "openai", "codex"] = "auto"
    agent_model: str | None = None
    harvest_opt_in: bool = False
    verify_enabled: bool = False
```

(`Literal` is already imported at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_profile_model_settings.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/profile/model.py tests/unit/test_profile_model_settings.py
git commit -m "feat(profile): agent_provider/agent_model/harvest_opt_in/verify_enabled fields"
```

---

### Task 2: `ProfileManager.update()`

**Files:**
- Modify: `src/labrat/profile/manager.py`
- Test: `tests/unit/test_profile_manager_update.py`

**Interfaces:**
- Consumes: `ProfileManager._load()`/`_save()` (existing private helpers), `ProfileError`.
- Produces: `ProfileManager.update(profile: Profile) -> None` — replaces an existing profile by name, raises `ProfileError` if absent, never touches keyring secrets. SettingsScreen (Task 10) calls this.

**Why:** `ProfileManager` has `add` (raises on existing name) and `remove` (deletes the keyring secret) but no `update` — a naive remove+add would destroy the stored secret.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_profile_manager_update.py
"""ProfileManager.update replaces a profile in place without touching secrets."""

from pathlib import Path

import pytest

from labrat.profile.manager import ProfileError, ProfileManager
from labrat.profile.model import Profile


def _mgr(tmp_path: Path) -> ProfileManager:
    return ProfileManager(profiles_path=tmp_path / "profiles.json")


def test_update_replaces_existing_profile(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    mgr.add(Profile(name="p1", dialect="duckdb", path="/tmp/a.duckdb"))
    updated = mgr.get("p1").model_copy(update={"verify_enabled": True})
    mgr.update(updated)
    assert mgr.get("p1").verify_enabled is True


def test_update_unknown_profile_raises(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    with pytest.raises(ProfileError):
        mgr.update(Profile(name="ghost", dialect="duckdb"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_profile_manager_update.py -v`
Expected: FAIL — `AttributeError: 'ProfileManager' object has no attribute 'update'`.

- [ ] **Step 3: Implement `update`**

In `src/labrat/profile/manager.py`, inside `ProfileManager` after `get`:

```python
    def update(self, profile: Profile) -> None:
        """Replace an existing profile by name.

        Raises ProfileError if the name is unknown. Never touches keyring
        secrets (unlike remove(), which deletes them) — settings edits must
        not invalidate stored credentials.
        """
        profiles = self._load()
        if profile.name not in profiles:
            raise ProfileError(f"Profile {profile.name!r} not found")
        profiles[profile.name] = profile
        self._save(profiles)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_profile_manager_update.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/profile/manager.py tests/unit/test_profile_manager_update.py
git commit -m "feat(profile): ProfileManager.update (secret-preserving in-place replace)"
```

---

### Task 3: Fix `RecallMemoriesTool` profile lookup (latent bug)

**Files:**
- Modify: `src/labrat/agent/tools/recall_memories.py`
- Test: `tests/unit/test_recall_memories_profile.py`

**Interfaces:**
- Consumes: `ToolContext.profile_name` (exists; `ToolContext` has NO `profile` attribute).

**Why:** the tool reads `getattr(ctx, "profile", "default")` — `ToolContext` has no `profile` attribute, so recall silently always reads the `"default"` profile's memories. In the rewired chat this must be the active profile.

- [ ] **Step 1: Write the failing test**

Open `src/labrat/agent/tools/recall_memories.py` first and read the `execute` method to see how the store is queried and what the output model is (do not guess — adapt the assertion below to the real output shape; the essential assertion is that the memory stored under profile `"prof-a"` is found when `ctx.profile_name == "prof-a"`).

```python
# tests/unit/test_recall_memories_profile.py
"""RecallMemoriesTool must key off ctx.profile_name, not a nonexistent ctx.profile."""

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.recall_memories import RecallMemoriesTool
from labrat.memory.model import Memory, MemoryKind, MemoryScope
from labrat.memory.store import MemoryStore


async def test_recall_uses_ctx_profile_name(tmp_path: Path, monkeypatch) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    store.append(
        Memory(
            profile="prof-a",
            scope=MemoryScope.global_,
            kind=MemoryKind.explicit_user_rule,
            text="always exclude test orders",
        )
    )
    # Point the tool's module-level singleton at the temp store.
    import labrat.agent.tools.recall_memories as mod

    monkeypatch.setattr(mod, "_memory_store", store)

    tool = RecallMemoriesTool()
    ctx = ToolContext(profile_name="prof-a")
    args = tool.input_model.model_validate({"query": "test orders"})
    out = await tool.execute(ctx, args)
    assert "exclude test orders" in str(out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_recall_memories_profile.py -v`
Expected: FAIL — the tool reads profile `"default"` (empty store file) and returns no memories.

- [ ] **Step 3: Fix the lookup**

In `src/labrat/agent/tools/recall_memories.py`, replace the profile read:

```python
# BEFORE
profile = getattr(ctx, "profile", "default")
# AFTER
profile = ctx.profile_name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_recall_memories_profile.py -v` — PASS.
Also run: `uv run pytest tests/unit -k recall -q` — any existing recall tests must still pass (if one asserted the `"default"` fallback, update it: the fallback is now `ToolContext`'s own default `profile_name="default"`, same observable behavior for a default-constructed ctx).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/recall_memories.py tests/unit/test_recall_memories_profile.py
git commit -m "fix(tools): recall_memories keys off ctx.profile_name (was always 'default')"
```

---

### Task 4: `agent/session.py` — `resolve_provider` + `build_agent_session`

**Files:**
- Create: `src/labrat/agent/session.py`
- Test: `tests/unit/test_agent_session.py`

**Interfaces:**
- Consumes: `AgentLoop.__init__(*, provider, registry, ctx, system="", dialect="duckdb", max_turns=None, max_tool_calls=None, verifier=None, max_verify_rounds=2, ledger=None)`; `provider_llm_fn(provider: Any, *, system: str = "") -> LLMFn` and `LLMVerifier(llm_fn)` from `labrat.agent.verifier`; `ContextLedger(store)` from `labrat.runtime.context_ledger`; `ResultStore(root)` from `labrat.results.store`; `build_provider(name, model, ...)` from `labrat.agent.providers`; `AnthropicProvider(model=...)`, `ClaudeCodeProvider(model=...)`; `Profile` (Task 1 fields).
- Produces (used by Tasks 5, 8 and plans M3/M4):
  - `PINNED_DEFAULT_MODEL = "claude-sonnet-4-6"`
  - `_LLM_FN_SYSTEM: str` (moved here from `runner.py`)
  - `resolve_provider(profile: Profile) -> tuple[ModelProvider, str | None]` — `(provider, degraded_warning)`; warning is a non-None message only when "auto" fell back to claude-code.
  - `build_agent_session(*, ctx, registry, provider, system_prompt="", dialect="duckdb", verify=False, max_verify_rounds=2, enable_ledger=True, ledger_dir=None, max_turns=None, max_tool_calls=None) -> AgentLoop`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_agent_session.py
"""build_agent_session / resolve_provider — the shared TUI+runner factory."""

from pathlib import Path

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.session import PINNED_DEFAULT_MODEL, build_agent_session, resolve_provider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.profile.model import Profile


def test_resolve_auto_prefers_anthropic_when_key_set(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    provider, warning = resolve_provider(Profile(name="p", dialect="duckdb"))
    assert isinstance(provider, AnthropicProvider)
    assert warning is None


def test_resolve_auto_falls_back_to_claude_code_with_warning(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider, warning = resolve_provider(Profile(name="p", dialect="duckdb"))
    assert isinstance(provider, ClaudeCodeProvider)
    assert warning is not None and "degraded" in warning.lower()


def test_resolve_explicit_provider_and_model(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile = Profile(
        name="p", dialect="duckdb", agent_provider="anthropic", agent_model="claude-opus-4-8"
    )
    provider, warning = resolve_provider(profile)
    assert isinstance(provider, AnthropicProvider)
    assert warning is None


def test_pinned_default_model() -> None:
    assert PINNED_DEFAULT_MODEL == "claude-sonnet-4-6"


def test_build_agent_session_injects_llm_fn_and_ledger(tmp_path: Path) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    assert ctx.llm_fn is None
    loop = build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="test system",
        ledger_dir=tmp_path / "ledger",
    )
    assert ctx.llm_fn is not None          # per-row primitives enabled
    assert loop._ledger is not None        # ledger attached
    assert loop._verifier is None          # verify defaults off


def test_build_agent_session_verify_and_no_ledger(tmp_path: Path) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    loop = build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        verify=True,
        enable_ledger=False,
    )
    assert loop._verifier is not None
    assert loop._ledger is None


def test_build_agent_session_respects_caller_llm_fn(tmp_path: Path) -> None:
    async def my_llm(prompt: str) -> str:
        return "x"

    ctx = ToolContext(
        connections={"main": object()}, catalogs={"main": object()}, primary="main", llm_fn=my_llm
    )
    build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        enable_ledger=False,
    )
    assert ctx.llm_fn is my_llm  # caller injection wins
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_session.py -v`
Expected: FAIL — `ModuleNotFoundError: labrat.agent.session`.

- [ ] **Step 3: Implement the module**

```python
# src/labrat/agent/session.py
"""Shared agent-session factory: one place that knows how to wire a real agent.

Used by ``run_agent_task`` (one-shot: benchmarks, scripts/run_task.py) and the
TUI chat path (persistent loop across turns). Building the loop here — llm_fn
injection, Context Ledger, optional verifier — keeps the two paths from
drifting apart, which is exactly what happened before this module existed
(see docs/tui-integration-handoff.md).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from labrat.agent.loop import AgentLoop
from labrat.agent.providers import build_provider
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.verifier import LLMVerifier, provider_llm_fn
from labrat.profile.model import Profile
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger

PINNED_DEFAULT_MODEL = "claude-sonnet-4-6"

# System prompt for the injected per-row llm_fn (llm_extract / llm_classify).
# Kept terse and format-obsessed: each per-row prompt carries its own full
# instructions; this only reinforces the output discipline.
_LLM_FN_SYSTEM = (
    "You are a precise per-row data-extraction engine. Follow the output-format "
    "instructions in each request exactly: reply with ONLY the requested JSON object "
    "or category value — no prose, no markdown fences, no explanation."
)

_DEGRADED_WARNING = (
    "No ANTHROPIC_API_KEY found — using the claude CLI (Max plan). "
    "Tool round-trips are degraded on this path; set an API key for full reliability."
)


def resolve_provider(profile: Profile) -> tuple[ModelProvider, str | None]:
    """Resolve the profile's provider setting to a concrete ModelProvider.

    Returns ``(provider, degraded_warning)``; the warning is non-None only when
    ``"auto"`` had to fall back to the claude CLI. Models are always pinned
    explicitly — a CLI default silently falling through to Opus burns Max-plan
    budget ~5x faster.
    """
    model = profile.agent_model or PINNED_DEFAULT_MODEL
    if profile.agent_provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return AnthropicProvider(model=model), None
        return ClaudeCodeProvider(model=model), _DEGRADED_WARNING
    return build_provider(profile.agent_provider, model), None


def build_agent_session(
    *,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str = "",
    dialect: str = "duckdb",
    verify: bool = False,
    max_verify_rounds: int = 2,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
) -> AgentLoop:
    """Return a fully wired, persistent AgentLoop.

    Wiring performed (mirrors what run_agent_task always did):
      - ``ctx.llm_fn`` injected from the loop's own provider when the caller
        left it None (enables llm_extract/llm_classify; caller injection wins);
      - ContextLedger attached when ``enable_ledger`` (durable at ``ledger_dir``
        or a per-call temp dir);
      - optional LLMVerifier (the sufficiency judge — NOT consensus).

    The caller owns the loop lifecycle: run once (run_agent_task) or keep it
    across turns (TUI chat — ``loop.history`` accumulates).
    """
    if ctx.llm_fn is None:
        ctx.llm_fn = provider_llm_fn(provider, system=_LLM_FN_SYSTEM)

    ledger: ContextLedger | None = None
    if enable_ledger:
        root = ledger_dir if ledger_dir is not None else Path(tempfile.mkdtemp(prefix="labrat-ledger-"))
        ledger = ContextLedger(ResultStore(root))

    verifier: LLMVerifier | None = None
    if verify:
        verifier = LLMVerifier(provider_llm_fn(provider))

    return AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system=system_prompt,
        dialect=dialect,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        verifier=verifier,
        max_verify_rounds=max_verify_rounds,
        ledger=ledger,
    )
```

Note: `loop._ledger` / `loop._verifier` are private but the tests may assert them (test-only introspection of wiring; acceptable and already done elsewhere in the suite). If pyright complains in tests, add `# type: ignore[reportPrivateUsage]` on those lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_session.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/session.py tests/unit/test_agent_session.py
git commit -m "feat(agent): session factory — resolve_provider + build_agent_session"
```

---

### Task 5: Refactor `run_agent_task` onto the factory (behavior-preserving)

**Files:**
- Modify: `src/labrat/agent/runner.py`

**Interfaces:**
- Consumes: `build_agent_session`, `_LLM_FN_SYSTEM` from Task 4.
- Produces: `run_agent_task` — **identical public signature and behavior**. `AgentTaskResult` unchanged.

- [ ] **Step 1: Refactor**

Rewrite the body of `run_agent_task` in `src/labrat/agent/runner.py` to delegate. Delete the local `_LLM_FN_SYSTEM` constant, the llm_fn/ledger/verifier blocks, and the `AgentLoop` construction; keep the docstring, signature, `AgentTaskResult`, and timing. Resulting body:

```python
from labrat.agent.session import build_agent_session  # top of file, with other imports


async def run_agent_task(
    *,
    prompt: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
    verify: bool = False,
    max_verify_rounds: int = 2,
    on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
) -> AgentTaskResult:
    # (keep the existing docstring verbatim)
    text_parts: list[str] = []

    def on_text(text: str) -> None:
        text_parts.append(text)

    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt=system_prompt,
        verify=verify,
        max_verify_rounds=max_verify_rounds,
        enable_ledger=enable_ledger,
        ledger_dir=ledger_dir,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
    )
    t0 = time.monotonic()
    await loop.run(prompt, on_text=on_text, on_tool_call=on_tool_call)
    latency = time.monotonic() - t0

    return AgentTaskResult(
        final_text="".join(text_parts),
        tool_calls=loop.tool_calls_used,
        latency_seconds=latency,
    )
```

Remove now-unused imports from `runner.py` (`tempfile`, `LLMVerifier` deferred import, `ContextLedger`, `ResultStore`, `provider_llm_fn` — check with ruff). Note one intentional subtlety disappears: the old code passed `system=system_prompt` where empty string meant "use build_system_prompt(dialect)" — preserved identically since `build_agent_session` forwards `system_prompt` to `AgentLoop(system=...)` the same way.

- [ ] **Step 2: Run the full existing runner/ledger/llm_fn test files unmodified**

Run: `uv run pytest tests/unit/test_agent_runner.py tests/unit/test_agent_runner_ledger.py tests/unit/test_agent_runner_llm_fn.py tests/unit/test_verifier.py -v`
Expected: ALL PASS with zero test-file changes. If any fail, the refactor changed behavior — fix `session.py`/`runner.py`, never the tests.

- [ ] **Step 3: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/runner.py
git commit -m "refactor(agent): run_agent_task delegates to build_agent_session (behavior-preserving)"
```

---

### Task 6: `build_data_tools_registry(run_sql_tool=…)`

**Files:**
- Modify: `src/labrat/agent/data_tools.py`
- Test: `tests/unit/test_data_tools_registry.py` (create or extend if it exists — check first with `ls tests/unit | grep data_tools`)

**Interfaces:**
- Produces: `build_data_tools_registry(include_program: bool = True, *, run_sql_tool: RunSqlTool | None = None) -> ToolRegistry` — when `run_sql_tool` is given, that instance is registered instead of a bare `RunSqlTool()`. Task 8 passes the TUI's callback-wired instance.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_data_tools_registry.py  (append if the file exists)
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.run_sql import RunSqlTool


def test_registry_uses_injected_run_sql_instance() -> None:
    calls: list[str] = []
    custom = RunSqlTool(on_draft=lambda sql: calls.append(sql))
    registry = build_data_tools_registry(run_sql_tool=custom)
    run_sql = next(t for t in registry.tools if t.name == "run_sql")
    assert run_sql is custom


def test_registry_default_run_sql_unchanged() -> None:
    registry = build_data_tools_registry()
    names = {t.name for t in registry.tools}
    assert "run_sql" in names and "run_program" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_data_tools_registry.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'run_sql_tool'`.

- [ ] **Step 3: Implement**

In `src/labrat/agent/data_tools.py`:

```python
def build_data_tools_registry(
    include_program: bool = True, *, run_sql_tool: RunSqlTool | None = None
) -> ToolRegistry:
```

and replace `registry.register(RunSqlTool())` with:

```python
    registry.register(run_sql_tool if run_sql_tool is not None else RunSqlTool())
```

Extend the docstring: `run_sql_tool` lets the TUI supply its callback-wired instance (`on_result`/`on_draft`); all other consumers keep the bare default.

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/unit/test_data_tools_registry.py -v` — PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/data_tools.py tests/unit/test_data_tools_registry.py
git commit -m "feat(agent): build_data_tools_registry accepts an injected run_sql instance"
```

---

### Task 7: TUI system-prompt addendum

**Files:**
- Create: `src/labrat/agent/prompts/tui_addendum.md`
- Modify: `src/labrat/agent/prompts/__init__.py`
- Test: `tests/unit/test_tui_prompt.py`

**Interfaces:**
- Consumes: `build_system_prompt(dialect)`, `_read(...)` (existing package-resource reader in `prompts/__init__.py`).
- Produces: `build_tui_system_prompt(dialect: str) -> str` = `build_system_prompt(dialect) + "\n\n" + tui_addendum.md`. Task 8 uses it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tui_prompt.py
from labrat.agent.prompts import build_system_prompt, build_tui_system_prompt


def test_tui_prompt_is_base_plus_addendum() -> None:
    base = build_system_prompt("duckdb")
    tui = build_tui_system_prompt("duckdb")
    assert tui.startswith(base)
    assert "draft_sql" in tui and "create_chart" in tui
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tui_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_tui_system_prompt'`.

- [ ] **Step 3: Create the addendum + builder**

`src/labrat/agent/prompts/tui_addendum.md`:

```markdown
## Interactive TUI session

You are running inside the LabRat TUI. Extra UI-connected tools are available:

- `draft_sql` — propose SQL into the user's editor WITHOUT executing it. Use it
  when the user asks you to "write" or "draft" a query, or when a statement is
  risky enough that the user should review before running.
- `run_sql` — executes and ALSO renders the result table in the results pane.
  Do not re-print large result tables in your prose; summarize and refer to the
  results pane instead.
- `create_chart` — renders a chart in the results pane. Prefer it over ASCII
  tables when the user asks to "show", "plot", or "visualize" a trend.
- `run_validations`, `recall_memories`, `search_query_history` — profile-scoped
  helpers; consult memories and history before re-deriving known facts.

Answers should stay conversational and short: the UI shows your tool activity,
so narrate findings, not mechanics.
```

In `src/labrat/agent/prompts/__init__.py` add:

```python
def build_tui_system_prompt(dialect: str) -> str:
    """Base system prompt plus TUI-specific tool guidance."""
    return f"{build_system_prompt(dialect)}\n\n{_read('tui_addendum.md')}"
```

Check how the package ships `.md` files (`pyproject.toml` package-data / hatch include); `system_base.md` already ships from this directory so `tui_addendum.md` follows the same mechanism — verify by running the test from an installed context (plain pytest suffices here since `_read` uses `importlib.resources`).

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_tui_prompt.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/prompts/ tests/unit/test_tui_prompt.py
git commit -m "feat(prompts): TUI addendum + build_tui_system_prompt"
```

---

### Task 8: Rewire `MainScreen.on_mount` through the factory (+ thread `Profile` through `app.py`)

**Files:**
- Modify: `src/labrat/app.py`, `src/labrat/screens/main.py`
- Test: `tests/tui/test_main_screen_agent_wiring.py`

**Interfaces:**
- Consumes: everything from Tasks 1, 4, 6, 7: `Profile` fields, `resolve_provider`, `build_agent_session`, `build_data_tools_registry(run_sql_tool=…)`, `build_tui_system_prompt`.
- Produces: `MainScreen.__init__(..., profile_obj: Profile | None = None)`; `MainScreen._agent_loop` (the persistent loop), `MainScreen._provider` (M3 harvest needs it for `provider_llm_fn`). `LabRatApp._launch_main(..., profile_obj=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_main_screen_agent_wiring.py
"""Phase-1 wiring: TUI chat registry is a superset of the benchmark registry,
ToolContext carries read_only/profile, and the loop is factory-built."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.agent.data_tools import build_data_tools_registry
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen
from labrat.widgets.chat_panel import ChatPanel


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _connected_screen(ecommerce_db: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    profile = Profile(name="testprof", dialect="duckdb", path=str(ecommerce_db))
    return MainScreen(
        profile="testprof",
        dialect="duckdb",
        catalog=catalog,
        connection=conn,
        profile_obj=profile,
    )


async def test_chat_registry_superset_and_ctx_wiring(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # deterministic provider path
    screen = _connected_screen(ecommerce_db)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop is not None
        tui_names = {t.name for t in loop._registry.tools}
        bench_names = {t.name for t in build_data_tools_registry().tools}
        assert bench_names <= tui_names                      # benchmark superset
        for extra in ("draft_sql", "create_chart", "run_validations",
                      "recall_memories", "search_query_history"):
            assert extra in tui_names
        ctx = loop._ctx
        assert ctx.profile_name == "testprof"
        assert ctx.read_only is True                          # from profile.is_read_only
        assert ctx.primary == "main" and "main" in ctx.connections
        assert ctx.llm_fn is not None                         # factory injected
        assert loop._ledger is not None                       # ledger attached


async def test_mount_without_connection_still_works() -> None:
    # Existing default-construction path (used by all current TUI tests) must not break.
    async with _Host(MainScreen()).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#chat-pane") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_main_screen_agent_wiring.py -v`
Expected: FAIL — `TypeError: MainScreen.__init__() got an unexpected keyword argument 'profile_obj'`.

- [ ] **Step 3: Thread the Profile through `app.py`**

In `src/labrat/app.py::_connect_and_launch`, pass the object through:

```python
        self._launch_main(
            profile=profile.name,
            dialect=profile.dialect,
            catalog=catalog,
            connection=conn if connected else None,
            profile_obj=profile,
        )
```

And `_launch_main` gains the parameter and forwards it:

```python
    def _launch_main(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        catalog: object = None,
        connection: object = None,
        profile_obj: object = None,
    ) -> None:
        from labrat.db.base import Connection
        from labrat.db.catalog import Catalog
        from labrat.profile.model import Profile
        from labrat.screens.main import MainScreen

        self.push_screen(
            MainScreen(
                profile=profile,
                dialect=dialect,
                catalog=catalog if isinstance(catalog, Catalog) else None,
                connection=connection if isinstance(connection, Connection) else None,
                profile_obj=profile_obj if isinstance(profile_obj, Profile) else None,
            )
        )
```

- [ ] **Step 4: Rewire `MainScreen`**

In `src/labrat/screens/main.py`:

(a) `__init__` gains `profile_obj: Profile | None = None` (add `from labrat.profile.model import Profile` to the `TYPE_CHECKING` block; annotate as `"Profile | None"`), stored as `self._profile_obj = profile_obj`. Also add `self._agent_loop = None` and `self._provider = None` initializers.

(b) Replace the agent-wiring block in `on_mount` (everything from the tool imports at current lines ~257–320) with:

```python
        from rich.text import Text

        from labrat.agent.data_tools import build_data_tools_registry
        from labrat.agent.prompts import build_tui_system_prompt
        from labrat.agent.session import build_agent_session, resolve_provider
        from labrat.agent.tools.base import ToolContext
        from labrat.agent.tools.create_chart import CreateChartTool
        from labrat.agent.tools.draft_sql import DraftSqlTool
        from labrat.agent.tools.recall_memories import RecallMemoriesTool
        from labrat.agent.tools.run_sql import RunSqlTool
        from labrat.agent.tools.run_validations import RunValidationsTool
        from labrat.agent.tools.search_query_history import SearchQueryHistoryTool
        from labrat.profile.model import Profile
        from labrat.widgets.chat_panel import ChatPanel
        from labrat.widgets.query_editor import QueryEditor

        editor = self.query_one("#editor-content", QueryEditor)
        table = self.query_one("#results-content", ResultsTable)
        chart_log = self.query_one("#chart-content", RichLog)

        def on_draft(sql: str) -> None:
            editor.load_text(sql)
            self._last_sql = sql

        def on_result(df: pl.DataFrame, elapsed_ms: float) -> None:
            table.load(df, execution_time=elapsed_ms)
            table.display = True
            chart_log.display = False

        def on_chart(chart_str: str) -> None:
            chart_log.clear()
            chart_log.write(Text.from_ansi(chart_str))
            chart_log.display = True
            table.display = False

        profile_obj = self._profile_obj or Profile(
            name=self._profile if self._profile != "—" else "default",
            dialect=self._dialect if self._dialect != "—" else "duckdb",
        )

        registry = build_data_tools_registry(
            run_sql_tool=RunSqlTool(on_result=on_result, on_draft=on_draft)
        )
        registry.register(DraftSqlTool(on_draft=on_draft))
        registry.register(CreateChartTool(on_chart=on_chart))
        registry.register(RunValidationsTool())
        registry.register(RecallMemoriesTool())
        registry.register(SearchQueryHistoryTool())

        catalogs: dict[str, object] = {}
        if self._catalog is not None:
            catalogs["main"] = self._catalog
        ctx = ToolContext(
            connections={"main": self._connection},
            catalogs=catalogs,
            primary="main",
            profile_name=profile_obj.name,
            read_only=profile_obj.is_read_only,
        )

        provider, degraded_warning = resolve_provider(profile_obj)
        if degraded_warning:
            self.notify(degraded_warning, severity="warning", timeout=8)
        self._provider = provider

        import time as _time
        from pathlib import Path as _Path

        ledger_dir = (
            _Path.home() / ".labrat" / "ledger" / profile_obj.name / str(int(_time.time()))
        )
        loop = build_agent_session(
            ctx=ctx,
            registry=registry,
            provider=provider,
            system_prompt=build_tui_system_prompt(
                self._dialect if self._dialect != "—" else "duckdb"
            ),
            dialect=self._dialect if self._dialect != "—" else "duckdb",
            verify=profile_obj.verify_enabled,
            enable_ledger=True,
            ledger_dir=ledger_dir,
        )
        self._agent_loop = loop
        self.query_one("#chat-content", ChatPanel).set_agent_loop(loop)
```

Keep the SQL-autocomplete wiring that follows (`SQLCompleter`) unchanged. Delete the now-unused per-tool imports (ListTablesTool etc. — they come from the shared registry now). `build_tui_system_prompt` raises `ValueError` on an unknown dialect — the `"—"`→`"duckdb"` fallbacks above cover the disconnected/default cases; MySQL etc. are all in `SUPPORTED_DIALECTS`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/tui/test_main_screen_agent_wiring.py tests/tui/test_main_screen.py -v`
Expected: new tests PASS; **all existing main-screen tests (incl. snapshots) PASS**. If a snapshot changed, something visual regressed — investigate; this task must be visually inert.

- [ ] **Step 6: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/app.py src/labrat/screens/main.py tests/tui/test_main_screen_agent_wiring.py
git commit -m "feat(tui): chat runs on the real agent stack (full registry + ledger + llm_fn)"
```

---

### Task 9: ChatPanel — first-class `on_tool_call`/`on_status` hooks

**Files:**
- Modify: `src/labrat/widgets/chat_panel.py`
- Test: `tests/widgets/test_chat_panel.py` (extend)

**Interfaces:**
- Consumes: `AgentLoop.run(user_message, *, on_text=None, on_status=None, on_tool_call=None)`; `on_tool_call` args are `(name: str, input: dict, ok: bool, output: str, latency_ms: float)`.
- Produces: trace lines rendered on tool **completion** (`▸ name({...}) ✓ 320ms` / `✗`), dim status lines from `on_status`. `ChatPanel.AgentToolCall` message still posted (arg shape unchanged). M4 extends these same hooks for the provenance footer.

- [ ] **Step 1: Write the failing test**

Read `tests/widgets/test_chat_panel.py` first to reuse its existing host-app/fake-loop pattern. Add:

```python
class _FakeLoop:
    """Drives the ChatPanel contract: run(msg, on_text=, on_status=, on_tool_call=)."""

    def __init__(self) -> None:
        self.received_kwargs: set[str] = set()

    async def run(self, message, *, on_text=None, on_status=None, on_tool_call=None):
        self.received_kwargs = {
            k for k, v in
            {"on_text": on_text, "on_status": on_status, "on_tool_call": on_tool_call}.items()
            if v is not None
        }
        if on_tool_call:
            on_tool_call("run_sql", {"query": "SELECT 1"}, True, '{"ok": true}', 12.5)
        if on_status:
            on_status("verifier: insufficient — missing filter")
        if on_text:
            on_text("done")


async def test_chat_panel_uses_first_class_hooks() -> None:
    # Host app pattern: mount a ChatPanel, wire the fake loop, submit a message.
    async with _PanelHost().run_test() as pilot:
        panel = pilot.app.query_one(ChatPanel)
        loop = _FakeLoop()
        panel.set_agent_loop(loop)
        await pilot.click("#user-input")
        await pilot.press(*"hi", "enter")
        await pilot.pause()
        assert loop.received_kwargs == {"on_text", "on_status", "on_tool_call"}
        assert "run_sql" in panel.transcript          # trace line landed
        assert "verifier" in panel.transcript         # status line landed
        assert "done" in panel.transcript
```

(`_PanelHost` = the file's existing tiny `App` that yields a `ChatPanel`; reuse or create it matching the file's current pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/widgets/test_chat_panel.py -v`
Expected: new test FAILS (`received_kwargs == {"on_text"}`; no trace/status lines).

- [ ] **Step 3: Implement**

In `ChatPanel._start_agent`, delete the whole monkey-patch block (`orig_dispatch` … `finally: self._agent_loop._registry.dispatch = orig_dispatch`) and call the loop with all three hooks:

```python
        def on_tool_call(
            name: str, args: dict[str, Any], ok: bool, output: str, latency_ms: float
        ) -> None:
            self.post_message(ChatPanel.AgentToolCall(name=name, args=args))
            args_str = json.dumps(args, separators=(",", ":"))
            mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
            tool_line = f"[dim]▸[/dim] [bold]{name}[/bold]({args_str}) {mark} [dim]{latency_ms:.0f}ms[/dim]"
            self._append_history(tool_line, f"▸ {name}({args_str})", is_trace=True)

        def on_status(text: str) -> None:
            self._append_history(f"[dim italic]{text}[/dim italic]", text, is_trace=True)

        _agent_error: Exception | None = None
        try:
            await self._agent_loop.run(
                message, on_text=on_text, on_status=on_status, on_tool_call=on_tool_call
            )
        except Exception as e:
            _agent_error = e
        finally:
            ...  # existing finally block unchanged
```

Long `args_str` can flood the pane — truncate: `args_str = args_str if len(args_str) <= 120 else args_str[:117] + "…"` before building `tool_line`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/widgets/test_chat_panel.py -v` — ALL PASS (fix any older test that asserted the pre-dispatch trace timing; completion-timing is the new contract).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/chat_panel.py tests/widgets/test_chat_panel.py
git commit -m "feat(tui): ChatPanel uses first-class on_tool_call/on_status hooks"
```

---

### Task 10: SettingsScreen (`ctrl+comma`)

**Files:**
- Create: `src/labrat/screens/settings.py`
- Modify: `src/labrat/screens/main.py` (binding + action), `src/labrat/screens/help.py` (Session section row)
- Test: `tests/tui/test_settings_screen.py`

**Interfaces:**
- Consumes: `Profile` (Task 1), `ProfileManager.update` (Task 2), `ConfirmScreen`-style modal conventions, `MemoriesViewerScreen`'s `DEFAULT_CSS`/compose pattern.
- Produces: `SettingsScreen(ModalScreen[Profile | None])` — dismisses with the updated `Profile` on save (so `MainScreen` can refresh `self._profile_obj`), `None` on cancel. Constructor: `SettingsScreen(profile: Profile, manager: ProfileManager | None = None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_settings_screen.py
"""SettingsScreen: toggle profile settings, persist via ProfileManager.update."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static, Switch

from labrat.profile.manager import ProfileManager
from labrat.profile.model import Profile
from labrat.screens.settings import SettingsScreen


class _Host(App[None]):
    def __init__(self, screen: SettingsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: Profile | None = None

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        def _cb(result: Profile | None) -> None:
            self.result = result

        self.push_screen(self._screen, _cb)


async def test_save_persists_toggles(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    profile = Profile(name="p1", dialect="duckdb")
    mgr.add(profile)
    host = _Host(SettingsScreen(profile, manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#harvest-switch", Switch).value = True
        pilot.app.screen.query_one("#verify-switch", Switch).value = True
        await pilot.click("#save-btn")
        await pilot.pause()
    assert host.result is not None and host.result.harvest_opt_in is True
    assert mgr.get("p1").harvest_opt_in is True
    assert mgr.get("p1").verify_enabled is True


async def test_cancel_dismisses_none(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    profile = Profile(name="p1", dialect="duckdb")
    mgr.add(profile)
    host = _Host(SettingsScreen(profile, manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert host.result is None
    assert mgr.get("p1").harvest_opt_in is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_settings_screen.py -v`
Expected: FAIL — `ModuleNotFoundError: labrat.screens.settings`.

- [ ] **Step 3: Implement the screen**

```python
# src/labrat/screens/settings.py
"""SettingsScreen: per-profile agent settings (provider, model, harvest, verify)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

if TYPE_CHECKING:
    from labrat.profile.manager import ProfileManager
    from labrat.profile.model import Profile

_PROVIDER_CHOICES = ["auto", "anthropic", "claude-code", "openai", "codex"]


class SettingsScreen(ModalScreen["Profile | None"]):
    """Edit the active profile's agent settings. Dismisses with the updated
    Profile on save (None on cancel). Provider/model/verify changes take
    effect on the next app start; harvest_opt_in is read live at harvest time."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
    ]

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }
    SettingsScreen > Vertical {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    SettingsScreen .row { height: 3; }
    SettingsScreen Button { margin: 0 1; min-width: 14; }
    SettingsScreen #status { color: $text-muted; }
    """

    def __init__(self, profile: Profile, manager: ProfileManager | None = None) -> None:
        super().__init__()
        self._profile = profile
        self._manager = manager

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"[bold]─ Settings · {self._profile.name} ─[/bold]", id="title", markup=True
            )
            with Horizontal(classes="row"):
                yield Label("Provider")
                yield Select(
                    [(c, c) for c in _PROVIDER_CHOICES],
                    value=self._profile.agent_provider,
                    id="provider-select",
                )
            with Horizontal(classes="row"):
                yield Label("Model")
                yield Input(
                    value=self._profile.agent_model or "",
                    placeholder="claude-sonnet-4-6 (pinned default)",
                    id="model-input",
                )
            with Horizontal(classes="row"):
                yield Label("Harvest corrections (M5)")
                yield Switch(value=self._profile.harvest_opt_in, id="harvest-switch")
            with Horizontal(classes="row"):
                yield Label("Verify answers")
                yield Switch(value=self._profile.verify_enabled, id="verify-switch")
            with Horizontal(id="actions"):
                yield Button("Save", id="save-btn", variant="primary")
                yield Button("Cancel  [Esc]", id="close-btn")
            yield Label("Provider/model/verify apply on next start.", id="status")

    @on(Button.Pressed, "#save-btn")
    def action_save(self) -> None:
        from labrat.profile.manager import ProfileError, ProfileManager

        provider_value = self.query_one("#provider-select", Select).value
        model_text = self.query_one("#model-input", Input).value.strip()
        updated = self._profile.model_copy(
            update={
                "agent_provider": provider_value
                if isinstance(provider_value, str)
                else "auto",
                "agent_model": model_text or None,
                "harvest_opt_in": self.query_one("#harvest-switch", Switch).value,
                "verify_enabled": self.query_one("#verify-switch", Switch).value,
            }
        )
        manager = self._manager if self._manager is not None else ProfileManager()
        try:
            manager.update(updated)
        except ProfileError as exc:
            self.query_one("#status", Label).update(f"[red]{exc}[/red]")
            return
        self.dismiss(updated)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Bind it on MainScreen + help**

`screens/main.py` BINDINGS list, add:

```python
        Binding("ctrl+comma", "open_settings", "Settings", show=True),
```

New action (with the other modal actions):

```python
    def action_open_settings(self) -> None:
        from labrat.screens.settings import SettingsScreen

        if self._profile_obj is None:
            self.notify("No profile loaded — settings unavailable.", severity="warning")
            return

        def _on_result(updated: object) -> None:
            from labrat.profile.model import Profile

            if isinstance(updated, Profile):
                self._profile_obj = updated
                self.notify("Settings saved. Provider/verify apply on next start.", timeout=4)

        self.app.push_screen(SettingsScreen(self._profile_obj), _on_result)
```

`screens/help.py` — append to the `Session` section list:

```python
        ("Ctrl+,", "Settings — provider / harvest / verify toggles"),
```

- [ ] **Step 5: Run tests, gates, commit**

Run: `uv run pytest tests/tui/test_settings_screen.py tests/tui -v` — new tests PASS, existing TUI tests PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/screens/settings.py src/labrat/screens/main.py src/labrat/screens/help.py tests/tui/test_settings_screen.py
git commit -m "feat(tui): SettingsScreen (ctrl+,) — provider/model/harvest/verify per profile"
```

---

### Task 11: TESTING.md manual gate + decisions.md + finish

**Files:**
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: Add the manual-verification section to TESTING.md**

Append (adjust heading level to match the file's existing structure):

```markdown
## M1 — chat through the real agent stack (manual gate)

Setup: `uv run labrat` against a profile pointing at `tests/fixtures/sample_dbs/ecommerce.duckdb`
(or any DuckDB file; create the profile via onboarding). With no ANTHROPIC_API_KEY exported,
expect a one-time "degraded" warning toast (claude CLI fallback).

1. Ask in chat: "profile this dataset" → expect a `profile_dataset` trace line (`▸ profile_dataset(...) ✓`)
   and a structured summary. This tool did not exist in the TUI before M1.
2. Ask: "which tables are relevant to revenue by product category?" → expect `link_schema` and/or
   `search_columns` traces.
3. Ask: "run a query counting orders per status and chart it" → expect `run_sql` trace, results table
   populating, then `create_chart` rendering in the results pane.
4. Ask: "draft (don't run) a query for top customers by spend" → expect `draft_sql` trace and SQL
   appearing in the editor, NOT executed.
5. With a read-only profile, ask the agent to `CREATE TABLE tmp1 AS SELECT 1` → expect the tool
   result to show "blocked: read-only Analyst mode" and the agent to relay the refusal.
6. `ctrl+,` → toggle "Verify answers" on, Save → restart → ask a question → expect a dim
   `verifier: …` status line only if the first answer was judged insufficient (usually none).
7. `ctrl+\` toggles tool-trace lines off/on including the new status lines.
```

- [ ] **Step 2: decisions.md entry**

Append a dated entry:

```markdown
## 2026-07-XX — TUI M1: chat through the real agent stack

The TUI chat path now builds its AgentLoop via `agent/session.py::build_agent_session` — the
same factory `run_agent_task` uses — with `build_data_tools_registry()` + 5 TUI extras (~25 tools),
multi-DB ToolContext (`primary="main"`, `read_only` from the profile), Context Ledger (durable under
`~/.labrat/ledger/<profile>/`), injected `llm_fn`, and an optional sufficiency verifier
(`Profile.verify_enabled`). Provider is per-profile (`agent_provider`, default "auto": Anthropic
with API key, else claude CLI + degraded warning). Spec:
docs/superpowers/specs/2026-07-06-tui-integration-design.md. Consensus verification stays
benchmark-only by design.
```

- [ ] **Step 3: Run the FULL gates one last time**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
```
Expected: all clean, full suite green.

- [ ] **Step 4: Execute the TESTING.md manual gate above yourself if you have a TTY; otherwise STOP and ask the human to run it.** Do not merge without it — `ClaudeCodeProvider` fragility under the bigger registry is exactly the risk unit tests can't catch.

- [ ] **Step 5: Commit + finish the branch**

```bash
git add TESTING.md decisions.md
git commit -m "docs: M1 manual gate + decisions entry"
```

Use superpowers:finishing-a-development-branch (merge to master + push per the finish-branch default; verify CI green after).
