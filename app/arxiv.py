"""arXiv fetching utilities for the ingest lane.

The function :func:`fetch_list_day` performs a single HTTP request to the arXiv API to
retrieve the five newest submissions in the ``cs.MA`` category for a specific list day.
The legacy :func:`fetch` retrieves the five newest overall (used when no list day is missing
and we need to re-fetch the latest list day). Both follow the behaviour required by the spec:

* The request URL is built from ``app.config.ARXIV_API_URL`` and the query
  parameters defined in the specification.
* A custom ``User‑Agent`` header defined in ``app.config.USER_AGENT`` is sent.
* The request times out after ten seconds.
* After a successful request the function waits three seconds before returning –
  a courtesy pause required by rule 9.
* Various error conditions (network error, non‑200 status, malformed XML,
  empty result set, or an ``<id>`` that starts with the arXiv error URL) raise a
  ``RuntimeError`` with a short message.  The caller (``app.job``) treats any
  exception as a failed run.
* On success a list of dictionaries is returned.  Each dictionary contains the
  keys ``stable_id``, ``version``, ``title``, ``pdf_link``, ``published`` and
  ``updated``.  Whitespace in the title is collapsed to a single space.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import List, Dict, Tuple
from zoneinfo import ZoneInfo

import requests

from . import config

_ARXIV_ERROR_PREFIX = "https://arxiv.org/api/errors"
_NY_TZ = ZoneInfo("America/New_York")


def _subtract_weekdays(d: date, n: int) -> date:
    """Subtract ``n`` weekdays from ``d``, skipping Saturday and Sunday."""
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Monday=0 .. Friday=4
            n -= 1
    return d


def _build_date_range(list_day: str | date) -> Tuple[str, str]:
    """Return start and end strings in ``YYYYMMDDHHMM`` UTC for the given ``list_day``.

    The ``list_day`` is a weekday (Monday–Friday). The window is defined by the spec:
    from 14:00 New York time on the weekday two weekdays before ``list_day``
    to 13:59 on the weekday before ``list_day``.
    """
    if isinstance(list_day, str):
        day = date.fromisoformat(list_day)
    else:
        day = list_day

    if day.weekday() >= 5:
        raise ValueError("list_day must be a weekday (Monday–Friday)")

    # Compute start and end in New York time.
    start_local = datetime.combine(_subtract_weekdays(day, 2), datetime.min.time()).replace(hour=14, tzinfo=_NY_TZ)
    end_local = datetime.combine(_subtract_weekdays(day, 1), datetime.min.time()).replace(hour=13, minute=59, tzinfo=_NY_TZ)

    # Convert to UTC and format as YYYYMMDDHHMM (no dashes, colons, seconds, Z).
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    start_str = start_utc.strftime("%Y%m%d%H%M")
    end_str = end_utc.strftime("%Y%m%d%H%M")
    return start_str, end_str


def fetch_list_day(list_day: str) -> List[Dict[str, str]]:
    """Fetch five papers for a specific ``list_day`` according to the spec.

    Builds a query limited to the appropriate submission window, sends the
    request with the required ``User-Agent`` header, respects the 10‑second
    timeout, pauses 3 seconds after a successful fetch, and returns parsed paper
    dicts. Errors raise ``RuntimeError``.
    """
    start_str, end_str = _build_date_range(list_day)
    date_query = f"submittedDate:[{start_str}+TO+{end_str}]"
    params = (
        f"search_query=cat:cs.MA+AND+{date_query}",
        "sortBy=submittedDate",
        "sortOrder=descending",
        "max_results=5",
    )
    url = f"{config.ARXIV_API_URL}?{'&'.join(params)}"
    headers = {"User-Agent": config.USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Request error: {exc}")

    if response.status_code != 200:
        raise RuntimeError(f"Non‑200 status: {response.status_code}")

    time.sleep(3)

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Unparseable XML: {exc}")

    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if not entries:
        raise RuntimeError("No entries in response")

    papers: List[Dict[str, str]] = []
    for entry in entries:
        raw_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
        if raw_id.startswith(_ARXIV_ERROR_PREFIX):
            raise RuntimeError("ArXiv error entry encountered")
        if "v" not in raw_id:
            raise RuntimeError("Malformed arXiv id")
        stable_part, version = raw_id.rsplit("v", 1)
        stable_id = stable_part.split("/")[-1]
        version = f"v{version}"
        raw_title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        title = " ".join(raw_title.split())
        pdf_link = ""
        for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
            if link.attrib.get("title") == "pdf":
                pdf_link = link.attrib.get("href", "")
                break
        published = entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
        updated = entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        papers.append(
            {
                "stable_id": stable_id,
                "version": version,
                "title": title,
                "pdf_link": pdf_link,
                "published": published,
                "updated": updated,
            }
        )
    return papers


def fetch() -> List[Dict[str, str]]:
    """Legacy helper – fetch the latest five papers (no list_day).

    Kept for compatibility; the job now re-fetches the latest list day instead.
    """
    params = (
        "search_query=cat:cs.MA",
        "sortBy=submittedDate",
        "sortOrder=descending",
        "max_results=5",
    )
    url = f"{config.ARXIV_API_URL}?{'&'.join(params)}"
    headers = {"User-Agent": config.USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Request error: {exc}")

    if response.status_code != 200:
        raise RuntimeError(f"Non‑200 status: {response.status_code}")

    time.sleep(3)

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Unparseable XML: {exc}")

    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if not entries:
        raise RuntimeError("No entries in response")

    papers: List[Dict[str, str]] = []
    for entry in entries:
        raw_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
        if raw_id.startswith(_ARXIV_ERROR_PREFIX):
            raise RuntimeError("ArXiv error entry encountered")
        if "v" not in raw_id:
            raise RuntimeError("Malformed arXiv id")
        stable_part, version = raw_id.rsplit("v", 1)
        stable_id = stable_part.split("/")[-1]
        version = f"v{version}"
        raw_title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        title = " ".join(raw_title.split())
        pdf_link = ""
        for link in entry.findall("{http://www.w3.org/2005/Atom}link"):
            if link.attrib.get("title") == "pdf":
                pdf_link = link.attrib.get("href", "")
                break
        published = entry.findtext("{http://www.w3.org/2005/Atom}published") or ""
        updated = entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        papers.append(
            {
                "stable_id": stable_id,
                "version": version,
                "title": title,
                "pdf_link": pdf_link,
                "published": published,
                "updated": updated,
            }
        )
    return papers
