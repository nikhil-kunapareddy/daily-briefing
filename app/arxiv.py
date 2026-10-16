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
from typing import List, Dict

import requests

from . import config

_ARXIV_ERROR_PREFIX = "https://arxiv.org/api/errors"


def _build_url() -> str:
    """Construct the full API URL for the five newest ``cs.MA`` submissions.

    The base URL comes from ``config.ARXIV_API_URL``; query parameters are
    hard‑coded as required by the specification.
    """
    params = (
        "search_query=cat:cs.MA",
        "sortBy=submittedDate",
        "sortOrder=descending",
        "max_results=5",
    )
    return f"{config.ARXIV_API_URL}?{'&'.join(params)}"


def fetch() -> List[Dict[str, str]]:
    """Fetch the latest five papers from arXiv.

    Returns a list of paper dictionaries.  Raises ``RuntimeError`` on any error
    condition defined by the spec.
    """
    url = _build_url()
    headers = {"User-Agent": config.USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=10)
    except Exception as exc:
        raise RuntimeError(f"Request error: {exc}")

    if response.status_code != 200:
        raise RuntimeError(f"Non‑200 status: {response.status_code}")

    # Politeness pause – rule 9.
    time.sleep(3)

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Unparseable XML: {exc}")

    # arXiv returns ``<entry>`` elements under the ``feed`` root.
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if not entries:
        raise RuntimeError("No entries in response")

    papers: List[Dict[str, str]] = []
    for entry in entries:
        # Extract the <id> element which includes the version, e.g.
        # https://arxiv.org/abs/2610.13870v2
        raw_id = entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
        if raw_id.startswith(_ARXIV_ERROR_PREFIX):
            raise RuntimeError("ArXiv error entry encountered")
        # Split stable id and version.
        # The version part always starts with 'v' at the end.
        if "v" not in raw_id:
            raise RuntimeError("Malformed arXiv id")
        stable_part, version = raw_id.rsplit("v", 1)
        stable_id = stable_part.split("/")[-1]
        version = f"v{version}"

        # Title – collapse whitespace.
        raw_title = entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
        title = " ".join(raw_title.split())

        # PDF link – look for <link title="pdf" href="..."/>
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

