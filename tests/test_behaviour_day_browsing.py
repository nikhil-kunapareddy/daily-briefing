import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest
from datetime import datetime, timedelta

# Ensure we use a temporary directory for the data root
os.environ["DAILY_BRIEFING_DATA"] = tempfile.gettempdir()

from app.web import create_app
from app import db, job

def _seed_list_days(papers):
    """Save papers as one successful run, each on the list day equal to its published day."""
    run_id = db.start_run("schedule")
    for p in papers:
        db.save_papers(run_id, [p], list_day=p["published"][:10])
    db.finish_run(run_id, new_count=len(papers))


@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.config.DATA_ROOT", Path(tmpdir)):
            # Force app.db to use the patched DATA_ROOT
            with patch("app.db._connection", None):
                yield Path(tmpdir)

@pytest.fixture
def app_client(temp_data_dir):
    """Flask test client for the web app."""
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app.test_client()

def test_day_browsing_basic_listing(app_client, temp_data_dir):
    """
    Acceptance: Each paper is listed under its <published> UTC date.
    Using fixtures/arxiv_sample.xml.
    """
    # First, seed the database with the fixture
    # We mock arxiv.fetch to return what's in arxiv_sample.xml
    # Sample dates: 10, 11, 12, 13, 14 Oct 2026
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "First Paper Title", "pdf_link": "https://arxiv.org/pdf/2610.10001v1.pdf", "published": "2026-10-10T12:00:00Z", "updated": "2026-10-10T12:00:00Z"},
        {"stable_id": "2610.10002", "version": "v2", "title": "Second Paper Title with spaces", "pdf_link": "https://arxiv.org/pdf/2610.10002v2.pdf", "published": "2026-10-11T12:00:00Z", "updated": "2026-10-11T13:00:00Z"},
        {"stable_id": "2610.10003", "version": "v1", "title": "Third Paper Title", "pdf_link": "https://arxiv.org/pdf/2610.10003v1.pdf", "published": "2026-10-12T12:00:00Z", "updated": "2026-10-12T30:00:00Z"},
        {"stable_id": "2610.10004", "version": "v1", "title": "Fourth Paper Title", "pdf_link": "https://arxiv.org/pdf/2610.10004v1.pdf", "published": "2026-10-13T12:00:00Z", "updated": "2026-10-13T12:00:00Z"},
        {"stable_id": "2610.10005", "version": "v1", "title": "Fifth Paper Title", "pdf_link": "https://arxiv.org/pdf/2610.10005v1.pdf", "published": "2026-10-14T12:00:00Z", "updated": "2026-10-14T12:00:00Z"},
    ]

    _seed_list_days(fixture_papers)

    # Check Oct 14
    resp = app_client.get("/?day=2026-10-14")
    assert resp.status_code == 200
    assert b"Fifth Paper Title" in resp.data
    assert b"2026-10-14" in resp.data or b"14 Oct" in resp.data

    # Check Oct 10
    resp = app_client.get("/?day=2026-10-10")
    assert resp.status_code == 200
    assert b"First Paper Title" in resp.data

def test_day_browsing_arrows(app_client, temp_data_dir):
    """
    Acceptance:
    - ← and → move one day
    - greyed out at oldest day with papers
    - greyed out at today
    """
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "P1", "pdf_link": "L1", "published": "2026-10-10T12:00:00Z", "updated": "U1"},
        {"stable_id": "2610.10005", "version": "v1", "title": "P5", "pdf_link": "L5", "published": "2026-10-14T12:00:00Z", "updated": "U5"},
    ]
    _seed_list_days(fixture_papers)

    # Mock 'today' for the test
    today_str = "2026-10-16"
    with patch("app.web.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 10, 16)
        mock_datetime.utcnow.return_value = datetime(2026, 10, 16)

        # Oldest day: 2026-10-10
        resp = app_client.get("/?day=2026-10-10")
        # The actual UTF-8 for ← (U+2190) is \xe2\x86\x90
        assert b"\xe2\x86\x90" in resp.data
        # Check if ← is greyed out (absence of <a ...day=2026-10-09)
        assert b"day=2026-10-09" not in resp.data


        # Middle day: 2026-10-11 (no papers)
        resp = app_client.get("/?day=2026-10-11")
        assert b"day=2026-10-10" in resp.data # ← works
        assert b"day=2026-10-12" in resp.data # → works

        # Today: 2026-10-16
        resp = app_client.get("/")
        assert b"day=2026-10-15" in resp.data # ← works
        # → should be greyed out
        assert b"day=2026-10-17" not in resp.data

def test_day_browsing_no_papers(app_client, temp_data_dir):
    """
    Acceptance: A day with no papers shows its one line (rule 11).
    """
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "P1", "pdf_link": "L1", "published": "2026-10-10T12:00:00Z", "updated": "U1"},
    ]
    _seed_list_days(fixture_papers)

    # Check a weekday with no papers (Tue 13 Oct 2026)
    resp = app_client.get("/?day=2026-10-13")
    assert resp.status_code == 200
    assert b"No new papers from" in resp.data
    # Papers table should not exist (but run history table should still be present)
    assert b"S.NO" not in resp.data
    assert b"DOI" not in resp.data
    assert b"TITLE" not in resp.data
    # Run history table should still be present
    assert b"Run history" in resp.data
    assert b"TIME" in resp.data

def test_day_browsing_out_of_range(app_client, temp_data_dir):
    """
    Acceptance: Out-of-range day (before oldest, after today) or malformed ?day= answers 404.
    """
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "P1", "pdf_link": "L1", "published": "2026-10-10T12:00:00Z", "updated": "U1"},
    ]
    _seed_list_days(fixture_papers)

    # Mock 'today' for the test
    with patch("app.web.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 10, 16)
        mock_datetime.utcnow.return_value = datetime(2026, 10, 16)

        # Before oldest (oldest is 10th)
        resp = app_client.get("/?day=2026-10-09")
        assert resp.status_code == 404

        # After today (today is 16th)
        resp = app_client.get("/?day=2026-10-17")
        assert resp.status_code == 404

        # Malformed
        resp = app_client.get("/?day=not-a-date")
        assert resp.status_code == 404

def test_day_browsing_run_history_consistency(app_client, temp_data_dir):
    """
    Acceptance:
    - Run history is the same on every day
    - first successful run says "First run: everything is new"
    """
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "P1", "pdf_link": "L1", "published": "2026-10-10T12:00:00Z", "updated": "U1"},
    ]
    _seed_list_days(fixture_papers)

    # Check history on day 1
    resp1 = app_client.get("/?day=2026-10-10")
    assert b"First run: everything is new" in resp1.data

    # Check history on day 2
    resp2 = app_client.get("/?day=2026-10-11")
    assert b"First run: everything is new" in resp2.data
    
    # Compare relevant parts of the data (the history section)
    # In a real test we might use BeautifulSoup, but for now we just check key strings
    assert resp1.data.split(b"Run history")[1] == resp2.data.split(b"Run history")[1]
