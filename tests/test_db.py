import os
import shutil
import tempfile
import time
import pytest

# Imports are performed inside the fixture after the environment variable is set.
db = None  # placeholder; will be imported in the fixture
config = None


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    """Create a temporary data directory and point config to it for each test.

    The ``app`` package reads ``DAILY_BRIEFING_DATA`` at import time, so we set the
    variable before importing ``app.config`` and ``app.db``. Some pytest runs do
    not include the repository root on ``sys.path``, therefore we prepend it
    manually.
    """
    import importlib, sys
    from pathlib import Path

    # Ensure the repo root is importable.
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    global db, config
    import app.config as cfg
    importlib.reload(cfg)
    config = cfg
    import app.db as dbmod
    importlib.reload(dbmod)
    db = dbmod
    yield
    # Cleanup after test.
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_start_and_finish_run():
    run_id = db.start_run("test")
    assert isinstance(run_id, int)
    db.finish_run(run_id, new_count=3)
    latest = db.latest_briefing()
    assert latest is not None
    assert latest["new_count"] == 3
    assert latest["date"] is not None
    assert latest["papers"] == []
    assert latest["is_first_run"] is True


def test_start_run_trigger():
    run_id = db.start_run("cron")
    runs = db.latest_runs(limit=1)
    assert len(runs) == 1
    assert runs[0]["trigger"] == "cron"
    assert runs[0]["ok"] is None  # in-progress run
    assert runs[0]["new_count"] == 0
    assert runs[0]["id"] == run_id

    # Now finish the run
    db.finish_run(run_id)
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is True


def test_manual_rate_limit_returns_wait():
    run1 = db.start_manual_run()
    assert isinstance(run1, int)
    db.finish_run(run1)
    # Second manual run within 10 minutes should return minutes to wait.
    wait = db.start_manual_run()
    assert isinstance(wait, int)
    assert 1 <= wait <= 10
    # Simulate passage of time by directly updating the timestamp.
    conn = db._get_connection()
    cur = conn.cursor()
    ten_minutes_ago = (time.time() - 600 - 1)
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ten_minutes_ago))
    cur.execute("UPDATE runs SET started_at = ? WHERE id = ?", (iso, run1))
    conn.commit()
    # Now a new manual run should succeed.
    run2 = db.start_manual_run()
    assert isinstance(run2, int)


