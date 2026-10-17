"""Job runner for the daily‑briefing ingest lane.

The public command ``python -m app.job`` starts a scheduled run (trigger "schedule").
The core function :func:`run` performs the steps required by issue #12:

* Acquire an exclusive lock file in the data directory (rule 8).  If another
  process holds the lock, this process waits until the lock disappears.
* Create a new run record via :func:`app.db.start_run`.
* Call :func:`app.arxiv.fetch` to retrieve the latest five papers.  Any exception
  from ``fetch`` is treated as a failed run.
* Determine which papers are *new* – a paper whose ``stable_id`` has never
  appeared in any earlier successful run (rule 1).
* Save **all** fetched papers with :func:`app.db.save_papers` and finish the run
  with the appropriate ``new_count``.  On failure ``new_count`` is ``0`` and the
  error message is stored.
* Finally, the lock file is removed.
"""

from __future__ import annotations

import os
import time
import sqlite3
from pathlib import Path
from typing import List, Dict

from . import config, db, arxiv

_LOCK_NAME = "run.lock"


def _acquire_lock() -> Path:
    """Create an exclusive lock file, waiting if necessary.

    The lock file lives in ``config.DATA_ROOT``.  The function returns the
    ``Path`` object so the caller can ``unlink`` it when done.
    """
    lock_path = config.DATA_ROOT / _LOCK_NAME
    while True:
        try:
            # ``os.O_EXCL`` ensures we fail if the file already exists.
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock_path
        except FileExistsError:
            # Another run is in progress – wait a bit before retrying.
            time.sleep(1)


def run(trigger: str = "schedule", run_id: int | None = None) -> None:
    """Execute a single ingest run.

    ``trigger`` is stored in the ``runs`` table and distinguishes scheduled
    runs from manual ``run‑now`` runs.

    ``run_id`` is optional. When provided the function **must not** create a new
    run row – it will use the existing ``run_id`` (this is required for the new
    manual‑run API that creates the row in a single transaction). When ``run_id``
    is ``None`` the existing behaviour (creating a new row via :func:`db.start_run`)
    is retained.
    """
    lock_path = _acquire_lock()
    try:
        # If a run_id is supplied we assume the caller has already inserted a row
        # (e.g. via ``start_manual_run``). Otherwise we create a fresh one.
        if run_id is None:
            run_id = db.start_run(trigger)
        # The rest of the logic is unchanged: fetch papers, compute new papers
        # based on previously *successful* runs, save everything and finish the
        # run. Any exception from ``fetch`` results in a failed run with a new
        # count of 0.
        try:
            papers = arxiv.fetch()
        except Exception as exc:  # pylint: disable=broad-except
            db.finish_run(run_id, error=str(exc), new_count=0)
            return

        # Determine which stable IDs have been seen in *successful* runs.
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

        # Save all fetched papers, tagging each with the correct ``list_day``.
        # The spec expects a paper to appear under the UTC day of its ``published``
        # timestamp. Extract the date portion from the ISO‑8601 timestamp.
        from datetime import date
        for paper in papers:
            try:
                # Extract the date portion (first 10 characters: YYYY-MM-DD).
                date_str = paper["published"][:10]
                # Validate it's a valid ISO date.
                date.fromisoformat(date_str)
                list_day = date_str
            except Exception:
                # Fallback to the configured first list day if parsing fails.
                list_day = config.FIRST_LIST_DAY.isoformat()
            # Save each paper individually with its computed list_day.
            db.save_papers(run_id, [paper], list_day=list_day)
        # Record the run outcome with the count of new papers.
        db.finish_run(run_id, new_count=len(new_papers))
    finally:
        # Release the lock – ignore errors if the file was already removed.
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    # Entry point used by the schedule (cron) and the ``Run now`` endpoint.
    run(trigger="schedule")

