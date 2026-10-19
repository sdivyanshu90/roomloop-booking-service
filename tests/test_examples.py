"""Run the three concrete creation/preview request bodies printed in README."""

import json
import re
from pathlib import Path


def test_readme_example_sequence(client):
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    requests = re.findall(
        r"curl -sS http://127\.0\.0\.1:8000(/[^\s]+).*?-d '([^']+)'", readme, re.DOTALL
    )
    assert [path for path, _ in requests] == ["/bookings", "/series/preview", "/series"]
    responses = [client.post(path, json=json.loads(payload)) for path, payload in requests]
    assert [response.status_code for response in responses] == [201, 200, 201]
    booking, preview, series = [response.json() for response in responses]
    expected = json.loads(re.findall(r"```json\n(.*?)\n```", readme, re.DOTALL)[0])
    expected["id"] = booking["id"]
    assert booking == expected
    expected = json.loads(re.findall(r"```json\n(.*?)\n```", readme, re.DOTALL)[1])
    expected["skipped"][0]["conflicting_booking_ids"] = [booking["id"]]
    assert preview == expected
    assert series["skipped"] == preview["skipped"]
    assert len(series["created"]) == 1
    assert series["created"][0]["start"] == "2026-07-09T09:00:00"
    assert series["created"][0]["series_id"] == series["series_id"]
