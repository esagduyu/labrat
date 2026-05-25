"""Persistent profile storage.

Non-secret fields are stored as JSON at ~/.local/share/labrat/profiles.json.
Secrets (passwords, tokens) are stored in the OS keyring under the service
name "labrat" with the key "{profile_name}.secret".
"""

import json
from pathlib import Path

import keyring

from labrat.profile.model import Profile

_SERVICE = "labrat"
_DATA_DIR = Path.home() / ".local" / "share" / "labrat"
_PROFILES_FILE = _DATA_DIR / "profiles.json"


def _profiles_path() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILES_FILE


def load_all(profiles_path: Path | None = None) -> dict[str, Profile]:
    """Load all profiles from disk. Returns an empty dict if no file exists."""
    path = profiles_path or _profiles_path()
    if not path.exists():
        return {}
    parsed = json.loads(path.read_text())
    # Support both the current list format and the legacy {profiles: [...]} format.
    if isinstance(parsed, dict):
        raw: list[dict[str, object]] = parsed.get("profiles", [])  # type: ignore[assignment]
    else:
        raw = parsed
    return {p["name"]: Profile.model_validate(p) for p in raw}  # type: ignore[index]


def save_all(profiles: dict[str, Profile], profiles_path: Path | None = None) -> None:
    """Persist all profiles to disk."""
    path = profiles_path or _profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [p.model_dump() for p in profiles.values()]
    path.write_text(json.dumps(data, indent=2))


def store_secret(profile_name: str, secret: str) -> None:
    """Store a secret in the OS keyring."""
    from keyring.errors import NoKeyringError  # pyright: ignore[reportUnknownMemberType]

    try:
        keyring.set_password(_SERVICE, f"{profile_name}.secret", secret)
    except NoKeyringError:  # pyright: ignore[reportUnknownVariableType]
        pass


def load_secret(profile_name: str) -> str | None:
    """Retrieve a secret from the OS keyring."""
    from keyring.errors import NoKeyringError  # pyright: ignore[reportUnknownMemberType]

    try:
        return keyring.get_password(_SERVICE, f"{profile_name}.secret")
    except NoKeyringError:  # pyright: ignore[reportUnknownVariableType]
        return None


def delete_secret(profile_name: str) -> None:
    """Delete a secret from the OS keyring (best-effort)."""
    from keyring.errors import (  # pyright: ignore[reportUnknownMemberType]
        NoKeyringError,
        PasswordDeleteError,
    )

    try:
        keyring.delete_password(_SERVICE, f"{profile_name}.secret")
    except (PasswordDeleteError, NoKeyringError):  # pyright: ignore[reportUnknownVariableType]
        pass