def test_save_papers():
    run_id = db.start_run("test")
    papers = [
        {
            "stable_id": "2610.12345",
            "version": "v1",
            "title": "  Test   Paper  Title  ",
            "pdf_link": "https://arxiv.org/pdf/2610.12345v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        }
    ]
    db.save_papers(run_id, papers)
    db.finish_run(run_id, new_count=1)
    briefing = db.latest_briefing()
    assert briefing is not None
    assert briefing["new_count"] == 1
    assert len(briefing["papers"]) == 1
    assert briefing["papers"][0]["stable_id"] == "2610.12345"
    assert briefing["papers"][0]["title"] == "Test Paper Title"  # whitespace collapsed


def test_save_papers_whitespace_collapsed():
    run_id = db.start_run("test")
    papers = [
        {
            "stable_id": "2610.11111",
            "version": "v1",
            "title": "Line1\nLine2\t\tTabbed",
            "pdf_link": "https://arxiv.org/pdf/2610.11111v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        }
    ]
    db.save_papers(run_id, papers)
    db.finish_run(run_id)
    briefing = db.latest_briefing()
    assert briefing["papers"][0]["title"] == "Line1 Line2 Tabbed"


def test_latest_briefing_is_first_run():
    run_id = db.start_run("cron")
    db.finish_run(run_id, new_count=2)
    briefing = db.latest_briefing()
    assert briefing["is_first_run"] is True

    run_id2 = db.start_run("cron")
    db.finish_run(run_id2, new_count=1)
    briefing2 = db.latest_briefing()
    assert briefing2["is_first_run"] is False


def test_last_briefing_with_papers():
    # First run with papers
    run1 = db.start_run("cron")
    papers1 = [
        {
            "stable_id": "2610.11111",
            "version": "v1",
            "title": "Paper 1",
            "pdf_link": "https://arxiv.org/pdf/2610.11111v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        }
    ]
    db.save_papers(run1, papers1)
    db.finish_run(run1, new_count=1)

    # Second run with no new papers
    run2 = db.start_run("cron")
    db.finish_run(run2, new_count=0)

    # last_briefing_with_papers should return the first run
    last = db.last_briefing_with_papers()
    assert last is not None
    assert last["new_count"] == 1
    assert last["papers"][0]["stable_id"] == "2610.11111"


def test_latest_runs_limit():
    for i in range(15):
        run_id = db.start_run("cron")
        db.finish_run(run_id, new_count=i)
    runs = db.latest_runs(limit=10)
    assert len(runs) == 10
    # Newest first
    assert runs[0]["new_count"] == 14
    assert runs[9]["new_count"] == 5


def test_latest_runs_error_short():
    run_id = db.start_run("cron")
    db.finish_run(run_id, error="Multi\nLine\nError")
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is False
    assert runs[0]["error_short"] == "Multi"


def test_health_no_runs():
    assert db.health() is None


def test_health_with_runs():
    run_id = db.start_run("cron")
    db.finish_run(run_id)
    health = db.health()
    assert health is not None
    assert health["status"] == "ok"
    assert health["latest_run_time"] is not None

    run_id2 = db.start_run("cron")
    db.finish_run(run_id2, error="Failed")
    health2 = db.health()
    assert health2["status"] == "error"


def test_finish_run_updates_new_count():
    run_id = db.start_run("test")
    db.finish_run(run_id, new_count=5)
    runs = db.latest_runs(limit=1)
    assert runs[0]["new_count"] == 5


def test_schema_indexes():
    conn = db._get_connection()
    cur = conn.cursor()
    # Check WAL mode
    cur.execute("PRAGMA journal_mode")
    assert cur.fetchone()[0] == "wal"
    # Check foreign keys
    cur.execute("PRAGMA foreign_keys")
    assert cur.fetchone()[0] == 1
    # Check busy_timeout
    cur.execute("PRAGMA busy_timeout")
    assert cur.fetchone()[0] == 5000
    # Check indexes exist
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_papers_stable_id'")
    assert cur.fetchone() is not None
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_runs_started_at'")
    assert cur.fetchone() is not None
    # Check unique constraint on papers(stable_id, version)
    cur.execute("PRAGMA index_list(papers)")
    indexes = cur.fetchall()
    unique_found = False
    for idx in indexes:
        if idx[2] == 1:  # unique
            cur.execute(f"PRAGMA index_info({idx[1]})")
            cols = [row[2] for row in cur.fetchall()]
            if set(cols) == {"stable_id", "version"}:
                unique_found = True
                break
    assert unique_found, "Unique index on (stable_id, version) not found"


def test_papers_unique_constraint():
    """Test that duplicate (stable_id, version) pairs are ignored."""
    run_id = db.start_run("test")
    papers = [
        {
            "stable_id": "2610.99999",
            "version": "v1",
            "title": "Paper",
            "pdf_link": "https://arxiv.org/pdf/2610.99999v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        }
    ]
    db.save_papers(run_id, papers)
    db.save_papers(run_id, papers)  # Should not raise, INSERT OR IGNORE
    db.finish_run(run_id)
    briefing = db.latest_briefing()
    assert len(briefing["papers"]) == 1


def test_multiple_versions_same_stable_id():
    """Different versions of same stable_id can be saved."""
    run_id = db.start_run("test")
    papers = [
        {
            "stable_id": "2610.88888",
            "version": "v1",
            "title": "Paper v1",
            "pdf_link": "https://arxiv.org/pdf/2610.88888v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        },
        {
            "stable_id": "2610.88888",
            "version": "v2",
            "title": "Paper v2",
            "pdf_link": "https://arxiv.org/pdf/2610.88888v2.pdf",
            "published": "2026-10-11T12:00:00Z",
            "updated": "2026-10-11T12:00:00Z",
        },
    ]
    db.save_papers(run_id, papers)
    db.finish_run(run_id)
    briefing = db.latest_briefing()
    assert len(briefing["papers"]) == 2


def test_latest_runs_in_progress_shows_none_ok():
    """Test that latest_runs() returns ok=None for in-progress runs."""
    # Create a run but don't finish it (in-progress)
    run_id = db.start_run("test")
    # Note: we don't call finish_run, so finished_at remains NULL

    runs = db.latest_runs(limit=1)
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["ok"] is None  # in-progress run should have ok=None
    assert runs[0]["trigger"] == "test"
    assert runs[0]["new_count"] == 0
    assert runs[0]["error_short"] is None

    # Now finish the run
    db.finish_run(run_id, new_count=3)
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is True  # finished successfully

    # Test with a failed run
    run_id2 = db.start_run("test")
    db.finish_run(run_id2, error="Something went wrong")
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is False
    assert runs[0]["error_short"] == "Something went wrong"  # first line of error
