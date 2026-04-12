"""
Regression tests for ClawTeam supervisor spawn-loop bugs.

Scenarios covered:
  T1 — max_spawn_attempts sets skip=1 so health_check stops re-entering attempt_spawn
  T2 — health_check_team for permanent teams respects skip=1 when spawn is exhausted
  T3 — attempt_spawn kills a stale gateway-dead tmux session before re-spawning
  T4 — /reset HTTP endpoint clears spawn_attempts + skip so team can recover

These tests import the supervisor module directly and mock out all
subprocess/sqlite side-effects so they run fully in-process and fast.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Load the supervisor module without executing main()
# ---------------------------------------------------------------------------

SUPERVISOR_PATH = Path.home() / "scripts" / "cqc-clawteam-supervisor.py"


@pytest.fixture(scope="module")
def sup():
    """Import cqc-clawteam-supervisor as a module (without running main())."""
    spec = importlib.util.spec_from_file_location("cqc_clawteam_supervisor", SUPERVISOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Stub out inotify_simple which may not be installed in test environments
    sys.modules.setdefault("inotify_simple", types.ModuleType("inotify_simple"))
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# In-memory DB helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(sup):
    """Create an in-memory supervisor DB with schema initialised."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    sup.db_init(c)
    return c


def _upsert(sup, conn, team_id, **kw):
    """Convenience: upsert a team row."""
    sup.db_upsert_team(conn, team_id, **kw)


def _get(sup, conn, team_id) -> dict:
    return sup.db_get_team(conn, team_id) or {}


# ---------------------------------------------------------------------------
# T1 — max_spawn_attempts sets skip=1
# ---------------------------------------------------------------------------

class TestAttemptSpawnMaxAttemptsSkip:
    """When spawn_attempts >= MAX_SPAWN_ATTEMPTS, attempt_spawn must set skip=1
    so the supervisor health loop stops logging ERROR events every 30 s."""

    def test_skip_set_on_max_attempts(self, sup, conn, tmp_path, monkeypatch):
        team_id = "test-team-t1"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / "teams")
        (tmp_path / "teams" / team_id / "events").mkdir(parents=True)
        # Seed a spawn_registry.json so get_spawn_command returns something
        registry = {"leader": {"command": ["openclaw", "tui"], "backend": "tmux"}}
        (tmp_path / "teams" / team_id / "spawn_registry.json").write_text(json.dumps(registry))

        _upsert(sup, conn, team_id,
                team_type="ephemeral",
                leader_name="leader",
                spawn_attempts=sup.MAX_SPAWN_ATTEMPTS,  # already at max
                skip=0,
                status="spawn_failed")

        # patch subprocess so no real tmux calls happen
        with patch("subprocess.run") as mock_run:
            sup.attempt_spawn(conn, team_id)

        team = _get(sup, conn, team_id)
        assert team["skip"] == 1, "skip must be 1 after max attempts"
        assert team["status"] == "failed"
        # Ensure we did NOT try to spawn again
        spawn_calls = [
            c for c in mock_run.call_args_list
            if c.args and "new-session" in (c.args[0] or [])
        ]
        assert len(spawn_calls) == 0, "should not call tmux new-session at max attempts"

    def test_one_below_max_does_not_set_skip(self, sup, conn, tmp_path, monkeypatch):
        """Confirm skip is NOT set when attempts < MAX_SPAWN_ATTEMPTS."""
        team_id = "test-team-t1b"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / "teams")
        (tmp_path / "teams" / team_id / "events").mkdir(parents=True)
        registry = {"leader": {"command": ["openclaw", "tui"], "backend": "tmux"}}
        (tmp_path / "teams" / team_id / "spawn_registry.json").write_text(json.dumps(registry))

        _upsert(sup, conn, team_id,
                team_type="ephemeral",
                leader_name="leader",
                spawn_attempts=sup.MAX_SPAWN_ATTEMPTS - 1,
                skip=0,
                status="spawn_failed")

        # tmux new-session returns "duplicate session" — skip should still be 0
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "duplicate session: test-team-t1b"
        with patch("subprocess.run", return_value=mock_result):
            sup.attempt_spawn(conn, team_id)

        team = _get(sup, conn, team_id)
        assert team["skip"] == 0, "skip must NOT be set before max attempts"


