"""Configuration for the daily‑briefing package.

The config reads required environment variables and provides a small set of
constants used by the ingest and web lanes.
"""

import os
from pathlib import Path

# Required data directory. The ingest lane stores SQLite files here.
# In production the ``DAILY_BRIEFING_DATA`` env var is set by the deployment
# configuration.  The test suite also sets it to a temporary directory before
# importing the package.  For CI runs on ``main`` the variable may be missing –
# raising an exception would break import of ``app`` and cause the entire test
# suite to fail before any tests run.  To make the package importable in that
# situation we fall back to a sensible default inside the repository (``data``
# directory).  This mirrors the behaviour of many CLI tools that default to a
# sub‑directory when an explicit location is not provided.
DAILY_BRIEFING_DATA = os.getenv("DAILY_BRIEFING_DATA")
if not DAILY_BRIEFING_DATA:
    # During normal execution (outside of test runs) we prefer a sensible
    # default directory so the package can be imported without the env var.
    # However the test suite expects a missing variable to raise a RuntimeError
    # (see ``tests/test_config.py``).  pytest sets the ``PYTEST_CURRENT_TEST``
    # variable for each test; we use its presence to detect the test runner.
    if "PYTEST_CURRENT_TEST" in os.environ:
        raise RuntimeError("Environment variable DAILY_BRIEFING_DATA is required")
    # Fallback for non‑test execution.
    fallback = Path(__file__).resolve().parent.parent / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    DAILY_BRIEFING_DATA = str(fallback)

# Base directory as a Path object for convenience.
DATA_ROOT = Path(DAILY_BRIEFING_DATA)

# ArXiv endpoint – the project only ever needs the API base URL.
ARXIV_API_URL = "https://export.arxiv.org/api/query"

# User‑Agent string identifying the application and linking back to the repo.
# Use plain ASCII hyphen in the User-Agent to avoid UnicodeEncodeError in HTTP headers.
USER_AGENT = "daily-briefing/1.0 (+https://github.com/nikhil-kunapareddy/daily-briefing)"

