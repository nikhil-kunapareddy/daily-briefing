"""Rule 11: the one-line message for a day with no papers."""
import importlib
import os
import sys
from datetime import date

import pytest


@pytest.fixture
def web(tmp_path, monkeypatch):
    repo_root = os.path.abspath(os.path.join(__file__, "../.."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    import app.config as cfg
    importlib.reload(cfg)
    import app.db as dbmod
    importlib.reload(dbmod)
    import app.web as webmod

    # One successful run with a paper on Mon 12 Oct 2026 (the oldest day).
    run_id = dbmod.start_run("schedule")
    dbmod.save_papers(
        run_id,
        [{
            "stable_id": "2610.00001",
            "version": "v1",
            "title": "Seed Paper",
            "pdf_link": "https://arxiv.org/pdf/2610.00001v1.pdf",
            "published": "2026-10-12T00:00:00Z",
            "updated": "2026-10-12T00:00:00Z",
        }],
        list_day="2026-10-12",
    )
    dbmod.finish_run(run_id, new_count=1)
    return webmod


def _get(web, monkeypatch, today, path):
    monkeypatch.setattr(web, "_today_utc", lambda: today)
    client = web.create_app().test_client()
    return client.get(path)


def test_today_weekday_without_papers(web, monkeypatch):
    # Wed 21 Oct 2026
    resp = _get(web, monkeypatch, date(2026, 10, 21), "/")
    assert resp.status_code == 200
    assert b"No papers for today yet. The next check is at 02:00 UTC." in resp.data
    assert b"Last checked" in resp.data


def test_today_weekday_with_day_param(web, monkeypatch):
    resp = _get(web, monkeypatch, date(2026, 10, 21), "/?day=2026-10-21")
    assert b"No papers for today yet. The next check is at 02:00 UTC." in resp.data


def test_saturday_and_sunday_show_weekend_line(web, monkeypatch):
    for day in ("2026-10-17", "2026-10-18"):
        resp = _get(web, monkeypatch, date(2026, 10, 21), f"/?day={day}")
        assert resp.status_code == 200
        assert b"arXiv doesn&#39;t post new papers on Saturdays and Sundays." in resp.data
        assert b"Last checked" in resp.data


def test_today_on_weekend_shows_weekend_line(web, monkeypatch):
    resp = _get(web, monkeypatch, date(2026, 10, 17), "/")
    assert b"on Saturdays and Sundays." in resp.data
    assert b"next check" not in resp.data


def test_earlier_weekday_without_papers(web, monkeypatch):
    resp = _get(web, monkeypatch, date(2026, 10, 21), "/?day=2026-10-19")
    assert resp.status_code == 200
    assert b"No new papers from Mon 19 Oct 2026." in resp.data
    assert b"Last checked" in resp.data
    assert b"<table class=\"papers\"" not in resp.data


def test_day_with_papers_has_table(web, monkeypatch):
    resp = _get(web, monkeypatch, date(2026, 10, 21), "/?day=2026-10-12")
    assert b"Seed Paper" in resp.data
    assert b"1 new paper from this day" in resp.data