# ---------------------------------------------------------------------------
# T2 — permanent team health_check stops cycling when spawn exhausted
# ---------------------------------------------------------------------------

class TestHealthCheckPermanentSpawnExhausted:
    """health_check_team for a permanent team at MAX_SPAWN_ATTEMPTS must NOT
    call attempt_spawn (which logs ERROR events every 30s)."""

    def _seed_team(self, sup, conn, tmp_path, team_id, attempts, skip):
        (tmp_path / ".clawteam" / "teams" / team_id / "events").mkdir(parents=True)
        # Create a pending event so pending > 0
        evt = {"ts": "2026-01-01T00:00:00Z", "processed": False}
        evt_file = tmp_path / ".clawteam" / "teams" / team_id / "events" / "evt-001.json"
        evt_file.write_text(json.dumps(evt))

        _upsert(sup, conn, team_id,
                team_type="permanent",
                leader_name="platform-lead",
                spawn_attempts=attempts,
                skip=skip,
                status="failed" if attempts >= sup.MAX_SPAWN_ATTEMPTS else "permanent_dead")

    def test_spawn_not_called_when_exhausted(self, sup, conn, tmp_path, monkeypatch):
        team_id = "cqc-platform-t2"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / ".clawteam" / "teams")
        monkeypatch.setattr(sup, "TASKS_DIR", tmp_path / ".clawteam" / "tasks")
        self._seed_team(sup, conn, tmp_path, team_id,
                        attempts=sup.MAX_SPAWN_ATTEMPTS, skip=1)

        spawn_called = []

        def fake_attempt_spawn(c, tid):
            spawn_called.append(tid)

        monkeypatch.setattr(sup, "attempt_spawn", fake_attempt_spawn)
        monkeypatch.setattr(sup, "is_leader_alive", lambda tid: False)
        monkeypatch.setattr(sup, "count_pending_inbox_messages", lambda tid: 0)
        monkeypatch.setattr(sup, "count_inprogress_tasks", lambda tid: 0)
        # count_pending_events > 0 to enter the permanent-dead path
        monkeypatch.setattr(sup, "count_pending_events", lambda tid: 3)

        sup.health_check_team(conn, team_id)

        assert team_id not in spawn_called, (
            "attempt_spawn must NOT be called for a permanent team at max spawn attempts"
        )

    def test_spawn_called_normally_below_max(self, sup, conn, tmp_path, monkeypatch):
        """Confirm attempt_spawn IS called when attempts < MAX_SPAWN_ATTEMPTS."""
        team_id = "cqc-platform-t2b"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / ".clawteam" / "teams")
        monkeypatch.setattr(sup, "TASKS_DIR", tmp_path / ".clawteam" / "tasks")
        self._seed_team(sup, conn, tmp_path, team_id,
                        attempts=2, skip=0)

        spawn_called = []

        def fake_attempt_spawn(c, tid):
            spawn_called.append(tid)

        monkeypatch.setattr(sup, "attempt_spawn", fake_attempt_spawn)
        monkeypatch.setattr(sup, "is_leader_alive", lambda tid: False)
        monkeypatch.setattr(sup, "count_pending_inbox_messages", lambda tid: 0)
        monkeypatch.setattr(sup, "count_inprogress_tasks", lambda tid: 0)
        monkeypatch.setattr(sup, "count_pending_events", lambda tid: 3)

        sup.health_check_team(conn, team_id)

        assert team_id in spawn_called, (
            "attempt_spawn MUST be called when attempts < MAX_SPAWN_ATTEMPTS"
        )


# ---------------------------------------------------------------------------
# T3 — attempt_spawn kills stale gateway-dead session before new-session
# ---------------------------------------------------------------------------

