import pytest
from conftest import counts


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/rooms/1"),
        ("get", "/bookings/missing"),
        ("get", "/series/missing"),
        ("post", "/bookings/missing/cancel"),
        ("post", "/series/missing/cancel"),
        ("get", "/missing"),
    ],
)
def test_missing_resource_json(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "details"}


def test_invalid_json(client):
    response = client.post("/bookings", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_busy_is_not_a_booking_conflict(client, db, booking):
    db.timeout = 0.05
    with db.transaction():
        response = client.post("/bookings", json=booking)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_busy"
    assert response.headers["Retry-After"] == "1"
    assert counts(db) == (0, 0)


def test_method_not_allowed_retains_allow_header(client):
    response = client.delete("/rooms")
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert response.json()["error"]["code"] == "http_error"


def test_unexpected_exception_rolls_back(client, db, app, series, monkeypatch):
    original = app.state.service.insert_booking
    calls = []

    def fail_after_one(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("injected application bug")
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.service, "insert_booking", fail_after_one)
    response = client.post("/series", json=series)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert counts(db) == (0, 0)
