# LabRat Tools Mode — mounting the MCP server into any host

LabRat's data tools (`profile_dataset`, `run_sql`, `search_columns`, `link_schema`,
`verify_join`, `attach_database`, `load_file`, `search_reference_docs`, `workflow`, …) are
also exposed as a standard [MCP](https://modelcontextprotocol.io/) server —
`labrat.mcp.server` — that any MCP-capable host (Claude Code, Codex, Cursor, OpenCode, …) can
mount over stdio. This is "Tools Mode": LabRat contributes the tools, the **host owns the
agent loop**.

## What Tools Mode is (and isn't)

`labrat.mcp.server` starts, resolves one or two connection env vars into a `ToolContext`, and
serves the standard tool registry (`build_data_tools_registry()`) over MCP stdio. It never
runs its own agent loop — the host process (Claude Code, Codex, whatever mounts it) drives the
tool-use round-trips. Concretely, that means:

- **No Context Ledger.** Each tool call's raw output goes straight to the host's context —
  there's no summarization/bounding layer the way there is inside `AgentLoop` or the TUI.
- **No injected `llm_fn`.** Tools that need a one-shot LLM call to do their job
  (`llm_extract`, `llm_classify`) have nothing to call.
- **No `subagent_runner`.** `dispatch_subagent` has no in-process loop to delegate into.
- **No sufficiency verifier.** The MCP path never wraps a "does this answer the question"
  judge around the host's final turn — that's an `AgentLoop`-only feature
  (`run_agent_task(verify=...)`).

This is a deliberate scope boundary, not a gap to be filled later (see `docs/superpowers/specs/2026-07-09-mcp-tools-mode-design.md` D5) — a host mounting an MCP server is, by
definition, running its own loop; giving the server a second, competing loop underneath it
would be the wrong layering. See "Which tools self-error over MCP" below for the concrete
list of tools that detect this and fail structurally instead of silently no-op'ing.

## Quick start

Generate a ready-to-paste host config with the `print_config` CLI — it's a pure formatter (no
I/O, no server startup), so the same command works whether or not you actually have a
database on disk yet.

```bash
uv run python -m labrat.mcp.print_config --host <claude-code|codex|generic> \
    [--profiles p1,p2] [--connections-json '{...}'] [--primary p1] [--log-dir /path]
```

