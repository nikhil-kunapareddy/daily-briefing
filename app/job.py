"""Job runner for the daily‑briefing ingest lane.

The public command ``python -m app.job`` starts a scheduled run (trigger "schedule").
The core function :func:`run` performs the steps required by the spec:

* Acquire an exclusive lock file in the data directory (rule 8). If another process
  holds the lock, this process waits until the lock disappears.
* Create a new run record via :func:`app.db.start_run` (unless a ``run_id`` is
  supplied, in which case the caller has already inserted a row).
* Determine which *list days* have already been fetched successfully via
  :func:`app.db.fetched_list_days`. For any missing list days from
  ``config.FIRST_LIST_DAY`` up to today (weekdays Monday–Friday), fetch up
  to five of the oldest missing days using :func:`app.arxiv.fetch_list_day`.
* A list day D is only fetchable from 01:00 UTC on D. A run before that time
  must not request D.
* If there are no missing list days, fetch the latest list day again with
  :func:`app.arxiv.fetch_list_day`, and save its papers with that list day.
  Drop the fallback to the old ``fetch()`` (the 5 newest overall, saved with no list day).
* For each fetched batch, compute the number of *new* papers – those whose
  ``stable_id`` has never appeared in a successful run.
* Save all fetched papers with :func:`app.db.save_papers`, passing the
  ``list_day`` when available, and finally record the run outcome with the
  total count of new papers.
"""

from __future__ import annotations

import os
import time
import sqlite3
from pathlib import Path
from typing import List
from datetime import datetime, date, timezone, timedelta

from . import config, db, arxiv

_LOCK_NAME = "run.lock"


def _acquire_lock() -> Path:
    """Create an exclusive lock file, waiting if necessary.

    The lock file lives in ``config.DATA_ROOT``. The function returns the ``Path``
    object so the caller can ``unlink`` it when done.
    """
    lock_path = config.DATA_ROOT / _LOCK_NAME
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            time.sleep(1)


def _list_day_dates() -> List[str]:
    """Return ISO‑date strings for every list day from ``FIRST_LIST_DAY`` up to today.

    List days are weekdays Monday–Friday (0–4) as defined by the ingest specification.
    """
    start = config.FIRST_LIST_DAY
    today = datetime.now(timezone.utc).date()
    day = start
    dates: List[str] = []
    while day <= today:
        if day.weekday() < 5:  # Monday–Friday
            dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


def _available_list_days() -> List[str]:
    """Return list days that are available for fetching (i.e., 01:00 UTC has passed on that day)."""
    now = datetime.now(timezone.utc)
    all_days = _list_day_dates()
    available = []
    for d_str in all_days:
        d = date.fromisoformat(d_str)
        # 01:00 UTC on day d
        cutoff = datetime.combine(d, datetime.min.time()).replace(hour=1, tzinfo=timezone.utc)
        if now >= cutoff:
            available.append(d_str)
    return available


def run(trigger: str = "schedule", run_id: int | None = None) -> None:
    """Execute a single ingest run.

    ``trigger`` is stored in the ``runs`` table and distinguishes scheduled runs
    from manual ``run‑now`` runs.

    ``run_id`` is optional. When provided the function **must not** create a new
    run row – it will use the existing ``run_id`` (required for the manual‑run API
    that creates the row in a single transaction). When ``run_id`` is ``None`` a
    new row is created via :func:`db.start_run`.
    """
    lock_path = _acquire_lock()
    try:
        if run_id is None:
            run_id = db.start_run(trigger)

        # Identify list days that still need fetching.
        fetched = set(db.fetched_list_days())
        needed_all = [d for d in _available_list_days() if d not in fetched]
        to_fetch = needed_all[:5]  # at most five per run, oldest first

        new_total = 0
        if to_fetch:
            for ld in to_fetch:
                try:
                    papers = arxiv.fetch_list_day(ld)
                except Exception as exc:  # pylint: disable=broad-except
                    db.finish_run(run_id, error=str(exc), new_count=0)
                    return

                # Compute new papers against all prior successful runs.
                conn = db._get_connection()
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT DISTINCT stable_id FROM papers WHERE run_id IN ("
                    "SELECT id FROM runs WHERE error IS NULL"
                    ")"
                )
                seen_ids = {row[0] for row in cur.fetchall()}
                new_papers = [p for p in papers if p["stable_id"] not in seen_ids]
                new_total += len(new_papers)
                db.save_papers(run_id, papers, list_day=ld)
        else:
            # No missing list days – fetch the latest list day again.
            # Determine the latest available list day (could be today if past 01:00 UTC).
            available = _available_list_days()
            if not available:
                # No list day available yet (should not happen after FIRST_LIST_DAY)
                db.finish_run(run_id, error="No list day available", new_count=0)
                return
            latest_day = available[-1]  # newest
            try:
                papers = arxiv.fetch_list_day(latest_day)
            except Exception as exc:  # pylint: disable=broad-except
                db.finish_run(run_id, error=str(exc), new_count=0)
                return

            conn = db._get_connection()
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT stable_id FROM papers WHERE run_id IN ("
                "SELECT id FROM runs WHERE error IS NULL"
                ")"
            )
            seen_ids = {row[0] for row in cur.fetchall()}
            new_papers = [p for p in papers if p["stable_id"] not in seen_ids]
            new_total = len(new_papers)
            db.save_papers(run_id, papers, list_day=latest_day)

        db.finish_run(run_id, new_count=new_total)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    run(trigger="schedule")
