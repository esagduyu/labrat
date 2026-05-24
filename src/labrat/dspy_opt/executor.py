"""dbt execution wrapper for Spider2-DBT tasks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import shutil
import sys

import yaml


def _dbt_binary() -> str:
    """Find the dbt binary: venv first, then PATH."""
    venv_dbt = Path(sys.executable).parent / "dbt"
    if venv_dbt.exists():
        return str(venv_dbt)
    found = shutil.which("dbt")
    if found:
        return found
    raise RuntimeError("dbt not found — install with: uv add dbt-duckdb && uv sync")


class DBTExecutor:
    """Copies a task's dbt project to a work directory, injects SQL, runs dbt."""

    def __init__(self, spider2_dbt_dir: Path, output_base: Path) -> None:
        self.spider2_dbt_dir = spider2_dbt_dir
        self.output_base = output_base
        self.output_base.mkdir(parents=True, exist_ok=True)

    def run_task(
        self,
        instance_id: str,
        model_writes: dict[str, str],
        timeout: int = 180,
    ) -> Path | None:
        """Copy project, inject SQL, run `dbt run`, return path to output DuckDB.

        Args:
            instance_id: Task ID, e.g. "playbook001"
            model_writes: {relative_path: sql_content} files to write before running
            timeout: Max seconds for `dbt run`

        Returns:
            Path to the output .duckdb file, or None on failure.
        """
        src = self.spider2_dbt_dir / "examples" / instance_id
        dst = self.output_base / instance_id

        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

        for rel_path, sql in model_writes.items():
            target = dst / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(sql)

        dbt = _dbt_binary()
        try:
            result = subprocess.run(
                [dbt, "run", "--project-dir", str(dst), "--profiles-dir", str(dst)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=dst,
            )
        except subprocess.TimeoutExpired:
            return None

        if result.returncode != 0:
            return None

        return self._find_output_db(dst)

    def _find_output_db(self, project_dir: Path) -> Path | None:
        profiles_file = project_dir / "profiles.yml"
        if not profiles_file.exists():
            return None

        profiles = yaml.safe_load(profiles_file.read_text())
        for _profile_name, profile in profiles.items():
            for _target_name, cfg in profile.get("outputs", {}).items():
                if cfg.get("type") == "duckdb":
                    db_path = project_dir / cfg["path"]
                    if db_path.exists():
                        return db_path
        return None


def load_project_files(project_dir: Path) -> str:
    """Concatenate all relevant dbt project files into one string for the LM."""
    parts: list[str] = []

    for rel in ["profiles.yml", "dbt_project.yml"]:
        path = project_dir / rel
        if path.exists():
            parts.append(f"=== {rel} ===\n{path.read_text()}")

    models_dir = project_dir / "models"
    if models_dir.exists():
        for ext in ("*.sql", "*.yml", "*.yaml"):
            for f in sorted(models_dir.glob(f"**/{ext}")):
                rel = f.relative_to(project_dir)
                parts.append(f"=== {rel} ===\n{f.read_text()}")

    return "\n\n".join(parts)


def identify_target_file(project_dir: Path, condition_tabs: list[str]) -> str:
    """Return the model file path (relative to project_dir) for the first condition table."""
    models_dir = project_dir / "models"
    for tab in condition_tabs:
        for sql_file in models_dir.glob("**/*.sql"):
            if sql_file.stem == tab:
                return str(sql_file.relative_to(project_dir))
    # Fallback: first SQL model file
    for sql_file in sorted(models_dir.glob("**/*.sql")):
        return str(sql_file.relative_to(project_dir))
    return "models/model.sql"
