"""Default read-only data-access tool set.

Shared between the standalone ``labrat run-task`` CLI, DAB harness, and any
non-TUI consumer that wants a baseline of "look at the data, write SQL, run it"
tools without the interactive callbacks the TUI registers.
"""

from __future__ import annotations

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolRegistry
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.agent.tools.explain_sql import ExplainSqlTool
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.load_mongo_collection import LoadMongoCollectionTool
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.sample_rows import SampleRowsTool
from labrat.agent.tools.search_columns import SearchColumnsTool


def build_data_tools_registry() -> ToolRegistry:
    """Return a registry with the standard read-only data-access tools.

    Tools included: list_tables, describe_table, search_columns, sample_rows,
    column_stats, run_sql, explain_sql, attach_database, load_mongo_collection.

    Excluded by design: draft_sql / create_chart (TUI callbacks),
    run_validations / recall_memories / search_query_history (profile-keyed,
    TUI-specific).
    """
    registry = ToolRegistry()
    registry.register(ListTablesTool())
    registry.register(DescribeTableTool())
    registry.register(SearchColumnsTool())
    registry.register(SampleRowsTool())
    registry.register(ColumnStatsTool())
    registry.register(RunSqlTool())
    registry.register(ExplainSqlTool())
    registry.register(AttachDatabaseTool())
    registry.register(LoadMongoCollectionTool())
    return registry
