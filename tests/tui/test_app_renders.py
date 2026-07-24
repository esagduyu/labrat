"""TUI tests: app boot and snapshot."""

from collections.abc import Callable

import pytest

from labrat.app import LabRatApp


async def test_app_smoke() -> None:
    """App boots and exits cleanly when 'q' is pressed."""
    async with LabRatApp().run_test() as pilot:
        await pilot.press("q")


def test_app_renders(snap_compare: Callable[..., bool], monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot of the real app's first-run render (no saved profiles → onboarding).

    ``LabRatApp.on_mount`` boots into ``ProfileManager().list_all()[0]`` and
    connects to that profile's live database. Snapshotting that directly bakes
    the developer's ambient profile name and its DB schema into the committed
    SVG, so the snapshot only ever matched the one machine it was captured on
    (and had to be skipped in CI). Forcing an empty profile store makes the app
    take its deterministic first-run path — the onboarding screen — which
    renders identically on every machine, so this now exercises the real
    boot-routing logic and can run in CI like the other TUI snapshots.
    """
    monkeypatch.setattr("labrat.profile.manager.ProfileManager.list_all", lambda self: [])
    assert snap_compare(LabRatApp())
