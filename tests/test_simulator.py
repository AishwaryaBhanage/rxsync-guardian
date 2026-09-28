"""Step 1 tests: the truth data.

Fast tests run a small world (SimConfig.small()); anything needing full volume
carries @pytest.mark.slow and is deselected by default. See pyproject.toml.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, time

import pytest

from simulator.config import SimConfig
from simulator.models import STATUS_SEQUENCE, TERMINAL_STATUSES
from simulator.schedule import (
    OpeningHours,
    advance_business_hours,
    next_open_moment,
)
from simulator.world import build_world, group_events_by_rx, index_pharmacies
from simulator.writers import TRUTH_FILES, write_truth

SUNDAY = 6


@pytest.fixture(scope="module")
def small_world():
    return build_world(SimConfig.small())


# --- schedule unit tests -------------------------------------------------


def _hours(sunday_open: bool) -> OpeningHours:
    weekday = (9, 17)
    return OpeningHours(windows=(weekday,) * 5 + ((10, 14), (11, 15) if sunday_open else None))


def test_advance_business_hours_skips_a_closed_sunday():
    closed = _hours(sunday_open=False)
    # Saturday 13:00, one hour left before close, then 4 more hours needed.
    saturday = datetime(2026, 9, 26, 13, 0)
    assert saturday.weekday() == 5
    landed = advance_business_hours(saturday, 5.0, closed)
    assert landed.weekday() != SUNDAY
    assert landed == datetime(2026, 9, 28, 13, 0)  # Monday, 4 h into the day


def test_advance_business_hours_consumes_open_time_only():
    hours = _hours(sunday_open=True)
    monday_morning = datetime(2026, 9, 21, 9, 0)
    assert advance_business_hours(monday_morning, 3.0, hours) == datetime(2026, 9, 21, 12, 0)


def test_next_open_moment_snaps_forward_past_closure():
    closed = _hours(sunday_open=False)
    sunday_noon = datetime(2026, 9, 27, 12, 0)
    assert sunday_noon.weekday() == SUNDAY
    assert next_open_moment(sunday_noon, closed) == datetime(2026, 9, 28, 9, 0)


def test_opening_hours_rejects_wrong_length():
    with pytest.raises(ValueError):
        OpeningHours(windows=((9, 17),) * 6)


# --- world shape ---------------------------------------------------------


def test_counts_match_config(small_world):
    config = small_world.config
    assert len(small_world.pharmacies) == config.n_pharmacies
    assert len(small_world.patients) == config.n_patients
    assert len(small_world.prescriptions) == config.n_prescriptions
    assert len(small_world.drugs) == 30
    assert small_world.fill_events, "expected at least some fill events"


def test_every_category_is_represented(small_world):
    """The small world must still cover each size, area and PMS type."""
    assert {p.size for p in small_world.pharmacies} == {"small", "medium", "large"}
    assert {p.area for p in small_world.pharmacies} == {"urban", "rural"}
    assert {p.pms_type for p in small_world.pharmacies} == {"A", "B", "C"}
    sundays = {p.closed_sunday for p in small_world.pharmacies}
    assert sundays == {True, False}, "need both Sunday-closed and Sunday-open"


def test_size_and_area_are_not_correlated(small_world):
    """Guards the bug where index arithmetic made every large pharmacy rural."""
    large_areas = {p.area for p in small_world.pharmacies if p.size == "large"}
    small_areas = {p.area for p in small_world.pharmacies if p.size == "small"}
    assert len(large_areas | small_areas) == 2


def test_prescriptions_reference_the_patient_home_pharmacy(small_world):
    home = {p.patient_id: p.home_pharmacy_id for p in small_world.patients}
    for rx in small_world.prescriptions:
        assert rx.pharmacy_id == home[rx.patient_id]


def test_prescription_fields_are_in_range(small_world):
    config = small_world.config
    for rx in small_world.prescriptions:
        assert rx.days_supply in (30, 90)
        assert 0 <= rx.refills_authorized <= 5
        assert config.window_start <= rx.written_on <= config.as_of


def test_some_prescriptions_have_refill_on_schedule(small_world):
    flagged = [rx for rx in small_world.prescriptions if rx.refill_on_schedule]
    # Needs to stay well above the 1% phantom_schedule fault rate of step 3.
    assert len(flagged) > 0.05 * len(small_world.prescriptions)


# --- fill events ---------------------------------------------------------


def test_no_event_falls_on_a_closed_sunday(small_world):
    """The Sunday edge case, checked across every status, not just `ready`."""
    pharmacies = index_pharmacies(small_world)
    closed = {p.pharmacy_id for p in small_world.pharmacies if p.closed_sunday}
    assert closed, "fixture must contain a Sunday-closed pharmacy"
    offenders = [
        event
        for event in small_world.fill_events
        if event.pharmacy_id in closed and event.occurred_at.weekday() == SUNDAY
    ]
    assert offenders == []
    # And the open-Sunday pharmacies are genuinely reachable on a Sunday, so the
    # assertion above is not passing merely because nothing lands on Sundays.
    assert any(not pharmacies[p].closed_sunday for p in pharmacies)


def test_zero_refill_prescriptions_have_no_refill_events(small_world):
    grouped = group_events_by_rx(small_world)
    zero_refill = [rx for rx in small_world.prescriptions if rx.refills_authorized == 0]
    assert zero_refill, "fixture must contain a 0-refill prescription"
    for rx in zero_refill:
        fills = {event.fill_number for event in grouped.get(rx.rx_number, [])}
        assert fills <= {0}


def test_fill_number_never_exceeds_refills_authorized(small_world):
    grouped = group_events_by_rx(small_world)
    for rx in small_world.prescriptions:
        for event in grouped.get(rx.rx_number, []):
            assert event.fill_number <= rx.refills_authorized


def test_events_never_pass_the_as_of_date(small_world):
    horizon = datetime.combine(small_world.config.as_of, time.max)
    assert all(event.occurred_at <= horizon for event in small_world.fill_events)


def test_each_fill_follows_the_status_sequence(small_world):
    grouped = group_events_by_rx(small_world)
    for events in grouped.values():
        by_fill: dict[int, list] = {}
        for event in events:
            by_fill.setdefault(event.fill_number, []).append(event)
        for fill_events in by_fill.values():
            statuses = [e.status for e in fill_events]
            timestamps = [e.occurred_at for e in fill_events]
            assert timestamps == sorted(timestamps), "events must be chronological"
            # A fill is a prefix of received/in_process/ready, optionally closed
            # by exactly one terminal status.
            terminal = [s for s in statuses if s in TERMINAL_STATUSES]
            assert len(terminal) <= 1
            core = [s for s in statuses if s not in TERMINAL_STATUSES]
            assert tuple(core) == STATUS_SEQUENCE[: len(core)]
            if terminal:
                assert statuses[-1] in TERMINAL_STATUSES
                assert tuple(core) == STATUS_SEQUENCE


def test_event_ids_are_unique(small_world):
    ids = [event.event_id for event in small_world.fill_events]
    assert len(ids) == len(set(ids))


def test_some_fills_are_never_collected(small_world):
    grouped = group_events_by_rx(small_world)
    uncollected = [
        events
        for events in grouped.values()
        if not any(e.status in TERMINAL_STATUSES for e in events)
    ]
    assert uncollected, "some patients should never collect"


# --- determinism ---------------------------------------------------------


def _write_truth_to(tmp_path, name, config) -> dict[str, bytes]:
    out = tmp_path / name
    paths = write_truth(build_world(config), out)
    return {key: path.read_bytes() for key, path in paths.items()}


def test_same_seed_gives_byte_identical_files(tmp_path):
    config = SimConfig.small()
    first = _write_truth_to(tmp_path, "run1", config)
    second = _write_truth_to(tmp_path, "run2", config)
    assert set(first) == set(TRUTH_FILES)
    for name in TRUTH_FILES:
        assert first[name] == second[name], f"{name}.csv differs between runs"


def test_a_different_seed_changes_the_output(tmp_path):
    baseline = _write_truth_to(tmp_path, "seed42", SimConfig.small(seed=42))
    other = _write_truth_to(tmp_path, "seed43", SimConfig.small(seed=43))
    assert baseline["patients"] != other["patients"]


def test_a_different_as_of_changes_the_output(tmp_path):
    """Byte-identity is a property of (seed, as_of), not the seed alone."""
    baseline = _write_truth_to(tmp_path, "asof1", SimConfig.small())
    shifted = _write_truth_to(tmp_path, "asof2", SimConfig.small(as_of=date(2026, 6, 30)))
    assert baseline["prescriptions"] != shifted["prescriptions"]


def test_truth_files_are_written_with_unix_newlines(tmp_path):
    written = _write_truth_to(tmp_path, "newlines", SimConfig.small())
    for name, blob in written.items():
        assert b"\r\n" not in blob, f"{name}.csv has CRLF line endings"


def test_truth_csvs_have_a_header_and_rows(tmp_path):
    written = _write_truth_to(tmp_path, "shape", SimConfig.small())
    for name, blob in written.items():
        lines = blob.decode().splitlines()
        assert len(lines) >= 2, f"{name}.csv has no data rows"


# --- full scale ----------------------------------------------------------


def _ready_hours_by_size(world) -> dict[str, list[float]]:
    """Wall-clock hours from `received` to `ready`, per pharmacy size.

    Elapsed time, not working time: a fill that crosses a closure absorbs the
    overnight gap, which is what the spec's targets are stated in.
    """
    pharmacies = index_pharmacies(world)
    durations: dict[str, list[float]] = {"large": [], "medium": [], "small": []}
    for events in group_events_by_rx(world).values():
        by_fill: dict[int, dict[str, datetime]] = {}
        for event in events:
            by_fill.setdefault(event.fill_number, {})[event.status] = event.occurred_at
        size = pharmacies[events[0].pharmacy_id].size
        for statuses in by_fill.values():
            if "received" in statuses and "ready" in statuses:
                elapsed = statuses["ready"] - statuses["received"]
                durations[size].append(elapsed.total_seconds() / 3600)
    return durations


@pytest.fixture(scope="module")
def full_scale_ready_hours():
    return _ready_hours_by_size(build_world(SimConfig()))


# Wall-clock median bands from specs/01-simulator.md.
@pytest.mark.slow
@pytest.mark.parametrize(
    ("size", "low", "high"),
    [("large", 1.0, 4.0), ("medium", 3.0, 10.0), ("small", 12.0, 30.0)],
)
def test_median_time_to_ready_is_in_band(full_scale_ready_hours, size, low, high):
    samples = full_scale_ready_hours[size]
    assert samples, f"no completed fills for {size} pharmacies"
    median = statistics.median(samples)
    assert low <= median <= high, (
        f"{size} median {median:.2f}h outside {low}-{high}h (n={len(samples)})"
    )


@pytest.mark.slow
def test_large_pharmacies_reach_ready_faster_than_small(full_scale_ready_hours):
    large = statistics.median(full_scale_ready_hours["large"])
    medium = statistics.median(full_scale_ready_hours["medium"])
    small = statistics.median(full_scale_ready_hours["small"])
    assert large < medium < small, (
        f"expected large < medium < small, got {large:.2f} / {medium:.2f} / {small:.2f}"
    )


@pytest.mark.slow
def test_full_scale_world_builds_within_the_time_budget():
    import time as timing

    started = timing.perf_counter()
    world = build_world(SimConfig())
    elapsed = timing.perf_counter() - started
    assert len(world.prescriptions) == 5_000
    # The spec's ceiling covers generation plus export; truth alone gets half.
    assert elapsed < 15, f"truth generation took {elapsed:.1f}s"


@pytest.mark.slow
def test_full_scale_truth_is_deterministic(tmp_path):
    first = _write_truth_to(tmp_path, "full1", SimConfig())
    second = _write_truth_to(tmp_path, "full2", SimConfig())
    for name in TRUTH_FILES:
        assert first[name] == second[name]
