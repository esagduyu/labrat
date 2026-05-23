"""Catalog JSON cache utilities."""

from pathlib import Path

from labrat.db.catalog import Catalog

_CACHE_DIR = Path.home() / ".local" / "share" / "labrat" / "catalogs"


def catalog_cache_path(profile_name: str) -> Path:
    return _CACHE_DIR / f"{profile_name}.json"


def load_cached_catalog(profile_name: str) -> Catalog | None:
    path = catalog_cache_path(profile_name)
    if not path.exists():
        return None
    try:
        return Catalog.model_validate_json(path.read_text())
    except Exception:
        return None


def save_catalog(profile_name: str, catalog: Catalog) -> None:
    path = catalog_cache_path(profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(catalog.model_dump_json())
