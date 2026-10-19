import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from roomloop.app import create_app


@pytest.fixture
def clock():
    return [datetime(2026, 7, 9, 7, 30, tzinfo=UTC)]


@pytest.fixture
def app(tmp_path, clock):
    return create_app(tmp_path / "test.sqlite3", clock=lambda: clock[0], db_timeout=5)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def db(app, client):
    return app.state.database


@pytest.fixture
def booking():
    return {
        "room_id": 3,
        "start": "2026-07-02T09:00:00",
        "end": "2026-07-02T10:00:00",
        "user": "Ada",
    }


@pytest.fixture
def series(booking):
    return {**booking, "rule": "weekly", "repeat_until": "2026-07-30"}


@pytest.fixture
def risk_data():
    return json.loads((Path(__file__).parent / "fixtures/high_risk.json").read_text())


def counts(db):
    with db.connect() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("bookings", "series")
        )
