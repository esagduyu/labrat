# T2a — MCP Tools Mode Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Profile-backed multi-warehouse connections for the MCP server (`LABRAT_MCP_PROFILES`, all 7 adapters via the existing `make_connection` factory, DuckDB+Postgres launch pair) + host config generators (`python -m labrat.mcp.print_config`) + Tools-Mode docs.

**Architecture:** Extract the server's env parsing into `mcp/config.py::resolve_from_env` (env-JSON path byte-compatible), add the additive profiles path (ProfileManager → make_connection → connect/introspect, safety-first `read_only` derivation, primary's `profile_name`), switch `server.py` onto it with `Connection`-typed lifecycle, and ship pure-function host-config generators + a module CLI. No loop, no ledger — hosts own their loop.

**Tech Stack:** Python 3.12, MCP stdio, Pydantic v2, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`src/labrat/mcp/` strict).

**Spec:** `docs/superpowers/specs/2026-07-09-mcp-tools-mode-design.md` — read before starting.

## Global Constraints

- Branch: `feat/mcp-tools-mode` off master.
- `LABRAT_MCP_CONNECTIONS` byte-compatible: same accepted shapes, same rejection messages, same `sys.exit(2)` semantics — the DAB claude-mcp driver and `tests/unit/test_dab_suite_run_trial.py` pins must be unaffected.
- Secrets never printed/logged: generators emit only explicitly-provided env values; keyring access only inside `make_connection` at server runtime.
- `read_only` derivation safety-first: True unless EVERY mounted source is explicitly writable.
- Server stays loop-less: no ledger/llm_fn/subagent_runner (self-erroring tools stay so; documented).
- All 7 adapters compile through the profiles path; only duckdb (live) + postgres (constructor-level, monkeypatched) are test-exercised.
- Additive: existing MCP tests pass unmodified or are MOVED verbatim (moves listed in reports).
- Pyright strict on `src/labrat/mcp/`. Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known local env flake `tests/tui/test_app_renders.py::test_app_renders` (fails on unmodified master, CI-skipped) — never a regression signal; restore `snapshot_report.html` via `git checkout` if regenerated.

---

## File Structure

- Create: `src/labrat/mcp/config.py`, `src/labrat/mcp/host_configs.py`, `src/labrat/mcp/print_config.py`, `docs/labrat-tools.md`.
- Modify: `src/labrat/mcp/server.py`, `CLAUDE.md` (MCP section note), `decisions.md`.
- Tests: `tests/unit/test_mcp_config.py`, `tests/unit/test_mcp_host_configs.py`; `tests/unit/test_mcp_server.py` keeps its 4 tests (log-writer trio stays; the `:memory:` test may move to `test_mcp_config.py` verbatim if the seam moves — implementer's call, noted).

---

### Task 1: `mcp/config.py` — extract env-JSON parsing (byte-compatible) + missing pins

**Files:**
- Create: `src/labrat/mcp/config.py`
- Modify: `src/labrat/mcp/server.py` (delegate `_build_context_from_env`'s parsing)
- Test: `tests/unit/test_mcp_config.py` (create)

**Interfaces:**
- Consumes: the CURRENT `_build_context_from_env` body (`src/labrat/mcp/server.py:79-130`) — read it in full first; the extraction must preserve every message and exit path verbatim. `DuckDBConnection` (`labrat.db.duckdb_engine`), `Connection` ABC (`labrat.db.base`).
- Produces (Tasks 2–3 rely on):
  - `@dataclass(frozen=True) ResolvedConnections`: `connections: dict[str, Connection]`, `catalogs: dict[str, object]`, `primary: str`, `read_only: bool`, `profile_name: str`.
  - `resolve_from_env(env: Mapping[str, str]) -> ResolvedConnections` — Task 1 scope: env-JSON path only (`LABRAT_MCP_CONNECTIONS` + `LABRAT_MCP_PRIMARY`); `read_only` = True unless every spec set `"read_only": false`; `profile_name="default"`. Missing/empty `LABRAT_MCP_CONNECTIONS` AND no profiles var → same error+exit as today.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_config.py
"""resolve_from_env: byte-compatible env-JSON path + previously-unpinned rejections."""

from pathlib import Path

import pytest

from labrat.mcp.config import ResolvedConnections, resolve_from_env


def _duckdb_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    db = tmp_path / "t.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    env = {"LABRAT_MCP_CONNECTIONS": f'{{"main": {{"db_type": "duckdb", "db_path": "{db}"}}}}'}
    env.update(extra)
    return env


def test_duckdb_env_json_resolves(tmp_path: Path) -> None:
    rc = resolve_from_env(_duckdb_env(tmp_path))
    assert isinstance(rc, ResolvedConnections)
    assert set(rc.connections) == {"main"} and rc.primary == "main"
    assert rc.read_only is True          # spec omitted read_only → safe default
    assert rc.profile_name == "default"
    assert "main" in rc.catalogs


def test_non_duckdb_db_type_rejected(tmp_path: Path) -> None:
    env = {"LABRAT_MCP_CONNECTIONS": '{"pg": {"db_type": "postgres", "db_path": "x"}}'}
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env)
    assert exc.value.code == 2


def test_unknown_primary_rejected(tmp_path: Path) -> None:
    env = _duckdb_env(tmp_path, LABRAT_MCP_PRIMARY="nope")
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env)
    assert exc.value.code == 2


