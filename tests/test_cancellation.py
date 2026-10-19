from datetime import UTC, datetime

import pytest


def test_cancel_standalone_repeated_and_reuse(client, booking):
    body = client.post("/bookings", json=booking).json()
    url = f"/bookings/{body['id']}/cancel"
    first = client.post(url)
    assert first.status_code == 200
    assert first.json()["status"] == "cancelled"
    assert client.post(url).json() == first.json()
    assert client.post("/bookings", json=booking).status_code == 201
    assert len(client.get("/bookings?status=cancelled").json()) == 1


@pytest.mark.parametrize(
    "now,expected",
    [
        (
            datetime(2026, 7, 9, 7, 30, tzinfo=UTC),
            ["active", "active", "cancelled", "cancelled", "cancelled"],
        ),
        (
            datetime(2026, 7, 9, 7, tzinfo=UTC),
            ["active", "cancelled", "cancelled", "cancelled", "cancelled"],
        ),
        (
            datetime(2026, 7, 9, 7, 0, 0, 500000, tzinfo=UTC),
            ["active", "active", "cancelled", "cancelled", "cancelled"],
        ),
    ],
)
def test_series_cancel_cutoff(client, db, clock, series, now, expected):
    clock[0] = now
    result = client.post("/series", json=series).json()
    url = f"/series/{result['series_id']}/cancel"
    first = client.post(url).json()
    assert first["cancelled_count"] == expected.count("cancelled")
    detail = client.get(f"/series/{result['series_id']}").json()
    assert detail["status"] == "cancelled"
    assert [b["status"] for b in detail["bookings"]] == expected
    with db.connect() as connection:
        timestamps = [
            r[0]
            for r in connection.execute(
                "SELECT cancelled_at FROM bookings WHERE status = 'cancelled'"
            )
        ]
        assert set(timestamps) == {now.timestamp()}
    clock[0] = datetime(2026, 7, 1, tzinfo=UTC)
    assert client.post(url).json()["cancelled_count"] == 0
    assert client.get(f"/series/{result['series_id']}").json() == detail
    slot = result["created"][-1]
    assert (
        client.post(
            "/bookings", json={key: slot[key] for key in ("room_id", "start", "end", "user")}
        ).status_code
        == 201
    )


def test_cancel_one_series_member_only(client, series):
    result = client.post("/series", json=series).json()
    client.post(f"/bookings/{result['created'][2]['id']}/cancel")
    detail = client.get(f"/series/{result['series_id']}").json()
    assert detail["status"] == "active"
    assert [b["status"] for b in detail["bookings"]] == [
        "active",
        "active",
        "cancelled",
        "active",
        "active",
    ]


def test_series_cancellation_failure_rolls_back_members(client, db, series):
    result = client.post("/series", json=series).json()
    url = f"/series/{result['series_id']}"
    before = client.get(url).json()
    with db.connect() as connection:
        connection.executescript("""
            CREATE TRIGGER fail_series_cancel BEFORE UPDATE ON series
            BEGIN SELECT RAISE(ABORT, 'injected cancellation failure'); END;
        """)
    response = client.post(url + "/cancel")
    assert response.status_code == 500
    assert client.get(url).json() == before
    with db.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM bookings WHERE cancelled_at IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


def test_series_uses_one_clock_read_and_persists_cancellation(client, app, db, series, monkeypatch):
    from fastapi.testclient import TestClient

    from roomloop.app import create_app

    calls = []

    def clock():
        calls.append(1)
        return datetime(2026, 7, 9, 7, 30, tzinfo=UTC)

    monkeypatch.setattr(app.state.service, "clock", clock)
    result = client.post("/series", json=series).json()
    url = f"/series/{result['series_id']}"
    assert client.post(url + "/cancel").json()["cancelled_count"] == 3
    assert len(calls) == 1
    before = client.get(url).json()
    with TestClient(create_app(db.path)) as reopened:
        assert reopened.get(url).json() == before
        assert reopened.post(url + "/cancel").json()["cancelled_count"] == 0
