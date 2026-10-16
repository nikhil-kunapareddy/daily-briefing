import os
import sys
import time
import pytest

# Ensure repository root is on the import path for the app package.
sys.path.append(os.getcwd())


class DummyResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def _load_fixture():
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "arxiv_sample.xml")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return f.read()


def test_job_first_and_second_run(monkeypatch, tmp_path):
    """Run the job twice and verify new‑paper detection and briefing flags.

    The first run should mark all five papers as new and ``is_first_run``
    should be True. The second run uses the same fixture, so no new papers
    are detected and ``is_first_run`` becomes False.
    """
    # Point the app to a temporary data directory.
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))

    # Reload config and db so they pick up the new env var.
    import importlib, app.config as cfg, app.db as dbmod
    importlib.reload(cfg)
    importlib.reload(dbmod)
    db = dbmod

    # Patch ``requests.get`` used inside ``app.arxiv``.
    import requests

    xml = _load_fixture()

    def fake_get(*_args, **_kwargs):
        return DummyResponse(xml, 200)

    monkeypatch.setattr(requests, "get", fake_get)

    # Import the arxiv module after patching requests.
    import importlib, app.arxiv as arxiv_mod
    importlib.reload(arxiv_mod)

    # Import and run the job.
    import importlib, app.job as job_mod
    importlib.reload(job_mod)

    # First run – expect 5 new papers.
    job_mod.run(trigger="manual")
    briefing1 = db.latest_briefing()
    assert briefing1 is not None
    assert briefing1["new_count"] == 5
    assert briefing1["is_first_run"] is True

    # Sleep briefly to ensure a different timestamp for the second run.
    time.sleep(0.1)

    # Second run – same fixture, no new papers.
    job_mod.run(trigger="manual")
    briefing2 = db.latest_briefing()
    assert briefing2["new_count"] == 0
    assert briefing2["is_first_run"] is False

    # Verify the lock file does not persist.
    lock_path = db.config.DATA_ROOT / "run.lock"
    assert not lock_path.exists()


def test_job_run_with_precreated_run_id(monkeypatch, tmp_path):
    """Test job.run() using a pre-created run_id (manual run flow)."""
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))

    import importlib, sys
    repo_root = __import__('pathlib').Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    import importlib, app.config as cfg, app.db as dbmod, app.arxiv as arxiv_mod, app.job as job_mod
    importlib.reload(cfg)
    importlib.reload(dbmod)
    importlib.reload(arxiv_mod)
    importlib.reload(job_mod)
    db = dbmod
    job = job_mod
    arxiv = arxiv_mod

    import requests
    xml = _load_fixture()

    def fake_get(*_args, **_kwargs):
        return DummyResponse(xml, 200)

    monkeypatch.setattr(requests, "get", fake_get)

    # Create a manual run row first (simulating start_manual_run)
    run_id = db.start_manual_run()
    assert isinstance(run_id, int)

    # Run job with the pre-created run_id
    job.run(trigger="manual", run_id=run_id)

    # Verify the run was completed
    briefing = db.latest_briefing()
    assert briefing is not None
    assert briefing["new_count"] == 5
    assert briefing["is_first_run"] is True

    # Verify the run is marked as finished
    runs = db.latest_runs(limit=1)
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["ok"] is True
    assert runs[0]["trigger"] == "manual"
    assert runs[0]["new_count"] == 5

