"""Endpoint contracts and boundary cases beyond the core booking scenarios."""

import pytest
from conftest import counts

from roomloop.models import BookingInput
from roomloop.scheduling import occurrences


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code


def test_room_details_match_legacy_list_and_explicit_timezones(client):
    rooms = client.get("/rooms").json()
    for room in rooms:
        response = client.get(f"/rooms/{room['id']}")
        assert response.status_code == 200
        expected_timezone = "Europe/Berlin" if room["id"] in (3, 4) else "America/Denver"
        assert response.json() == {**room, "timezone": expected_timezone}
    assert_error(client.get("/rooms/not-an-integer"), 422, "validation_error")


@pytest.mark.parametrize("endpoint", ["/bookings", "/series", "/series/preview"])
def test_creation_endpoints_reject_invalid_bodies_without_writes(client, db, endpoint):
    for content in ("{", "null", "[]", "{}"):
        response = client.post(
            endpoint, content=content, headers={"Content-Type": "application/json"}
        )
        assert_error(response, 422, "validation_error")
        assert counts(db) == (0, 0)


@pytest.mark.parametrize("endpoint", ["/series", "/series/preview"])
def test_series_endpoints_reject_missing_rooms(client, db, series, endpoint):
    assert_error(client.post(endpoint, json={**series, "room_id": 1}), 404, "room_not_found")
    assert counts(db) == (0, 0)


@pytest.mark.parametrize("endpoint", ["/bookings", "/series"])
@pytest.mark.parametrize("query", ["limit=0", "limit=501", "offset=-1", "timezone=UTC"])
def test_list_query_validation(client, endpoint, query):
    assert_error(client.get(f"{endpoint}?{query}"), 422, "validation_error")


@pytest.mark.parametrize("query", ["day=2026-02-30", "status=pending", "room_id=abc"])
def test_booking_filter_validation(client, query):
    assert_error(client.get(f"/bookings?{query}"), 422, "validation_error")


@pytest.mark.parametrize("endpoint", ["/bookings", "/series", "/series/preview"])
def test_out_of_range_room_id_is_validation_error(client, db, booking, series, endpoint):
    payload = booking if endpoint == "/bookings" else series
    assert_error(client.post(endpoint, json={**payload, "room_id": 2**63}), 422, "validation_error")
    assert counts(db) == (0, 0)


@pytest.mark.parametrize(
    "path",
    [
        f"/rooms/{2**63}",
        f"/bookings?room_id={2**63}",
        f"/bookings?offset={2**63}",
        f"/series?offset={2**63}",
    ],
)
def test_out_of_range_query_and_path_integers_are_validation_errors(client, path):
    assert_error(client.get(path), 422, "validation_error")


