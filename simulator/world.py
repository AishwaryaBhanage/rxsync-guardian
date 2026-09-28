"""Build the truth: pharmacies, patients, prescriptions and fill events.

This is the clean world, before any PMS export distorts it. Determinism is a
design constraint here, not a nice-to-have, so every random draw comes from a
stage-scoped RNG (see `_rng`).
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from faker import Faker

from simulator.config import SimConfig
from simulator.drugs import DRUGS
from simulator.models import (
    Drug,
    FillEvent,
    Patient,
    Pharmacy,
    Prescription,
    World,
)
from simulator.schedule import (
    OpeningHours,
    advance_business_hours,
    next_open_moment,
    processing_hours,
)

# Weekday opening windows by pharmacy size; Saturday and Sunday are set per
# pharmacy in _build_pharmacies.
_WEEKDAY_WINDOW = {"large": (8, 20), "medium": (9, 18), "small": (9, 17)}
_SATURDAY_WINDOW = (10, 14)
_SUNDAY_WINDOW = (11, 15)

# Proportions, expressed as a pattern that is tiled and then shuffled.
_SIZE_PATTERN = ("small",) * 5 + ("medium",) * 3 + ("large",) * 2
_AREA_PATTERN = ("urban",) * 3 + ("rural",) * 2
_PMS_PATTERN = ("A", "B", "C")
_CLOSED_SUNDAY_PATTERN = (True, True, False, False, False)

# Fraction of total processing time at which a fill flips to in_process.
_IN_PROCESS_FRACTION = 0.25


def build_world(config: SimConfig) -> World:
    """Generate the whole truth for one (seed, as_of) pair."""
    pharmacies = _build_pharmacies(config)
    patients = _build_patients(config, pharmacies)
    prescriptions = _build_prescriptions(config, patients)
    fill_events = _build_fill_events(config, pharmacies, prescriptions)
    return World(
        config=config,
        pharmacies=pharmacies,
        patients=patients,
        drugs=DRUGS,
        prescriptions=prescriptions,
        fill_events=fill_events,
    )


# --- index helpers -------------------------------------------------------
# Callers build an index once rather than rescanning the world per record.


def index_pharmacies(world: World) -> dict[str, Pharmacy]:
    return {p.pharmacy_id: p for p in world.pharmacies}


def index_patients(world: World) -> dict[str, Patient]:
    return {p.patient_id: p for p in world.patients}


def index_drugs(world: World) -> dict[str, Drug]:
    return {d.drug_id: d for d in world.drugs}


def group_events_by_rx(world: World) -> dict[str, list[FillEvent]]:
    grouped: dict[str, list[FillEvent]] = {}
    for event in world.fill_events:
        grouped.setdefault(event.rx_number, []).append(event)
    return grouped


def ready_hours_by_size(world: World) -> dict[str, list[float]]:
    """Wall-clock hours from `received` to `ready`, bucketed by pharmacy size.

    Elapsed time, not working time: a fill that crosses a closure absorbs the
    overnight gap. This is the measure the spec's speed targets are stated in.
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


# --- determinism helpers -------------------------------------------------


def _rng(config: SimConfig, stage: str) -> random.Random:
    """A generator scoped to one stage.

    Deriving a stream per stage means adding a stage later cannot reshuffle the
    draws of earlier ones, so an answer key generated today stays valid.
    """
    return random.Random(f"{config.seed}:{stage}")


def _faker(config: SimConfig, stage: str) -> Faker:
    """Locale-pinned, instance-seeded Faker.

    `Faker.seed()` is class-level global state; `seed_instance` keeps the stream
    local to this generator.
    """
    fake = Faker("en_US")
    fake.seed_instance(f"{config.seed}:{stage}")
    return fake


def _tiled_shuffled[T](pattern: tuple[T, ...], n: int, rng: random.Random) -> list[T]:
    """Tile `pattern` to length `n`, then shuffle it.

    Tiling keeps the proportions exact and guarantees every category appears
    (for n >= len(pattern)), which matters for the small test world. Shuffling
    each attribute with its own RNG stops attributes from correlating by index —
    plain `i % 10` for size and `i % 5` for area would, for instance, make every
    large pharmacy rural.
    """
    values = [pattern[i % len(pattern)] for i in range(n)]
    rng.shuffle(values)
    return values


# --- stages --------------------------------------------------------------


def _build_pharmacies(config: SimConfig) -> tuple[Pharmacy, ...]:
    fake = _faker(config, "pharmacies")
    sizes = _tiled_shuffled(_SIZE_PATTERN, config.n_pharmacies, _rng(config, "size"))
    areas = _tiled_shuffled(_AREA_PATTERN, config.n_pharmacies, _rng(config, "area"))
    pms = _tiled_shuffled(_PMS_PATTERN, config.n_pharmacies, _rng(config, "pms"))
    closed_sundays = _tiled_shuffled(
        _CLOSED_SUNDAY_PATTERN, config.n_pharmacies, _rng(config, "sunday")
    )

    pharmacies = []
    for index in range(config.n_pharmacies):
        size = sizes[index]
        weekday = _WEEKDAY_WINDOW[size]
        windows = (
            weekday,
            weekday,
            weekday,
            weekday,
            weekday,
            _SATURDAY_WINDOW,
            None if closed_sundays[index] else _SUNDAY_WINDOW,
        )
        pharmacies.append(
            Pharmacy(
                pharmacy_id=f"PH{index + 1:03d}",
                name=f"{fake.city()} Pharmacy",
                size=size,
                area=areas[index],
                pms_type=pms[index],
                hours=OpeningHours(windows=windows),
            )
        )
    return tuple(pharmacies)


