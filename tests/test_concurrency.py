from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import counts

from roomloop.database import Database
from roomloop.errors import DomainError
from roomloop.models import BookingInput, SeriesInput
from roomloop.service import BookingService


@pytest.mark.parametrize(
    "kinds", [("single", "single"), ("single", "series"), ("series", "series")]
)
def test_independent_writers_never_overlap(db, booking, series, kinds):
    barrier = Barrier(2)

    def write(kind):
        # Distinct service instances and connections; only the database file is shared.
        service = BookingService(Database(db.path, timeout=5))
        barrier.wait(timeout=5)
        try:
            if kind == "single":
                return service.create_booking(BookingInput.model_validate(booking))
            return service.create_series(SeriesInput.model_validate(series))
        except DomainError as error:
            assert error.status == 409
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, kinds))
    with db.connect() as connection:
        overlaps = connection.execute("""
            SELECT count(*) FROM bookings a JOIN bookings b
            ON a.id < b.id AND a.room_id = b.room_id
            AND a.start_utc < b.end_utc AND b.start_utc < a.end_utc
            WHERE a.status = 'active' AND b.status = 'active'
        """).fetchone()[0]
        assert overlaps == 0
    if kinds == ("single", "single"):
        assert sum(result is not None for result in results) == 1
        assert counts(db) == (1, 0)
    else:
        assert counts(db) == (5, 1)


def test_simultaneous_http_requests(client, db, booking):
    barrier = Barrier(2)

    def write(_):
        barrier.wait(timeout=5)
        return client.post("/bookings", json=booking).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, range(2))) == [201, 409]
    assert counts(db) == (1, 0)
