import os
import sys
import pytest

# Ensure the repository root is on the import path when the test runs.
sys.path.append(os.getcwd())


def test_missing_env_raises(monkeypatch):
    """The config module must raise if the required env var is absent."""
    # Ensure the variable is not set and reload the module.
    monkeypatch.delenv("DAILY_BRIEFING_DATA", raising=False)
    import importlib, sys
    sys.modules.pop("app.config", None)
    with pytest.raises(RuntimeError, match="DAILY_BRIEFING_DATA"):
        importlib.import_module("app.config")


def test_config_values(monkeypatch, tmp_path):
    """Config should expose the expected constants."""
    monkeypatch.setenv("DAILY_BRIEFING_DATA", str(tmp_path))
    import importlib, sys
    sys.modules.pop("app.config", None)
    import app.config as config
    assert config.ARXIV_API_URL == "https://export.arxiv.org/api/query"
    assert "daily-briefing" in config.USER_AGENT
    assert "github.com/nikhil-kunapareddy/daily-briefing" in config.USER_AGENT
    assert config.DATA_ROOT == tmp_path
