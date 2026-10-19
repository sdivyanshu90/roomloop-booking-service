PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    timezone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    user TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    timezone TEXT NOT NULL,
    rule TEXT NOT NULL CHECK (rule = 'weekly'),
    repeat_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    cancelled_at REAL
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    series_id TEXT REFERENCES series(id),
    user TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    timezone TEXT NOT NULL,
    start_utc INTEGER NOT NULL,
    end_utc INTEGER NOT NULL CHECK (end_utc > start_utc),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    cancelled_at REAL,
    UNIQUE (series_id, start)
);

CREATE INDEX IF NOT EXISTS active_room_times
ON bookings(room_id, start_utc, end_utc) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS series_membership ON bookings(series_id);
