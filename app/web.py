"""Web (HTTP) layer for the daily‑briefing application.

This module lives in the *builder‑web* lane.  It provides the Flask
application factory ``create_app()`` that is used by gunicorn (see the
Spec).  The routes implement the public page (GET ``/``), the *Run now*
endpoint (POST ``/run-now``) and the health check (GET ``/healthz``).

All data access goes through :pymod:`app.db`.  The module deliberately
contains only the minimal logic required by the specification – more
behaviour (e.g. the actual background job) is added by other lanes.

The HTML template lives in ``app/templates/index.html`` and the CSS in
``app/static/style.css``.  The template is deliberately simple but
exposes the variables the spec describes so that the forthcoming tests
can verify the layout.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for, abort

from . import db, job


def _format_date(iso: str) -> str:
    """Return a date string like 'Wed 14 Oct 2026' from an ISO timestamp."""
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b %Y")


def _format_time(iso: str) -> str:
    """Return a time string like '14:32' from an ISO timestamp (UTC)."""
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%H:%M")


def _format_datetime(iso: str) -> str:
    """Return a date and time string like 'Wed 14 Oct 2026, 14:32 UTC'."""
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b %Y, %H:%M UTC")


def _format_day(d: date) -> str:
    """Return a date string like 'Wed 14 Oct 2026' from a date object."""
    return d.strftime("%a %d %b %Y")


def _today_utc() -> date:
    """Return today's date in UTC."""
    return datetime.now(timezone.utc).date()


def _parse_day_param(day_str: str) -> date:
    """Parse YYYY-MM-DD string to date, raise ValueError if invalid."""
    return date.fromisoformat(day_str)


def _empty_day_message(day: date, today: date) -> str:
    """Return the rule 11 line for a day with no papers.

    Saturdays and Sundays (arXiv posts no list) get the weekend line; today
    (Monday to Friday) says the next check is at 02:00 UTC; any earlier
    weekday says there were no new papers from that day.
    """
    if day.weekday() >= 5:
        return "arXiv doesn't post new papers on Saturdays and Sundays."
    if day == today:
        return "No papers for today yet. The next check is at 02:00 UTC."
    return f"No new papers from {_format_day(day)}."


def _build_context(day_param: Optional[date], run_now_wait: Optional[int] = None) -> Dict[str, Any]:
    """Build the template context for the index page or rate-limited page.

    Args:
        day_param: The day to display, or None for today.
        run_now_wait: Minutes to wait when rate limited, or None.

    Returns:
        A dict of variables for ``index.html``.
    """
    today = _today_utc()
    oldest_day = db.oldest_paper_day()
    latest_run = db.latest_briefing()
    latest_run_time = _format_time(latest_run["date"]) if latest_run else None

    if latest_run is None:
        # No runs at all.
        selected_day = today
        papers = []
        briefing_msg = "No data available yet"
    else:
        if day_param is None:
            # No explicit day – show today's briefing (UTC).
            selected_day = today
            papers = db.papers_for_day(selected_day)
            if papers:
                briefing_msg = f"{len(papers)} new paper{'s' if len(papers) != 1 else ''} from this day"
            else:
                # No papers for today – rule 11 line.
                briefing_msg = _empty_day_message(selected_day, today)
        else:
            # Explicit day parameter – enforce bounds.
            # Future days are not allowed. The special case of `day` equal to
            # today should behave the same as omitting the parameter – it must
            # **not** be treated as a future day. Therefore we only abort when
            # the requested day is strictly greater than today.
            if day_param > today:
                abort(404, description="Cannot view future days.")
            # Days before the earliest available day are not allowed.
            if oldest_day and day_param < oldest_day:
                abort(404, description="No papers before this date.")
            selected_day = day_param
            papers = db.papers_for_day(selected_day)
            if not papers:
                # No papers for the requested day – spec rule 11: show friendly line.
                briefing_msg = _empty_day_message(selected_day, today)
            else:
                briefing_msg = f"{len(papers)} new paper{'s' if len(papers) != 1 else ''} from this day"

    # Determine if this is the oldest day with papers (for greying out left arrow)
    is_oldest_day = oldest_day is not None and selected_day == oldest_day

    # Determine if this is today (for greying out right arrow)
    is_today = selected_day == today

    # Previous and next day for navigation links
    prev_day = selected_day - timedelta(days=1)
    next_day = selected_day + timedelta(days=1)

    # Check if previous day is valid (not before oldest_day)
    prev_day_valid = oldest_day is None or prev_day >= oldest_day

    # Check if next day is valid (not after today)
    next_day_valid = next_day <= today

    # History (newest 10 runs) – format timestamps.
    # Also determine which run is the first successful run for "First run: everything is new" display.
    conn = db._get_connection()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM runs WHERE error IS NULL")
    total_successful = cur.fetchone()[0]

    raw_runs = db.latest_runs(limit=10)
    runs: List[Dict[str, Any]] = []
    for r in raw_runs:
        is_first_successful = (total_successful == 1 and r["ok"])
        runs.append(
            {
                "time": _format_time(r["time"]),
                "date_fmt": _format_date(r["time"]),
                "datetime_fmt": _format_datetime(r["time"]),
                "ok": r["ok"],
                "trigger": r["trigger"],
                "new_count": r["new_count"],
                "error_short": r["error_short"],
                "is_first_run": is_first_successful,
            }
        )

    # Ensure ``day_label`` is available for the template.
    day_label = _format_day(selected_day)

    return {
        "selected_day": selected_day,
        "day_label": day_label,
        "papers": papers,
        "briefing_msg": briefing_msg,
        "latest_run_time": latest_run_time,
        "is_oldest_day": is_oldest_day,
        "is_today": is_today,
        "prev_day": prev_day,
        "next_day": next_day,
        "prev_day_valid": prev_day_valid,
        "next_day_valid": next_day_valid,
        "runs": runs,
        "run_now_wait": run_now_wait,
    }


