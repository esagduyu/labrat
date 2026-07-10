"""CLI: print the team-scent status surface (moat extra 2.3, D1).

    uv run python -m labrat.maze.print_status
    uv run python -m labrat.maze.print_status --profile work --db /path/to.duckdb
    uv run python -m labrat.maze.print_status --project-root /path/to/project

Read-only: builds a ``MazeStore`` the same way ``MazeStore.from_env``/
``project_scent_dir`` do (``--project-root`` or ``LABRAT_MAZE_DIR`` or the
cwd), optionally connects a read-only DuckDB database via ``--db`` to derive
a live catalog fingerprint for freshness, and prints ``render_status``.
Usage/connection errors go to stderr and exit 2; success exits 0.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.status import build_status, render_status
from labrat.maze.store import MazeStore, project_scent_dir, user_scent_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m labrat.maze.print_status",
        description="Print the team-scent status surface (read-only).",
    )
    parser.add_argument("--profile", default=None, help="maze profile (default: 'default')")
    parser.add_argument("--db", default=None, help="path to a DuckDB file for freshness checks")
    parser.add_argument(
        "--project-root", default=None, help="project root (default: LABRAT_MAZE_DIR or cwd)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root or os.environ.get("LABRAT_MAZE_DIR") or os.getcwd())
    profile = args.profile or "default"
    store = MazeStore(project_root=project_root, home=Path.home(), profile=profile)

    catalog: Catalog | None = None
    connection: DuckDBConnection | None = None
    if args.db:
        connection = DuckDBConnection(args.db, read_only=True)
        try:
            connection.connect()
            catalog = connection.introspect_catalog()
        except Exception as exc:  # surface any connection/introspection failure as exit 2
            print(f"error: could not connect to {args.db!r}: {exc}", file=sys.stderr)
            return 2
        finally:
            connection.disconnect()

    status = build_status(
        store,
        catalog=catalog,
        user_scent_dir=user_scent_dir(profile),
        project_scent_dir=project_scent_dir(project_root),
    )
    sys.stdout.write(render_status(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
