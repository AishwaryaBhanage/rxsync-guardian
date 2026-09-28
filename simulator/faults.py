"""Plant the faults the detectors are supposed to find, and write the answer key.

Two kinds of fault, and the distinction matters for where they are applied:

* **`phantom_schedule` is a truth-layer fault.** "Refill on schedule is on but no
  refill ever happens" describes reality, not a recording error, so it is planted
  by stripping the refill events from the truth itself. It is therefore visible in
  `data/truth/` and in *every* export — `desync` should find it inside a single
  source.
* **`dropped`, `stale_status` and `duplicate` are export-layer faults.** The truth
  is intact; a particular PMS wrote it down wrongly. They are recorded in the plan
  here and applied while rendering an export.

Faults are **disjoint**: no prescription carries two. That keeps the answer key
unambiguous and makes the rate assertions exact rather than probabilistic.
"""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

from simulator.models import Fault, FaultPlan, World
from simulator.world import group_events_by_rx

ANSWER_KEY_FILENAME = "faults.jsonl"

FAULT_TYPES = ("duplicate", "dropped", "stale_status", "phantom_schedule")

# Which export file each PMS type writes to, for the answer key's details.
_EXPORT_FILE = {"A": "pms_a.csv", "B": "pms_b.json", "C": "pms_c.txt"}


def plant(world: World) -> tuple[World, FaultPlan]:
    """Return the world with truth-layer faults applied, plus the full plan.

    Selection order is phantom_schedule, then stale_status, then dropped, then
    duplicate. phantom must come first because it rewrites the truth the later
    selections read; stale_status comes next because it is the only export-layer
    fault with an eligibility constraint (it needs an earlier status to show), so
    picking it from the full pool guarantees its exact count.
    """
    phantom = _select_phantom(world)
    world = _strip_phantom_refills(world, phantom)

    taken = {fault.rx_number for fault in phantom}
    stale = _select_stale_status(world, taken)
    taken |= {fault.rx_number for fault in stale}
    dropped = _select_dropped(world, taken)
    taken |= {fault.rx_number for fault in dropped}
    duplicate = _select_duplicate(world, taken)

    return world, FaultPlan(faults=_numbered(phantom + stale + dropped + duplicate))


def write_answer_key(plan: FaultPlan, out_dir: Path) -> Path:
    """Write `faults.jsonl` — one JSON object per planted fault."""
    from simulator.writers import write_jsonl

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ANSWER_KEY_FILENAME
    write_jsonl(path, [fault.as_record() for fault in plan.faults])
    return path


# --- selection ------------------------------------------------------------


def _rng(world: World, stage: str) -> random.Random:
    return random.Random(f"{world.config.seed}:faults:{stage}")


def _pharmacy_types(world: World) -> dict[str, str]:
    return {p.pharmacy_id: p.pms_type for p in world.pharmacies}


def _select_phantom(world: World) -> tuple[Fault, ...]:
    """Prescriptions whose scheduled refills we are about to make vanish.

    Only prescriptions that *did* generate a refill are eligible: stripping one
    that never had refills anyway would put an undetectable row in the answer key.
    """
    events = group_events_by_rx(world)
    pms = _pharmacy_types(world)
    eligible = [
        rx
        for rx in world.prescriptions
        if rx.refill_on_schedule
        and rx.refills_authorized > 0
        and any(event.fill_number > 0 for event in events.get(rx.rx_number, ()))
    ]
    wanted = world.config.fault_count(world.config.rate_phantom_schedule)
    chosen = _sample(eligible, wanted, _rng(world, "phantom"))
    return tuple(
        Fault(
            fault_id="",  # assigned by _numbered
            type="phantom_schedule",
            rx_number=rx.rx_number,
            pharmacy_id=rx.pharmacy_id,
            details={
                "refills_authorized": rx.refills_authorized,
                "days_supply": rx.days_supply,
                "refill_events_removed": sum(
                    1 for e in events.get(rx.rx_number, ()) if e.fill_number > 0
                ),
                "export_file": _EXPORT_FILE[pms[rx.pharmacy_id]],
            },
        )
        for rx in chosen
    )


