from datetime import UTC, datetime

import pytest

from fluxyr.tools import clock
from fluxyr.tools.registry import Registry


@pytest.mark.parametrize(
    "instant,zone,expected",
    [
        ("2026-10-08T01:30:00+00:00", "America/Sao_Paulo", "2026-10-07T22:30:00-03:00"),
        ("2026-07-01T12:00:00+00:00", "America/New_York", "2026-07-01T08:00:00-04:00"),
        ("2026-01-01T12:00:00+00:00", "America/New_York", "2026-01-01T07:00:00-05:00"),
    ],
)
def test_clock_timezone_date_boundary_and_daylight_saving(
    monkeypatch, instant, zone, expected
):
    fixed = datetime.fromisoformat(instant)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz)

    monkeypatch.setattr(clock, "datetime", FrozenDatetime)
    result = clock.current_datetime(zone)
    assert result["datetime"] == expected
    assert result["date"] == expected[:10]
    assert result["time"] == expected[11:19]
    assert result["utc_offset"] == expected[-6:]
    assert result["unix_timestamp"] == int(fixed.timestamp())
    assert result["utc_datetime"] == instant
    assert result["timezone"] == zone
    assert result["timezone_source"] == "requested"
    assert result["weekday"] == (
        "Thursday" if instant.startswith("2026-01") else "Wednesday"
    )


def test_clock_default_is_fresh_server_local_time():
    before = datetime.now(UTC)
    result = clock.current_datetime()
    after = datetime.now(UTC)
    assert int(before.timestamp()) <= result["unix_timestamp"] <= int(after.timestamp())
    assert result["timezone_source"] == "server_local"
    assert (
        datetime.fromisoformat(result["datetime"]).utcoffset()
        == after.astimezone().utcoffset()
    )


@pytest.mark.parametrize("builder", [False, True])
def test_clock_native_tool_available_and_validated(make_app, builder):
    _, engine, _ = make_app()
    engine.store.enqueue("Check today's date")
    job = engine.store.claim(engine.owner)
    if builder:
        job["input"]["builder"] = {"skill_id": "example"}
    definitions = Registry(engine, job, lambda *_: None, lambda: False).definitions()
    tool = next(t for t in definitions if t["name"] == "get_current_datetime")
    assert tool["parallel_safe"] and not tool["side_effecting"]
    result, mode = tool["function"](timezone="UTC")
    assert mode == "continue"
    assert result["utc_offset"] == "+00:00"
    invalid, mode = tool["function"](timezone="Invalid/Timezone")
    assert mode == "continue" and invalid["type"] == "validation_error"
    invalid, _ = tool["function"](timezone=42)
    assert invalid["executed"] is False
