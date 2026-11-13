"""Starter app tests (need TEST_DATABASE_URL, a throwaway database that already has the schema and seed)."""
import os
import sys

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture()
def client():
    os.environ["DATABASE_URL"] = URL
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_catalogue_lists_all_18_items_and_banner(client):
    html = client.get("/").get_data(as_text=True)
    assert "Demo shop: nothing is delivered or charged" in html
    assert html.count('class="card"') == 18


def test_health_reports_counts_and_state(client):
    data = client.get("/health").get_json()
    assert data["status"] == "ok" and data["counts"]["products"] == 18 and data["state"] in {"SETUP", "ROUND1", "BETWEEN", "ROUND2", "CLOSED"}


def test_no_static_folder_used(client):
    from app import app
    assert app.static_folder is None
