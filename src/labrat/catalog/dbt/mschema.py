"""M-Schema serializer for dbt catalog entries.

Produces the M-Schema format shown to be most effective for SQL generation
(+2.03% EX vs raw DDL on BIRD, per Thinkquel/DAIL-SQL literature).

Format example:
    [DB_ID] ecommerce

    # Table stg_orders
    (order_id, INTEGER, Unique order identifier, PK, [1001, 1002, 1003])
    (customer_id, INTEGER, FK to dim_customers.customer_id, FK)
    (status, VARCHAR, Order fulfillment status, [pending, shipped, delivered])
    (created_at, TIMESTAMP, When the order was placed)

    [Foreign Keys]
    stg_orders.customer_id → dim_customers.customer_id
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from labrat.catalog.base import CatalogEntry


_MAX_SAMPLE_VALUES = 5
_MAX_MODELS = 50  # guard against context overflow for huge projects


def serialize_m_schema(
    entries: dict[str, CatalogEntry],
    db_id: str = "project",
    selected: list[str] | None = None,
    sample_values: dict[str, dict[str, list[Any]]] | None = None,
    max_models: int = _MAX_MODELS,
) -> str:
    """Serialise catalog entries to M-Schema format.

    Args:
        entries:       dict {model_name: CatalogEntry}
        db_id:         identifier shown in [DB_ID] header
        selected:      if given, only include these model names
        sample_values: {model_name: {col_name: [val, ...]}} — optional sample data
        max_models:    truncate at this many models (context-budget guard)

    Returns:
        M-Schema string ready to inject into an agent prompt.
    """
    if sample_values is None:
        sample_values = {}

    names = list(selected) if selected else list(entries)
    names = names[:max_models]

    parts: list[str] = [f"[DB_ID] {db_id}", ""]

    fk_lines: list[str] = []

    for model_name in names:
        entry = entries.get(model_name)
        if entry is None:
            continue

        parts.append(f"# Table {model_name}")

        model_samples = sample_values.get(model_name, {})
        pk_cols = _infer_pk_cols(model_name, entry)
        fk_cols = _infer_fk_cols(model_name, entry, entries)

        for col_name, col in entry.columns.items():
            tags: list[str] = []
            if col_name in pk_cols:
                tags.append("PK")
            fk_target = fk_cols.get(col_name)
            if fk_target:
                tags.append("FK")
                fk_lines.append(f"{model_name}.{col_name} → {fk_target}")

            samples = model_samples.get(col_name, [])
            sample_str = ""
            if samples:
                rendered = [_render_val(v) for v in samples[:_MAX_SAMPLE_VALUES]]
                sample_str = f", [{', '.join(rendered)}]"

            dtype = col.data_type or "VARCHAR"
            desc = col.description or ""
            tag_str = (", " + ", ".join(tags)) if tags else ""

            parts.append(f"({col_name}, {dtype}, {desc}{tag_str}{sample_str})")

        parts.append("")

    if fk_lines:
        parts.append("[Foreign Keys]")
        parts.extend(fk_lines)
        parts.append("")

    return "\n".join(parts).rstrip()


def collect_sample_values(
    entries: dict[str, CatalogEntry],
    connection: Any,
    varchar_only: bool = True,
    max_per_col: int = _MAX_SAMPLE_VALUES,
) -> dict[str, dict[str, list[Any]]]:
    """Query the live DB for sample values of VARCHAR/BOOLEAN columns.

    Args:
        entries:     catalog entries
        connection:  any object with an .execute(sql) -> DataFrame method
        varchar_only: if True, only fetch samples for VARCHAR and BOOLEAN columns
        max_per_col: max distinct values to fetch per column

    Returns:
        {model_name: {col_name: [val, ...]}}
    """
    result: dict[str, dict[str, list[Any]]] = {}
    for model_name, entry in entries.items():
        col_samples: dict[str, list[Any]] = {}
        for col_name, col in entry.columns.items():
            dtype_upper = (col.data_type or "").upper()
            if varchar_only and not any(
                t in dtype_upper for t in ("VARCHAR", "TEXT", "CHAR", "BOOLEAN", "BOOL")
            ):
                continue
            try:
                df = connection.execute(
                    f"SELECT DISTINCT {col_name} FROM {model_name} LIMIT {max_per_col}"
                )
                col_samples[col_name] = [row[0] for row in df.iter_rows()]
            except Exception:
                pass
        if col_samples:
            result[model_name] = col_samples
    return result


# ── helpers ───────────────────────────────────────────────────────────────────


def _infer_pk_cols(model_name: str, entry: CatalogEntry) -> set[str]:
    """Heuristic: columns whose name equals '<model_stem>_id' are likely PKs."""
    stem = model_name.removeprefix("stg_").removeprefix("fct_").removeprefix("dim_")
    candidates = {f"{stem}_id", "id"}
    return {c for c in entry.columns if c in candidates}


def _infer_fk_cols(
    model_name: str,
    entry: CatalogEntry,
    all_entries: dict[str, CatalogEntry],
) -> dict[str, str]:
    """Heuristic FK inference: col named '<other_model>_id' where other_model exists."""
    fks: dict[str, str] = {}
    for col_name in entry.columns:
        if not col_name.endswith("_id"):
            continue
        ref_stem = col_name[:-3]  # strip '_id'
        for candidate in (ref_stem, f"stg_{ref_stem}", f"dim_{ref_stem}", f"fct_{ref_stem}"):
            if candidate in all_entries and candidate != model_name:
                pk_col = f"{candidate}_id"
                fks[col_name] = f"{candidate}.{pk_col}"
                break
    return fks


def _render_val(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return f"'{v}'"
    return str(v)
