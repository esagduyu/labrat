"""Tests for ThreadManager CRUD and branch semantics (M17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.thread.manager import ThreadManager


@pytest.fixture()
def mgr(tmp_path: Path) -> ThreadManager:
    """ThreadManager backed by a temporary directory."""
    return ThreadManager(store_dir=tmp_path)


def test_create_thread(mgr: ThreadManager) -> None:
    """create_thread returns a Thread with the given name and profile."""
    t = mgr.create_thread(name="analysis", profile_name="prod")
    assert t.name == "analysis"
    assert t.profile_name == "prod"
    assert t.parent_version_id is None
    assert t.id  # non-empty


def test_list_threads_empty(mgr: ThreadManager) -> None:
    """list_threads returns an empty list when no threads exist."""
    assert mgr.list_threads() == []


def test_list_threads_multiple(mgr: ThreadManager) -> None:
    """list_threads returns all created threads."""
    mgr.create_thread(name="a", profile_name="prod")
    mgr.create_thread(name="b", profile_name="prod")
    mgr.create_thread(name="c", profile_name="dev")
    threads = mgr.list_threads()
    assert len(threads) == 3
    names = {t.name for t in threads}
    assert names == {"a", "b", "c"}


def test_get_thread_by_id(mgr: ThreadManager) -> None:
    """get_thread returns the correct thread by ID."""
    t = mgr.create_thread(name="target", profile_name="prod")
    found = mgr.get_thread(t.id)
    assert found is not None
    assert found.id == t.id


def test_get_thread_missing_returns_none(mgr: ThreadManager) -> None:
    """get_thread returns None for an unknown ID."""
    assert mgr.get_thread("nonexistent-id") is None


def test_append_version(mgr: ThreadManager) -> None:
    """append_version creates a Version linked to the thread."""
    t = mgr.create_thread(name="q1", profile_name="prod")
    v = mgr.append_version(
        thread_id=t.id,
        sql="SELECT COUNT(*) FROM orders",
        chat_history=[{"role": "user", "content": "how many orders?"}],
    )
    assert v.thread_id == t.id
    assert v.sql == "SELECT COUNT(*) FROM orders"
    assert v.results_ref is None


def test_get_versions_returns_in_order(mgr: ThreadManager) -> None:
    """get_versions returns versions in creation order."""
    t = mgr.create_thread(name="multi", profile_name="prod")
    v1 = mgr.append_version(t.id, sql="SELECT 1", chat_history=[])
    v2 = mgr.append_version(t.id, sql="SELECT 2", chat_history=[])
    v3 = mgr.append_version(t.id, sql="SELECT 3", chat_history=[])
    versions = mgr.get_versions(t.id)
    assert len(versions) == 3
    assert [v.id for v in versions] == [v1.id, v2.id, v3.id]


def test_branch_from_version(mgr: ThreadManager) -> None:
    """branch_from_version creates a new thread with parent_version_id set."""
    t = mgr.create_thread(name="original", profile_name="prod")
    v1 = mgr.append_version(t.id, sql="SELECT 1", chat_history=[])
    mgr.append_version(t.id, sql="SELECT 2", chat_history=[])

    branched = mgr.branch_from_version(version_id=v1.id, name="branch-a")
    assert branched.parent_version_id == v1.id
    assert branched.name == "branch-a"
    assert branched.id != t.id


def test_persistence_survives_reload(tmp_path: Path) -> None:
    """Threads and versions written by one manager are readable by another."""
    mgr1 = ThreadManager(store_dir=tmp_path)
    t = mgr1.create_thread(name="persistent", profile_name="dev")
    v = mgr1.append_version(t.id, sql="SELECT 99", chat_history=[])

    mgr2 = ThreadManager(store_dir=tmp_path)
    threads = mgr2.list_threads()
    assert any(th.id == t.id for th in threads)
    versions = mgr2.get_versions(t.id)
    assert any(vv.id == v.id for vv in versions)
