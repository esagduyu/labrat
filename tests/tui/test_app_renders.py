"""TUI tests: app boot and snapshot."""

import os
from collections.abc import Callable

import pytest

from labrat.app import LabRatApp


async def test_app_smoke() -> None:
    """App boots and exits cleanly when 'q' is pressed."""
    async with LabRatApp().run_test() as pilot:
        await pilot.press("q")


@pytest.mark.skipif(bool(os.getenv("CI")), reason="TUI snapshots are environment-specific")
def test_app_renders(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of the initial app state showing the LabRat banner."""
    assert snap_compare(LabRatApp())
