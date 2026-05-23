"""CLI entry point for LabRat."""

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
