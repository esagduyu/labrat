"""ContextBundle: structured personal domain context (M29).

Serialized to ~/.local/share/labrat/context/{profile}.json.
Slots for external catalog data (dbt, MCP) are included but left None
until M30 fills them in.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

_DEFAULT_CONTEXT_DIR = Path.home() / ".local" / "share" / "labrat" / "context"


class TableContext(BaseModel):
    """Per-table context derived from query history and optional catalog data."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    schema_name: str = "public"
    description: str = ""
    frequent_columns: list[str] = []
    common_filters: list[str] = []
    common_joins: list[str] = []
    row_count_estimate: int | None = None
    catalog_description: str | None = None


class ContextBundle(BaseModel):
    """Complete personal context bundle for one profile.

    Loaded at session start and injected into the agent's system prompt.
    External catalog slots (dbt_models, mcp_descriptions) are filled by M30.
    """

    model_config = ConfigDict(frozen=False)

    profile: str
    generated_at: datetime
    tables: list[TableContext] = []
    idioms: list[str] = []
    dbt_models: dict[str, Any] | None = None
    mcp_descriptions: dict[str, str] | None = None

    def to_prompt_snippet(self, top_k: int = 8) -> str:
        """Return a compact system-prompt section summarising the top-K tables."""
        lines = ["## Your personal database context\n"]
        for t in self.tables[:top_k]:
            lines.append(f"### {t.schema_name}.{t.table_name}")
            if t.description:
                lines.append(t.description)
            if t.frequent_columns:
                lines.append(f"**Often used columns:** {', '.join(t.frequent_columns)}")
            if t.common_filters:
                lines.append("**Common filters:**")
                for f in t.common_filters:
                    lines.append(f"  - {f}")
            if t.common_joins:
                lines.append("**Common joins:**")
                for j in t.common_joins:
                    lines.append(f"  - {j}")
            lines.append("")
        if self.idioms:
            lines.append("## Common patterns")
            for idiom in self.idioms:
                lines.append(f"- {idiom}")
        return "\n".join(lines)

    def save(self, context_dir: Path | None = None) -> Path:
        path = (context_dir or _DEFAULT_CONTEXT_DIR) / f"{self.profile}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
        return path

    @classmethod
    def load(cls, profile: str, context_dir: Path | None = None) -> ContextBundle | None:
        path = (context_dir or _DEFAULT_CONTEXT_DIR) / f"{profile}.json"
        if not path.exists():
            return None
        return cls.model_validate_json(path.read_text())
