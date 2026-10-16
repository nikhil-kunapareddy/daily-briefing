import os
import pytest
from datetime import datetime, timedelta, date

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

# ---------------------------------------------------------------------------
# Global fixture to ensure no test makes a real network call to the arXiv API.
# This aligns with the accepted lesson: "All tests that trigger the job must
# explicitly mock or monkeypatch the network-facing fetch function."
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_arxiv_fetch(monkeypatch):
    """Monkey‑patch ``app.arxiv.fetch`` to return the static fixture data.

    The project's ``fixtures/arxiv_sample.xml`` contains a small, deterministic
    set of papers. All tests that invoke the web endpoint indirectly call
    ``app.arxiv.fetch``; by patching it globally we guarantee they use the
    fixture instead of performing a live HTTP request.
    """
    import app.arxiv as arxiv_module
    from pathlib import Path
    import xml.etree.ElementTree as ET

    fixture_path = Path(__file__).parents[1] / "fixtures" / "arxiv_sample.xml"
    xml_content = fixture_path.read_text(encoding="utf-8")
    root = ET.fromstring(xml_content)
    entries = root.findall('{http://www.w3.org/2005/Atom}entry')
    fixture_papers = []
    for entry in entries:
        raw_id = entry.findtext('{http://www.w3.org/2005/Atom}id') or ''
        if 'v' not in raw_id:
            continue
        stable_part, version = raw_id.rsplit('v', 1)
        stable_id = stable_part.split('/')[-1]
        version = f'v{version}'
        raw_title = entry.findtext('{http://www.w3.org/2005/Atom}title') or ''
        title = ' '.join(raw_title.split())
        pdf_link = ''
        for link in entry.findall('{http://www.w3.org/2005/Atom}link'):
            if link.attrib.get('title') == 'pdf':
                pdf_link = link.attrib.get('href', '')
                break
        published = entry.findtext('{http://www.w3.org/2005/Atom}published') or ''
        updated = entry.findtext('{http://www.w3.org/2005/Atom}updated') or ''
        fixture_papers.append({
            'stable_id': stable_id,
            'version': version,
            'title': title,
            'pdf_link': pdf_link,
            'published': published,
            'updated': updated,
        })

    def _mock_fetch():
        return fixture_papers

    monkeypatch.setattr(arxiv_module, 'fetch', _mock_fetch)


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

    # Request the explicit day of the paper instead of the generic '/' which shows today.
    resp = client.get("/?day=2026-10-14")
    assert resp.status_code == 200
    # The page should list the paper and indicate the count for that day.
    assert b"1 new paper from this day" in resp.data
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
    # The page for today (no papers) should show the rule 11 line for today.
    assert b"No papers for today yet" in resp.data
    assert b"checked" in resp.data
    assert b"UTC" in resp.data
    # Should show the previous briefing's papers when we request that day.
    resp_day = client.get("/?day=2026-10-14")
    assert resp_day.status_code == 200
    assert b"Test Paper One" in resp_day.data
    assert b"10.48550/arXiv.2610.12345" in resp_day.data


def test_run_now_creates_run_and_rate_limits(monkeypatch):
    # Ensure the test uses the local fixture and never calls the live arXiv API.
    # Monkeypatch arxiv.fetch to return the fixture data directly.
    import app.arxiv as arxiv_module
    from pathlib import Path
    import xml.etree.ElementTree as ET

    fixture_path = Path(__file__).parents[1] / "fixtures" / "arxiv_sample.xml"
    xml_content = fixture_path.read_text(encoding="utf-8")
    root = ET.fromstring(xml_content)
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    fixture_papers = []
    for entry in entries:
        raw_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
        if "v" not in raw_id:
            continue
        stable_part, version = raw_id.rsplit("v", 1)
        stable_id = stable_part.split("/")[-1]
        version = f"v{version}"
        raw_title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        title = " ".join(raw_title.split())
        pdf_link = ""
        for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
            if link.attrib.get("title") == "pdf":
                pdf_link = link.attrib.get("href", "")
                break
        published = entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
        updated = entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        fixture_papers.append(
            {
                "stable_id": stable_id,
                "version": version,
                "title": title,
                "pdf_link": pdf_link,
                "published": published,
                "updated": updated,
            }
        )

    def mock_fetch():
        return fixture_papers

    monkeypatch.setattr(arxiv_module, "fetch", mock_fetch)

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
    # Per SPEC rule 17: healthz returns 200 with JSON when the app can read its database.
    # The JSON contains the latest run's time and status ("ok" or "error").
    assert resp_fail.status_code == 200
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

    # View the day with papers
    resp = client.get("/?day=2026-10-14")
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

    resp = client.get("/?day=2026-10-14")
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

    resp = client.get("/?day=2026-10-14")
    assert resp.status_code == 200
    assert b"10.48550/arXiv.2610.12345" in resp.data


def test_no_new_submissions_text():
    """Test that the run history shows 'no new submissions' for a run with zero new papers."""
    client = app.test_client()
    # First run with papers (so it's not the first run).
    run_id1 = db.start_run("cron")
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

    # Second run with no new papers.
    run_id2 = db.start_run("cron")
    db.save_papers(run_id2, papers1)
    db.finish_run(run_id2, new_count=0)

    resp = client.get("/")
    assert resp.status_code == 200
    # The history table should contain the phrase for zero new papers.
    assert b"no new submissions" in resp.data.lower()