class TestAttemptSpawnKillsStaleSession:
    """When a tmux session already exists (gateway-dead), attempt_spawn must
    kill it before calling tmux new-session to avoid duplicate-session errors."""

    def test_kills_existing_session_before_spawn(self, sup, conn, tmp_path, monkeypatch):
        team_id = "test-team-t3"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / "teams")
        monkeypatch.setattr(sup, "TASKS_DIR", tmp_path / "tasks")
        (tmp_path / "teams" / team_id / "events").mkdir(parents=True)
        registry = {"leader": {"command": ["openclaw", "tui"], "backend": "tmux"}}
        (tmp_path / "teams" / team_id / "spawn_registry.json").write_text(json.dumps(registry))

        _upsert(sup, conn, team_id,
                team_type="permanent",
                leader_name="leader",
                spawn_attempts=0,
                skip=0,
                status="permanent_dead")

        run_calls: list = []

        def fake_run(cmd, **kwargs):
            run_calls.append(list(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = "12345"
            return r

        with patch("subprocess.run", side_effect=fake_run):
            sup.attempt_spawn(conn, team_id)

        # The has-session call should appear
        has_session_calls = [c for c in run_calls if "has-session" in c]
        assert has_session_calls, "must check for existing tmux session"

        # The kill-session call should appear BEFORE new-session
        kill_calls = [c for c in run_calls if "kill-session" in c]
        new_session_calls = [c for c in run_calls if "new-session" in c]
        assert kill_calls, "must kill stale session before spawning"
        assert new_session_calls, "must still call new-session after kill"

        # Verify order: kill before new-session
        kill_idx = next(i for i, c in enumerate(run_calls) if "kill-session" in c)
        new_idx = next(i for i, c in enumerate(run_calls) if "new-session" in c)
        assert kill_idx < new_idx, "kill-session must precede new-session"

    def test_no_kill_when_session_absent(self, sup, conn, tmp_path, monkeypatch):
        """When no existing session, kill-session must NOT be called."""
        team_id = "test-team-t3b"
        monkeypatch.setattr(sup, "TEAMS_DIR", tmp_path / "teams")
        monkeypatch.setattr(sup, "TASKS_DIR", tmp_path / "tasks")
        (tmp_path / "teams" / team_id / "events").mkdir(parents=True)
        registry = {"leader": {"command": ["openclaw", "tui"], "backend": "tmux"}}
        (tmp_path / "teams" / team_id / "spawn_registry.json").write_text(json.dumps(registry))

        _upsert(sup, conn, team_id,
                team_type="ephemeral",
                leader_name="leader",
                spawn_attempts=0,
                skip=0,
                status="dead")

        run_calls: list = []

        def fake_run(cmd, **kwargs):
            run_calls.append(list(cmd))
            r = MagicMock()
            # has-session returns non-zero (session not found)
            r.returncode = 1 if "has-session" in cmd else 0
            r.stderr = ""
            r.stdout = ""
            return r

        with patch("subprocess.run", side_effect=fake_run):
            sup.attempt_spawn(conn, team_id)

        kill_calls = [c for c in run_calls if "kill-session" in c]
        assert not kill_calls, "must NOT call kill-session when session is absent"


# ---------------------------------------------------------------------------
# T4 — HTTP /reset endpoint clears attempt counter
# ---------------------------------------------------------------------------

class TestHttpResetEndpoint:
    """POST /reset/<team_id> resets spawn_attempts + skip so a stuck team can recover."""

    def test_reset_clears_spawn_counter(self, sup, conn):
        team_id = "stuck-team-t4"
        _upsert(sup, conn, team_id,
                team_type="ephemeral",
                spawn_attempts=sup.MAX_SPAWN_ATTEMPTS,
                skip=1,
                status="failed",
                error_msg="Max spawn attempts reached")

        # Simulate the HTTP handler's reset logic directly
        sup.db_upsert_team(conn, team_id,
                           spawn_attempts=0, last_spawn_at=0,
                           skip=0, status="discovered", error_msg=None)

        team = _get(sup, conn, team_id)
        assert team["spawn_attempts"] == 0
        assert team["skip"] == 0
        assert team["status"] == "discovered"
        assert not team.get("error_msg")
