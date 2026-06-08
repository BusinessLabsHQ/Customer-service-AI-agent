from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.case import FinalAction


def test_run_case_endpoint_with_fixture(tmp_path) -> None:
    app = create_app(Settings(APP_ENV="test", STATE_DB_PATH=str(tmp_path / "state.sqlite3")))
    client = TestClient(app)

    response = client.post(
        "/cases/run",
        json={
            "fixture_id": "duplicate_charge",
            "user_message": "I got charged twice last night for the same order.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_action"] == FinalAction.PROCESS_REFUND
    assert payload["escalate"] is False
    assert "process_refund" in payload["tool_calls"]
    assert payload["customer_id"] == "cus_123"
    assert payload["order_id"] == "ord_123"
    assert isinstance(payload["policy_refs"], list)
    assert payload.get("policy_explanation") is not None

    stored = client.get(f"/cases/{payload['case_id']}")

    assert stored.status_code == 200
    assert stored.json()["case_id"] == payload["case_id"]


def test_list_fixtures_endpoint(tmp_path) -> None:
    app = create_app(Settings(APP_ENV="test", STATE_DB_PATH=str(tmp_path / "state.sqlite3")))
    client = TestClient(app)

    response = client.get("/cases/fixtures")

    assert response.status_code == 200
    fixture_ids = {fixture["fixture_id"] for fixture in response.json()}
    assert {"duplicate_charge", "service_incident", "account_locked"} <= fixture_ids


def test_run_fixture_endpoint_persists_result(tmp_path) -> None:
    app = create_app(Settings(APP_ENV="test", STATE_DB_PATH=str(tmp_path / "state.sqlite3")))
    client = TestClient(app)

    response = client.post("/cases/run-fixture/service_incident")

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_id"] == "demo_service_incident"
    assert payload["final_action"] == FinalAction.EXPLAIN_INCIDENT_AND_ROUTE

    stored = client.get("/cases/demo_service_incident")

    assert stored.status_code == 200
    assert stored.json()["final_action"] == FinalAction.EXPLAIN_INCIDENT_AND_ROUTE


def test_missing_case_returns_404(tmp_path) -> None:
    app = create_app(Settings(APP_ENV="test", STATE_DB_PATH=str(tmp_path / "state.sqlite3")))
    client = TestClient(app)

    response = client.get("/cases/nope")

    assert response.status_code == 404