def _build_patients(config: SimConfig, pharmacies: tuple[Pharmacy, ...]) -> tuple[Patient, ...]:
    fake = _faker(config, "patients")
    rng = _rng(config, "patients")
    return tuple(
        Patient(
            patient_id=f"PT{index + 1:05d}",
            name=fake.name(),
            dob=fake.date_of_birth(minimum_age=18, maximum_age=95),
            phone=fake.numerify("###-###-####"),
            home_pharmacy_id=rng.choice(pharmacies).pharmacy_id,
        )
        for index in range(config.n_patients)
    )


def _build_prescriptions(
    config: SimConfig,
    patients: tuple[Patient, ...],
) -> tuple[Prescription, ...]:
    fake = _faker(config, "prescriptions")
    rng = _rng(config, "prescriptions")
    home = {p.patient_id: p.home_pharmacy_id for p in patients}

    prescriptions = []
    for index in range(config.n_prescriptions):
        patient = rng.choice(patients)
        drug = rng.choice(DRUGS)
        written_on = config.window_start + timedelta(days=rng.randint(0, config.window_days))
        prescriptions.append(
            Prescription(
                rx_number=f"RX{1_000_000 + index}",
                patient_id=patient.patient_id,
                # Prescriptions are always filled at the patient's home pharmacy,
                # which is what makes "one pharmacy, one PMS format" hold.
                pharmacy_id=home[patient.patient_id],
                drug_id=drug.drug_id,
                days_supply=rng.choice((30, 90)),
                refills_authorized=rng.randint(0, 5),
                prescriber=f"Dr. {fake.last_name()}",
                written_on=written_on,
                refill_on_schedule=rng.random() < config.refill_on_schedule_share,
            )
        )
    return tuple(prescriptions)


def _build_fill_events(
    config: SimConfig,
    pharmacies: tuple[Pharmacy, ...],
    prescriptions: tuple[Prescription, ...],
) -> tuple[FillEvent, ...]:
    rng = _rng(config, "fill-events")
    by_id = {p.pharmacy_id: p for p in pharmacies}
    # Nothing is observed after the as-of date, so late fills are simply not
    # recorded yet. That is what leaves some prescriptions mid-flight.
    horizon = datetime.combine(config.as_of, time.max)

    events: list[FillEvent] = []
    counter = 0
    for rx in prescriptions:
        pharmacy = by_id[rx.pharmacy_id]
        cursor = _received_at(config, rx, pharmacy, rng)
        # fill 0 is the original; 1..refills_authorized are the refills, so a
        # prescription with 0 refills never enters a second iteration.
        for fill_number in range(rx.refills_authorized + 1):
            stages, collected_at = _one_fill(config, pharmacy, cursor, rng)
            truncated = False
            for status, occurred_at in stages:
                if occurred_at > horizon:
                    truncated = True
                    break
                counter += 1
                events.append(
                    FillEvent(
                        event_id=f"EV{counter:07d}",
                        rx_number=rx.rx_number,
                        pharmacy_id=rx.pharmacy_id,
                        fill_number=fill_number,
                        status=status,
                        occurred_at=occurred_at,
                    )
                )
            if truncated or collected_at is None:
                # Never collected (or the window ran out) means no refill follows.
                break
            cursor = _next_fill_start(rx, pharmacy, collected_at, rng)
    return tuple(events)


def _received_at(
    config: SimConfig, rx: Prescription, pharmacy: Pharmacy, rng: random.Random
) -> datetime:
    """When the first fill is handed in: some point during the open day.

    The arrival hour, not just the processing time, decides elapsed time: a fill
    that cannot finish before closing resumes the next morning and absorbs the
    overnight gap. `arrival_morning_bias` therefore tunes how often that happens.
    """
    opens_at = next_open_moment(datetime.combine(rx.written_on, time.min), pharmacy.hours)
    window = pharmacy.hours.window_for(opens_at.date())
    if window is None:  # unreachable: next_open_moment only lands on open days
        raise AssertionError(f"{pharmacy.pharmacy_id} closed on {opens_at.date()}")
    open_hours = (window[1] - window[0]).total_seconds() / 3600
    offset = open_hours * rng.random() ** config.arrival_morning_bias
    return advance_business_hours(opens_at, offset, pharmacy.hours)


def _one_fill(
    config: SimConfig,
    pharmacy: Pharmacy,
    received_at: datetime,
    rng: random.Random,
) -> tuple[list[tuple[str, datetime]], datetime | None]:
    """The event chain for a single fill, plus when it was collected (if ever)."""
    hours = processing_hours(pharmacy.size, pharmacy.area, rng)
    stages = [
        ("received", received_at),
        (
            "in_process",
            advance_business_hours(received_at, hours * _IN_PROCESS_FRACTION, pharmacy.hours),
        ),
        ("ready", advance_business_hours(received_at, hours, pharmacy.hours)),
    ]
    if rng.random() < config.never_collected_share:
        return stages, None

    ready_at = stages[-1][1]
    # Mode well below the maximum gives a long tail: most collect within a day,
    # some take the better part of a week.
    delay_hours = rng.triangular(1.0, 72.0, 8.0)
    collected_at = next_open_moment(
        ready_at + timedelta(minutes=round(delay_hours * 60)), pharmacy.hours
    )
    status = "delivered" if rng.random() < config.delivered_share else "picked_up"
    stages.append((status, collected_at))
    return stages, collected_at


def _next_fill_start(
    rx: Prescription,
    pharmacy: Pharmacy,
    collected_at: datetime,
    rng: random.Random,
) -> datetime:
    """When the next refill is requested: roughly a supply later, early or late."""
    drift_hours = round(rng.triangular(-72.0, 168.0, 0.0))
    gap = timedelta(days=rx.days_supply) + timedelta(hours=drift_hours)
    return next_open_moment(collected_at + gap, pharmacy.hours)
