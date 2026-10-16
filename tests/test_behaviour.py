import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import sqlite3
from flask import Flask

# We must set this BEFORE importing app components that check it.
os.environ["DAILY_BRIEFING_DATA"] = tempfile.gettempdir()

from app.web import create_app
from app import db, job, arxiv

@pytest.fixture
def temp_data_dir():
    """Create a temporary data directory for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.config.DATA_ROOT", Path(tmpdir)):
            with patch("app.db._connection", None):
                yield Path(tmpdir)

@pytest.fixture
def app_client(temp_data_dir):
    """Flask test client for the web app."""
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app.test_client()

def test_behaviour_full_flow(temp_data_dir):
    """
    Exercises the whole flow across lanes:
    1. Initial run with fixture.
    2. Second run with same fixture (0 new).
    3. Version rule check (v1 -> v2 is not new).
    4. Titles whitespace collapse.
    """
    fixture_papers = [
        {"stable_id": "2610.10001", "version": "v1", "title": "First Paper Title", "pdf_link": "...", "published": "...", "updated": "..."},
        {"stable_id": "2610.10002", "version": "v2", "title": "Second   Paper   Title", "pdf_link": "...", "published": "...", "updated": "..."},
        {"stable_id": "2610.10003", "version": "v1", "title": "Third\nPaper Title", "pdf_link": "...", "published": "...", "updated": "..."},
        {"stable_id": "2610.10004", "version": "v1", "title": "Fourth Paper Title", "pdf_link": "...", "published": "...", "updated": "..."},
        {"stable_id": "2610.10005", "version": "v1", "title": "Fifth Paper Title", "pdf_link": "...", "published": "...", "updated": "..."},
    ]

    with patch("app.arxiv.fetch", return_value=fixture_papers):
        job.run(trigger="schedule")
        
        latest = db.latest_briefing()
        assert latest is not None
        assert latest["new_count"] == 5
        assert latest["is_first_run"] is True
        
        papers = latest["papers"]
        assert papers[1]["title"] == "Second Paper Title"
        
        job.run(trigger="schedule")
        
        latest = db.latest_briefing()
        assert latest["new_count"] == 0
        
        updated_papers = [
            {"stable_id": "2610.10001", "version": "v2", "title": "First Paper Title v2", "pdf_link": "...", "published": "...", "updated": "..."},
            {"stable_id": "2610.10006", "version": "v1", "title": "Sixth Paper", "pdf_link": "...", "published": "...", "updated": "..."},
        ]
        with patch("app.arxiv.fetch", return_value=updated_papers):
            job.run(trigger="schedule")
            
            latest = db.latest_briefing()
            assert latest["new_count"] == 1
            
            papers = latest["papers"]
            assert any(p["stable_id"] == "2610.10006" for p in papers)
            assert any(p["stable_id"] == "2610.10001" for p in papers)

def test_behaviour_fetch_failures(temp_data_dir):
    """
    Tests that failed fetches, empty responses, and arXiv errors are saved as failed runs.
    """
    with patch("app.arxiv.fetch", side_effect=RuntimeError("Request error")):
        job.run(trigger="schedule")
        
    runs = db.latest_runs(limit=1)
    assert len(runs) == 1
    assert runs[0]["ok"] is False
    assert "Request error" in runs[0]["error_short"]

    with patch("app.arxiv.fetch", side_effect=RuntimeError("No entries in response")):
        job.run(trigger="schedule")
        
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is False
    assert "No entries in response" in runs[0]["error_short"]

    with patch("app.arxiv.fetch", side_effect=RuntimeError("ArXiv error entry encountered")):
        job.run(trigger="schedule")
        
    runs = db.latest_runs(limit=1)
    assert runs[0]["ok"] is False
    assert "ArXiv error entry encountered" in runs[0]["error_short"]

def test_behaviour_run_now_rate_limit(app_client, temp_data_dir):
    """
    Tests the 'Run now' rate limit and concurrent presses.
    """
    resp = app_client.post("/run-now")
    assert resp.status_code == 302

    resp = app_client.post("/run-now")
    assert resp.status_code == 200
    assert b"Rate limit" in resp.data or b"minutes" in resp.data

def test_behaviour_healthz(app_client, temp_data_dir):
    """
    Tests the /healthz endpoint.
    """
    resp = app_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {}

    with patch("app.arxiv.fetch", return_value=[{"stable_id": "1", "version": "v1", "title": "T", "pdf_link": "L", "published": "P", "updated": "U"}]):
        job.run(trigger="schedule")
    
    resp = app_client.get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "latest_run_time" in data
    assert data["status"] == "ok"

def test_behaviour_healthz_failed_run(app_client, temp_data_dir):
    """
    Tests /healthz returns 503 when the latest run has an error.
    """
    with patch("app.arxiv.fetch", side_effect=RuntimeError("Failed")):
        job.run(trigger="schedule")
    
    resp = app_client.get("/healthz")
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "error"
