"""Additional tests for the ``/healthz`` endpoint.

These tests cover the edge‑case where the database cannot be read.  The
``app.web.healthz`` view is supposed to return a ``503`` status with a JSON
body containing an ``error`` key when an exception is raised while calling
``db.health()``.  The existing test suite only exercised the *readable*
path, so we add an explicit test for the *unreadable* case.

The test uses ``monkeypatch`` to replace ``app.db.health`` with a function
that raises ``RuntimeError``.  The Flask test client is then used to make a
GET request to ``/healthz`` and assert that the response matches the SPEC
rule 17 expectations.
"""

import pytest

from app import web


@pytest.fixture
def client(app_client):
    """Expose the Flask ``test_client`` fixture under a short name.

    ``app_client`` is provided by the project’s test configuration and yields a
    ``FlaskClient`` instance with the application context already pushed.
    """
    return app_client


def test_healthz_unreadable_database(client, monkeypatch):
    """``/healthz`` should return 503 when the database cannot be read.

    The ``db.health`` function is patched to raise an exception, simulating a
    situation such as a missing file or permission error.  The endpoint must
    translate this to a *service unavailable* response as required by SPEC rule
    17.
    """

    # Patch ``app.db.health`` to raise an exception.
    monkeypatch.setattr("app.db.health", lambda: (_ for _ in ()).throw(RuntimeError("unreadable")))

    resp = client.get("/healthz")
    # The endpoint should answer with HTTP 503.
    assert resp.status_code == 503
    # The JSON body must contain an ``error`` key indicating the problem.
    json_body = resp.get_json()
    assert isinstance(json_body, dict)
    assert "error" in json_body

