"""Configuration for the daily‑briefing package.

The config reads required environment variables and provides a small set of
constants used by the ingest and web lanes.
"""

import os
from datetime import date
from pathlib import Path

# Required data directory. The ingest lane stores SQLite files here. Raise a
# clear error if it is missing – callers expect this variable to be set.
DAILY_BRIEFING_DATA = os.getenv("DAILY_BRIEFING_DATA")
if not DAILY_BRIEFING_DATA:
    raise RuntimeError("Environment variable DAILY_BRIEFING_DATA is required")

# Base directory as a Path object for convenience.
DATA_ROOT = Path(DAILY_BRIEFING_DATA)

# ArXiv endpoint – the project only ever needs the API base URL.
ARXIV_API_URL = "https://export.arxiv.org/api/query"

# User‑Agent string identifying the application and linking back to the repo.
# Use plain ASCII hyphen in the User-Agent to avoid UnicodeEncodeError in HTTP headers.
USER_AGENT = "daily-briefing/1.0 (+https://github.com/nikhil-kunapareddy/daily-briefing)"

# The first day for which we want to fetch listings. The ingest job will fetch
# listings for this day and all subsequent days up to the current day.
FIRST_LIST_DAY = date(2026, 10, 15)

