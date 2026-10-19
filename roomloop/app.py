import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from roomloop.database import PACKAGE, Database
from roomloop.errors import DomainError
from roomloop.models import MAX_SQLITE_INTEGER, BookingInput, SeriesInput
from roomloop.service import BookingService

logger = logging.getLogger(__name__)


def error_response(status, code, message, details=None):
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else [],
            }
        },
    )


def create_app(db_path=None, clock=None, db_timeout=5):
    database = Database(db_path or os.getenv("ROOMLOOP_DB", "var/roomloop.sqlite3"), db_timeout)
    service = BookingService(database, clock)

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(title="RoomLoop", version="1.0.0", lifespan=lifespan)
    app.state.database = database
    app.state.service = service
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request):
        rooms = [service.room_detail(room["id"]) for room in service.rooms()]
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "rooms": rooms,
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        details = [
            {"field": ".".join(map(str, item["loc"])), "message": item["msg"]}
            for item in exc.errors()
        ]
        return error_response(422, "validation_error", "Check the request fields", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        response = error_response(exc.status_code, "http_error", str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        ):
            response = error_response(503, "database_busy", "Database busy; retry shortly")
            response.headers["Retry-After"] = "1"
            return response
        logger.exception("Database operation failed")
        return error_response(500, "database_error", "Database operation failed; no changes saved")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.exception("Unexpected request failure")
        return error_response(500, "internal_error", "An unexpected error occurred")

    @app.get("/rooms")
    def rooms():
        return service.rooms()

    @app.get("/rooms/{room_id}")
    def room(room_id: int = Path(gt=0, le=MAX_SQLITE_INTEGER)):
        return service.room_detail(room_id)

    @app.post("/bookings", status_code=201)
    def create_booking(request: BookingInput):
        return service.create_booking(request)

    @app.get("/bookings")
    def bookings(
        room_id: int | None = Query(None, gt=0, le=MAX_SQLITE_INTEGER),
        series_id: str | None = None,
        status: Literal["active", "cancelled"] | None = None,
        day: date | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0, le=MAX_SQLITE_INTEGER),
        timezone: Literal["Europe/Berlin", "America/Denver"] | None = None,
    ):
        return service.list_bookings(room_id, series_id, status, day, limit, offset, timezone)

    @app.get("/bookings/{booking_id}")
    def booking(booking_id: str):
        return service.get_booking(booking_id)

    @app.post("/bookings/{booking_id}/cancel")
    def cancel_booking(booking_id: str):
        return service.cancel_booking(booking_id)

    @app.post("/series/preview")
    def preview(request: SeriesInput):
        return service.preview(request)

    @app.post("/series", status_code=201)
    def create_series(request: SeriesInput):
        return service.create_series(request)

    @app.get("/series")
    def all_series(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0, le=MAX_SQLITE_INTEGER),
        timezone: Literal["Europe/Berlin", "America/Denver"] | None = None,
    ):
        return service.list_series(limit, offset, timezone)

    @app.get("/series/{series_id}")
    def series(series_id: str):
        return service.get_series(series_id)

    @app.post("/series/{series_id}/cancel")
    def cancel_series(series_id: str):
        return service.cancel_series(series_id)

    return app


app = create_app()