def test_missing_connections_env_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        resolve_from_env({})
    assert exc.value.code == 2


def test_memory_primary_writable(tmp_path: Path) -> None:
    # Ported invariant from test_mcp_server.py: :memory: cannot be read-only.
    env = {"LABRAT_MCP_CONNECTIONS": '{"ws": {"db_type": "duckdb", "db_path": ":memory:"}}'}
    rc = resolve_from_env(env)
    conn = rc.connections["ws"]
    conn.execute("CREATE TABLE t (x INTEGER)")  # type: ignore[attr-defined]
```

Adapt the last test's execute call to the real `DuckDBConnection` API the existing `test_mcp_server.py::test_build_context_from_env_allows_in_memory_primary` uses — read that test and PORT its assertion mechanics verbatim (remember `DuckDBConnection.execute` is SELECT-only; the existing test knows the right call — likely `_connection.execute`).

- [ ] **Step 2: Run to verify FAIL** — `ModuleNotFoundError: labrat.mcp.config`.

- [ ] **Step 3: Implement**

Create `src/labrat/mcp/config.py`: move the parsing logic of `_build_context_from_env` (server.py:79-130) into `resolve_from_env(env)` — same JSON parsing, same duckdb-only guard with the SAME stderr message + `sys.exit(2)`, same `:memory:`-writable forcing, same `LABRAT_MCP_PRIMARY` validation. Additions beyond a pure move: (a) take `env: Mapping[str, str]` instead of reading `os.environ` (testability); (b) compute `read_only = not all(spec.get("read_only") is False for spec in specs.values())` — i.e. True unless EVERY spec explicitly set `false`; (c) return `ResolvedConnections(connections, catalogs, primary, read_only, "default")` with `connections` typed `dict[str, Connection]`.

In `server.py`, `_build_context_from_env` becomes:

```python
def _build_context_from_env() -> tuple[ToolContext, list[Connection]]:
    rc = resolve_from_env(os.environ)
    ctx = ToolContext(
        connections=dict(rc.connections),
        catalogs=dict(rc.catalogs),
        primary=rc.primary,
        read_only=rc.read_only,
        profile_name=rc.profile_name,
    )
    return ctx, list(rc.connections.values())