def _select_stale_status(world: World, taken: set[str]) -> tuple[Fault, ...]:
    """Show an older status than the truth. Needs at least two events."""
    events = group_events_by_rx(world)
    pms = _pharmacy_types(world)
    eligible = [
        rx
        for rx in world.prescriptions
        if rx.rx_number not in taken and len(events.get(rx.rx_number, ())) >= 2
    ]
    wanted = world.config.fault_count(world.config.rate_stale_status)
    rng = _rng(world, "stale")
    chosen = _sample(eligible, wanted, rng)

    faults = []
    for rx in chosen:
        chain = sorted(events[rx.rx_number], key=lambda e: e.occurred_at)
        # How many events the export admits to; at least one, never all of them.
        kept = rng.randint(1, len(chain) - 1)
        shown, actual = chain[kept - 1], chain[-1]
        faults.append(
            Fault(
                fault_id="",
                type="stale_status",
                rx_number=rx.rx_number,
                pharmacy_id=rx.pharmacy_id,
                details={
                    "events_kept": kept,
                    "events_hidden": len(chain) - kept,
                    "shown_status": shown.status,
                    "shown_at": shown.occurred_at.isoformat(timespec="seconds"),
                    "actual_status": actual.status,
                    "actual_at": actual.occurred_at.isoformat(timespec="seconds"),
                    "export_file": _EXPORT_FILE[pms[rx.pharmacy_id]],
                },
            )
        )
    return tuple(faults)


def _select_dropped(world: World, taken: set[str]) -> tuple[Fault, ...]:
    events = group_events_by_rx(world)
    pms = _pharmacy_types(world)
    eligible = [rx for rx in world.prescriptions if rx.rx_number not in taken]
    wanted = world.config.fault_count(world.config.rate_dropped)
    chosen = _sample(eligible, wanted, _rng(world, "dropped"))
    return tuple(
        Fault(
            fault_id="",
            type="dropped",
            rx_number=rx.rx_number,
            pharmacy_id=rx.pharmacy_id,
            details={
                "events_in_truth": len(events.get(rx.rx_number, ())),
                "export_file": _EXPORT_FILE[pms[rx.pharmacy_id]],
            },
        )
        for rx in chosen
    )


def _select_duplicate(world: World, taken: set[str]) -> tuple[Fault, ...]:
    pms = _pharmacy_types(world)
    eligible = [rx for rx in world.prescriptions if rx.rx_number not in taken]
    wanted = world.config.fault_count(world.config.rate_duplicate)
    chosen = _sample(eligible, wanted, _rng(world, "duplicate"))
    return tuple(
        Fault(
            fault_id="",
            type="duplicate",
            rx_number=rx.rx_number,
            pharmacy_id=rx.pharmacy_id,
            details={
                # What the second copy renders differently. The exact rendering is
                # each format's business; see simulator/exporters/.
                "varies": ["drug_text", "name_case", "date_format"],
                "export_file": _EXPORT_FILE[pms[rx.pharmacy_id]],
            },
        )
        for rx in chosen
    )


def _sample[T](population: list[T], wanted: int, rng: random.Random) -> list[T]:
    """Deterministic sample that keeps population order.

    Shuffles a copy so the pick is seeded, then re-sorts by original index so the
    resulting faults are emitted in a stable order regardless of draw order.
    """
    if wanted >= len(population):
        return list(population)
    indexes = list(range(len(population)))
    rng.shuffle(indexes)
    return [population[index] for index in sorted(indexes[:wanted])]


def _numbered(faults: tuple[Fault, ...]) -> tuple[Fault, ...]:
    """Assign stable ids: sorted by type then rx_number, so a re-run matches."""
    ordered = sorted(faults, key=lambda f: (f.type, f.rx_number))
    return tuple(
        replace(fault, fault_id=f"f{index:05d}") for index, fault in enumerate(ordered, start=1)
    )


# --- truth-layer application ---------------------------------------------


def _strip_phantom_refills(world: World, phantom: tuple[Fault, ...]) -> World:
    """Remove every refill event from the phantom prescriptions.

    Event ids are left as they were, so the surviving ids have gaps. That is
    harmless — they only need to be unique and reproducible — and it keeps this a
    pure filter over the built truth rather than a second generation pass.
    """
    if not phantom:
        return world
    targets = {fault.rx_number for fault in phantom}
    kept = tuple(
        event
        for event in world.fill_events
        if not (event.rx_number in targets and event.fill_number > 0)
    )
    return replace(world, fill_events=kept)
