"""One-time setup: load all DAB datasets' PG + Mongo databases.

Walks every `query_<DATASET>/db_config.yaml` in the DAB repo, finds entries with
db_type `postgres` or `mongo`, and loads each idempotently. File-based
DBs (sqlite, duckdb) need no load — they sit in `query_dataset/` already.

Run: uv run python scripts/dab_setup.py [--dab-dir ~/repos/DataAgentBench]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DAB_DIR = Path("~/repos/DataAgentBench").expanduser()


def pg_database_exists(name: str) -> bool:
    """True if a PG database with this name exists."""
    result = subprocess.run(
        [
            "psql",
            "-h",
            "localhost",
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{name}';",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "1"


def pg_load_dataset(name: str, sql_file: Path) -> None:
    """Create PG database `name` and load `sql_file`. Idempotent."""
    if pg_database_exists(name):
        print(f"  [pg] {name}: already loaded, skipping")
        return
    subprocess.run(
        ["psql", "-h", "localhost", "-d", "postgres", "-c", f"CREATE DATABASE {name};"],
        check=True,
    )
    subprocess.run(
        ["psql", "-h", "localhost", "-d", name, "-f", str(sql_file)],
        check=True,
    )
    print(f"  [pg] {name}: loaded from {sql_file}")


def mongo_database_exists(name: str) -> bool:
    """True if a Mongo database with this name has at least one collection."""
    result = subprocess.run(
        ["mongosh", "--quiet", name, "--eval", "db.getCollectionNames().length"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def mongo_load_dataset(name: str, dump_dir: Path) -> None:
    """Restore `dump_dir` into Mongo database `name`. Idempotent."""
    if mongo_database_exists(name):
        print(f"  [mongo] {name}: already loaded, skipping")
        return
    subprocess.run(["mongorestore", "--db", name, str(dump_dir)], check=True)
    print(f"  [mongo] {name}: loaded from {dump_dir}")


def _walk_dataset_configs(dab_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Return (dataset_dir, parsed_config) for each query_<DATASET>/db_config.yaml."""
    result: list[tuple[Path, dict[str, Any]]] = []
    for entry in sorted(dab_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("query_"):
            continue
        config_path = entry / "db_config.yaml"
        if not config_path.exists():
            continue
        config = yaml.safe_load(config_path.read_text())
        if not isinstance(config, dict):
            continue
        result.append((entry, config))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load DAB datasets into local PG + Mongo.")
    parser.add_argument("--dab-dir", type=Path, default=DEFAULT_DAB_DIR)
    args = parser.parse_args(argv)

    dab_dir: Path = args.dab_dir.expanduser()
    if not dab_dir.exists():
        print(f"DAB dir not found: {dab_dir}", file=sys.stderr)
        return 1

    print(f"Loading datasets from {dab_dir}")
    for dataset_dir, config in _walk_dataset_configs(dab_dir):
        clients = config.get("db_clients") or {}
        if not isinstance(clients, dict):
            continue
        print(f"\n{dataset_dir.name}:")
        for client_name, spec in clients.items():
            if not isinstance(spec, dict):
                continue
            db_type = (spec.get("db_type") or "").lower()
            if db_type == "postgres":
                db_name = spec.get("db_name")
                sql_rel = spec.get("sql_file")
                if not db_name or not sql_rel:
                    print(f"  [pg] {client_name}: config missing db_name or sql_file — skipping")
                    continue
                sql_file = dataset_dir / sql_rel
                if not sql_file.exists():
                    print(
                        f"  [pg] {db_name}: dump not found at {sql_file} — skipping",
                        file=sys.stderr,
                    )
                    continue
                pg_load_dataset(db_name, sql_file)
            elif db_type == "mongo":
                db_name = spec.get("db_name")
                dump_rel = spec.get("dump_folder")
                if not db_name or not dump_rel:
                    print(
                        f"  [mongo] {client_name}: config missing db_name or dump_folder — skipping"
                    )
                    continue
                dump_dir = dataset_dir / dump_rel
                if not dump_dir.exists():
                    print(
                        f"  [mongo] {db_name}: dump not found at {dump_dir} — skipping",
                        file=sys.stderr,
                    )
                    continue
                mongo_load_dataset(db_name, dump_dir)
            # sqlite / duckdb need no load step
    return 0


if __name__ == "__main__":
    sys.exit(main())