```

and the lifecycle/disconnect typing widens from `DuckDBConnection` to `Connection` (import from `labrat.db.base`). NOTE this changes served-ctx behavior: the ctx now carries `read_only=True` for specs that didn't opt out — TODAY's server passed no read_only (False default) so mutating tools were open. Check the DAB env builder: `src/labrat/eval/benchmarks/dab/suite.py`'s `LABRAT_MCP_CONNECTIONS` construction — if it does NOT set `"read_only": false`, DAB trials would newly run read-only and `run_program`/`load_file` would start refusing. Grep it; if the DAB specs omit the flag, set the env-JSON default to preserve TODAY'S behavior instead: `read_only=False` when the spec omits the key (matching the current opened-connection semantics server.py:113 uses per-connection), and safety-first True applies ONLY to the profiles path (Task 2). Document which default you shipped and why in the report + a comment; the binding invariant is DAB-byte-compatibility, which outranks the safety-first preference for the legacy env path.

- [ ] **Step 4: Run tests + the existing MCP + DAB suites**

`uv run pytest tests/unit/test_mcp_config.py tests/unit/test_mcp_server.py tests/unit/test_dab_suite_run_trial.py -v` — all green; existing files unmodified (except a verbatim port noted).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/mcp/config.py src/labrat/mcp/server.py tests/unit/test_mcp_config.py
git commit -m "refactor(mcp): extract resolve_from_env (byte-compatible) + pin unwritten invariants"
```

---

### Task 2: Profiles path (`LABRAT_MCP_PROFILES`)

**Files:**
- Modify: `src/labrat/mcp/config.py`
- Test: `tests/unit/test_mcp_config.py` (extend)

**Interfaces:**
- Consumes: `ProfileManager(profiles_path=None).get(name)` (raises `ProfileError` on unknown — `labrat.profile.manager`), `make_profile(...)`, `make_connection(profile) -> Connection` (`manager.py:83` — dispatches all 7 dialects, keyring inside), `Connection.connect()/.introspect_catalog()`.
- Produces: `resolve_from_env` handles `LABRAT_MCP_PROFILES` (comma-separated names; whitespace-tolerant): each → get → make_connection → connect → introspect; key = profile name; collision with env-JSON name → stderr + exit 2; unknown profile → stderr naming it + exit 2. `read_only`: profiles contribute `profile.is_read_only`; combined rule = env-JSON contributions per Task 1's shipped default AND every profile must be explicitly writable for False. `profile_name` = primary's profile name when the primary is profile-backed else `"default"`. For testability: `resolve_from_env` gains kwargs `_manager: object | None = None` and `_connect: Callable[..., Connection] | None = None`? NO — keep the seam honest: use `manager_factory: Callable[[], ProfileManager] = ProfileManager` and `connection_factory: Callable[[Profile], Connection] = make_connection` as defaulted kwargs (explicit, typed, documented as test seams).

- [ ] **Step 1: Write the failing tests** (append)

