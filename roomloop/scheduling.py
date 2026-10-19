from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from roomloop.errors import DomainError
from roomloop.models import BookingInput, SeriesInput


def utc_seconds(local: datetime, timezone: str) -> int:
    zone = ZoneInfo(timezone)
    candidates = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        # ZoneInfo construction alone accepts gaps. A round trip detects them.
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(int(instant.timestamp()))
    if len(candidates) != 1:
        kind = "nonexistent" if not candidates else "ambiguous"
        raise DomainError(
            f"{kind}_local_time",
            f"{local.isoformat()} is {kind} in {timezone}; choose another time.",
            details={"local": local.isoformat(), "timezone": timezone},
        )
    return candidates.pop()


def occurrences(request: BookingInput, timezone: str) -> list[dict]:
    count = 1
    if isinstance(request, SeriesInput):
        count = (request.repeat_until - request.start.date()).days // 7 + 1
    result = []
    for index in range(count):
        try:
            start = request.start + timedelta(weeks=index)
            end = request.end + timedelta(weeks=index)
        except OverflowError:
            raise DomainError("invalid_date", "Recurrence exceeds supported dates") from None
        if end.year > 9998:
            raise DomainError("invalid_date", "Recurrence exceeds supported dates")
        start_utc, end_utc = utc_seconds(start, timezone), utc_seconds(end, timezone)
        if start_utc >= end_utc:
            raise DomainError("invalid_duration", "End must follow start in UTC")
        result.append(
            {
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "timezone": timezone,
                "start_utc": start_utc,
                "end_utc": end_utc,
            }
        )
    return result
