"""Job runner for the daily‑briefing ingest lane.

The public command ``python -m app.job`` starts a scheduled run (trigger "schedule").
The core function :func:`run` performs the steps required by issue #73:

* Acquire an exclusive lock file in the data directory (rule 8). If another process
  holds the lock, this process waits until the lock disappears.
* Create a new run record via :func:`app.db.start_run` (unless a ``run_id`` is
  supplied, in which case the caller has already inserted a row).
* Determine which *list days* have already been fetched successfully via
  :func:`app.db.fetched_list_days`. For any missing list days from
  ``config.FIRST_LIST_DAY`` up to today (Friday, Monday, Tuesday only), fetch up
  to five of the oldest missing days using :func:`app.arxiv.fetch_list_day`.
* If there are no missing list days, fall back to the legacy ``arxiv.fetch``
  (the latest five papers).
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
from datetime import datetime, timezone, timedelta

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

    List days are the weekdays Friday (4), Monday (0) and Tuesday (1) as defined by
    the ingest specification.
    """
    start = config.FIRST_LIST_DAY
    today = datetime.now(timezone.utc).date()
    day = start
    dates: List[str] = []
    while day <= today:
        if day.weekday() in (4, 0, 1):  # Friday, Monday, Tuesday
            dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


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
        needed = [d for d in _list_day_dates() if d not in fetched]
        to_fetch = needed[:5]  # at most five per run, oldest first

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
            # No missing list days – fall back to the legacy latest fetch.
            try:
                papers = arxiv.fetch()
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
            db.save_papers(run_id, papers, list_day=None)

        db.finish_run(run_id, new_count=new_total)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    run(trigger="schedule")
