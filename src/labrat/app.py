"""Main Textual application for LabRat."""

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Static

from labrat.branding import ASSETS_DIR


class LabRatApp(App[None]):
    """LabRat — terminal-native data agent."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        mascot = (ASSETS_DIR / "mascot_small.txt").read_text()
        yield Static(mascot, markup=False)

    def on_mount(self) -> None:
        """Show main screen if profiles exist, else show onboarding."""
        from labrat.profile.manager import ProfileManager

        profiles = ProfileManager().list_all()
        if profiles:
            self._connect_and_launch(profiles[0])
        else:
            self._launch_onboarding()

    def _connect_and_launch(self, profile: object) -> None:
        from labrat.db.catalog import Catalog
        from labrat.db.catalog_cache import load_cached_catalog, save_catalog
        from labrat.profile.manager import make_connection
        from labrat.profile.model import Profile

        if not isinstance(profile, Profile):
            self._launch_main()
            return

        conn = make_connection(profile)
        catalog: Catalog | None = None
        connected = False
        try:
            conn.connect()
            connected = True
            catalog = load_cached_catalog(profile.name)
            if catalog is None:
                catalog = conn.introspect_catalog()
                save_catalog(profile.name, catalog)
        except Exception:
            pass

        self._launch_main(
            profile=profile.name,
            dialect=profile.dialect,
            catalog=catalog,
            connection=conn if connected else None,
        )

    def _launch_main(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        catalog: object = None,
        connection: object = None,
    ) -> None:
        from labrat.db.base import Connection
        from labrat.db.catalog import Catalog
        from labrat.screens.main import MainScreen

        self.push_screen(
            MainScreen(
                profile=profile,
                dialect=dialect,
                catalog=catalog if isinstance(catalog, Catalog) else None,
                connection=connection if isinstance(connection, Connection) else None,
            )
        )

    def _launch_onboarding(self) -> None:
        from labrat.screens.onboarding import OnboardingScreen, OnboardingResult

        def _on_result(result: object) -> None:
            if isinstance(result, OnboardingResult):
                self._save_onboarding_result(result)
                from labrat.profile.manager import ProfileManager, ProfileError
                try:
                    profile = ProfileManager().get(result.profile_name)
                    self._connect_and_launch(profile)
                    return
                except ProfileError:
                    pass
            self._launch_main()

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
