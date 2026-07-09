"""Session-scoped, LLM-free capture of correction candidates."""

from labrat.memory.correction_buffer import ChatCorrection, CorrectionBuffer


def test_add_chat_and_drain() -> None:
    buf = CorrectionBuffer()
    buf.add_chat("no, exclude refunds", "SELECT sum(amount) FROM orders")
    assert buf.pending_count == 1
    chats, edits = buf.drain()
    assert chats == [ChatCorrection("no, exclude refunds", "SELECT sum(amount) FROM orders")]
    assert edits == []
    assert buf.pending_count == 0


def test_add_edit_builds_query_event_with_diff() -> None:
    buf = CorrectionBuffer()
    recorded = buf.add_edit(
        profile="p1",
        thread_id="t1",
        draft_sql="SELECT * FROM orders",
        executed_sql="SELECT * FROM orders WHERE status != 'test'",
    )
    assert recorded is True
    _, edits = buf.drain()
    assert len(edits) == 1
    ev = edits[0]
    assert ev.profile == "p1"
    assert ev.sql_initial == "SELECT * FROM orders"
    assert ev.sql_final == "SELECT * FROM orders WHERE status != 'test'"
    assert ev.edit_diff and "status != 'test'" in ev.edit_diff
    assert ev.executed is True


def test_identical_sql_records_nothing() -> None:
    buf = CorrectionBuffer()
    recorded = buf.add_edit(
        profile="p1",
        thread_id="t1",
        draft_sql="SELECT 1",
        executed_sql="SELECT 1",
    )
    assert recorded is False
    assert buf.pending_count == 0
