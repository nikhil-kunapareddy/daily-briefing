"""Database helpers for the ingest lane.

All functions work with a SQLite database stored under ``app/config.py``'s
``DATA_ROOT``. The schema is created lazily the first time a connection is
opened.
"""

import sqlite3
import threading
from datetime import datetime, date, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import config

_conn_lock = threading.Lock()
_connection: Optional[sqlite3.Connection] = None


def _get_connection() -> sqlite3.Connection:
    """Return a thread‑safe SQLite connection, creating the schema if needed."""
    global _connection
    if _connection is None:
        with _conn_lock:
            if _connection is None:
                db_path = config.DATA_ROOT / "daily_briefing.db"
                # Ensure the directory exists.
                config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(db_path), detect_types=sqlite3.PARSE_DECLTYPES)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA foreign_keys=ON;")
                conn.execute("PRAGMA busy_timeout=5000;")
                _initialize_schema(conn)
                _connection = conn
    return _connection  # type: ignore[return-value]


def _initialize_schema(conn: sqlite3.Connection) -> None:
    """Create tables and indexes required by the ingest lane.

    The schema is deliberately simple: a ``runs`` table tracks each execution and a
    ``papers`` table stores the fetched paper metadata linked to a run.
    Handles additive migrations for existing databases.
    """
    cur = conn.cursor()
    # First, create tables without the list_day column in the papers definition
    # (we'll add it via migration if needed). This avoids issues with CREATE TABLE IF NOT EXISTS
    # on tables that exist with a different schema.
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            trigger TEXT NOT NULL,
            finished_at TEXT,
            error TEXT,
            new_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            stable_id TEXT NOT NULL,
            version TEXT NOT NULL,
            title TEXT NOT NULL,
            pdf_link TEXT NOT NULL,
            published TEXT NOT NULL,
            updated TEXT NOT NULL,
            UNIQUE(stable_id, version)
        );

        CREATE INDEX IF NOT EXISTS idx_papers_stable_id ON papers(stable_id);
        CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);

        -- New table to associate a paper version with the arXiv list day it was fetched for.
        CREATE TABLE IF NOT EXISTS paper_list_days (
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            stable_id TEXT NOT NULL,
            list_day TEXT NOT NULL,
            PRIMARY KEY (run_id, stable_id, list_day)
        );

        CREATE INDEX IF NOT EXISTS idx_paper_list_days_day ON paper_list_days(list_day);
        """
    )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start_run(trigger: str) -> int:
    """Insert a new run record and return its ``id``.

    ``trigger`` is a short string identifying what caused the run (e.g. "cron",
    "manual").
    """
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO runs (started_at, trigger) VALUES (?, ?)",
        (_now_iso(), trigger),
    )
    conn.commit()
    return cur.lastrowid


def start_manual_run() -> int:
    """Start a manual run, enforcing a 10‑minute rate limit.

    Returns the run id if allowed, otherwise returns minutes to wait (int).
    The check and the new run record are done in one transaction.
    """
    conn = _get_connection()
    cur = conn.cursor()
    # Use a transaction to check rate limit and insert atomically.
    cur.execute("BEGIN IMMEDIATE")
    try:
        # Find the most recent manual run.
        cur.execute(
            "SELECT started_at FROM runs WHERE trigger = 'manual' ORDER BY started_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row:
            last_time = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - last_time
            if delta.total_seconds() < 600:
                # Rate limited: compute minutes to wait (ceiling).
                wait_seconds = 600 - delta.total_seconds()
                wait_minutes = int((wait_seconds + 59) // 60)
                cur.execute("ROLLBACK")
                return wait_minutes
        # Not rate limited: insert the new manual run.
        cur.execute(
            "INSERT INTO runs (started_at, trigger) VALUES (?, ?)",
            (_now_iso(), "manual"),
        )
        run_id = cur.lastrowid
        cur.execute("COMMIT")
        return run_id
    except Exception:
        cur.execute("ROLLBACK")
        raise


def save_papers(
    run_id: int,
    papers: Iterable[Dict[str, Any]],
    list_day: str | None = None,
) -> None:
    """Insert paper records linked to ``run_id``.

    ``papers`` is an iterable of dicts with keys:
    ``stable_id, version, title, pdf_link, published, updated``.
    Whitespace in the title is collapsed to a single space.

    If ``list_day`` is provided, a row is added to the new ``paper_list_days``
    table for each paper, linking the ``run_id`` and ``stable_id`` to the list
    day. When omitted, no ``paper_list_days`` rows are created – callers must
    explicitly request a list‑day record.
    """

    conn = _get_connection()
    cur = conn.cursor()
    for p in papers:
        title = " ".join(p["title"].split())
        # Insert the paper record (unchanged schema – no list_day column).
        cur.execute(
            "INSERT OR IGNORE INTO papers (run_id, stable_id, version, title, pdf_link, published, updated)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                p["stable_id"],
                p["version"],
                title,
                p["pdf_link"],
                p["published"],
                p["updated"],
            ),
        )
        # Record the list day if supplied.
        if list_day is not None:
            cur.execute(
                "INSERT OR IGNORE INTO paper_list_days (run_id, stable_id, list_day) VALUES (?, ?, ?)",
                (run_id, p["stable_id"], list_day),
            )
    conn.commit()


def finish_run(run_id: int, error: Optional[str] = None, new_count: int = 0) -> None:
    """Mark a run as finished, optionally storing an error string and new count."""
    conn = _get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE runs SET finished_at = ?, error = ?, new_count = ? WHERE id = ?",
        (_now_iso(), error, new_count, run_id),
    )
    conn.commit()


def _run_summary(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "trigger": row["trigger"],
        "new_count": row["new_count"],
        "error": row["error"],
    }


def latest_briefing() -> Optional[Dict[str, Any]]:
    """Return a dict for the most recent *successful* run.

    The dict contains ``date`` (ISO string), ``new_count``, ``papers`` (list of
    dicts) and ``is_first_run`` (bool).
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM runs WHERE error IS NULL ORDER BY started_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    run = _run_summary(row)
    cur.execute(
        "SELECT stable_id, version, title, pdf_link, published, updated FROM papers WHERE run_id = ?",
        (run["id"],),
    )
    papers = [dict(r) for r in cur.fetchall()]
    # Determine if this is the first successful run.
    cur.execute("SELECT COUNT(*) FROM runs WHERE error IS NULL")
    total = cur.fetchone()[0]
    return {
        "date": run["started_at"],
        "new_count": run["new_count"],
        "papers": papers,
        "is_first_run": total == 1,
    }