```python
from labrat.profile.manager import ProfileManager, make_profile


def _mgr_with(tmp_path: Path, *profiles) -> ProfileManager:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    for p in profiles:
        mgr.add(p)
    return mgr


def test_profile_backed_duckdb_resolves(tmp_path: Path) -> None:
    db = tmp_path / "p.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    mgr = _mgr_with(
        tmp_path, make_profile(name="warehouse", dialect="duckdb", path=str(db))
    )
    rc = resolve_from_env(
        {"LABRAT_MCP_PROFILES": "warehouse"}, manager_factory=lambda: mgr
    )
    assert set(rc.connections) == {"warehouse"}
    assert rc.primary == "warehouse"
    assert rc.profile_name == "warehouse"
    assert rc.read_only is True                    # default is_read_only=True
    assert "warehouse" in rc.catalogs


def test_postgres_profile_uses_factory(tmp_path: Path) -> None:
    # Launch-pair coverage without a live PG: assert make_connection would be
    # called with the postgres profile, via the connection_factory seam.
    calls: list[str] = []

    class _FakeConn:
        def connect(self) -> None: ...
        def introspect_catalog(self) -> object:
            return object()
        def disconnect(self) -> None: ...

    def fake_factory(profile) -> object:
        calls.append(f"{profile.name}:{profile.dialect}")
        return _FakeConn()

    mgr = _mgr_with(
        tmp_path,
        make_profile(name="pg", dialect="postgres", host="h", port=5432,
                     database="d", username="u"),
    )
    rc = resolve_from_env(
        {"LABRAT_MCP_PROFILES": "pg"},
        manager_factory=lambda: mgr,
        connection_factory=fake_factory,  # type: ignore[arg-type]
    )
    assert calls == ["pg:postgres"]
    assert set(rc.connections) == {"pg"}


def test_unknown_profile_exits_2(tmp_path: Path) -> None:
    mgr = _mgr_with(tmp_path)
    with pytest.raises(SystemExit) as exc:
        resolve_from_env({"LABRAT_MCP_PROFILES": "ghost"}, manager_factory=lambda: mgr)
    assert exc.value.code == 2


def test_name_collision_exits_2(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    mgr = _mgr_with(tmp_path, make_profile(name="main", dialect="duckdb", path=str(db)))
    env = _duckdb_env(tmp_path)  # env-JSON already defines "main"
    env["LABRAT_MCP_PROFILES"] = "main"
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env, manager_factory=lambda: mgr)
    assert exc.value.code == 2


def test_mixed_sources_and_writable_profile(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    writable = make_profile(
        name="rw", dialect="duckdb", path=str(db)
    ).model_copy(update={"is_read_only": False})
    mgr = _mgr_with(tmp_path, writable)
    env = _duckdb_env(tmp_path)
    env["LABRAT_MCP_PROFILES"] = "rw"
    env["LABRAT_MCP_PRIMARY"] = "rw"
    rc = resolve_from_env(env, manager_factory=lambda: mgr)
    assert set(rc.connections) == {"main", "rw"}
    assert rc.primary == "rw" and rc.profile_name == "rw"
    # combined read_only per the shipped rule — assert the value your Task-1
    # default implies and COMMENT the derivation inline:
    # env-JSON "main" omitted read_only (contributes per Task-1 default) AND
    # profile "rw" is explicitly writable.
```

Finish the last assertion concretely once Task 1's shipped default is known (if env-JSON-omitted contributes False/open: combined False only if profile also writable → assert accordingly; write the exact expected literal, never leave it conditional).

(`make_profile` may not accept `is_read_only` directly — hence the `model_copy(update=...)`; check `manager.py:174-201` and use whichever is real. `ProfileManager.add` signature: check whether it takes `secret=` kwarg — pass nothing.)

- [ ] **Step 2: FAIL** (`TypeError: unexpected keyword 'manager_factory'`), **Step 3: implement** per the Interfaces block (parse `LABRAT_MCP_PROFILES`, iterate names, `manager_factory().get(name)` catching `ProfileError` → stderr + exit 2; `connection_factory(profile)` → `.connect()` → `.introspect_catalog()`; collisions checked before connecting; primary/read_only/profile_name per spec D1/D3), **Step 4: run + gates**, **Step 5: commit**

```bash
git add src/labrat/mcp/config.py tests/unit/test_mcp_config.py
git commit -m "feat(mcp): LABRAT_MCP_PROFILES — profile-backed multi-warehouse connections"
```

---

### Task 3: Server switchover smoke + docstring

**Files:**
- Modify: `src/labrat/mcp/server.py` (docstring env-var reference), `tests/unit/test_mcp_server.py` (extend)

