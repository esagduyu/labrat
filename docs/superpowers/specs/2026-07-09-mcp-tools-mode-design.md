# T2a — MCP Tools Mode Hardening — Design

**Date:** 2026-07-09 · **Status:** approved (re-scoped from the deferred full-stack-runtime plan M5, user-approved: spec + build both slices)
**Carve:** full-stack plan Milestone 5 minus its "provider-agnostic TUI" tail (already shipped as TUI-M1's `Profile.agent_provider`). Two slices: profile-backed multi-warehouse connections + host config generators. The ledger extraction of June proved this carving pattern; this is its second application.

## 1. Problem

The MCP server — LabRat's Altimate-style adoption wedge ("mount into any host") — is DuckDB-only and benchmark-shaped: `LABRAT_MCP_CONNECTIONS` hard-rejects every non-duckdb `db_type` (`server.py:99-107`), it never touches `labrat.profile` (no keyring, no warehouses), its `ToolContext` omits `read_only`/`profile_name`, and no host-config generator exists (even the DAB driver hand-inlines its `mcpServers` dict). Meanwhile `make_connection(profile)` already dispatches all seven adapters with keyring secrets — the factory just isn't wired to the server.

## 2. Decisions

- **D1 — Profile-backed connections via a new additive env path:** `LABRAT_MCP_PROFILES` (comma-separated profile names). Each resolves through `ProfileManager().get(name)` → `make_connection(profile)` → `.connect()`/`.introspect_catalog()`. Coexists with `LABRAT_MCP_CONNECTIONS` (both may appear; names must not collide → exit 2). The DuckDB env path stays byte-compatible (the DAB invariant; it is the only pinned behavior today).
- **D2 — Widen the server's connection typing** from `DuckDBConnection` to `db.base.Connection` (the concrete-type assumptions at `server.py:94,114-118` are the edit sites). The `:memory:`-writable special case stays DuckDB-path-only.
- **D3 — ctx fidelity:** the ctx now carries `read_only` and `profile_name`, so profile-keyed tools (`recall_memories`, `search_query_history`, Scent retrieval) finally work over MCP. `ToolContext.read_only` is a single flag over all connections; derivation per §3.1.4 (amended): profiles contribute safety-first (any read-only profile → True), the legacy env-JSON path keeps today's open default (omitted → False; DAB byte-compat outranks). `profile_name` = the primary's profile name when profile-backed, else `"default"`.
- **D4 — Host config generators:** new `src/labrat/mcp/host_configs.py` — pure functions emitting the `mcpServers` JSON for `claude-code`, `codex`, and `generic` hosts from explicit inputs (profiles or connections JSON, optional log/maze dirs). CLI: `uv run python -m labrat.mcp.print_config --host claude-code --profiles p1,p2` (module CLI, no packaging changes). The DAB driver is NOT migrated onto it in this build (its inline dict is test-pinned and sandbox-load-bearing; note as follow-up).
- **D5 — Env passthrough completion (M5-S4 remainder):** `LABRAT_MAZE_DIR` already flows implicitly (store resolution); the planned `LABRAT_PROFILE` var is superseded by D1's `LABRAT_MCP_PROFILES`; `LABRAT_RESULT_DIR` is deferred — the MCP path has no ledger because hosts own their loop (documented, not built).
- **D6 — Launch pair = DuckDB + Postgres:** all seven adapters are wired by construction (the factory dispatches), but tests/docs exercise duckdb (real) + postgres (constructor-level, no live server in CI); the other five inherit factory coverage.
- **D7 — Docs:** `docs/labrat-tools.md` — install, host snippets (generated, not hand-written), env-var reference, the "host owns the loop" honesty note, and which tools self-error over MCP (`llm_extract`/`llm_classify`/`dispatch_subagent` — no `llm_fn`/runner) with the reason.

## 3. Design

### 3.1 `src/labrat/mcp/config.py` (new)

`ResolvedConnections` (frozen dataclass): `connections: dict[str, Connection]`, `catalogs: dict[str, object]`, `primary: str`, `read_only: bool`, `profile_name: str`.
`resolve_from_env(env: Mapping[str, str]) -> ResolvedConnections` — pure-ish (keyring/db I/O inside, no globals):
1. Parse `LABRAT_MCP_CONNECTIONS` exactly as today (duckdb-only, same error messages/exit semantics — extracted verbatim from `_build_context_from_env`).
2. Parse `LABRAT_MCP_PROFILES`: for each name, `ProfileManager().get(name)` (unknown → exit 2 with the profile name), `make_connection(profile)`, `.connect()`, `.introspect_catalog()`. Connection key = profile name; collision with an env-JSON name → exit 2.
3. `LABRAT_MCP_PRIMARY` validated against the union (default: first env-JSON name, else first profile).
4. `read_only` (amended 2026-07-09, T1 review — DAB byte-compat outranks safety-first on the legacy path): env-JSON specs contribute False unless EVERY spec explicitly sets `read_only: true` (omitted → False, preserving today's open ctx for DAB); profiles contribute safety-first (any profile with `is_read_only=True` → True). Combined = env-JSON contribution OR profiles contribution. The per-spec flag still governs the DuckDB open mode as today; ctx-level read_only is the tool gate. `profile_name`: the primary's profile name when the primary is profile-backed, else `"default"`.
`server.py::_build_context_from_env` becomes a thin call into `resolve_from_env(os.environ)` + `ToolContext(connections=…, catalogs=…, primary=…, read_only=…, profile_name=…)`; lifecycle disconnect iterates `db.base.Connection`.

### 3.2 `src/labrat/mcp/host_configs.py` (new)

`build_mcp_server_config(*, profiles: list[str] | None = None, connections_json: str | None = None, primary: str | None = None, log_dir: str | None = None) -> dict` — the inner `{command, args, env}` block (command `uv`, args `["run", "python", "-m", "labrat.mcp.server"]`, env only for provided values).
`render_host_config(host: Literal["claude-code", "codex", "generic"], server: dict) -> str` — host-shaped JSON text: claude-code/generic → `{"mcpServers": {"labrat": server}}` (stdout is pure config text for every host — the once-planned stderr preamble was dropped as-built; pipeable output wins. Amended at branch review); codex → the TOML-ish `mcp_servers.labrat` form Codex uses (`command`/`args`/`env` tables).
`src/labrat/mcp/print_config.py` — argparse CLI: `--host`, `--profiles`, `--connections-json`, `--primary`, `--log-dir`; prints to stdout; exit 2 on bad host/no source.

### 3.3 Tests

`tests/unit/test_mcp_config.py`: env-JSON path byte-compatible (reuse/port the 4 existing pins + add the duckdb-rejection + primary-validation pins that were never written); profiles path with a temp `ProfileManager` (duckdb profile round-trip live; postgres profile → constructor-level assertion via monkeypatched `make_connection` — no live PG in CI); collision + unknown-profile exits; read_only/profile_name derivation matrix. `tests/unit/test_mcp_host_configs.py`: snapshot-style assertions per host; env contains only provided keys; CLI smoke via `python -m` subprocess.

## 4. Non-negotiables

1. `LABRAT_MCP_CONNECTIONS` byte-compatible: same accepted shapes, same rejections, same messages/exit codes (DAB claude-mcp + its pinned tests unaffected).
2. Secrets never printed/logged: host-config generators emit env VAR references only when values were explicitly provided; keyring access happens only inside `make_connection` at server runtime.
3. ctx `read_only` derivation is safety-first ON THE PROFILES PATH (any ambiguity → True); the legacy env-JSON path preserves today's open default (omitted → False) — DAB byte-compat outranks (amended, T1 review).
4. Server remains loop-less: no ledger, no llm_fn, no subagent_runner — self-erroring tools stay self-erroring (documented).
5. All seven adapters compile through the path; only duckdb+postgres are test-exercised (D6).
6. Additive only: existing MCP tests pass unmodified (or are moved verbatim into the new file — moves noted).
7. Pyright strict (`mcp/` is strict); repo gates per commit; known env flake `test_app_renders` non-signal.

## 5. Consumers checked

DAB claude-mcp driver (inline dict untouched; follow-up ticket to migrate onto `host_configs` later); `labrat.mcp.toy` (untouched); TUI (unaffected); README/CLAUDE.md MCP snippet (updated to mention profiles path).

## 6. Testing summary

Unit per §3.3 + a live end-to-end smoke: launch the real server via stdio with `LABRAT_MCP_PROFILES=<temp duckdb profile>` and drive one `list_tables` call through the MCP client machinery if a lightweight harness exists (else: subprocess boots + clean exit pin). Manual spot-check: `print-config --host claude-code` output pasted into a scratch config actually mounts (controller verifies the JSON shape matches Claude Code's schema by inspection; full host-mount test deferred to first real use).
