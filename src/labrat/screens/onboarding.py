"""Onboarding wizard screen for LabRat.

Walks new users through: welcome → dialect → credentials → test connection
→ external catalog (optional) → finish.
"""

from pathlib import Path
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Center, Middle, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from labrat.branding import ASSETS_DIR
from labrat.profile.model import Dialect

_DIALECTS: list[tuple[str, Dialect]] = [
    ("DuckDB (local file or in-memory)", "duckdb"),
    ("PostgreSQL", "postgres"),
    ("Snowflake", "snowflake"),
    ("BigQuery", "bigquery"),
    ("Redshift", "redshift"),
    ("Trino / Presto", "trino"),
    ("MySQL", "mysql"),
]

_STEP_LABELS = ["welcome", "dialect", "credentials", "catalog", "finish"]


class OnboardingResult:
    """Result returned by the onboarding screen on success."""

    profile_name: str
    dialect: Dialect
    path: str | None
    host: str | None
    port: int | None
    database: str | None
    username: str | None
    secret: str | None
    catalog_type: str | None
    catalog_path: str | None

    def __init__(
        self,
        profile_name: str,
        dialect: Dialect,
        *,
        path: str | None = None,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        username: str | None = None,
        secret: str | None = None,
        catalog_type: str | None = None,
        catalog_path: str | None = None,
    ) -> None:
        self.profile_name = profile_name
        self.dialect = dialect
        self.path = path
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.secret = secret
        self.catalog_type = catalog_type
        self.catalog_path = catalog_path


class _StepIndicator(Static):
    """Step progress indicator: ●─●─○─○─○  step N of M: label."""

    def __init__(self, step: int, total: int, label: str) -> None:
        super().__init__(id="step-indicator")
        self._step = step
        self._total = total
        self._label = label

    def render(self) -> str:
        dots = "─".join("●" if i <= self._step else "○" for i in range(self._total))
        return f"{dots}    step {self._step + 1} of {self._total}: {self._label}"


