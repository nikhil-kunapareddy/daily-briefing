import os
import sys
import pytest

# Ensure repo root is on import path.
sys.path.append(os.getcwd())


class DummyResponse:
    """Minimal ``requests.Response`` stub used for mocking."""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


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

