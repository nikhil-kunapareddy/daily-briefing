import os
import pytest

# The repository root is added to sys.path by the existing test fixtures
# (see tests/conftest.py).  We'll follow the same pattern to ensure imports
# work when this file runs.


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    """Create an isolated data folder for the duration of each test.

    The ``app.config`` module reads ``DAILY_BRIEFING_DATA`` on import, so we
    must set the environment variable *before* importing the Flask app.
    """
    import sys
    repo_root = os.path.abspath(os.path.join(__file__, "../.."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))

    # Reload modules to pick up the new env var.
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    import app.db as dbmod
    importlib.reload(dbmod)
    # Export for tests.
    global db, app
    db = dbmod
    from app.web import create_app
    app = create_app()
    yield
    # No explicit cleanup needed; the temporary directory will be removed.


def test_index_shows_no_data_initially():
    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    # When no runs exist the template renders a placeholder message.
    assert b"No data available yet" in resp.data


def test_index_shows_first_run_message():
    client = app.test_client()
    # Create a successful run with some papers
    run_id = db.start_manual_run()
    papers = [{
        "stable_id": "2610.12345",
        "version": "v1",
        "title": "Test Paper One",
        "pdf_link": "https://arxiv.org/pdf/2610.12345.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run_id, papers)
    db.finish_run(run_id, new_count=1)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"First run: everything is new" in resp.data
    assert b"Test Paper One" in resp.data
    assert b"10.48550/arXiv.2610.12345" in resp.data


def test_index_shows_no_new_with_previous_briefing():
    client = app.test_client()
    # First run with papers
    run_id1 = db.start_manual_run()
    papers1 = [{
        "stable_id": "2610.12345",
        "version": "v1",
        "title": "Test Paper One",
        "pdf_link": "https://arxiv.org/pdf/2610.12345.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run_id1, papers1)
    db.finish_run(run_id1, new_count=1)

    # Second run with no new papers (same paper)
    run_id2 = db.start_run("cron")  # Use cron trigger to avoid rate limit
    db.save_papers(run_id2, papers1)
    db.finish_run(run_id2, new_count=0)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"No new submissions since the last run" in resp.data
    assert b"checked" in resp.data
    assert b"UTC" in resp.data
    # Should show the previous briefing
    assert b"Test Paper One" in resp.data


def test_run_now_creates_run_and_rate_limits():
    client = app.test_client()
    # First POST should create a run and redirect back to index.
    resp1 = client.post("/run-now", follow_redirects=False)
    # ``start_manual_run`` returns an int, the view redirects.
    assert resp1.status_code in (302, 303)

    # Immediately POST again – should see the rate‑limit note in the page.
    resp2 = client.post("/run-now", follow_redirects=True)
    assert resp2.status_code == 200
    assert b"Try again in" in resp2.data

    # The history table should now contain at least one row.
    assert b"Run history" in resp2.data
    # The row includes the trigger "manual".
    assert b"manual" in resp2.data


def test_health_endpoint_statuses():
    client = app.test_client()
    # No runs yet – health should return empty JSON with 200.
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {}

    # Create a successful run.
    run_id = db.start_manual_run()
    db.finish_run(run_id)
    resp_ok = client.get("/healthz")
    assert resp_ok.status_code == 200
    data = resp_ok.get_json()
    assert data["status"] == "ok"

    # Create a failed run using cron trigger to avoid rate limit.
    fail_id = db.start_run("cron")
    db.finish_run(fail_id, error="boom")
    resp_fail = client.get("/healthz")
    assert resp_fail.status_code == 503
    assert resp_fail.get_json()["status"] == "error"


def test_history_table_columns():
    client = app.test_client()
    # Create a few runs
    for i in range(3):
        run_id = db.start_run("cron" if i > 0 else "manual")
        db.finish_run(run_id, new_count=i)

    resp = client.get("/")
    assert resp.status_code == 200
    # Check for table headers
    assert b"TIME" in resp.data
    # Header uses check/cross symbols or STATUS text
    assert b"TRIGGER" in resp.data
    assert b"RESULT" in resp.data
    # Check for status symbols (check/cross)
    # These are Unicode characters, check via string
    resp_text = resp.data.decode('utf-8')
    assert '✓' in resp_text or '✗' in resp_text


def test_footer_content():
    client = app.test_client()
    run_id = db.start_manual_run()
    db.finish_run(run_id)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Thank you to arXiv for use of its open access interoperability" in resp.data
    assert b"Source: the arXiv API" in resp.data
    assert b"/healthz" in resp.data


def test_responsive_classes_present():
    """Check that the template includes data-label attributes for responsive layout."""
    client = app.test_client()
    run_id = db.start_manual_run()
    papers = [{
        "stable_id": "2610.12345",
        "version": "v1",
        "title": "Test Paper",
        "pdf_link": "https://arxiv.org/pdf/2610.12345.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run_id, papers)
    db.finish_run(run_id, new_count=1)

    resp = client.get("/")
    assert resp.status_code == 200
    # Check for data-label attributes on table cells
    assert b'data-label="S.NO"' in resp.data
    assert b'data-label="TITLE"' in resp.data
    assert b'data-label="DOI"' in resp.data
    assert b'data-label="PDF"' in resp.data
    # History table data-labels
    assert b'data-label="TIME"' in resp.data
    assert b'data-label="TRIGGER"' in resp.data
    assert b'data-label="RESULT"' in resp.data


def test_paper_title_bold():
    client = app.test_client()
    run_id = db.start_manual_run()
    papers = [{
        "stable_id": "2610.12345",
        "version": "v1",
        "title": "Test Paper",
        "pdf_link": "https://arxiv.org/pdf/2610.12345.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run_id, papers)
    db.finish_run(run_id, new_count=1)

    resp = client.get("/")
    assert resp.status_code == 200
    # Title should be wrapped in <strong>
    assert b"<strong>Test Paper</strong>" in resp.data


def test_doi_format():
    client = app.test_client()
    run_id = db.start_manual_run()
    papers = [{
        "stable_id": "2610.12345",
        "version": "v1",
        "title": "Test Paper",
        "pdf_link": "https://arxiv.org/pdf/2610.12345.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run_id, papers)
    db.finish_run(run_id, new_count=1)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"10.48550/arXiv.2610.12345" in resp.data


def test_no_new_submissions_text():
    client = app.test_client()
    run_id = db.start_manual_run()
    db.finish_run(run_id, new_count=0)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"no new submissions" in resp.data.lower()

