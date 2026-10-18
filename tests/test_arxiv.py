import os
import sys
import pytest
from datetime import date

# Ensure repo root is on import path.
sys.path.append(os.getcwd())


class DummyResponse:
    """Minimal ``requests.Response`` stub used for mocking."""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def test_build_date_range(monkeypatch, tmp_path):
    """_build_date_range should return correct UTC windows in YYYYMMDDHHMM format."""
    # Point config to a temporary data directory.
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    import importlib, app.config as cfg  # reload config with env set
    importlib.reload(cfg)
    import app.arxiv as arxiv_mod
    importlib.reload(arxiv_mod)

    tests = [
        ("2026-10-15", ("202610131800", "202610141759")),  # Thu
        ("2026-10-16", ("202610141800", "202610151759")),  # Fri
        ("2026-10-19", ("202610151800", "202610161759")),  # Mon
        ("2026-10-20", ("202610161800", "202610191759")),  # Tue
        ("2026-11-03", ("202610301800", "202611021859")),  # Tue across DST end
    ]
    for d_str, expected in tests:
        start, end = arxiv_mod._build_date_range(d_str)
        assert start == expected[0], f"start mismatch for {d_str}: got {start}, expected {expected[0]}"
        assert end == expected[1], f"end mismatch for {d_str}: got {end}, expected {expected[1]}"


def test_fetch_list_day_success(monkeypatch, tmp_path):
    """fetch_list_day should parse fixture and return five papers for a given list day."""
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    import importlib, app.config as cfg
    importlib.reload(cfg)
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "arxiv_sample.xml")
    with open(fixture_path, "r", encoding="utf-8") as f:
        xml_content = f.read()
    import requests
    def fake_get(*_args, **_kwargs):
        return DummyResponse(xml_content, 200)
    monkeypatch.setattr(requests, "get", fake_get)
    import app.arxiv as arxiv_mod
    importlib.reload(arxiv_mod)
    papers = arxiv_mod.fetch_list_day("2026-10-15")
    assert len(papers) == 5
    first = papers[0]
    assert first["stable_id"] == "2610.10001"
    assert first["version"] == "v1"


def test_fetch_success(monkeypatch, tmp_path):
    """arxiv.fetch should parse the fixture and return five clean dicts."""
    # Point config to a temporary data directory.
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    import importlib, app.config as cfg  # reload config with env set
    importlib.reload(cfg)

    # Load fixture XML.
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "arxiv_sample.xml")
    with open(fixture_path, "r", encoding="utf-8") as f:
        xml_content = f.read()

    # Patch ``requests.get`` to return the fixture.
    import requests

    def fake_get(*_args, **_kwargs):
        return DummyResponse(xml_content, 200)

    monkeypatch.setattr(requests, "get", fake_get)

    # Import the module after monkeypatching.
    import app.arxiv as arxiv_mod
    import importlib
    importlib.reload(arxiv_mod)

    papers = arxiv_mod.fetch()
    assert len(papers) == 5
    # Check whitespace collapse on titles.
    titles = [p["title"] for p in papers]
    assert "Second Paper Title with spaces" in titles
    assert "Third Paper Title" in titles
    # Ensure stable IDs and versions look correct.
    first = papers[0]
    assert first["stable_id"] == "2610.10001"
    assert first["version"] == "v1"
    assert first["pdf_link"].endswith("2610.10001v1.pdf")

