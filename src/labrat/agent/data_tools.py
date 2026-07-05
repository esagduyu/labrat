"""Default read-only data-access tool set.

Shared between the standalone ``labrat run-task`` CLI, DAB harness, and any
non-TUI consumer that wants a baseline of "look at the data, write SQL, run it"
tools without the interactive callbacks the TUI registers.
"""

from __future__ import annotations

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolRegistry
from labrat.agent.tools.check_sql import CheckSqlTool
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.agent.tools.explain_lineage import ExplainLineageTool
from labrat.agent.tools.explain_sql import ExplainSqlTool
from labrat.agent.tools.link_schema import LinkSchemaTool
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.agent.tools.llm_extract import LlmExtractTool
from labrat.agent.tools.load_file import LoadFileTool
from labrat.agent.tools.load_mongo_collection import LoadMongoCollectionTool
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.sample_rows import SampleRowsTool
from labrat.agent.tools.search_columns import SearchColumnsTool
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.agent.tools.workflow import WorkflowTool


def build_data_tools_registry() -> ToolRegistry:
    """Return a registry with the standard read-only data-access tools.

    Tools included: search_reference_docs, workflow, profile_dataset, list_tables,
    describe_table, search_columns, link_schema, sample_rows, column_stats,
    run_sql, explain_sql, explain_lineage, verify_join, attach_database, load_file,
    load_mongo_collection, llm_extract, llm_classify.

    llm_extract / llm_classify are per-row LLM primitives: they self-error with a
    structured result whenever ``ctx.llm_fn`` is None (every path except the
    labrat-agent runner, which injects it) — so registering them here adds no LLM
    dependency to deterministic consumers.

    Excluded by design: draft_sql / create_chart (TUI callbacks),
    run_validations / recall_memories / search_query_history (profile-keyed,
    TUI-specific).
    """
    registry = ToolRegistry()
    registry.register(SearchReferenceDocsTool())
    registry.register(WorkflowTool())
    registry.register(ProfileDatasetTool())
    registry.register(ListTablesTool())
    registry.register(DescribeTableTool())
    registry.register(SearchColumnsTool())
    registry.register(LinkSchemaTool())
    registry.register(SampleRowsTool())
    registry.register(ColumnStatsTool())
    registry.register(RunSqlTool())
    registry.register(ExplainSqlTool())
    registry.register(ExplainLineageTool())
    registry.register(VerifyJoinTool())
    registry.register(CheckSqlTool())
    registry.register(AttachDatabaseTool())
    registry.register(LoadFileTool())
    registry.register(LoadMongoCollectionTool())
    registry.register(LlmExtractTool())
    registry.register(LlmClassifyTool())
    return registry
