"""Main Textual application for LabRat."""

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Static

from labrat.branding import get_banner_renderable


class LabRatApp(App[None]):
    """LabRat — terminal-native data agent."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Static(get_banner_renderable("splash"))

    def on_mount(self) -> None:
        """Show onboarding if no profiles are configured."""
        from labrat.profile.manager import ProfileManager

        if not ProfileManager().list_all():
            self._launch_onboarding()

    def _launch_onboarding(self) -> None:
        from labrat.screens.onboarding import OnboardingScreen

        def _on_result(result: object) -> None:
            if result is not None:
                self._save_onboarding_result(result)

        self.push_screen(OnboardingScreen(), _on_result)

    def _save_onboarding_result(self, result: object) -> None:
        from labrat.screens.onboarding import OnboardingResult

        if not isinstance(result, OnboardingResult):
            return
        from labrat.profile.manager import ProfileManager, make_profile

        profile = make_profile(
            name=result.profile_name,
            dialect=result.dialect,
            path=result.path,
            host=result.host,
            port=result.port,
            database=result.database,
            username=result.username,
            has_secret=result.secret is not None,
            description="Created via onboarding",
        )
        try:
            ProfileManager().add(profile, secret=result.secret)
        except Exception:
            pass
