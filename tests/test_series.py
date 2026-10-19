from datetime import datetime, timedelta

import pytest
from conftest import counts


@pytest.mark.parametrize(
    "until,expected", [("2026-07-02", 1), ("2026-07-08", 1), ("2026-07-09", 2), ("2026-07-30", 5)]
)
def test_weekly_boundary(client, series, until, expected):
    response = client.post("/series", json={**series, "repeat_until": until})
    assert response.status_code == 201
    body = response.json()
    assert len(body["created"]) == expected
    assert body["skipped"] == []
    for index, item in enumerate(body["created"]):
        assert item["series_id"] == body["series_id"]
        assert datetime.fromisoformat(item["start"]) == datetime(2026, 7, 2, 9) + timedelta(
            weeks=index
        )
    detail = client.get(f"/series/{body['series_id']}").json()
    assert detail["bookings"] == body["created"]
    assert detail["rule"] == "weekly"
    assert detail["repeat_until"] == until
    assert len(client.get("/series").json()) == 1


@pytest.mark.parametrize("conflict_count", [1, 2, 3])
def test_conflicting_subset(client, db, series, risk_data, conflict_count):
    occupied = risk_data["conflicts"]["occupied_dates"][:conflict_count]
    ids = []
    for day in occupied:
        ids.append(
            client.post(
                "/bookings",
                json={
                    "room_id": 3,
                    "start": day + "T09:30:00",
                    "end": day + "T10:30:00",
                    "user": "Grace",
                },
            ).json()["id"]
        )
    response = client.post("/series", json=series)
    assert response.status_code == 201
    body = response.json()
    assert len(body["created"]) == 5 - conflict_count
    assert [item["start"][:10] for item in body["skipped"]] == occupied
    assert [item["conflicting_booking_ids"] for item in body["skipped"]] == [[id] for id in ids]
    assert all(item["reason"] == "booking_conflict" for item in body["skipped"])
    assert counts(db) == (5, 1)


def test_all_conflict_creates_no_empty_series(client, db, series):
    assert client.post("/series", json=series).status_code == 201
    response = client.post("/series", json=series)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "all_occurrences_conflict"
    assert len(response.json()["error"]["details"]["skipped"]) == 5
    assert counts(db) == (5, 1)


def test_real_database_failure_rolls_back_metadata_and_first_insert(client, db, series):
    with db.connect() as connection:
        connection.executescript("""
        CREATE TRIGGER fail_second BEFORE INSERT ON bookings
        WHEN NEW.start = '2026-07-09T09:00:00'
        BEGIN SELECT RAISE(ABORT, 'injected persistence failure'); END;
        """)
    response = client.post("/series", json=series)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
    assert "injected" not in response.text
    assert counts(db) == (0, 0)
    with db.connect() as connection:
        connection.execute("DROP TRIGGER fail_second")
    assert client.post("/series", json=series).status_code == 201
    assert counts(db) == (5, 1)


def test_preview_has_no_writes_and_creation_rechecks(client, db, booking, series):
    preview = client.post("/series/preview", json=series).json()
    assert len(preview["available"]) == 5
    assert preview["advisory"] is True
    assert counts(db) == (0, 0)
    client.post("/bookings", json=booking)
    result = client.post("/series", json=series).json()
    assert len(result["created"]) == 4
    assert len(result["skipped"]) == 1
    preview = client.post("/series/preview", json=series).json()
    assert preview["can_create"] is False
    assert len(preview["skipped"]) == 5
    assert counts(db) == (5, 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"repeat_until": "2026-07-01"},
        {"repeat_until": "2030-01-01"},
        {"rule": "daily"},
        {"repeat_until": "2026-07-30T00:00:00"},
        {"repeat_until": 1785000000},
    ],
)
def test_invalid_recurrence(client, db, series, changes):
    assert client.post("/series", json={**series, **changes}).status_code == 422
    assert counts(db) == (0, 0)


def test_104_occurrences_and_105_rejected(client, db, series):
    seed = datetime.fromisoformat(series["start"])
    until = (seed + timedelta(weeks=103)).date().isoformat()
    response = client.post("/series", json={**series, "repeat_until": until})
    assert response.status_code == 201
    assert len(response.json()["created"]) == 104
    until = (seed + timedelta(weeks=104)).date().isoformat()
    assert client.post("/series", json={**series, "repeat_until": until}).status_code == 422
    assert counts(db) == (104, 1)
