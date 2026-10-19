from datetime import UTC, datetime
from uuid import uuid4

from roomloop.database import Database
from roomloop.errors import DomainError
from roomloop.models import BookingInput, SeriesInput
from roomloop.scheduling import occurrences

BOOKING_FIELDS = "id, room_id, series_id, user, start, end, timezone, status"


def public_booking(row):
    return {key: row[key] for key in BOOKING_FIELDS.split(", ")}


class BookingService:
    def __init__(self, db: Database, clock=None):
        self.db = db
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def room(connection, room_id):
        row = connection.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if row is None:
            raise DomainError("room_not_found", "Room does not exist", 404)
        return dict(row)

    def rooms(self):
        with self.db.connect() as connection:
            return [
                dict(row)
                for row in connection.execute("SELECT id, name, capacity FROM rooms ORDER BY id")
            ]

    def room_detail(self, room_id):
        with self.db.connect() as connection:
            return self.room(connection, room_id)

    @staticmethod
    def conflicts(connection, room_id, occurrence):
        return [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM bookings WHERE room_id = ? AND status = 'active' "
                "AND start_utc < ? AND end_utc > ? ORDER BY start_utc, id",
                (room_id, occurrence["end_utc"], occurrence["start_utc"]),
            )
        ]

    def plan(self, connection, request: SeriesInput):
        room = self.room(connection, request.room_id)
        # Validate all occurrences before deciding which may be skipped.
        candidates = occurrences(request, room["timezone"])
        accepted, skipped = [], []
        for candidate in candidates:
            conflicts = self.conflicts(connection, request.room_id, candidate)
            if conflicts:
                skipped.append(
                    {
                        "start": candidate["start"],
                        "end": candidate["end"],
                        "timezone": candidate["timezone"],
                        "reason": "booking_conflict",
                        "conflicting_booking_ids": conflicts,
                    }
                )
            else:
                accepted.append(candidate)
        return accepted, skipped

    @staticmethod
    def insert_booking(connection, request, occurrence, series_id=None):
        booking = {
            **occurrence,
            "id": str(uuid4()),
            "room_id": request.room_id,
            "series_id": series_id,
            "user": request.user,
            "status": "active",
        }
        connection.execute(
            "INSERT INTO bookings(id, room_id, series_id, user, start, end, timezone, "
            "start_utc, end_utc, status) VALUES (:id, :room_id, :series_id, :user, "
            ":start, :end, :timezone, :start_utc, :end_utc, :status)",
            booking,
        )
        return public_booking(booking)

    def create_booking(self, request: BookingInput):
        with self.db.transaction() as connection:
            room = self.room(connection, request.room_id)
            occurrence = occurrences(request, room["timezone"])[0]
            conflicts = self.conflicts(connection, request.room_id, occurrence)
            if conflicts:
                raise DomainError(
                    "booking_conflict",
                    "This room is already booked",
                    409,
                    {"conflicting_booking_ids": conflicts},
                )
            return self.insert_booking(connection, request, occurrence)

    def preview(self, request: SeriesInput):
        with self.db.connect() as connection:
            connection.execute("BEGIN")
            accepted, skipped = self.plan(connection, request)
            return {
                "available": [
                    {key: item[key] for key in ("start", "end", "timezone")} for item in accepted
                ],
                "skipped": skipped,
                "can_create": bool(accepted),
                "advisory": True,
            }

    def create_series(self, request: SeriesInput):
        with self.db.transaction() as connection:
            accepted, skipped = self.plan(connection, request)
            if not accepted:
                raise DomainError(
                    "all_occurrences_conflict",
                    "Every occurrence conflicts; nothing saved",
                    409,
                    {"skipped": skipped},
                )
            series_id = str(uuid4())
            connection.execute(
                "INSERT INTO series(id, room_id, user, start, end, timezone, rule, repeat_until) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    series_id,
                    request.room_id,
                    request.user,
                    request.start.isoformat(),
                    request.end.isoformat(),
                    accepted[0]["timezone"],
                    request.rule,
                    request.repeat_until.isoformat(),
                ),
            )
            created = [
                self.insert_booking(connection, request, item, series_id) for item in accepted
            ]
            return {"series_id": series_id, "created": created, "skipped": skipped}

    def list_bookings(
        self,
        room_id=None,
        series_id=None,
        status=None,
        day=None,
        limit=100,
        offset=0,
        timezone=None,
    ):
        clauses, args = [], []
        for key, value in (
            ("room_id", room_id),
            ("series_id", series_id),
            ("status", status),
            ("timezone", timezone),
        ):
            if value is not None:
                clauses.append(f"{key} = ?")
                args.append(value)
        if day is not None:
            # Local calendar date, including overnight meetings that cross into it.
            clauses.append("substr(start, 1, 10) <= ? AND end > ?")
            args.extend((day.isoformat(), day.isoformat() + "T00:00:00"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.db.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    f"SELECT {BOOKING_FIELDS} FROM bookings{where} "
                    "ORDER BY start_utc, id LIMIT ? OFFSET ?",
                    (*args, limit, offset),
                )
            ]

    def get_booking(self, booking_id):
        with self.db.connect() as connection:
            row = connection.execute(
                f"SELECT {BOOKING_FIELDS} FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
            if row is None:
                raise DomainError("booking_not_found", "Booking does not exist", 404)
            return dict(row)

    def list_series(self, limit=100, offset=0, timezone=None):
        where = " WHERE timezone = ?" if timezone else ""
        args = (timezone, limit, offset) if timezone else (limit, offset)
        with self.db.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, room_id, user, start, end, timezone, rule, repeat_until, status "
                    f"FROM series{where} ORDER BY start, id LIMIT ? OFFSET ?",
                    args,
                )
            ]

    def get_series(self, series_id):
        with self.db.connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
            if row is None:
                raise DomainError("series_not_found", "Series does not exist", 404)
            result = dict(row)
            result.pop("cancelled_at")
            result["bookings"] = [
                dict(item)
                for item in connection.execute(
                    f"SELECT {BOOKING_FIELDS} FROM bookings WHERE series_id = ? ORDER BY start_utc",
                    (series_id,),
                )
            ]
            return result

    def cancellation_instant(self):
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Cancellation clock must return an aware datetime")
        # Keep subsecond precision: a meeting that started 0.5s ago is ongoing.
        return now.timestamp()

    def cancel_booking(self, booking_id):
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
            if row is None:
                raise DomainError("booking_not_found", "Booking does not exist", 404)
            if row["status"] == "active":
                connection.execute(
                    "UPDATE bookings SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                    (self.cancellation_instant(), booking_id),
                )
            result = public_booking(row)
            result["status"] = "cancelled"
            return result

    def cancel_series(self, series_id):
        with self.db.transaction() as connection:
            row = connection.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
            if row is None:
                raise DomainError("series_not_found", "Series does not exist", 404)
            if row["status"] == "cancelled":
                return {"series_id": series_id, "status": "cancelled", "cancelled_count": 0}
            now = self.cancellation_instant()
            cursor = connection.execute(
                "UPDATE bookings SET status = 'cancelled', cancelled_at = ? "
                "WHERE series_id = ? AND status = 'active' AND start_utc >= ?",
                (now, series_id, now),
            )
            connection.execute(
                "UPDATE series SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                (now, series_id),
            )
            return {
                "series_id": series_id,
                "status": "cancelled",
                "cancelled_count": cursor.rowcount,
            }
