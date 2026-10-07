from datetime import datetime

from fluxyr_agent.routines import next_occurrence


def test_preview_uses_scheduler_timezone_and_never_creates_a_routine(make_app):
    app, e, _ = make_app()
    c = app.test_client()
    result = c.post(
        "/api/routines/preview",
        json={"cron": "0 8 * * 1-5", "timezone": "America/Sao_Paulo"},
    )
    assert result.status_code == 200
    occurrences = [datetime.fromisoformat(x) for x in result.json["occurrences"]]
    assert len(occurrences) == 3
    assert all(x.hour == 8 and x.minute == 0 and x.weekday() < 5 for x in occurrences)
    assert occurrences == sorted(occurrences)
    assert e.routines.list() == []
    for cron, timezone in [
        ("not cron", "UTC"),
        ("0 8 * * * *", "UTC"),
        ("0 8 * * *", "Invalid/Zone"),
    ]:
        assert (
            c.post(
                "/api/routines/preview", json={"cron": cron, "timezone": timezone}
            ).status_code
            == 400
        )
    # Invalid cron must not be saved silently just because scheduling is disabled.
    assert (
        c.post(
            "/api/routines",
            json={
                "name": "Invalid",
                "prompt": "Hello",
                "cron": "invalid",
                "enabled": False,
            },
        ).status_code
        == 400
    )
    assert e.routines.list() == []


def test_calendar_skips_missing_month_days_and_preserves_local_time_through_dst():
    start = datetime.fromisoformat("2026-02-01T00:00:00+00:00").timestamp()
    following = next_occurrence("0 8 31 * *", "UTC", start)
    assert (
        datetime.fromtimestamp(
            following, datetime.fromisoformat("2026-01-01T00:00:00+00:00").tzinfo
        ).isoformat()
        == "2026-03-31T08:00:00+00:00"
    )
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 7, 8, tzinfo=zone).timestamp()
    following = datetime.fromtimestamp(
        next_occurrence("0 8 * * *", "America/New_York", start), zone
    )
    assert following.hour == 8 and following.day == 8
    assert following.utcoffset().total_seconds() == -4 * 3600
