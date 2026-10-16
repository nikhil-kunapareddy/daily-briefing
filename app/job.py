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


def run(trigger: str = "schedule", *, run_id: int | None = None) -> None:
    """Execute a single ingest run.

    Supports both scheduled runs (default) and manual runs invoked via the ``Run now`` endpoint. If ``run_id`` is ``None`` a new run record is created via :func:`app.db.start_run`. When a ``run_id`` is supplied (the manual case) the existing run is used and no additional row is inserted.
    """
    lock_path = _acquire_lock()
    try:
        # Determine which run identifier to use.
        if run_id is None:
            # Scheduled execution – create a fresh run entry.
            run_id = db.start_run(trigger)
        # Otherwise we already have the manual run ID.

        try:
            papers = arxiv.fetch()
        except Exception as exc:  # pylint: disable=broad-except
            # Any exception from ``fetch`` counts as a failed run.
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

        # Save all fetched papers (the spec says to call ``save_papers`` with the full list).
        db.save_papers(run_id, papers)
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

