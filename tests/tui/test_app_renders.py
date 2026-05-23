"""TUI tests: app boot and snapshot."""

from collections.abc import Callable

from labrat.app import LabRatApp


async def test_app_smoke() -> None:
    """App boots and exits cleanly when 'q' is pressed."""
    async with LabRatApp().run_test() as pilot:
        await pilot.press("q")


def test_app_renders(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of the initial app state showing the LabRat banner."""
    assert snap_compare(LabRatApp())
