import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SQLITE_INTEGER = 2**63 - 1


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    room_id: int = Field(gt=0, le=MAX_SQLITE_INTEGER, strict=True)
    start: datetime
    end: datetime
    user: str = Field(min_length=1, max_length=100)

    @field_validator("start", "end", mode="before")
    @classmethod
    def local_iso(cls, value):
        # Explicitly exclude offsets, epoch numbers and fractional seconds.
        if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", value
        ):
            raise ValueError("Use a naive local ISO timestamp: YYYY-MM-DDTHH:MM:SS")
        result = datetime.fromisoformat(value)
        if not 2 <= result.year <= 9998:
            raise ValueError("Supported years are 0002 through 9998")
        return result

    @model_validator(mode="after")
    def duration(self):
        if not timedelta(0) < self.end - self.start <= timedelta(days=1):
            raise ValueError("End must follow start; maximum local duration is 24 hours")
        return self


class SeriesInput(BookingInput):
    rule: Literal["weekly"] = "weekly"
    repeat_until: date

    @field_validator("repeat_until", mode="before")
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a calendar date: YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def recurrence_bounds(self):
        days = (self.repeat_until - self.start.date()).days
        if days < 0:
            raise ValueError("Repeat-until must be on or after the first start date")
        if days // 7 + 1 > 104:
            raise ValueError("A series may contain at most 104 weekly occurrences")
        return self
