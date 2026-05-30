from labrat.agent.tools.base import ToolContext
from tests.fixtures.fake_catalog import FakeCatalog
from tests.fixtures.fake_connection import FakeConnection


def test_tool_context_legacy_single_db_construction() -> None:
    """Legacy construction: ctx.connection / ctx.catalog work as before."""
    conn = FakeConnection("duckdb")
    cat = FakeCatalog()
    ctx = ToolContext(connection=conn, catalog=cat)
    assert ctx.connection is conn
    assert ctx.catalog is cat


def test_tool_context_legacy_preserves_profile_name() -> None:
    conn = FakeConnection("duckdb")
    cat = FakeCatalog()
    ctx = ToolContext(connection=conn, catalog=cat, profile_name="my_profile")
    assert ctx.profile_name == "my_profile"


def test_tool_context_multi_db_construction() -> None:
    """New construction: connections/catalogs dict with explicit primary."""
    conns = {"main": FakeConnection("duckdb"), "books": FakeConnection("postgres")}
    cats = {"main": FakeCatalog(), "books": FakeCatalog()}
    ctx = ToolContext(connections=conns, catalogs=cats, primary="main")
    assert ctx.connection is conns["main"]
    assert ctx.catalog is cats["main"]
    assert set(ctx.connections.keys()) == {"main", "books"}


def test_tool_context_multi_db_shims_match_primary() -> None:
    """ctx.connection / ctx.catalog always point at the primary db."""
    conns = {"main": FakeConnection("duckdb"), "aux": FakeConnection("postgres")}
    cats = {"main": FakeCatalog(), "aux": FakeCatalog()}
    ctx = ToolContext(connections=conns, catalogs=cats, primary="aux")
    assert ctx.connection is conns["aux"]
    assert ctx.catalog is cats["aux"]


def test_tool_context_legacy_and_multi_db_match() -> None:
    """Single-DB legacy form and multi-DB form produce the same ctx.connection."""
    conn = FakeConnection("duckdb")
    cat = FakeCatalog()
    legacy = ToolContext(connection=conn, catalog=cat)
    new = ToolContext(connections={"primary": conn}, catalogs={"primary": cat}, primary="primary")
    assert legacy.connection is new.connection
    assert legacy.catalog is new.catalog
