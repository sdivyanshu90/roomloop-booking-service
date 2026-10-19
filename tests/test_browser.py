"""Optional real-browser suite: pytest -m browser (requires Playwright Chromium)."""

import os
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import uvicorn

pytestmark = pytest.mark.browser


@pytest.fixture
def live_url(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test server failed to start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.fixture
def page(live_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        # Deliberately use a third timezone to expose accidental browser-local conversions.
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            timezone_id="Asia/Tokyo",
            reduced_motion="reduce",
        )
        page = context.new_page()
        page.clock.install(time=datetime(2026, 7, 9, 7, 30, tzinfo=UTC))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_url)
        page.wait_for_function(
            "!document.querySelector('#booking-list').textContent.includes('Loading')"
        )
        yield page
        assert errors == []
        context.close()
        browser.close()


def fill_booking(page, user, start, end):
    page.get_by_label("Booked by").fill(user)
    page.get_by_label("Starts", exact=True).fill(start)
    page.get_by_label("Ends", exact=True).fill(end)


def test_booking_preview_skips_and_series_cancellation(page):
    from playwright.sync_api import expect

    # Current day defaults to Berlin, even though this browser is in Tokyo.
    expect(page.locator("#calendar-date")).to_have_value("2026-07-09")
    fill_booking(page, "Design review", "2026-07-16T09:00", "2026-07-16T10:00")
    page.get_by_role("button", name="Create booking").click()
    expect(page.locator("#form-feedback")).to_contain_text("Aurora is booked")
    expect(page.locator("#booking-list")).to_contain_text("09:00 – 10:00")
    page.get_by_label("Weekly series", exact=False).check()
    fill_booking(page, "Product sync", "2026-07-09T09:00", "2026-07-09T10:00")
    page.get_by_label("Repeat until", exact=False).fill("2026-07-30")
    page.get_by_role("button", name="Preview weekly dates").click()
    expect(page.locator("#preview-result")).to_contain_text("3 accepted · 1 skipped")
    expect(page.locator("#preview-result .skipped")).to_contain_text("16 Jul 2026")
    page.get_by_role("button", name="Create weekly series").click()
    expect(page.locator("#form-feedback")).to_contain_text("3 dates booked · 1 skipped")
    expect(page.locator("#series-list")).to_contain_text("Product sync")
    page.get_by_role("button", name="Cancel future dates for Product sync").click()
    expect(page.get_by_role("dialog")).to_contain_text("Past and ongoing meetings stay unchanged")
    page.get_by_role("button", name="Confirm cancellation").click()
    expect(page.locator("#page-feedback")).to_contain_text("2 future dates cancelled")
    expect(page.locator("#series-list")).to_contain_text("Cancelled")
    expect(page.locator("#booking-list")).to_contain_text("Product sync")  # ongoing preserved
    page.get_by_role("button", name="Cancel booking for Product sync").click()
    page.get_by_role("button", name="Confirm cancellation").click()
    expect(page.locator("#booking-list")).to_contain_text("A clear calendar")


def test_mobile_and_screenshot(page, live_url, tmp_path):
    from playwright.sync_api import expect

    for payload in [
        {
            "room_id": 3,
            "start": "2026-07-09T09:00:00",
            "end": "2026-07-09T10:00:00",
            "user": "Product team",
        },
        {
            "room_id": 4,
            "start": "2026-07-09T11:00:00",
            "end": "2026-07-09T11:45:00",
            "user": "Design catch-up",
        },
        {
            "room_id": 9,
            "start": "2026-07-09T09:30:00",
            "end": "2026-07-09T10:30:00",
            "user": "Denver stand-up",
        },
    ]:
        assert page.request.post(f"{live_url}/bookings", data=payload).status == 201
    assert (
        page.request.post(
            f"{live_url}/series",
            data={
                "room_id": 17,
                "user": "Engineering sync",
                "start": "2026-07-09T14:00:00",
                "end": "2026-07-09T14:30:00",
                "repeat_until": "2026-08-06",
            },
        ).status
        == 201
    )
    page.reload()
    expect(page.locator("#booking-list")).to_contain_text("Product team")
    expect(page.locator("#series-list")).to_contain_text("Engineering sync")
    directory = Path(os.getenv("ROOMLOOP_SCREENSHOTS", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / "dashboard.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Denver", exact=True).click()
    expect(page.locator(".room-card:visible")).to_have_count(2)
    expect(page.locator("#timezone-hint")).to_contain_text("America/Denver")
    expect(page.locator("#booking-list")).to_contain_text("Denver stand-up")
    expect(page.locator("#booking-list")).not_to_contain_text("Product team")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    fill_booking(page, "Mobile team", "2026-07-09T12:00", "2026-07-09T12:30")
    page.get_by_role("button", name="Create booking").click()
    expect(page.locator("#form-feedback")).to_contain_text("Cinder is booked")
    expect(page.locator("#booking-list")).to_contain_text("Mobile team")
    page.screenshot(path=str(directory / "dashboard-mobile.png"), full_page=True)


def test_preview_invalidates_after_edit_and_dst_error(page):
    from playwright.sync_api import expect

    page.get_by_label("Weekly series", exact=False).check()
    fill_booking(page, "DST team", "2026-03-22T02:15", "2026-03-22T03:15")
    page.get_by_label("Repeat until", exact=False).fill("2026-03-29")
    page.get_by_role("button", name="Preview weekly dates").click()
    expect(page.locator("#form-feedback")).to_contain_text("nonexistent in Europe/Berlin")
    fill_booking(page, "DST team", "2026-03-22T09:00", "2026-03-22T10:00")
    page.get_by_role("button", name="Preview weekly dates").click()
    expect(page.locator("#preview-result")).to_contain_text("2 accepted · 0 skipped")
    page.get_by_label("Starts", exact=True).fill("2026-03-22T09:30")
    expect(page.locator("#preview-result")).to_be_hidden()


def test_failed_preview_does_not_leave_old_availability_visible(page):
    from playwright.sync_api import expect

    page.get_by_label("Weekly series", exact=False).check()
    fill_booking(page, "Availability review", "2026-07-09T09:00", "2026-07-09T10:00")
    page.get_by_label("Repeat until", exact=False).fill("2026-07-30")
    page.get_by_role("button", name="Preview weekly dates").click()
    expect(page.locator("#preview-result")).to_contain_text("4 accepted")
    page.route(
        "**/series/preview",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body='{"error":{"code":"database_busy","message":"Database busy; retry shortly","details":[]}}',
        ),
    )
    page.get_by_role("button", name="Preview weekly dates").click()
    expect(page.locator("#form-feedback")).to_contain_text("Database busy")
    expect(page.locator("#preview-result")).to_be_hidden()


def test_saved_booking_with_failed_refresh_is_not_reported_as_failed_save(page):
    from playwright.sync_api import expect

    page.route("**/bookings?*", lambda route: route.abort())
    fill_booking(page, "Refresh review", "2026-07-09T12:00", "2026-07-09T13:00")
    page.get_by_role("button", name="Create booking").click()
    expect(page.locator("#form-feedback")).to_contain_text("Aurora is booked")
    expect(page.locator("#page-feedback")).to_contain_text("Your change was saved")