class OnboardingScreen(Screen[OnboardingResult | None]):
    """Multi-step first-run wizard."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "back", "Back / Skip"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._dialect: Dialect = "duckdb"
        self._profile_name = "default"
        self._path: str | None = None
        self._host: str | None = None
        self._port: int | None = None
        self._database: str | None = None
        self._username: str | None = None
        self._secret: str | None = None
        self._catalog_type: str | None = None
        self._catalog_path: str | None = None
        self._dbt_detected: Path | None = _detect_dbt()

    def compose(self) -> ComposeResult:
        yield from self._step_widgets()

    # ── step composition ─────────────────────────────────────────────────────

    def _step_widgets(self) -> ComposeResult:
        yield _StepIndicator(self._step, len(_STEP_LABELS), _STEP_LABELS[self._step])
        if self._step == 0:
            yield from self._compose_welcome()
        elif self._step == 1:
            yield from self._compose_dialect()
        elif self._step == 2:
            yield from self._compose_credentials()
        elif self._step == 3:
            yield from self._compose_catalog()
        else:
            yield from self._compose_finish()

    def _compose_welcome(self) -> ComposeResult:
        mascot = (ASSETS_DIR / "mascot_small.txt").read_text()
        yield Center(
            Middle(
                Static(mascot, id="mascot", markup=False),
                Label("[bold cyan]L  A  B  R  A  T[/bold cyan]", id="banner"),
                Label(""),
                Label("[dim]Find the cheese in your maze.[/dim]", id="tagline"),
                Label(""),
                Button("Press Enter to begin", id="btn-continue", variant="primary"),
                Label("Press Esc to skip", id="skip-hint"),
            )
        )

    def _compose_dialect(self) -> ComposeResult:
        yield Center(
            Middle(
                Label("Pick the database you'll connect to.", id="prompt"),
                Label(""),
                RadioSet(
                    *[RadioButton(label, id=f"dialect-{code}") for label, code in _DIALECTS],
                    id="dialect-set",
                ),
                Label(""),
                Button("Continue", id="btn-continue", variant="primary"),
                Button("Back", id="btn-back", variant="default"),
            )
        )

    def _compose_credentials(self) -> ComposeResult:
        fields: list[Input] = self._credential_fields()
        yield Center(
            Vertical(
                Label(f"Connect to {self._dialect.upper()}", id="prompt"),
                Label(""),
                Input(placeholder="Profile name", id="inp-name", value=self._profile_name),
                *fields,
                Label(""),
                Button("Continue", id="btn-continue", variant="primary"),
                Button("Back", id="btn-back", variant="default"),
            )
        )

    def _credential_fields(self) -> list[Input]:
        if self._dialect == "duckdb":
            return [Input(placeholder="Path (leave blank for in-memory)", id="inp-path")]
        return [
            Input(placeholder="Host", id="inp-host"),
            Input(placeholder="Port", id="inp-port"),
            Input(placeholder="Database", id="inp-database"),
            Input(placeholder="Username", id="inp-username"),
            Input(placeholder="Password", id="inp-secret", password=True),
        ]

    def _compose_catalog(self) -> ComposeResult:
        prefill = str(self._dbt_detected) if self._dbt_detected else ""
        hint = "(detected from cwd ✓)" if self._dbt_detected else ""
        yield Center(
            Vertical(
                Label("External catalog? (optional)", id="prompt"),
                Label("Improves accuracy. Skip to set up later."),
                Label(""),
                RadioSet(
                    RadioButton("I have a dbt project", id="catalog-dbt"),
                    RadioButton("Skip — set up later", id="catalog-skip"),
                    id="catalog-set",
                ),
                Label(""),
                Input(placeholder="Path to dbt project", id="inp-catalog-path", value=prefill),
                Label(hint, id="dbt-hint"),
                Label(""),
                Button("Continue", id="btn-continue", variant="primary"),
                Button("Back", id="btn-back", variant="default"),
            )
        )

    def _compose_finish(self) -> ComposeResult:
        yield Center(
            Middle(
                Label("All done! LabRat is ready.", id="prompt"),
                Label(f"Profile: {self._profile_name}  ({self._dialect})"),
                Label(""),
                Button("Start LabRat", id="btn-continue", variant="primary"),
            )
        )

    # ── navigation ───────────────────────────────────────────────────────────

    async def _advance(self) -> None:
        self._step = min(self._step + 1, len(_STEP_LABELS) - 1)
        await self._reload_step()

    async def _go_back(self) -> None:
        if self._step == 0:
            self.dismiss(None)
            return
        self._step -= 1
        await self._reload_step()

    async def _reload_step(self) -> None:
        """Remove all current content and mount the widgets for the new step."""
        await self.query("*").remove()
        await self.mount(*list(self._step_widgets()))

    # ── button handlers ──────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-continue")
    async def handle_continue(self) -> None:
        if self._step == 1:
            self._collect_dialect()
        elif self._step == 2:
            self._collect_credentials()
        elif self._step == 3:
            self._collect_catalog()

        if self._step < len(_STEP_LABELS) - 1:
            await self._advance()
        else:
            self.dismiss(self._make_result())

    @on(Button.Pressed, "#btn-back")
    async def handle_back(self) -> None:
        await self._go_back()

    async def action_back(self) -> None:
        await self._go_back()

    # ── data collection ──────────────────────────────────────────────────────

    def _collect_dialect(self) -> None:
        try:
            radio_set = self.query_one("#dialect-set", RadioSet)
            if radio_set.pressed_button and radio_set.pressed_button.id:
                code = radio_set.pressed_button.id.replace("dialect-", "")
                self._dialect = code  # type: ignore[assignment]
        except Exception:
            pass

    def _collect_credentials(self) -> None:
        def val(selector: str) -> str | None:
            try:
                return self.query_one(selector, Input).value.strip() or None
            except Exception:
                return None

        name_val = val("#inp-name")
        if name_val:
            self._profile_name = name_val
        if self._dialect == "duckdb":
            self._path = val("#inp-path")
        else:
            self._host = val("#inp-host")
            port_str = val("#inp-port")
            self._port = int(port_str) if port_str and port_str.isdigit() else None
            self._database = val("#inp-database")
            self._username = val("#inp-username")
            self._secret = val("#inp-secret")

    def _collect_catalog(self) -> None:
        try:
            radio_set = self.query_one("#catalog-set", RadioSet)
            if radio_set.pressed_button and radio_set.pressed_button.id == "catalog-dbt":
                path_val = self.query_one("#inp-catalog-path", Input).value.strip()
                if path_val:
                    self._catalog_type = "dbt"
                    self._catalog_path = path_val
        except Exception:
            pass

    def _make_result(self) -> OnboardingResult:
        return OnboardingResult(
            profile_name=self._profile_name,
            dialect=self._dialect,
            path=self._path,
            host=self._host,
            port=self._port,
            database=self._database,
            username=self._username,
            secret=self._secret,
            catalog_type=self._catalog_type,
            catalog_path=self._catalog_path,
        )


def _detect_dbt() -> Path | None:
    """Return the cwd path to dbt_project.yml if found, else None."""
    candidate = Path.cwd() / "dbt_project.yml"
    return candidate if candidate.exists() else None