**Interfaces:**
- Consumes: Tasks 1–2 (server already delegates via Task 1's edit).
- Produces: a boot-level pin that the served ctx carries the profiles-path fidelity.

- [ ] **Step 1: Write the failing test** (append to `test_mcp_server.py`)

```python
def test_build_context_profiles_path(tmp_path, monkeypatch) -> None:
    import duckdb

    from labrat.profile.manager import ProfileManager, make_profile

    db = tmp_path / "s.duckdb"
    duckdb.connect(str(db)).close()
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    mgr.add(make_profile(name="served", dialect="duckdb", path=str(db)))
    # Route the module-level default manager at the config seam:
    import labrat.mcp.config as mcp_config

    monkeypatch.setattr(mcp_config, "ProfileManager", lambda: mgr)
    monkeypatch.setenv("LABRAT_MCP_PROFILES", "served")
    monkeypatch.delenv("LABRAT_MCP_CONNECTIONS", raising=False)

    from labrat.mcp.server import _build_context_from_env

    ctx, live = _build_context_from_env()
    assert ctx.profile_name == "served" and ctx.read_only is True
    assert set(ctx.connections) == {"served"} and len(live) == 1
    for conn in live:
        conn.disconnect()
```

(The monkeypatch target assumes `config.py` references `ProfileManager` at module level as the `manager_factory` default — implement Task 2 accordingly, or adjust the patch to the real seam and note it.)

- [ ] **Step 2: FAIL/PASS check** — if Task 2 was implemented with the module-level default, this may pass first-run: then mutation-verify (break the profile_name threading → red → restore).
- [ ] **Step 3:** update `server.py`'s module docstring env-var list (`LABRAT_MCP_PROFILES` documented beside `LABRAT_MCP_CONNECTIONS`, one line each, plus the read-only derivation sentence).
- [ ] **Step 4: gates + commit**

```bash
git add src/labrat/mcp/server.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): served ctx carries profile fidelity; document LABRAT_MCP_PROFILES"
```

---

### Task 4: Host config generators + CLI

**Files:**
- Create: `src/labrat/mcp/host_configs.py`, `src/labrat/mcp/print_config.py`
- Test: `tests/unit/test_mcp_host_configs.py`

**Interfaces:**
- Produces:
  - `build_mcp_server_config(*, profiles: list[str] | None = None, connections_json: str | None = None, primary: str | None = None, log_dir: str | None = None) -> dict[str, object]` — `{"command": "uv", "args": ["run", "python", "-m", "labrat.mcp.server"], "env": {...}}`; env holds ONLY provided values (`LABRAT_MCP_PROFILES` comma-joined / `LABRAT_MCP_CONNECTIONS` verbatim / `LABRAT_MCP_PRIMARY` / `LABRAT_MCP_LOG_DIR`); raises `ValueError` when neither source given.
  - `render_host_config(host, server) -> str`: `"claude-code"`/`"generic"` → `json.dumps({"mcpServers": {"labrat": server}}, indent=2)`; `"codex"` → TOML text: `[mcp_servers.labrat]`, `command = "uv"`, `args = [...]`, `[mcp_servers.labrat.env]` table. Unknown host → `ValueError`.
  - CLI `python -m labrat.mcp.print_config --host X [--profiles a,b] [--connections-json JSON] [--primary P] [--log-dir D]` → stdout config text, exit 0; usage errors → stderr + exit 2.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_host_configs.py
"""Host config generators: exact shapes per host; env only for provided values."""

import json
import subprocess
import sys

import pytest

from labrat.mcp.host_configs import build_mcp_server_config, render_host_config


def test_server_block_env_only_provided() -> None:
    server = build_mcp_server_config(profiles=["w1", "w2"], log_dir="/logs")
    assert server["command"] == "uv"
    assert server["args"] == ["run", "python", "-m", "labrat.mcp.server"]
    assert server["env"] == {
        "LABRAT_MCP_PROFILES": "w1,w2",
        "LABRAT_MCP_LOG_DIR": "/logs",
    }


def test_no_source_raises() -> None:
    with pytest.raises(ValueError):
        build_mcp_server_config()


def test_claude_code_shape_round_trips() -> None:
    server = build_mcp_server_config(profiles=["w"])
    text = render_host_config("claude-code", server)
    parsed = json.loads(text)
    assert parsed["mcpServers"]["labrat"]["env"]["LABRAT_MCP_PROFILES"] == "w"


def test_codex_toml_shape() -> None:
    server = build_mcp_server_config(profiles=["w"])
    text = render_host_config("codex", server)
    assert "[mcp_servers.labrat]" in text
    assert 'command = "uv"' in text
    assert "[mcp_servers.labrat.env]" in text
    assert 'LABRAT_MCP_PROFILES = "w"' in text


def test_unknown_host_raises() -> None:
    with pytest.raises(ValueError):
        render_host_config("cursor", build_mcp_server_config(profiles=["w"]))


def test_cli_smoke() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "labrat.mcp.print_config",
         "--host", "claude-code", "--profiles", "w1"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["mcpServers"]["labrat"]["env"][
        "LABRAT_MCP_PROFILES"] == "w1"


def test_cli_bad_host_exit_2() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "labrat.mcp.print_config", "--host", "vim"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
```

- [ ] **Step 2: FAIL**, **Step 3: implement** (pure functions per the Interfaces block — TOML rendering by string-building, no tomllib dependency for WRITING; escape nothing beyond quotes since inputs are paths/names — document that values containing `"` are rejected with ValueError, one guard line), **Step 4: run + gates**, **Step 5: commit**

```bash
git add src/labrat/mcp/host_configs.py src/labrat/mcp/print_config.py tests/unit/test_mcp_host_configs.py
git commit -m "feat(mcp): host config generators + print_config CLI (claude-code/codex/generic)"
```

---

### Task 5: Docs + finish

**Files:**
- Create: `docs/labrat-tools.md`
- Modify: `CLAUDE.md`, `decisions.md`

- [ ] **Step 1: `docs/labrat-tools.md`** — sections: What Tools Mode is (host owns the loop — honesty note); Quick start (generate a config: the `print_config` command + paste destination per host); Env-var reference (`LABRAT_MCP_CONNECTIONS` legacy JSON, `LABRAT_MCP_PROFILES`, `LABRAT_MCP_PRIMARY`, `LABRAT_MCP_LOG_DIR`, `LABRAT_MAZE_DIR`, read-only derivation rule); Which tools self-error over MCP and why (`llm_extract`/`llm_classify`/`dispatch_subagent` — no in-process loop); Security notes (keyring at runtime; secrets never in configs). Generate the embedded snippets by RUNNING the CLI and pasting real output (note the command used).
- [ ] **Step 2: CLAUDE.md** — in the MCP section, one sentence: profiles path + pointer to docs/labrat-tools.md + the print_config command.
- [ ] **Step 3: decisions.md** —

```markdown
## 2026-07-09 — T2a: MCP Tools Mode hardening

The MCP server gains profile-backed multi-warehouse connections (`LABRAT_MCP_PROFILES` →
ProfileManager/make_connection, all 7 adapters, keyring at runtime; DuckDB+Postgres launch
pair) additively beside the byte-compatible legacy `LABRAT_MCP_CONNECTIONS` (DAB invariant),
threads read_only (safety-first derivation) + profile_name into the served ctx, and ships
host config generators (`python -m labrat.mcp.print_config --host claude-code|codex|generic`)
+ docs/labrat-tools.md. Carved from the deferred full-stack plan's M5 (the TUI-provider tail
was already shipped as TUI-M1). Follow-up: migrate the DAB driver's inline mcp-config dict
onto host_configs.
```

- [ ] **Step 4: Full gates + commit** (`docs: labrat-tools guide + T2a decisions entry`).
- [ ] **Step 5: Manual spot-check** (controller): run the real CLI for each host, eyeball JSON/TOML shape; boot the real server once with `LABRAT_MCP_PROFILES` against a scratch profile (subprocess, expect clean stdio startup or clean exit-2 diagnostics). Then superpowers:finishing-a-development-branch.
