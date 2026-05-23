"""Profile CRUD operations."""

from pathlib import Path

from labrat.db.base import Connection
from labrat.profile import storage
from labrat.profile.model import Dialect, Profile


class ProfileError(Exception):
    """Raised for profile management errors."""


class ProfileManager:
    """Manages the collection of connection profiles."""

    def __init__(self, profiles_path: Path | None = None) -> None:
        self._path = profiles_path

    def _load(self) -> dict[str, Profile]:
        return storage.load_all(self._path)

    def _save(self, profiles: dict[str, Profile]) -> None:
        storage.save_all(profiles, self._path)

    def add(self, profile: Profile, secret: str | None = None) -> None:
        """Add a new profile. Raises ProfileError if name already exists."""
        profiles = self._load()
        if profile.name in profiles:
            raise ProfileError(f"Profile '{profile.name}' already exists.")
        profiles[profile.name] = profile
        self._save(profiles)
        if secret is not None:
            storage.store_secret(profile.name, secret)

    def get(self, name: str) -> Profile:
        """Return a profile by name. Raises ProfileError if not found."""
        profiles = self._load()
        if name not in profiles:
            raise ProfileError(f"Profile '{name}' not found.")
        return profiles[name]

    def list_all(self) -> list[Profile]:
        """Return all profiles sorted by name."""
        return sorted(self._load().values(), key=lambda p: p.name)

    def remove(self, name: str) -> None:
        """Remove a profile by name. Also removes its keyring secret."""
        profiles = self._load()
        if name not in profiles:
            raise ProfileError(f"Profile '{name}' not found.")
        del profiles[name]
        self._save(profiles)
        storage.delete_secret(name)

    def test_connection(self, name: str) -> bool:
        """Test a profile's connection. Returns True on success."""
        profile = self.get(name)
        conn = make_connection(profile)
        try:
            conn.connect()
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False
        finally:
            conn.disconnect()


def make_connection(profile: Profile) -> Connection:
    """Instantiate a Connection for the given profile."""
    if profile.dialect == "duckdb":
        from labrat.db.duckdb_engine import DuckDBConnection

        path = profile.path or ":memory:"
        return DuckDBConnection(path=path, read_only=profile.is_read_only)

    raise NotImplementedError(f"Dialect '{profile.dialect}' not yet supported.")


def make_profile(
    name: str,
    dialect: Dialect,
    *,
    path: str | None = None,
    host: str | None = None,
    port: int | None = None,
    database: str | None = None,
    username: str | None = None,
    has_secret: bool = False,
    is_read_only: bool = True,
    default_schema: str | None = None,
    description: str = "",
) -> Profile:
    """Convenience constructor for Profile."""
    return Profile(
        name=name,
        dialect=dialect,
        path=path,
        host=host,
        port=port,
        database=database,
        username=username,
        has_secret=has_secret,
        is_read_only=is_read_only,
        default_schema=default_schema,
        description=description,
    )
