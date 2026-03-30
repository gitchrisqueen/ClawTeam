from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from clawteam.team.models import TaskStatus
from clawteam.team.tasks import TaskStore


def _claim_task(
    data_dir: str,
    task_id: str,
    agent_name: str,
    save_delay: float,
    result_queue,
) -> None:
    os.environ["CLAWTEAM_DATA_DIR"] = data_dir
    store = TaskStore("demo")
    original_save = TaskStore._save_unlocked

    def delayed_save(self, task):
        if save_delay:
            time.sleep(save_delay)
        return original_save(self, task)

    TaskStore._save_unlocked = delayed_save
    try:
        task = store.update(task_id, status=TaskStatus.in_progress, caller=agent_name)
        result_queue.put((agent_name, "ok", task.locked_by if task else None))
    except Exception as exc:
        result_queue.put((agent_name, "err", type(exc).__name__))
    finally:
        TaskStore._save_unlocked = original_save


@pytest.mark.skipif("fork" not in mp.get_all_start_methods(), reason="requires fork start method")
def test_only_one_agent_can_claim_task_concurrently(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
    store = TaskStore("demo")
    task = store.create("demo task")

    ctx = mp.get_context("fork")
    result_queue = ctx.Queue()

    proc_a = ctx.Process(
        target=_claim_task,
        args=(str(tmp_path), task.id, "agent-a", 0.3, result_queue),
    )
    proc_b = ctx.Process(
        target=_claim_task,
        args=(str(tmp_path), task.id, "agent-b", 0.0, result_queue),
    )

    proc_a.start()
    time.sleep(0.05)
    proc_b.start()

    results = sorted(result_queue.get(timeout=10) for _ in range(2))

    proc_a.join(timeout=10)
    proc_b.join(timeout=10)

    assert [result[1] for result in results].count("ok") == 1
    assert [result[1] for result in results].count("err") == 1
    assert any(result[2] == "TaskLockError" for result in results if result[1] == "err")

    final_task = TaskStore("demo").get(task.id)
    assert final_task is not None
    assert final_task.status == TaskStatus.in_progress
    assert final_task.locked_by in {"agent-a", "agent-b"}


# ── Stale lock tests ─────────────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from clawteam.team.tasks import TaskLockError, _is_lock_stale, STALE_LOCK_TIMEOUT_SECONDS


def _make_old_iso(seconds: int) -> str:
    """Return an ISO timestamp that is `seconds` in the past."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


class TestIsLockStale:
    def test_recent_lock_not_stale(self):
        recent = _make_old_iso(60)  # 1 minute ago
        assert _is_lock_stale(recent) is False

    def test_old_lock_is_stale(self):
        old = _make_old_iso(STALE_LOCK_TIMEOUT_SECONDS + 10)
        assert _is_lock_stale(old) is True

    def test_empty_string_is_stale(self):
        assert _is_lock_stale("") is True

    def test_malformed_timestamp_is_stale(self):
        assert _is_lock_stale("not-a-timestamp") is True


class TestAcquireLockUnregisteredAgent:
    """_acquire_lock should release locks from unregistered agents once stale."""

    def test_unregistered_recent_lock_blocks_claim(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
        store = TaskStore("team")
        task = store.create("t1")

        # Manually set an unregistered lock that is recent
        with store._write_lock():
            task = store._get_unlocked(task.id)
            task.locked_by = "agent"  # generic unregistered holder
            task.locked_at = _make_old_iso(60)  # 1 minute ago — within timeout
            store._save_unlocked(task)

        # is_agent_alive("team", "agent") → None (not registered)
        with patch("clawteam.spawn.registry.is_agent_alive", return_value=None):
            with pytest.raises(TaskLockError):
                store.update(task.id, status=TaskStatus.in_progress, caller="new-agent")

    def test_unregistered_stale_lock_allows_claim(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
        store = TaskStore("team")
        task = store.create("t2")

        # Manually set an unregistered lock that is OLD
        with store._write_lock():
            task = store._get_unlocked(task.id)
            task.locked_by = "agent"
            task.locked_at = _make_old_iso(STALE_LOCK_TIMEOUT_SECONDS + 60)
            store._save_unlocked(task)

        # is_agent_alive returns None (unregistered) — lock is stale → should succeed
        with patch("clawteam.spawn.registry.is_agent_alive", return_value=None):
            updated = store.update(task.id, status=TaskStatus.in_progress, caller="new-agent")

        assert updated is not None
        assert updated.locked_by == "new-agent"
        assert updated.status == TaskStatus.in_progress

    def test_dead_agent_lock_always_released(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
        store = TaskStore("team")
        task = store.create("t3")

        with store._write_lock():
            task = store._get_unlocked(task.id)
            task.locked_by = "dead-agent"
            task.locked_at = _make_old_iso(10)  # recent, but agent is dead
            store._save_unlocked(task)

        # is_agent_alive returns False — should still release even if recent
        with patch("clawteam.spawn.registry.is_agent_alive", return_value=False):
            updated = store.update(task.id, status=TaskStatus.in_progress, caller="new-agent")

        assert updated is not None
        assert updated.locked_by == "new-agent"


class TestReleaseStaleLocksUnregistered:
    """release_stale_locks should include unregistered stale locks."""

    def test_releases_unregistered_stale_lock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
        store = TaskStore("team")
        task = store.create("t4")

        with store._write_lock():
            task = store._get_unlocked(task.id)
            task.locked_by = "agent"
            task.locked_at = _make_old_iso(STALE_LOCK_TIMEOUT_SECONDS + 60)
            store._save_unlocked(task)

        with patch("clawteam.spawn.registry.is_agent_alive", return_value=None):
            released = store.release_stale_locks()

        assert task.id in released
        refreshed = store.get(task.id)
        assert refreshed.locked_by == ""

    def test_does_not_release_recent_unregistered_lock(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path))
        store = TaskStore("team")
        task = store.create("t5")

        with store._write_lock():
            task = store._get_unlocked(task.id)
            task.locked_by = "agent"
            task.locked_at = _make_old_iso(60)  # recent
            store._save_unlocked(task)

        with patch("clawteam.spawn.registry.is_agent_alive", return_value=None):
            released = store.release_stale_locks()

        assert task.id not in released
        refreshed = store.get(task.id)
        assert refreshed.locked_by == "agent"  # unchanged
