from datetime import UTC, datetime

import pytest
from conftest import counts


@pytest.mark.parametrize("index", range(4))
def test_dst_wall_clock_and_expected_utc(client, db, risk_data, index):
    case = risk_data["dst"][index]
    payload = {key: case[key] for key in ("room_id", "start", "end", "repeat_until")}
    response = client.post("/series", json={**payload, "user": "DST reviewer"})
    assert response.status_code == 201
    for item in response.json()["created"]:
        assert item["start"].endswith("T09:00:00")
        assert item["end"].endswith("T10:00:00")
        assert datetime.fromisoformat(item["start"]).tzinfo is None
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM bookings ORDER BY start_utc").fetchall()
        assert [datetime.fromtimestamp(row["start_utc"], UTC).hour for row in rows] == case[
            "utc_hours"
        ]
        assert all(row["start"].endswith("T09:00:00") for row in rows)


@pytest.mark.parametrize("index", range(4))
def test_invalid_later_occurrence_aborts_everything(client, db, risk_data, index):
    case = risk_data["invalid_local"][index]
    payload = {key: case[key] for key in ("room_id", "start", "end", "repeat_until")}
    for endpoint in ("/series/preview", "/series"):
        response = client.post(endpoint, json={**payload, "user": "DST reviewer"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == case["code"]
        assert counts(db) == (0, 0)


@pytest.mark.parametrize(
    "room_id,start,end,code",
    [
        (3, "2026-03-29T01:30:00", "2026-03-29T02:30:00", "nonexistent_local_time"),
        (9, "2026-03-08T01:30:00", "2026-03-08T02:30:00", "nonexistent_local_time"),
        (3, "2026-10-25T01:30:00", "2026-10-25T02:30:00", "ambiguous_local_time"),
        (9, "2026-11-01T00:30:00", "2026-11-01T01:30:00", "ambiguous_local_time"),
    ],
)
def test_invalid_endpoints(client, db, room_id, start, end, code):
    response = client.post(
        "/bookings", json={"room_id": room_id, "start": start, "end": end, "user": "End validation"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert counts(db) == (0, 0)


def test_spanning_dst_uses_actual_elapsed_time(client, db):
    response = client.post(
        "/bookings",
        json={
            "room_id": 9,
            "start": "2026-03-08T01:30:00",
            "end": "2026-03-08T03:30:00",
            "user": "Night team",
        },
    )
    assert response.status_code == 201
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM bookings").fetchone()
        assert row["end_utc"] - row["start_utc"] == 3600
