"""Domain routing groundwork: extractors resolve and stamp table_scope."""

from datetime import UTC, datetime

from labrat.history.events import QueryEvent
from labrat.memory.extractor import (
    ChatCorrectionExtractor,
    EditExtractor,
    resolve_table_scope,
)

_KNOWN = ["orders", "customers", "products"]


async def _fake_llm(_prompt: str) -> str:
    return "Filter out test orders with status != 'test'."


def test_resolve_single_known_table() -> None:
    assert resolve_table_scope("SELECT * FROM orders WHERE x=1", _KNOWN) == "orders"


def test_resolve_join_of_two_known_tables_is_none() -> None:
    sql = "SELECT * FROM orders o JOIN customers c ON o.cid = c.id"
    assert resolve_table_scope(sql, _KNOWN) is None  # ambiguous → conservative None


def test_resolve_unknown_table_is_none() -> None:
    assert resolve_table_scope("SELECT * FROM staging_tmp", _KNOWN) is None


def test_resolve_unparseable_sql_is_none() -> None:
    assert resolve_table_scope("not sql at all (((", _KNOWN) is None


async def test_chat_extractor_stamps_table_scope() -> None:
    ex = ChatCorrectionExtractor("p1", _fake_llm, known_tables=_KNOWN)
    memories = await ex.extract("no — exclude test orders", "SELECT count(*) FROM orders")
    assert memories and memories[0].table_scope == "orders"


async def test_edit_extractor_stamps_table_scope() -> None:
    ex = EditExtractor("p1", _fake_llm, known_tables=_KNOWN)
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="p1",
        thread_id="t",
        version_id="v",
        sql_final="SELECT * FROM orders WHERE status != 'test'",
        sql_initial="SELECT * FROM orders",
        edit_diff="+ WHERE status != 'test'",
    )
    memories = await ex.extract(event)
    assert memories and memories[0].table_scope == "orders"


async def test_no_known_tables_leaves_scope_none() -> None:
    ex = ChatCorrectionExtractor("p1", _fake_llm)  # default: no catalog
    memories = await ex.extract("fix it", "SELECT * FROM orders")
    assert memories and memories[0].table_scope is None