At least one of `--profiles` / `--connections-json` is required (mirrors the server's own "at
least one connection source" requirement). Output goes to stdout only — nothing is written to
disk for you; paste it into your host's own config file.

### Claude Code

```bash
uv run python -m labrat.mcp.print_config --host claude-code \
    --connections-json '{"main":{"db_type":"duckdb","db_path":"/path/to/warehouse.duckdb"}}' \
    --primary main
```

```json
{
  "mcpServers": {
    "labrat": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "labrat.mcp.server"
      ],
      "env": {
        "LABRAT_MCP_CONNECTIONS": "{\"main\":{\"db_type\":\"duckdb\",\"db_path\":\"/path/to/warehouse.duckdb\"}}",
        "LABRAT_MCP_PRIMARY": "main"
      }
    }
  }
}
```

Paste into a project's `.mcp.json`, or feed the `mcpServers.labrat` block directly to `claude
mcp add-json`. Also usable as-is for the DAB `claude-mcp` driver's `--mcp-config` (the driver
hand-inlines its own equivalent dict today — see the follow-up note in `decisions.md`).

### Codex

```bash
uv run python -m labrat.mcp.print_config --host codex \
    --profiles main --log-dir /Users/ege/.labrat/mcp-logs
```

```toml
[mcp_servers.labrat]
command = "uv"
args = ["run", "python", "-m", "labrat.mcp.server"]

[mcp_servers.labrat.env]
LABRAT_MCP_PROFILES = "main"
LABRAT_MCP_LOG_DIR = "/Users/ege/.labrat/mcp-logs"
```

Paste the `[mcp_servers.labrat]` table into `~/.codex/config.toml`.

### Generic (any other JSON-config host)

```bash
uv run python -m labrat.mcp.print_config --host generic --profiles main,warehouse
```

```json
{
  "mcpServers": {
    "labrat": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "-m",
        "labrat.mcp.server"
      ],
      "env": {
        "LABRAT_MCP_PROFILES": "main,warehouse"
      }
    }
  }
}
```

Same `mcpServers` shape as `claude-code` — use it for Cursor, OpenCode, or anything else that
takes the conventional `{"mcpServers": {"<name>": {"command", "args", "env"}}}` block. The
first name in `--profiles`/`--connections-json` becomes the primary connection unless
`--primary` overrides it.

## Env-var reference

The server (`labrat.mcp.server` → `labrat.mcp.config.resolve_from_env`) reads these at
process start. At least one of the first two must be set or the server exits 2 with
`"LABRAT_MCP_CONNECTIONS env var is required (JSON connection spec)."`.

| Var | Shape | Notes |
|---|---|---|
| `LABRAT_MCP_CONNECTIONS` | JSON: `{name: {"db_type": "duckdb", "db_path": "...", "read_only": bool}}` | **Legacy path, duckdb-only** — any other `db_type` exits 2 (`attach_database` from inside a session is the way to reach SQLite/Postgres/MySQL/etc. on this path). This is the byte-compatible path DAB's `claude-mcp` driver depends on; `:memory:` is always forced writable regardless of the spec's `read_only`. |
| `LABRAT_MCP_PROFILES` | comma-separated profile names, e.g. `"main,warehouse"` | Each name resolves through `ProfileManager().get(name)` → `make_connection(profile)` (all seven adapters: DuckDB, Postgres, Snowflake, BigQuery, Redshift, Trino, MySQL) → `.connect()`/`.introspect_catalog()`. Secrets come from the OS keyring at this point — never from the env var itself. A name colliding with an `LABRAT_MCP_CONNECTIONS` entry is a hard error (exit 2), checked before either connection opens. |
| `LABRAT_MCP_PRIMARY` | connection/profile name | Which connection is `ctx.primary` (routes tools with no explicit `database` field). Defaults to the first `LABRAT_MCP_CONNECTIONS` name if any, else the first `LABRAT_MCP_PROFILES` name. Must be a name that actually resolved, or exit 2. |
| `LABRAT_MCP_LOG_DIR` | directory path | Optional. When set, every tool dispatch is appended to `<dir>/mcp_tool_calls.jsonl` (tool name, input, ok/error, output, latency) — the same audit-trace format `claude-mcp`'s DAB driver uses (`append_tool_trace`). Unset = no logging. |
| `LABRAT_MAZE_DIR` | directory path | Optional, not server-specific — read directly by `labrat.maze.store.MazeStore.from_env` (defaults to `cwd`) wherever a tool touches Scent (`search_reference_docs`, `search_trails`, the Cartographer pre-pass). Not generated by `print_config` today; add it to the host config's `env` block by hand if you want the served process to read/write Scent somewhere other than its working directory. |

### Read-only derivation (`ctx.read_only`)

`ToolContext.read_only` — the gate every `mutating=True` tool (`run_sql` writes,
`attach_database`, `load_file`, `run_program`, …) checks — is the **OR of two independently
derived contributions** (amended 2026-07-09, see `resolve_from_env`'s docstring in
`src/labrat/mcp/config.py` for the full rationale):

- **`LABRAT_MCP_CONNECTIONS` contribution:** `False` unless **every** entry in the spec
  explicitly sets `"read_only": true`. Omitting the key defaults to `False` (open) — this
  preserves the legacy/DAB behavior byte-for-byte; DAB's spec never sets the key, so DAB stays
  writable. An empty/absent env-JSON spec also contributes `False` (nothing to opt in).
- **`LABRAT_MCP_PROFILES` contribution:** safety-first — `True` if **any** resolved profile
  has `is_read_only=True` (the `Profile` default). One read-only warehouse in the mix makes
  the whole served ctx read-only.
- **Combined:** `env_json_contribution OR profiles_contribution`.

This is a distinct concept from the **per-connection DuckDB open-mode flag** (also called
`read_only` in the `LABRAT_MCP_CONNECTIONS` spec) — that one governs how the on-disk `.duckdb`
file itself is opened at the driver level (defaults to `True` unless the path is `:memory:`)
and is unrelated to the tool-level gate above.

## Which tools self-error over MCP, and why

Because the MCP server never runs its own `AgentLoop` (see "What Tools Mode is" above), three
tools detect the missing capability at dispatch time and return a structured `ok=False` result
instead of silently doing nothing or crashing the process:

| Tool | Requires | Self-error message |
|---|---|---|
| `llm_extract` | `ctx.llm_fn` (per-row LLM extraction) | *"llm_extract requires an LLM-enabled context (no llm_fn is injected on this path). Use run_sql string functions (regexp_extract, string_split, ...) instead."* |
| `llm_classify` | `ctx.llm_fn` (per-row LLM classification) | *"llm_classify requires an LLM-enabled context (no llm_fn is injected on this path). Use run_sql CASE/string expressions instead."* |
| `dispatch_subagent` | `ctx.subagent_runner` (scoped sub-agent loop) | *"dispatch_subagent unavailable: no subagent runner on this host (requires an in-process AgentLoop provider)"* |

All three are only wired on the `labrat-agent` driver (`AgentLoop` via `run_agent_task`) and
the TUI (`agent/session.py::build_agent_session`), which inject `llm_fn`/`subagent_runner`
onto the `ToolContext` they build. `claude-mcp` and any other MCP-mounted host — including
this one — leave both `None` by construction, so these three tools fail loud and structured
rather than fail silent. Every other tool in the registry is fully deterministic (no LLM call
inside the tool itself) and works identically over MCP.

## Security notes

- **Secrets are never written into a generated config.** `print_config`/`host_configs.py`
  only emit the env var *names and values you explicitly pass* — `LABRAT_MCP_PROFILES` carries
  profile *names*, never passwords/tokens; `LABRAT_MCP_CONNECTIONS` (duckdb-only) has no
  credential fields to leak in the first place.
- **Keyring access happens only inside `make_connection`, at server runtime**, not during
  config generation. A profile's secret (password/token) is stored in the OS keyring under
  service `"labrat"`, key `"<profile_name>.secret"` (`src/labrat/profile/storage.py`), and is
  only fetched when the MCP server process actually connects — the generated config file on
  disk never contains it.
- Non-secret profile fields (name, dialect, host, connection params) live in
  `~/.local/share/labrat/profiles.json`, managed via `labrat.profile.manager.ProfileManager`.
