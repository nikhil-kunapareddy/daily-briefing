import os
import shutil
import tempfile
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
    repo_root = Path(__file__).resolve().parents[1]
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


import pytest

def test_fetched_list_days_success_and_failure():
    """Fetched list days should include only days from successful runs that
    stored at least one paper.
    """
    global db

    # Successful run with two list days.
    run_success = db.start_run("test")
    papers = [
        {
            "stable_id": "2610.12345",
            "version": "v1",
            "title": "Paper A",
            "pdf_link": "https://arxiv.org/pdf/2610.12345v1.pdf",
            "published": "2026-10-10T12:00:00Z",
            "updated": "2026-10-10T12:00:00Z",
        }
    ]
    # Save the same paper for two distinct list days.
    db.save_papers(run_success, papers, list_day="2026-10-10")
    db.save_papers(run_success, papers, list_day="2026-10-12")
    db.finish_run(run_success, new_count=1)

    # Failed run that also tries to record a list day – it must not appear.
    run_fail = db.start_run("test")
    db.save_papers(run_fail, papers, list_day="2026-10-11")
    db.finish_run(run_fail, error="boom", new_count=1)

    # Successful run with no papers – should not contribute a list day.
    run_empty = db.start_run("test")
    db.finish_run(run_empty, new_count=0)

    # The function should return the two days from the successful run in
    # chronological order.
    days = db.fetched_list_days()
    assert days == ["2026-10-10", "2026-10-12"]


def test_fetched_list_days_no_successful_runs():
    """When there are no successful runs with papers, the function returns an
    empty list.
    """
    global db

    # Only a failed run with papers.
    run_fail = db.start_run("test")
    db.save_papers(run_fail, [], list_day="2026-10-10")
    db.finish_run(run_fail, error="error", new_count=0)

    assert db.fetched_list_days() == []
