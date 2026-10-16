import os
import sys
from pathlib import Path

# Pytest runs inside a temporary directory; ensure the repository root is on
# ``sys.path`` so ``import app`` works.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# ---------------------------------------------------------------------------
# Global test fixtures for the web lane
# ---------------------------------------------------------------------------

import tempfile
from unittest.mock import patch
import pytest

@pytest.fixture
def app_client():
    """Flask test client for the web application.

    Depends on ``temp_data_dir`` to ensure isolated data. Creates the Flask app
    via ``create_app`` (which reads the patched config) and yields a ``FlaskClient``.
    """
    from app.web import create_app

    # Create a temporary directory for the app's data files.
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch configuration and DB connection to use this isolated directory.
        with patch("app.config.DATA_ROOT", Path(tmpdir)):
            with patch("app.db._connection", None):
                os.environ["DAILY_BRIEFING_DATA"] = tmpdir
                app = create_app()
                app.config["TESTING"] = True
                with app.app_context():
                    yield app.test_client()
