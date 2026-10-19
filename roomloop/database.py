import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


class Database:
    def __init__(self, path: str | Path, timeout: float = 5):
        self.path = Path(path)
        self.timeout = timeout

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            # The lock covers the availability read AND all following writes.
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript((PACKAGE / "schema.sql").read_text())
        with self.transaction() as connection:
            for room in json.loads((PACKAGE / "data/rooms.json").read_text()):
                connection.execute(
                    "INSERT OR IGNORE INTO rooms(id, name, capacity, timezone) "
                    "VALUES (:id, :name, :capacity, :timezone)",
                    room,
                )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize RoomLoop's database and rooms")
    parser.add_argument("path", nargs="?", default="var/roomloop.sqlite3")
    Database(parser.parse_args().path).initialize()
