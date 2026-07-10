"""CLI entry point for LabRat."""

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    name="labrat",
    help="LabRat — terminal-native data agent. Find the cheese in your maze.",
    no_args_is_help=False,
)

conn_app = typer.Typer(name="conn", help="Manage database connection profiles.")
app.add_typer(conn_app, name="conn")
app.add_typer(conn_app, name="connection")

scent_app = typer.Typer(name="scent", help="Check and refresh dbt-paired Scent.")
app.add_typer(scent_app, name="scent")

_OPT = typer.Option  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
_ARG = typer.Argument  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch the LabRat TUI."""
    if ctx.invoked_subcommand is None:
        from labrat.app import LabRatApp

        LabRatApp().run()


@conn_app.command("add")
def conn_add(
    name: Annotated[str, _OPT("--name", "-n", help="Profile name")],
    dialect: Annotated[str, _OPT("--dialect", "-d", help="Database dialect")],
    path: Annotated[str | None, _OPT("--path", help="DuckDB file path")] = None,
    host: Annotated[str | None, _OPT("--host", help="Database host")] = None,
    port: Annotated[int | None, _OPT("--port", help="Database port")] = None,
    database: Annotated[str | None, _OPT("--database", help="Database name")] = None,
    username: Annotated[str | None, _OPT("--username", "-u", help="Username")] = None,
    read_only: Annotated[bool, _OPT("--read-only/--writable")] = True,
) -> None:
    """Add a new connection profile."""
    from labrat.profile.manager import ProfileError, ProfileManager, make_profile

    valid_dialects = ("duckdb", "postgres", "mysql", "snowflake", "bigquery", "redshift", "trino")
    if dialect not in valid_dialects:
        typer.echo(f"Error: dialect must be one of {valid_dialects}", err=True)
        raise typer.Exit(1) from None

    profile = make_profile(
        name=name,
        dialect=dialect,  # type: ignore[arg-type]
        path=path,
        host=host,
        port=port,
        database=database,
        username=username,
        is_read_only=read_only,
    )
    try:
        ProfileManager().add(profile)
        typer.echo(f"Profile '{name}' added.")
    except ProfileError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@conn_app.command("list")
def conn_list() -> None:
    """List all connection profiles."""
    from labrat.profile.manager import ProfileManager

    profiles = ProfileManager().list_all()
    if not profiles:
        typer.echo("No profiles configured. Use 'labrat conn add' to add one.")
        return
    for p in profiles:
        marker = "[RO]" if p.is_read_only else "[RW]"
        typer.echo(f"  {marker} {p.name}  ({p.dialect})")


@conn_app.command("remove")
def conn_remove(
    name: Annotated[str, _ARG(help="Profile name to remove")],
) -> None:
    """Remove a connection profile."""
    from labrat.profile.manager import ProfileError, ProfileManager

    try:
        ProfileManager().remove(name)
        typer.echo(f"Profile '{name}' removed.")
    except ProfileError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@conn_app.command("test")
def conn_test(
    name: Annotated[str, _ARG(help="Profile name to test")],
) -> None:
    """Test a connection profile."""
    from labrat.profile.manager import ProfileError, ProfileManager

    try:
        ok = ProfileManager().test_connection(name)
        if ok:
            typer.echo(f"Connection '{name}' OK.")
        else:
            typer.echo(f"Connection '{name}' failed.", err=True)
            raise typer.Exit(1) from None
    except ProfileError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@conn_app.command("set-default")
def conn_set_default(
    name: Annotated[str, _ARG(help="Profile name to set as default")],
) -> None:
    """Set the default connection profile."""
    from labrat.profile.manager import ProfileError, ProfileManager

    try:
        ProfileManager().get(name)
        typer.echo(f"Default profile set to '{name}'.")
    except ProfileError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


def _resolve_dbt_project(dbt_project: str | None, profile: str) -> Path:
    """Resolve the dbt project root: explicit flag, else `profile`'s `dbt_project_path`."""
    if dbt_project:
        return Path(dbt_project)

    from labrat.profile.manager import ProfileError, ProfileManager

    path: str | None = None
    try:
        path = ProfileManager().get(profile).dbt_project_path
    except ProfileError:
        path = None

    if not path:
        typer.echo(
            "Error: no --dbt-project given and profile "
            f"{profile!r} has no dbt_project_path configured. "
            "Pass --dbt-project <path> or configure one on the profile.",
            err=True,
        )
        raise typer.Exit(1) from None
    return Path(path)


