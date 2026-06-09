from datetime import date

from astrobot.lunar import compute_phases


def test_compute_phases_returns_events_within_window():
    """Smoke test: any 60-day window should contain ~2 new moons and ~2 full moons."""
    start = date(2026, 6, 1)
    end = date(2026, 7, 31)
    events = compute_phases(start, end)
    assert 3 <= len(events) <= 6, f"unexpected event count: {len(events)} in {events}"
    kinds = {e.kind for e in events}
    assert "new" in kinds, "no new moon detected in 2 months"
    assert "full" in kinds, "no full moon detected in 2 months"


def test_compute_phases_dates_in_range():
    start = date(2026, 1, 1)
    end = date(2026, 3, 31)
    events = compute_phases(start, end)
    for e in events:
        assert start <= e.event_date <= end


def test_compute_phases_empty_for_one_day_window():
    """A 0-day range (start == end) returns no events because the loop body
    requires at least one step past `start`."""
    d = date(2026, 6, 10)
    events = compute_phases(d, d)
    assert events == []
