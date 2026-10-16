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
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for

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

    @app.route("/", methods=["GET"])
    def index():  # noqa: D401 – simple view function
        """Render the public briefing page.

        The view gathers the latest successful briefing, the most recent
        briefing that actually has papers (for the *no new* case) and the
        recent run history.  Dates are formatted as the spec expects so the
        template can stay simple.
        """
        latest = db.latest_briefing()
        if latest is None:
            # No runs yet – render an empty page.
            return render_template(
                "index.html",
                briefing=None,
                no_new=False,
                last_with_papers=None,
                runs=[],
                run_now_wait=None,
                latest_run_time=None,
            )

        # Determine if we need the *no new* placeholder.
        no_new = latest["new_count"] == 0
        last_with_papers: Optional[Dict[str, Any]] = None
        if no_new:
            last_with_papers = db.last_briefing_with_papers()

        # Prepare a copy of latest with formatted date/time strings.
        briefing_ctx = dict(latest)
        briefing_ctx["date_fmt"] = _format_date(latest["date"])
        briefing_ctx["time_fmt"] = _format_time(latest["date"])
        briefing_ctx["datetime_fmt"] = _format_datetime(latest["date"])
        briefing_ctx["is_first_run"] = latest.get("is_first_run", False)

        # Prepare last_with_papers similarly, if present.
        if last_with_papers:
            last_ctx = dict(last_with_papers)
            last_ctx["date_fmt"] = _format_date(last_with_papers["date"])
            last_ctx["time_fmt"] = _format_time(last_with_papers["date"])
            last_ctx["datetime_fmt"] = _format_datetime(last_with_papers["date"])
        else:
            last_ctx = None

        # History (newest 10 runs) – format timestamps.
        raw_runs = db.latest_runs(limit=10)
        runs: List[Dict[str, Any]] = []
        for r in raw_runs:
            runs.append(
                {
                    "time": _format_time(r["time"]),
                    "date_fmt": _format_date(r["time"]),
                    "datetime_fmt": _format_datetime(r["time"]),
                    "ok": r["ok"],
                    "trigger": r["trigger"],
                    "new_count": r["new_count"],
                    "error_short": r["error_short"],
                }
            )

        # Get the latest run time for the "Last checked" display
        latest_run_time = _format_time(latest["date"])

        return render_template(
            "index.html",
            briefing=briefing_ctx,
            no_new=no_new,
            last_with_papers=last_ctx,
            runs=runs,
            run_now_wait=None,
            latest_run_time=latest_run_time,
        )

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
            from datetime import datetime, timezone

            run_time = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            # If the run was started within the last 30 seconds, consider it the freshly created manual run.
            if abs((now - run_time).total_seconds()) < 30:
                is_new_run = True

        if is_new_run:
            # Run the job synchronously as required by the spec (rule 15).
            try:
                job.run(run_id=result)
            except Exception:
                # ``job.run`` handles its own errors and records a failed run.
                pass
            # After the job finishes (or fails) redirect to the index page which will now show the updated run.
            return redirect(url_for("index"))

        # ``result`` is the minutes to wait (rate‑limited case).
        wait_minutes = int(result)
        # Render the index page with a note about the rate‑limit.
        latest = db.latest_briefing()
        if latest is None:
            # No runs yet – render an empty page.
            return render_template(
                "index.html",
                briefing=None,
                no_new=False,
                last_with_papers=None,
                runs=[],
                run_now_wait=wait_minutes,
                latest_run_time=None,
            )

        # Determine if we need the *no new* placeholder.
        no_new = latest["new_count"] == 0
        last_with_papers: Optional[Dict[str, Any]] = None
        if no_new:
            last_with_papers = db.last_briefing_with_papers()

        # Prepare a copy of latest with formatted date/time strings.
        briefing_ctx = dict(latest)
        briefing_ctx["date_fmt"] = _format_date(latest["date"])
        briefing_ctx["time_fmt"] = _format_time(latest["date"])
        briefing_ctx["datetime_fmt"] = _format_datetime(latest["date"])
        briefing_ctx["is_first_run"] = latest.get("is_first_run", False)

        # Prepare last_with_papers similarly, if present.
        if last_with_papers:
            last_ctx = dict(last_with_papers)
            last_ctx["date_fmt"] = _format_date(last_with_papers["date"])
            last_ctx["time_fmt"] = _format_time(last_with_papers["date"])
            last_ctx["datetime_fmt"] = _format_datetime(last_with_papers["date"])
        else:
            last_ctx = None

        # History (newest 10 runs) – format timestamps.
        raw_runs = db.latest_runs(limit=10)
        runs: List[Dict[str, Any]] = []
        for r in raw_runs:
            runs.append(
                {
                    "time": _format_time(r["time"]),
                    "date_fmt": _format_date(r["time"]),
                    "datetime_fmt": _format_datetime(r["time"]),
                    "ok": r["ok"],
                    "trigger": r["trigger"],
                    "new_count": r["new_count"],
                    "error_short": r["error_short"],
                }
            )

        latest_run_time = _format_time(latest["date"])

        return render_template(
            "index.html",
            briefing=briefing_ctx,
            no_new=no_new,
            last_with_papers=last_ctx,
            runs=runs,
            run_now_wait=wait_minutes,
            latest_run_time=latest_run_time,
        )

    @app.route("/healthz", methods=["GET"])
    def healthz():  # noqa: D401
        """Simple health endpoint used by the spec (rule 17)."""
        status = db.health()
        if status is None:
            # No runs yet – treat as healthy but report empty data.
            return jsonify({}), 200
        if status["status"] == "ok":
            return jsonify(status), 200
        # Any non‑ok status should be reported as a service error.
        return jsonify(status), 503

    return app
