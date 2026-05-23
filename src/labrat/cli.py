"""CLI entry point for LabRat."""

import typer

app = typer.Typer(
    name="labrat",
    help="LabRat — terminal-native data agent. Find the cheese in your maze.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Launch the LabRat TUI."""
    if ctx.invoked_subcommand is None:
        from labrat.app import LabRatApp

        LabRatApp().run()
