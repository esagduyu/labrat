"""Active connection session for multi-profile switching (M24).

ConnectionSession tracks which profile is active and manages the lifecycle
of its underlying Connection. Threads are scoped to the profile that created
them; assert_thread_scope enforces this invariant.
"""

from __future__ import annotations

from labrat.db.base import Connection
from labrat.profile.manager import make_connection
from labrat.profile.model import Profile


class ConnectionScopeError(Exception):
    """Raised when a thread's profile does not match the active connection."""


class ConnectionSession:
    """Tracks the active profile and manages connection lifecycle.

    One session per LabRat process. Switching profiles closes the previous
    connection (if open) and lazily creates a new one on next access.
    """

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._conn: Connection | None = None

    @property
    def active_profile(self) -> Profile:
        return self._profile

    def get_connection(self) -> Connection:
        """Return (and cache) the Connection for the active profile."""
        if self._conn is None:
            self._conn = make_connection(self._profile)
        return self._conn

    def switch(self, profile: Profile) -> None:
        """Switch to a new profile, closing the current connection if open."""
        if self._conn is not None:
            try:
                self._conn.disconnect()
            except Exception:
                pass
            self._conn = None
        self._profile = profile

    def assert_thread_scope(self, thread_profile_name: str) -> None:
        """Raise ConnectionScopeError if thread's profile differs from active.

        A thread created against profile A must not run queries against profile B.
        """
        if thread_profile_name != self._profile.name:
            raise ConnectionScopeError(
                f"Thread is scoped to profile '{thread_profile_name}', "
                f"but active connection is '{self._profile.name}'. "
                f"Switch to '{thread_profile_name}' before reactivating this thread."
            )