@scent_app.command("check")
def scent_check(
    dbt_project: Annotated[
        str | None, _OPT("--dbt-project", help="Path to the dbt project root.")
    ] = None,
    scent_dir: Annotated[
        str | None, _OPT("--scent-dir", help="Path to the project Scent directory.")
    ] = None,
    profile: Annotated[
        str, _OPT("--profile", help="Connection profile to resolve --dbt-project from.")
    ] = "default",
    warn_only: Annotated[bool, _OPT("--warn-only", help="Report drift but always exit 0.")] = False,
    skip_if_no_manifest: Annotated[
        bool,
        _OPT("--skip-if-no-manifest", help="Exit 0 (instead of 1) if manifest.json is missing."),
    ] = False,
    output_format: Annotated[
        str, _OPT("--format", help="Output format: 'text' or 'json'.")
    ] = "text",
) -> None:
    """Check whether the committed Scent still matches the committed dbt project.

    Read-only gate — never writes to disk. Intended for CI: run `dbt parse`
    first so `target/manifest.json` exists, then run this against the
    committed `labrat_maze/scent` directory.
    """
    from labrat.maze.ci import check_scent_freshness
    from labrat.maze.store import MazeStore, project_scent_dir

    if output_format not in ("text", "json"):
        typer.echo(f"Error: --format must be 'text' or 'json', got {output_format!r}", err=True)
        raise typer.Exit(1) from None

    project_path = _resolve_dbt_project(dbt_project, profile)
    resolved_scent_dir = Path(scent_dir) if scent_dir else project_scent_dir()
    store = MazeStore.from_env(profile)

    res = check_scent_freshness(project_path, resolved_scent_dir, store=store)

    if output_format == "json":
        typer.echo(res.model_dump_json())
    else:
        if res.ok:
            typer.echo(f"scent check: OK ({res.checked} domain(s) checked)")
        else:
            typer.echo(f"scent check: STALE ({len(res.stale)} stale domain(s))")
            for s in res.stale:
                typer.echo(f"  - {s.domain}: {s.reason}")
        for w in res.warnings:
            typer.echo(f"  warning: {w}")
        if not res.ok:
            typer.echo(f"fix: {res.fix_command}")

    exit_ok = res.ok or warn_only or (not res.manifest_found and skip_if_no_manifest)
    raise typer.Exit(0 if exit_ok else 1)


@scent_app.command("ingest")
def scent_ingest(
    dbt_project: Annotated[
        str | None, _OPT("--dbt-project", help="Path to the dbt project root.")
    ] = None,
    profile: Annotated[
        str, _OPT("--profile", help="Connection profile to resolve --dbt-project from.")
    ] = "default",
) -> None:
    """Ingest the dbt project's semantic layer into Scent — headless fix for `scent check`.

    The only write path in this command group: replaces each affected
    domain's `semantic_layer` section and refreshes the manifest-fingerprint
    sidecar, through the same audited write path as the Cartographer.
    """
    from labrat.maze.ci import catalog_from_dbt
    from labrat.maze.semantic_ingest import ingest_dbt_semantics
    from labrat.maze.store import MazeStore, project_scent_dir

    project_path = _resolve_dbt_project(dbt_project, profile)
    manifest_path = project_path / "target" / "manifest.json"
    catalog = catalog_from_dbt(project_path)
    store = MazeStore.from_env(profile)

    outcome = ingest_dbt_semantics(
        manifest_path=manifest_path,
        catalog=catalog,
        store=store,
        project_scent_dir=project_scent_dir(),
        force=True,
        git_root=Path.cwd(),
    )

    # force=True above means the fingerprint-unchanged/drifted-without-force
    # branch of ingest_dbt_semantics never fires here — outcome.drifted is
    # unreachable on this path. A skip *with* warnings is an error-skip
    # (unreadable/non-dict manifest); a skip with no warnings means no
    # semantic content to ingest, and stays exit 0.
    if outcome.skipped:
        if outcome.warnings:
            typer.echo("scent ingest: skipped (see warnings below)")
        else:
            typer.echo("scent ingest: skipped (no semantic content)")
    else:
        typer.echo(
            f"scent ingest: wrote {outcome.sections_written} section(s) "
            f"across {len(outcome.domains)} domain(s): {', '.join(outcome.domains)}"
        )
    for w in outcome.warnings:
        typer.echo(f"  warning: {w}")

    if outcome.skipped and outcome.warnings:
        raise typer.Exit(1) from None


_INIT_CI_WORKFLOWS: dict[str, str] = {
    "github": """\
name: labrat-scent

on:
  pull_request:
    paths:
      - "models/**"
      - "labrat_maze/**"

jobs:
  scent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dbt + labrat
        run: pip install dbt-core labrat

      - name: dbt parse
        run: dbt parse

      - name: labrat scent check
        run: labrat scent check
""",
}


@scent_app.command("init-ci")
def scent_init_ci(
    platform: Annotated[
        str, _OPT("--platform", help="CI platform to scaffold a workflow for.")
    ] = "github",
    path: Annotated[
        str, _OPT("--path", help="Where to write the workflow file.")
    ] = ".github/workflows/labrat-scent.yml",
) -> None:
    """Scaffold a starter CI workflow that runs `dbt parse` then `labrat scent check`.

    No-clobber: refuses to overwrite an existing file at --path. See
    docs/dbt-ci-pairing.md for the full setup + fix-a-failure walkthrough.
    """
    if platform not in _INIT_CI_WORKFLOWS:
        typer.echo(
            f"Error: --platform must be one of {tuple(_INIT_CI_WORKFLOWS)}, got {platform!r}",
            err=True,
        )
        raise typer.Exit(1) from None

    target = Path(path)
    if target.exists():
        typer.echo(
            f"Error: {target} already exists — not overwriting. "
            "Remove it first, or pass a different --path.",
            err=True,
        )
        raise typer.Exit(1) from None

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_INIT_CI_WORKFLOWS[platform], encoding="utf-8")
    typer.echo(f"wrote {target}")
