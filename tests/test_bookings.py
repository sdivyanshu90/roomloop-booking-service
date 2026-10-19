import pytest
from conftest import counts
from fastapi.testclient import TestClient

from roomloop.app import create_app


def test_create_read_and_persist(client, db, booking):
    response = client.post("/bookings", json=booking)
    assert response.status_code == 201
    body = response.json()
    assert {key: body[key] for key in booking} == booking
    assert body["timezone"] == "Europe/Berlin"
    assert body["series_id"] is None
    assert body["status"] == "active"
    assert client.get(f"/bookings/{body['id']}").json() == body
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM bookings").fetchone()
        assert (row["start"], row["end"]) == (booking["start"], booking["end"])
        assert row["end_utc"] - row["start_utc"] == 3600
    with TestClient(create_app(db.path)) as reopened:
        assert reopened.get("/bookings").json() == [body]


@pytest.mark.parametrize(
    "start,end",
    [
        ("09:00", "10:00"),
        ("08:30", "09:30"),
        ("09:30", "10:30"),
        ("09:15", "09:45"),
        ("08:00", "11:00"),
    ],
)
def test_overlaps(client, db, booking, start, end):
    first = client.post("/bookings", json=booking).json()
    candidate = {**booking, "start": f"2026-07-02T{start}:00", "end": f"2026-07-02T{end}:00"}
    response = client.post("/bookings", json=candidate)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["conflicting_booking_ids"] == [first["id"]]
    assert counts(db) == (1, 0)


@pytest.mark.parametrize("start,end", [("08:00", "09:00"), ("10:00", "11:00")])
def test_adjacent(client, booking, start, end):
    assert client.post("/bookings", json=booking).status_code == 201
    assert (
        client.post(
            "/bookings",
            json={
                **booking,
                "start": f"2026-07-02T{start}:00",
                "end": f"2026-07-02T{end}:00",
            },
        ).status_code
        == 201
    )


def test_distinct_rooms(client, booking):
    for room_id in (3, 4, 9, 17):
        assert client.post("/bookings", json={**booking, "room_id": room_id}).status_code == 201
    assert len(client.get("/bookings").json()) == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"end": "2026-07-02T08:00:00"},
        {"end": "2026-07-02T09:00:00"},
        {"end": "2026-07-03T09:00:01"},
        {"user": "  "},
        {"user": "A" * 101},
        {"start": "2026-07-02T09:00:00Z"},
        {"start": "2026-07-02T09:00:00+02:00"},
        {"start": 1782982800},
        {"start": "2026-07-02"},
        {"start": "2026-02-30T09:00:00"},
        {"start": "2026-07-02T09:00:00.5"},
        {"room_id": True},
        {"room_id": "3"},
        {"room_id": 0},
        {"surprise": "field"},
    ],
)
def test_input_validation(client, db, booking, changes):
    response = client.post("/bookings", json={**booking, **changes})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert counts(db) == (0, 0)


def test_nonexistent_room(client, db, booking):
    response = client.post("/bookings", json={**booking, "room_id": 1})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "room_not_found"
    assert counts(db) == (0, 0)


def test_legacy_rooms_exact(client):
    assert client.get("/rooms").json() == [
        {"id": 3, "name": "Aurora", "capacity": 8},
        {"id": 4, "name": "Basalt", "capacity": 4},
        {"id": 9, "name": "Cinder", "capacity": 12},
        {"id": 17, "name": "Dune", "capacity": 6},
    ]
    assert client.get("/rooms/17").json()["timezone"] == "America/Denver"


def test_list_filters_and_pagination(client, booking):
    first = client.post("/bookings", json=booking).json()
    client.post("/bookings", json={**booking, "room_id": 4})
    assert client.get("/bookings?room_id=3&day=2026-07-02").json() == [first]
    assert client.get("/bookings?day=2026-07-03").json() == []
    assert len(client.get("/bookings?limit=1").json()) == 1
    assert len(client.get("/bookings?limit=1&offset=1").json()) == 1
    assert client.get("/bookings?offset=2").json() == []
    assert client.get("/bookings?limit=501").status_code == 422


def test_overnight_date_filter(client, booking):
    body = client.post(
        "/bookings", json={**booking, "start": "2026-07-02T23:30:00", "end": "2026-07-03T00:30:00"}
    ).json()
    assert client.get("/bookings?day=2026-07-02").json() == [body]
    assert client.get("/bookings?day=2026-07-03").json() == [body]
    assert client.get("/bookings?day=2026-07-04").json() == []