def create_app() -> Flask:
    """Factory that creates and configures the Flask application.

    The function is referenced by ``gunicorn`` in the deployment workflow
    (``app.web:create_app``).  It registers three routes required by the
    spec and makes the Jinja template folder available.
    """
    app = Flask(__name__, static_folder="static", template_folder="templates")
    # Make the helpers available in Jinja templates.
    app.jinja_env.globals["_format_date"] = _format_date
    app.jinja_env.globals["_format_time"] = _format_time
    app.jinja_env.globals["_format_datetime"] = _format_datetime
    app.jinja_env.globals["_format_day"] = _format_day

    @app.route("/", methods=["GET"])
    def index():  # noqa: D401 – simple view function
        """Render the public briefing page for a specific UTC day.

        The view handles the ``day`` query parameter (YYYY-MM-DD). If absent,
        shows today's papers. Uses ``db.papers_for_day`` and ``db.oldest_paper_day``
        to fetch papers and determine navigation boundaries.
        """
        # Determine which day to display.
        day_str = request.args.get("day")
        try:
            day_param = _parse_day_param(day_str) if day_str else None
        except ValueError:
            abort(404, description="Invalid date format. Use YYYY-MM-DD.")

        ctx = _build_context(day_param)
        return render_template("index.html", **ctx)

    @app.route("/run-now", methods=["POST"])
    def run_now():  # noqa: D401
        """Trigger a manual run, respecting the 10‑minute rate limit.

        ``db.start_manual_run`` returns either a new run id (int) *or* the
        number of minutes the user must wait.  Because both are positive
        integers we disambiguate by checking whether a run with that id
        actually exists in the database.
        """
        result = db.start_manual_run()
        # ``result`` is either a new run id (positive int) or minutes to wait (positive int).
        # To distinguish, we look up the run and ensure its ``started_at`` timestamp is very recent.
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT started_at FROM runs WHERE id = ?", (result,))
        row = cur.fetchone()
        is_new_run = False
        if row:
            # Compare the timestamp with now; if within a short window we treat it as the run we just created.
            from datetime import datetime as dt_dt, timezone as dt_tz

            run_time = dt_dt.fromisoformat(row[0]).replace(tzinfo=dt_tz.utc)
            now = dt_dt.now(dt_tz.utc)
            # If the run was started within the last 30 seconds, consider it the freshly created manual run.
            if abs((now - run_time).total_seconds()) < 30:
                is_new_run = True

        if is_new_run:
            # Run the job synchronously as required by the spec (rule 15).
            try:
                job.run(run_id=result)
            except Exception:
                # ``job.run`` handles its own errors and records a failed run.
                pass
            # After the job finishes (or fails) redirect to the index page which will now show the updated run.
            return redirect(url_for("index"))

        # ``result`` is the minutes to wait (rate‑limited case).
        wait_minutes = int(result)
        # Build context for the same day as the request (respect ?day= param).
        day_str = request.args.get("day")
        try:
            day_param = _parse_day_param(day_str) if day_str else None
        except ValueError:
            day_param = None  # treat as today

        ctx = _build_context(day_param, run_now_wait=wait_minutes)
        return render_template("index.html", **ctx)

    @app.route("/healthz", methods=["GET"])
    def healthz():  # noqa: D401
        """Simple health endpoint used by the spec (rule 17).

        Returns ``200`` with JSON when the app can read its database (the latest
        run's time and status). Returns ``503`` when the database cannot be read.
        Any unexpected exception while accessing the database is caught and
        translated to a ``503`` response, satisfying the lesson to wrap database
        connection attempts in a ``try``/``except`` block.
        """
        try:
            status = db.health()
        except Exception:
            # The database is unreadable (e.g., file permission error, corrupt
            # file, etc.). Return a service‑available status instead of letting
            # Flask generate a generic 500 error.
            return jsonify({"error": "database unavailable"}), 503

        if status is None:
            # No runs yet – treat as healthy but report empty data.
            return jsonify({}), 200
        # Database is readable; return 200 with the latest run's time and status.
        # The status field in the JSON will be "ok" or "error" per the run.
        return jsonify(status), 200

    return app