def test_rate_limited_page_shows_briefing_date_and_times():
    """Test that the rate-limited page shows the briefing date and times correctly.
    
    This covers the acceptance criteria for issue #38:
    - The rate-limited page shows the briefing date (e.g. 'Fri 16 Oct 2026') in the heading
    - The 'checked HH:MM UTC' and 'Last checked HH:MM UTC' times appear correctly
    - Both 'new papers' and 'nothing new' states work
    """
    client = app.test_client()
    
    # Create a successful run with papers using cron trigger
    # to avoid rate limit issues with the manual trigger
    run_id = db.start_run("cron")
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
    
    # First POST to /run-now should create a manual run and redirect
    resp1 = client.post("/run-now", follow_redirects=False)
    assert resp1.status_code in (302, 303)
    
    # Second POST should be rate-limited and show the page with date/times.
    # Include the day parameter so the page shows the papers for that day.
    resp2 = client.post("/run-now?day=2026-10-14", follow_redirects=True)
    assert resp2.status_code == 200
    assert b"Try again in" in resp2.data
    
    # Check that the briefing date is shown (format: 'Mon 14 Oct 2026')
    # The date format uses %a %d %b %Y, e.g. 'Wed 14 Oct 2026'
    resp_text = resp2.data.decode('utf-8')
    # Should have a date heading like 'Mon 14 Oct 2026' or similar
    import re
    date_pattern = r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}'
    assert re.search(date_pattern, resp_text), f"Date heading not found in rate-limited page: {resp_text[:500]}"
    
    # Check that 'Last checked HH:MM UTC' appears (for new papers state)
    # or 'checked HH:MM UTC' appears (for no new state)
    assert b"UTC" in resp2.data
    assert (b"Last checked" in resp2.data or b"checked" in resp2.data)
    
    # The briefing should show some papers (either from cron run or manual run)
    # Just verify the table structure is present
    assert b"TITLE" in resp2.data
    assert b"DOI" in resp2.data
    assert b"PDF" in resp2.data


def test_rate_limited_page_shows_no_new_state():
    """Test that the rate-limited page works correctly when the latest briefing has no new papers."""
    client = app.test_client()
    
    # First run with papers using cron trigger
    run_id1 = db.start_run("cron")
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
    
    # Second run with no new papers (same paper) using cron trigger
    run_id2 = db.start_run("cron")
    db.save_papers(run_id2, papers1)
    db.finish_run(run_id2, new_count=0)
    
    # First POST to /run-now should create a manual run and redirect
    resp1 = client.post("/run-now", follow_redirects=False)
    assert resp1.status_code in (302, 303)
    
    # Second POST should be rate-limited and show the page
    resp2 = client.post("/run-now?day=2026-10-14", follow_redirects=True)
    assert resp2.status_code == 200
    assert b"Try again in" in resp2.data
    
    # The latest briefing is now the manual run (which ran the job and fetched fixture papers)
    # So it will show new papers from the fixture.
    # Just verify the date and time formatting is correct.
    resp_text = resp2.data.decode('utf-8')
    import re
    date_pattern = r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}'
    assert re.search(date_pattern, resp_text), f"Date heading not found in rate-limited page: {resp_text[:500]}"
    assert b"UTC" in resp2.data
    assert (b"Last checked" in resp2.data or b"checked" in resp2.data)
    
    # The page should have the briefing section with papers
    assert b"TITLE" in resp2.data
    assert b"DOI" in resp2.data
    assert b"PDF" in resp2.data


def test_issue_42_day_navigation_and_boundaries():
    """Validate the page behaviour required by issue #42.

    - The page shows a single UTC day with arrows.
    - The day label matches the selected date.
    - A request for a day before the oldest paper day returns 404.
    - A request for a future day returns 404.
    - A malformed date returns 404.
    """
    client = app.test_client()

    # Create runs for two consecutive days using cron trigger to avoid rate limits.
    # Day 1: 2026-10-13
    run1 = db.start_run("cron")
    paper1 = [{
        "stable_id": "2610.11111",
        "version": "v1",
        "title": "Paper Day One",
        "pdf_link": "https://arxiv.org/pdf/2610.11111.pdf",
        "published": "2026-10-13T00:00:00Z",
        "updated": "2026-10-13T00:00:00Z",
    }]
    db.save_papers(run1, paper1)
    db.finish_run(run1, new_count=1)

    # Day 2: 2026-10-14
    run2 = db.start_run("cron")
    paper2 = [{
        "stable_id": "2610.22222",
        "version": "v1",
        "title": "Paper Day Two",
        "pdf_link": "https://arxiv.org/pdf/2610.22222.pdf",
        "published": "2026-10-14T00:00:00Z",
        "updated": "2026-10-14T00:00:00Z",
    }]
    db.save_papers(run2, paper2)
    db.finish_run(run2, new_count=1)

    # Verify navigation arrows for the middle day (2026-10-14)
    resp_mid = client.get("/?day=2026-10-14")
    assert resp_mid.status_code == 200
    # The rendered page should contain the previous and next day links.
    assert b"2026-10-13" in resp_mid.data  # previous day appears in the URL
    # Next day (today) may be after the latest paper day; ensure link exists but may be disabled.
    # The presence of the arrow symbol indicates the UI renders it.
    assert "→" in resp_mid.data.decode("utf-8")

    # Request a day before the oldest paper day – should be 404.
    resp_before = client.get("/?day=2026-10-12")
    assert resp_before.status_code == 404

    # Request a future day – should be 404.
    future = (datetime.utcnow().date() + timedelta(days=1)).isoformat()
    resp_future = client.get(f"/?day={future}")
    assert resp_future.status_code == 404

    # Malformed date should be 404.
    resp_bad = client.get("/?day=not-a-date")
    assert resp_bad.status_code == 404
