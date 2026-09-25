"""Smoke tests for the Flask app and data layer.

Deliberately shallow: these check the app boots, routes are wired, and the
auth gate behaves. They run without a database file present, so CI does not
need the 270MB SQLite index.
"""
from __future__ import annotations

import pytest

from app.server import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health_endpoint_is_open(client):
    """Health must not sit behind auth or the platform cannot probe it."""
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_api_routes_are_registered(client):
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    for expected in ("/", "/api/health", "/api/ask", "/api/company/search"):
        assert expected in rules, f"route missing: {expected}"


def test_ask_rejects_empty_question(client):
    """Guard against sending an empty prompt to a paid API."""
    resp = client.post("/api/ask", json={"question": "   "})
    assert resp.status_code in (400, 401)  # 401 if the auth gate is enabled


def test_store_module_imports():
    """store.py must import without touching the database, so the module can
    be loaded in environments that have no data file."""
    from app import store
    assert hasattr(store, "get_store")