def last_briefing_with_papers() -> Optional[Dict[str, Any]]:
    """Like ``latest_briefing`` but returns the last run that *found* papers.

    If the latest successful run had zero new papers we walk back until a run
    with ``new_count > 0``.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM runs WHERE error IS NULL AND new_count > 0 ORDER BY started_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    run = _run_summary(row)
    cur.execute(
        "SELECT stable_id, version, title, pdf_link, published, updated FROM papers WHERE run_id = ?",
        (run["id"],),
    )
    papers = [dict(r) for r in cur.fetchall()]
    return {
        "date": run["started_at"],
        "new_count": run["new_count"],
        "papers": papers,
    }


def latest_runs(limit: int = 10) -> List[Dict[str, Any]]:
    """Return a list of recent runs ordered newest first.

    Each dict contains ``id``, ``time``, ``ok`` (bool | None), ``trigger``, ``new_count`` and a
    short ``error_short`` (first line of error if any).
    ``ok`` is ``None`` for in-progress runs (``finished_at`` is NULL), ``True`` for
    successful finished runs, and ``False`` for failed finished runs.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
    )
    runs: List[Dict[str, Any]] = []
    for r in cur.fetchall():
        # A run is considered "ok" (shown with a checkmark) only when it has
        # finished and did not error. In-progress runs have ``finished_at`` NULL
        # and should have ``ok`` set to ``None`` so the UI can hide the checkmark.
        ok_flag: bool | None = None
        if r["finished_at"] is not None:
            ok_flag = r["error"] is None
        runs.append(
            {
                "id": r["id"],
                "time": r["started_at"],
                "ok": ok_flag,
                "trigger": r["trigger"],
                "new_count": r["new_count"],
                "error_short": (r["error"].splitlines()[0] if r["error"] else None),
            }
        )
    return runs


def fetched_list_days() -> List[str]:
    """Return a list of ISO‑format ``list_day`` strings for successful runs.

    The function queries distinct ``list_day`` values from the new
    ``paper_list_days`` table where the associated run succeeded. The result is
    sorted chronologically (oldest first) as required by the spec.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT pld.list_day
        FROM paper_list_days pld
        JOIN runs r ON pld.run_id = r.id
        WHERE r.error IS NULL
        ORDER BY pld.list_day ASC
        """
    )
    return [row[0] for row in cur.fetchall()]


def health() -> Optional[Dict[str, Any]]:
    """Return a tiny health dict or ``None`` if no runs exist yet.

    ``latest_run_time`` is the ISO timestamp of the most recent run (finished or
    in‑progress) and ``status`` is ``"ok"`` for a successful run or ``"error"``
    otherwise.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    status = "ok" if row["error"] is None else "error"
    return {"latest_run_time": row["started_at"], "status": status}


def papers_for_day(day: date | str) -> List[Dict[str, Any]]:
    """Return papers published on the given UTC day, ordered newest first.

    The web layer may pass a ``str`` (e.g. from a query parameter) instead of a
    ``datetime.date`` instance. This function now accepts both and attempts to
    coerce a string to a ``date`` using ``date.fromisoformat``. If conversion
    fails, a ``ValueError`` is raised to make the caller aware of the invalid
    input.

    Args:
        day: Either a ``date`` object or an ISO‑format ``YYYY‑MM‑DD`` string
            representing the UTC day.

    Returns:
        A list of dicts, each with keys ``stable_id``, ``version``, ``title``,
        ``pdf_link``, ``published``, ``updated``. Ordered by ``published`` DESC.
    """
    # Accept string input from the web layer and convert to ``date``.
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except Exception as exc:
            raise ValueError(f"Invalid date string '{day}': {exc}") from exc

    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    day_str = day.isoformat()
    # Join with paper_list_days to get papers associated with the requested list_day.
    cur.execute(
        """
        SELECT p.stable_id, p.version, p.title, p.pdf_link, p.published, p.updated
        FROM papers p
        JOIN paper_list_days pld ON p.run_id = pld.run_id AND p.stable_id = pld.stable_id
        WHERE pld.list_day = ?
        ORDER BY p.published DESC
        """,
        (day_str,),
    )
    return [dict(r) for r in cur.fetchall()]


def oldest_paper_day() -> Optional[date]:
    """Return the earliest list day for which we have saved papers.

    The original implementation looked at the ``published`` timestamp, but the
    spec now defines ``list_day`` as the canonical day a paper belongs to. We
    therefore query the minimum ``list_day`` value from the ``papers`` table.

    Returns:
        A ``date`` representing the earliest ``list_day`` present, or ``None``
        when the database contains no papers.
    """
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MIN(list_day) FROM paper_list_days
        """
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return date.fromisoformat(row[0])