def test_integer_boundary_and_nonpositive_room_ids(client, db, booking):
    largest = 2**63 - 1
    assert_error(client.get(f"/rooms/{largest}"), 404, "room_not_found")
    assert_error(
        client.post("/bookings", json={**booking, "room_id": largest}), 404, "room_not_found"
    )
    for path in (
        f"/bookings?room_id={largest}",
        f"/bookings?offset={largest}",
        f"/series?offset={largest}",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == []
    for room_id in (0, -1, -(2**63) - 1):
        assert_error(client.get(f"/rooms/{room_id}"), 422, "validation_error")
        assert_error(client.get(f"/bookings?room_id={room_id}"), 422, "validation_error")
    assert counts(db) == (0, 0)


def test_empty_and_missing_list_filters(client):
    for path in (
        "/bookings",
        "/series",
        "/bookings?room_id=1",
        "/bookings?series_id=missing",
        "/bookings?offset=100",
        "/series?offset=100",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == []


def test_booking_filters_sort_by_instants_and_exclude_other_series(client, booking, series):
    # Denver has the earlier wall-clock time, but the later instant.
    denver = client.post(
        "/bookings",
        json={
            **booking,
            "room_id": 9,
            "start": "2026-07-02T08:00:00",
            "end": "2026-07-02T09:00:00",
        },
    ).json()
    berlin = client.post("/bookings", json=booking).json()
    assert client.get("/bookings").json() == [berlin, denver]
    assert client.get("/bookings?limit=1&offset=1").json() == [denver]
    result = client.post("/series", json={**series, "room_id": 4}).json()
    url = f"/bookings?series_id={result['series_id']}"
    assert client.get(url).json() == result["created"]
    cancelled_id = result["created"][0]["id"]
    client.post(f"/bookings/{cancelled_id}/cancel")
    cancelled = client.get(f"/bookings/{cancelled_id}").json()
    assert cancelled["status"] == "cancelled"
    assert client.get(
        url + "&status=cancelled&room_id=4&timezone=Europe/Berlin&day=2026-07-02"
    ).json() == [cancelled]
    assert client.get(url + "&timezone=America/Denver").json() == []
    assert len(client.get(url + "&status=active").json()) == 4
    assert set(cancelled) == {
        "id",
        "room_id",
        "series_id",
        "user",
        "start",
        "end",
        "timezone",
        "status",
    }


def test_series_list_pagination_metadata_and_cancellation_status(client, series):
    later = client.post(
        "/series",
        json={
            **series,
            "room_id": 17,
            "start": "2026-07-09T09:00:00",
            "end": "2026-07-09T10:00:00",
        },
    ).json()
    earlier = client.post("/series", json=series).json()
    rows = client.get("/series").json()
    assert [row["id"] for row in rows] == [earlier["series_id"], later["series_id"]]
    assert client.get("/series?limit=1&offset=1").json() == rows[1:]
    assert client.get("/series?offset=2").json() == []
    assert client.get("/series?timezone=America/Denver").json() == rows[1:]
    detail = client.get(f"/series/{earlier['series_id']}").json()
    assert detail.pop("bookings") == earlier["created"]
    assert detail == rows[0]
    client.post(f"/series/{earlier['series_id']}/cancel")
    assert client.get("/series").json()[0]["status"] == "cancelled"


@pytest.mark.parametrize("endpoint", ["/bookings", "/series", "/booking-cancel", "/series-cancel"])
def test_each_mutation_handles_writer_timeout_without_changes(
    client, db, booking, series, endpoint
):
    single = client.post("/bookings", json={**booking, "room_id": 4}).json()
    recurring = client.post("/series", json={**series, "room_id": 17}).json()
    paths = {
        "/bookings": ("/bookings", booking),
        "/series": ("/series", series),
        "/booking-cancel": (f"/bookings/{single['id']}/cancel", {}),
        "/series-cancel": (f"/series/{recurring['series_id']}/cancel", {}),
    }
    path, payload = paths[endpoint]
    before = client.get("/bookings").json(), client.get("/series").json()
    db.timeout = 0.05
    with db.transaction():
        response = client.post(path, json=payload)
    assert_error(response, 503, "database_busy")
    assert response.headers["Retry-After"] == "1"
    assert (client.get("/bookings").json(), client.get("/series").json()) == before


def test_preview_reads_committed_snapshot_without_writing(client, db, app, booking, series):
    payload = BookingInput.model_validate(booking)
    with db.transaction() as connection:
        app.state.service.insert_booking(
            connection, payload, occurrences(payload, "Europe/Berlin")[0]
        )
        response = client.post("/series/preview", json=series)
        assert response.status_code == 200
        assert len(response.json()["available"]) == 5  # uncommitted row is invisible
        assert counts(db) == (0, 0)
    response = client.post("/series/preview", json=series)
    assert len(response.json()["available"]) == 4
    assert len(response.json()["skipped"]) == 1
    assert counts(db) == (1, 0)


def test_single_insert_database_failure_does_not_create_booking(client, db, booking):
    with db.connect() as connection:
        connection.executescript("""
            CREATE TRIGGER fail_insert BEFORE INSERT ON bookings
            BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END;
        """)
    assert_error(client.post("/bookings", json=booking), 500, "database_error")
    assert counts(db) == (0, 0)


def test_single_cancel_database_failure_keeps_slot_reserved(client, db, booking):
    result = client.post("/bookings", json=booking).json()
    with db.connect() as connection:
        connection.executescript("""
            CREATE TRIGGER fail_update BEFORE UPDATE ON bookings
            BEGIN SELECT RAISE(ABORT, 'injected update failure'); END;
        """)
    assert_error(client.post(f"/bookings/{result['id']}/cancel"), 500, "database_error")
    assert client.get(f"/bookings/{result['id']}").json() == result
    assert_error(client.post("/bookings", json=booking), 409, "booking_conflict")


def test_preview_and_creation_report_multiple_conflicts_for_one_occurrence(client, series, booking):
    ids = []
    for start, end in (("09:00", "09:30"), ("09:30", "10:00")):
        result = client.post(
            "/bookings",
            json={**booking, "start": f"2026-07-02T{start}", "end": f"2026-07-02T{end}"},
        ).json()
        ids.append(result["id"])
    preview = client.post("/series/preview", json=series).json()
    assert preview["skipped"][0]["conflicting_booking_ids"] == ids
    assert len(preview["available"]) == 4
    created = client.post("/series", json=series).json()
    assert created["skipped"] == preview["skipped"]
    assert len(created["created"]) == 4


@pytest.mark.parametrize("endpoint", ["/series", "/series/preview"])
def test_recurrence_date_overflow_is_validation_error(client, db, series, endpoint):
    response = client.post(
        endpoint,
        json={
            **series,
            "start": "9998-12-30T09:00:00",
            "end": "9998-12-30T10:00:00",
            "repeat_until": "9999-01-06",
        },
    )
    assert_error(response, 422, "invalid_date")
    assert counts(db) == (0, 0)


def test_api_documentation_static_files_and_wrong_methods(client):
    expected_paths = {
        "/rooms",
        "/rooms/{room_id}",
        "/bookings",
        "/bookings/{booking_id}",
        "/bookings/{booking_id}/cancel",
        "/series",
        "/series/preview",
        "/series/{series_id}",
        "/series/{series_id}/cancel",
    }
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert set(schema.json()["paths"]) == expected_paths
    for path in ("/", "/docs", "/redoc", "/docs/oauth2-redirect"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
    for path, content_type in (
        ("/static/dashboard.css", "text/css"),
        ("/static/dashboard.js", "javascript"),
        ("/static/favicon.svg", "image/svg+xml"),
    ):
        response = client.get(path)
        assert response.status_code == 200 and response.content
        assert content_type in response.headers["content-type"]
    assert_error(client.get("/static/missing.css"), 404, "http_error")
    for path in (
        "/",
        "/rooms",
        "/rooms/3",
        "/bookings",
        "/bookings/missing",
        "/bookings/missing/cancel",
        "/series",
        "/series/preview",
        "/series/missing",
        "/series/missing/cancel",
    ):
        response = client.delete(path)
        assert_error(response, 405, "http_error")
        assert response.headers["Allow"]
