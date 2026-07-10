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
                    allow_blank=False,
                )
            with Horizontal(classes="row"):
                yield Label("Model")
                yield Input(
                    value=self._profile.agent_model or "",
                    placeholder="claude-sonnet-4-6 (pinned default)",
                    id="model-input",
                )
            with Horizontal(classes="row"):
                yield Label("dbt project")
                yield Input(
                    value=self._profile.dbt_project_path or "",
                    placeholder="path to dbt project root (optional)",
                    id="dbt-path-input",
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
                "agent_provider": provider_value if isinstance(provider_value, str) else "auto",
                "agent_model": model_text or None,
                "harvest_opt_in": self.query_one("#harvest-switch", Switch).value,
                "verify_enabled": self.query_one("#verify-switch", Switch).value,
                "dbt_project_path": (
                    self.query_one("#dbt-path-input", Input).value.strip() or None
                ),
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
