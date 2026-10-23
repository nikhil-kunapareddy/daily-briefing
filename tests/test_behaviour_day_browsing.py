"""Behaviour tests for the day pages, built on arXiv's list days (issue #80).

Papers are placed under the arXiv list day they were fetched for, never under their
<published> date (SPEC.md Goal, rules 7, 9, 10, 11; Done when 5 and 6).
Nothing here calls the real arXiv API: the job's fetch is mocked, and the one test
that goes through ``fetch_list_day`` mocks ``requests.get`` with fixtures/arxiv_sample.xml.
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# config reads this at import time; every test then points the data folder at its own tmp dir.
os.environ["DAILY_BRIEFING_DATA"] = tempfile.gettempdir()

from app import arxiv, db, job  # noqa: E402
from app.web import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURE_XML = REPO / "fixtures" / "arxiv_sample.xml"

# Every list day from FIRST_LIST_DAY (Thu 15 Oct) to Fri 23 Oct 2026, the "today" used below.
ALL_LIST_DAYS = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
                 "2026-10-21", "2026-10-22", "2026-10-23"]
NOW_FRI = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def paper(stable_id, title, published, version="v1"):
    return {
        "stable_id": stable_id,
        "version": version,
        "title": title,
        "pdf_link": f"https://arxiv.org/pdf/{stable_id}{version}.pdf",
        "published": published,
        "updated": published,
    }


# Papers keyed by the list day they are listed under. Their <published> dates deliberately
# differ from the list day, e.g. Alpha was submitted Wed 14 Oct but is on Friday's list.
PAPERS_BY_LIST_DAY = {
    "2026-10-15": [paper("2610.10015", "Echo Paper", "2026-10-12T12:00:00Z")],
    "2026-10-16": [
        paper("2610.10016", "Alpha Paper", "2026-10-14T15:00:00Z"),
        paper("2610.10017", "Bravo Paper", "2026-10-15T20:00:00Z"),
    ],
    "2026-10-19": [paper("2610.10019", "Charlie Paper", "2026-10-15T21:00:00Z")],
    "2026-10-20": [paper("2610.10020", "Delta Paper", "2026-10-19T09:00:00Z")],
    "2026-10-21": [paper("2610.10021", "Golf Paper", "2026-10-20T09:00:00Z")],
    "2026-10-22": [paper("2610.10022", "Hotel Paper", "2026-10-21T09:00:00Z")],
    "2026-10-23": [paper("2610.10023", "India Paper", "2026-10-22T09:00:00Z")],
}


@pytest.fixture
def data_dir(tmp_path):
    """Point the database at a fresh temporary folder for each test."""
    with patch("app.config.DATA_ROOT", tmp_path), patch("app.db._connection", None):
        yield tmp_path
        if db._connection is not None:
            db._connection.close()
            db._connection = None


@pytest.fixture
def clock():
    """Replace 'now' in the job and the web layer with a settable clock."""

    class FakeDatetime(datetime):
        current = NOW_FRI

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    with patch("app.job.datetime", FakeDatetime), patch("app.web.datetime", FakeDatetime):
        yield FakeDatetime


@pytest.fixture
def client(data_dir):
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app.test_client()


def fake_fetch_for(papers_by_day, calls):
    """A stand-in for arxiv.fetch_list_day: like the real one, a day with no entries fails."""

    def _fetch(list_day):
        calls.append(list_day)
        if list_day not in papers_by_day:
            raise RuntimeError("No entries in response")
        return list(papers_by_day[list_day])

    return _fetch


def run_job_at(clock, now, papers_by_day, calls=None):
    """Run the job once, at a fixed time, with the fetch mocked."""
    clock.current = now
    calls = [] if calls is None else calls
    with patch("app.arxiv.fetch_list_day", side_effect=fake_fetch_for(papers_by_day, calls)):
        job.run(trigger="schedule")
    return calls


def catch_up(clock, papers_by_day=PAPERS_BY_LIST_DAY, now=NOW_FRI):
    """Run the job until every list day up to today is saved (at most 5 per run)."""
    for _ in range(3):
        run_job_at(clock, now, papers_by_day)
    assert db.fetched_list_days() == ALL_LIST_DAYS


def seed_list_days(papers_by_day):
    """Save papers through the db helpers only (for cases the job can't reach, like a weekday
    with no papers), one successful run with each list day."""
    run_id = db.start_run("schedule")
    for list_day, papers in papers_by_day.items():
        db.save_papers(run_id, papers, list_day=list_day)
    db.finish_run(run_id, new_count=sum(len(p) for p in papers_by_day.values()))


# ---------------------------------------------------------------- list windows (arxiv)

@pytest.mark.parametrize(
    "list_day, start, end",
    [
        ("2026-10-16", "202610141800", "202610151759"),  # Friday: Wed 14:00 to Thu 13:59 NY
        ("2026-10-19", "202610151800", "202610161759"),  # Monday: Thu to Fri
        ("2026-10-20", "202610161800", "202610191759"),  # Tuesday: Fri to Mon
        # Across the daylight-saving change (NY goes EDT to EST on Sun 1 Nov 2026):
        # start Fri 30 Oct is EDT (UTC-4), end Mon 2 Nov 13:59 is EST (UTC-5).
        ("2026-11-03", "202610301800", "202611021859"),
        ("2026-11-06", "202611041900", "202611051859"),  # Friday after the change: both EST
    ],
)
def test_list_day_windows(list_day, start, end):
    assert arxiv._build_date_range(list_day) == (start, end)


def test_fetch_list_day_queries_its_window_and_places_papers_under_it():
    """The real fetch_list_day with the fixture: request carries the window, and the
    papers come back under that list day, not their <published> dates (2026-10-10..14)."""
    xml = FIXTURE_XML.read_text(encoding="utf-8")
    response = MagicMock(status_code=200, text=xml)
    with patch("app.arxiv.requests.get", return_value=response) as get, patch("app.arxiv.time.sleep"):
        papers = arxiv.fetch_list_day("2026-10-16")

    url = get.call_args[0][0]
    assert "submittedDate:[202610141800+TO+202610151759]" in url
    assert "max_results=5" in url
    assert len(papers) == 5

    # Save under list day 2026-10-16 (through the db helpers), then read it back by list day.
    run_id = db.start_run("schedule")
    db.save_papers(run_id, papers, list_day="2026-10-16")
    db.finish_run(run_id, new_count=5)
    listed = db.papers_for_day("2026-10-16")
    assert {p["stable_id"] for p in listed} == {p["stable_id"] for p in papers}
    assert db.papers_for_day("2026-10-10") == []  # the <published> day shows nothing


# ---------------------------------------------------------------- the job's list-day choice

def test_run_fetches_missing_list_days_oldest_first_at_most_five(clock, data_dir):
    calls = run_job_at(clock, NOW_FRI, PAPERS_BY_LIST_DAY)
    assert calls == ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]

    calls = run_job_at(clock, NOW_FRI, PAPERS_BY_LIST_DAY)
    assert calls == ["2026-10-22", "2026-10-23"]

    # Nothing missing any more: the latest list day is fetched again.
    calls = run_job_at(clock, NOW_FRI, PAPERS_BY_LIST_DAY)
    assert calls == ["2026-10-23"]
    assert db.latest_runs(limit=1)[0]["new_count"] == 0


def test_list_day_not_requested_before_0100_utc(clock, data_dir):
    # Thu 22 Oct at 00:30 UTC: its list is not up yet, so 22 is not requested.
    calls = run_job_at(clock, datetime(2026, 10, 22, 0, 30, tzinfo=timezone.utc), PAPERS_BY_LIST_DAY)
    assert "2026-10-22" not in calls
    assert calls == ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]

    calls = run_job_at(clock, datetime(2026, 10, 22, 1, 30, tzinfo=timezone.utc), PAPERS_BY_LIST_DAY)
    assert calls == ["2026-10-22"]


def test_papers_are_listed_under_the_list_day_fetched_for(clock, client):
    catch_up(clock)
    # Alpha was submitted Wed 14 Oct but sits on Friday's list.
    friday = client.get("/?day=2026-10-16").data
    assert b"Alpha Paper" in friday and b"Bravo Paper" in friday
    assert b"Charlie Paper" not in friday

    monday = client.get("/?day=2026-10-19").data
    assert b"Charlie Paper" in monday
    assert b"Alpha Paper" not in monday and b"Bravo Paper" not in monday


# ---------------------------------------------------------------- arrows and bounds

def test_arrows_move_one_day_and_grey_out_at_oldest_and_today(clock, client):
    catch_up(clock)

    # Oldest day with papers (Thu 15 Oct): ← is greyed out, → goes to the next day.
    oldest = client.get("/?day=2026-10-15").data
    assert b'<span class="day-arrow disabled">\xe2\x86\x90</span>' in oldest
    assert b'href="/?day=2026-10-14"' not in oldest
    assert b'href="/?day=2026-10-16"' in oldest

    # A middle day: both arrows move one day.
    middle = client.get("/?day=2026-10-19").data
    assert b'href="/?day=2026-10-18"' in middle
    assert b'href="/?day=2026-10-20"' in middle

    # Today (Fri 23 Oct, also at "/"): → is greyed out, ← goes back one day.
    for url in ("/", "/?day=2026-10-23"):
        today = client.get(url).data
        assert b'<span class="day-arrow disabled">\xe2\x86\x92</span>' in today
        assert b'href="/?day=2026-10-24"' not in today
        assert b'href="/?day=2026-10-22"' in today


def test_weekday_without_papers_and_saturday_show_their_one_line(clock, client):
    # Only Thu 15 and Fri 16 have papers; Wed 21 has none, and Fri 23 (today) has none yet.
    seed_list_days({
        "2026-10-15": PAPERS_BY_LIST_DAY["2026-10-15"],
        "2026-10-16": PAPERS_BY_LIST_DAY["2026-10-16"],
    })

    weekday = client.get("/?day=2026-10-21").data
    assert b"No new papers from Wed 21 Oct 2026." in weekday
    assert b'href="/?day=2026-10-20"' in weekday and b'href="/?day=2026-10-22"' in weekday
    assert b"S.NO" not in weekday

    saturday = client.get("/?day=2026-10-17").data
    # Jinja escapes the apostrophe as &#39; (browsers show it as ').
    assert b"arXiv doesn&#39;t post new papers on Saturdays and Sundays." in saturday
    assert b'href="/?day=2026-10-16"' in saturday and b'href="/?day=2026-10-18"' in saturday
    assert b"S.NO" not in saturday

    today = client.get("/").data
    assert b"No papers for today yet. The next check is at 02:00 UTC." in today
    assert b"Last checked" in today


def test_out_of_range_or_malformed_day_is_404(clock, client):
    catch_up(clock)
    assert client.get("/?day=2026-10-14").status_code == 404   # before the oldest list day
    assert client.get("/?day=2026-10-24").status_code == 404   # after today
    assert client.get("/?day=2026-02-30").status_code == 404   # no such date
    assert client.get("/?day=not-a-date").status_code == 404
    assert client.get("/?day=2026-10-23").status_code == 200   # today is allowed


# ---------------------------------------------------------------- run history stays the same

def test_run_history_is_the_same_on_every_day(clock, client):
    seed_list_days({"2026-10-15": PAPERS_BY_LIST_DAY["2026-10-15"]})
    first = client.get("/?day=2026-10-15").data
    second = client.get("/?day=2026-10-16").data
    assert b"First run: everything is new" in first
    assert first.split(b"Run history")[1] == second.split(b"Run history")[1]
