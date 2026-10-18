"""arXiv fetching utilities for the ingest lane.

The function :func:`fetch` performs a single HTTP request to the arXiv API to
retrieve the five newest submissions in the ``cs.MA`` category.  It follows the
behaviour required by issue #12:

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
from typing import List, Dict
from zoneinfo import ZoneInfo

import requests

from . import config

_ARXIV_ERROR_PREFIX = "https://arxiv.org/api/errors"


def _build_url() -> str:
    """Construct the base URL for fetching the *latest* five ``cs.MA`` submissions.

    Used by the legacy :func:`fetch` which does not take a ``list_day``.
    """
    params = (
        "search_query=cat:cs.MA",
        "sortBy=submittedDate",
        "sortOrder=descending",
        "max_results=5",
    )
    return f"{config.ARXIV_API_URL}?{'&'.join(params)}"


def _build_date_range(list_day: str) -> tuple[str, str]:
    """Return start and end ISO‑8601 UTC strings for the given ``list_day``.

    The ``list_day`` is interpreted in the America/New_York timezone. Supported
    weekdays are Friday, Monday, and Tuesday, with windows defined by the spec.
    """
    ny_tz = ZoneInfo("America/New_York")
    if isinstance(list_day, str):
        try:
            day = date.fromisoformat(list_day)
        except Exception as exc:
            raise ValueError(f"Invalid list_day '{list_day}': {exc}") from exc
    else:
        day = list_day

    wd = day.weekday()
    if wd == 4:  # Friday
        start_local = datetime.combine(day - timedelta(days=2), datetime.min.time()).replace(hour=14, tzinfo=ny_tz)
        end_local = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(hour=13, minute=59, tzinfo=ny_tz)
    elif wd == 0:  # Monday
        start_local = datetime.combine(day - timedelta(days=4), datetime.min.time()).replace(hour=14, tzinfo=ny_tz)
        end_local = datetime.combine(day - timedelta(days=3), datetime.min.time()).replace(hour=13, minute=59, tzinfo=ny_tz)
    elif wd == 1:  # Tuesday
        start_local = datetime.combine(day - timedelta(days=4), datetime.min.time()).replace(hour=14, tzinfo=ny_tz)
        end_local = datetime.combine(day - timedelta(days=1), datetime.min.time()).replace(hour=13, minute=59, tzinfo=ny_tz)
    else:
        raise ValueError("fetch_list_day only supports Friday, Monday, or Tuesday list days")

    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    return start_utc, end_utc


def fetch_list_day(list_day: str) -> List[Dict[str, str]]:
    """Fetch five papers for a specific ``list_day`` according to the spec.

    Builds a query limited to the appropriate submission window, sends the
    request with the required ``User-Agent`` header, respects the 10‑second
    timeout, pauses 3 seconds after a successful fetch, and returns parsed paper
    dicts. Errors raise ``RuntimeError``.
    """
    start_iso, end_iso = _build_date_range(list_day)
    date_query = f"submittedDate:[{start_iso} TO {end_iso}]"
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
    """Legacy helper – fetch the latest five papers (no list_day)."""
    url = _build_url()
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

