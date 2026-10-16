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

import importlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for

# Lazy import helper for ``app.db`` to avoid import‑time failures when the
# required environment variable ``DAILY_BRIEFING_DATA`` is not set (e.g. during
# early health‑check probes in a fresh deployment).

def _db():
    """Return the ``app.db`` module, importing it lazily.

    Importing ``app.db`` at module import time can raise a ``RuntimeError``
    if the required environment variable ``DAILY_BRIEFING_DATA`` is not set –
    a situation that occurs during early health‑check probes. By loading the
    module inside each request we let the Flask app start, and the health
    endpoint can then decide what to return instead of the whole process
    crashing with a 502.
    """
    return importlib.import_module("app.db")


def _format_date(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b %Y")


def _format_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%H:%M")


def _format_datetime(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b %Y, %H:%M UTC")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
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
        db = _db()
        latest = db.latest_briefing()
        if latest is None:
            return render_template(
                "index.html",
                briefing=None,
                no_new=False,
                last_with_papers=None,
                runs=[],
                run_now_wait=None,
                latest_run_time=None,
            )
        no_new = latest["new_count"] == 0
        last_with_papers: Optional[Dict[str, Any]] = None
        if no_new:
            last_with_papers = db.last_briefing_with_papers()
        briefing_ctx = dict(latest)
        briefing_ctx["date_fmt"] = _format_date(latest["date"])
        briefing_ctx["time_fmt"] = _format_time(latest["date"])
        briefing_ctx["datetime_fmt"] = _format_datetime(latest["date"])
        briefing_ctx["is_first_run"] = latest.get("is_first_run", False)
        if last_with_papers:
            last_ctx = dict(last_with_papers)
            last_ctx["date_fmt"] = _format_date(last_with_papers["date"])
            last_ctx["time_fmt"] = _format_time(last_with_papers["date"])
            last_ctx["datetime_fmt"] = _format_datetime(last_with_papers["date"])
        else:
            last_ctx = None
        raw_runs = db.latest_runs(limit=10)
        runs: List[Dict[str, Any]] = []
        for r in raw_runs:
            runs.append({
                "time": _format_time(r["time"]),
                "date_fmt": _format_date(r["time"]),
                "datetime_fmt": _format_datetime(r["time"]),
                "ok": r["ok"],
                "trigger": r["trigger"],
                "new_count": r["new_count"],
                "error_short": r["error_short"],
            })
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
        db = _db()
        result = db.start_manual_run()
        conn = db._get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM runs WHERE id = ?", (result,))
        if cur.fetchone():
            return redirect(url_for("index"))
        wait_minutes = int(result)
        latest = db.latest_briefing()
        no_new = latest["new_count"] == 0 if latest else False
        last_with_papers = db.last_briefing_with_papers() if no_new else None
        raw_runs = db.latest_runs(limit=10)
        runs: List[Dict[str, Any]] = []
        for r in raw_runs:
            runs.append({
                "time": _format_time(r["time"]),
                "date_fmt": _format_date(r["time"]),
                "datetime_fmt": _format_datetime(r["time"]),
                "ok": r["ok"],
                "trigger": r["trigger"],
                "new_count": r["new_count"],
                "error_short": r["error_short"],
            })
        latest_run_time = _format_time(latest["date"]) if latest else None
        return render_template(
            "index.html",
            briefing=latest,
            no_new=no_new,
            last_with_papers=last_with_papers,
            runs=runs,
            run_now_wait=wait_minutes,
            latest_run_time=latest_run_time,
        )

    @app.route("/healthz", methods=["GET"])
    def healthz():  # noqa: D401
        """Simple health endpoint used by the spec (rule 17)."""
        db = _db()
        status = db.health()
        if status is None:
            return jsonify({}), 200
        if status["status"] == "ok":
            return jsonify(status), 200
        return jsonify(status), 503

    return app
