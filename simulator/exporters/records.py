"""One assembled prescription, ready for any PMS format to render.

The three exporters need the same joins (patient, drug, that prescription's
events) and the same derived values. Doing it once here keeps the formats to
pure rendering, and keeps the walk over ~24k events linear rather than one
rescan per prescription.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from simulator.models import (
    Drug,
    FaultPlan,
    FillEvent,
    Patient,
    Pharmacy,
    Prescription,
    World,
)
from simulator.world import group_events_by_rx, index_drugs, index_patients


@dataclass(frozen=True)
class ExportRecord:
    pharmacy: Pharmacy
    patient: Patient
    rx: Prescription
    drug: Drug
    events: tuple[FillEvent, ...]  # chronological; may be empty
    # True on the second copy of a duplicate fault. Each format renders the copy
    # with its conventions flipped, which is the "small differences" the spec asks
    # for; see the variant branches in pms_a / pms_b / pms_c.
    is_duplicate_copy: bool = False

    @property
    def current(self) -> FillEvent | None:
        """The latest event, or None for a prescription with no fill activity.

        Not every prescription has events: one written just before the as-of date
        can have its arrival snapped past the horizon, so it is on file with
        nothing recorded against it yet. Formats must render that case.
        """
        return self.events[-1] if self.events else None

    @property
    def last_fill_number(self) -> int:
        return self.events[-1].fill_number if self.events else 0

    @property
    def refills_left(self) -> int:
        """Authorized refills not yet started. Never negative."""
        return max(0, self.rx.refills_authorized - self.last_fill_number)


def apply_export_faults(records: list[ExportRecord], plan: FaultPlan | None) -> list[ExportRecord]:
    """Distort the record list according to the export-layer faults.

    `phantom_schedule` is absent here on purpose: it was already applied to the
    truth, so it needs no rendering-time change.
    """
    if plan is None:
        return records
    by_rx = plan.by_rx()

    out: list[ExportRecord] = []
    for record in records:
        fault = by_rx.get(record.rx.rx_number)
        if fault is None or fault.type == "phantom_schedule":
            out.append(record)
        elif fault.type == "dropped":
            continue  # the export simply never mentions it
        elif fault.type == "stale_status":
            kept = int(fault.details["events_kept"])  # decided at selection time
            out.append(replace(record, events=record.events[:kept]))
        elif fault.type == "duplicate":
            out.append(record)
            out.append(replace(record, is_duplicate_copy=True))
        else:  # pragma: no cover - guards a typo in a new fault type
            raise ValueError(f"unknown export fault type {fault.type!r}")
    return out


def build_records(
    world: World, pharmacies: tuple[Pharmacy, ...], plan: FaultPlan | None = None
) -> list[ExportRecord]:
    """Records for the given pharmacies, in world prescription order.

    With a `plan`, export-layer faults are applied, so the result no longer maps
    one-to-one onto prescriptions.
    """
    wanted = {pharmacy.pharmacy_id: pharmacy for pharmacy in pharmacies}
    patients = index_patients(world)
    drugs = index_drugs(world)
    events = group_events_by_rx(world)

    records = []
    for rx in world.prescriptions:
        pharmacy = wanted.get(rx.pharmacy_id)
        if pharmacy is None:
            continue
        rx_events = tuple(sorted(events.get(rx.rx_number, []), key=lambda e: e.occurred_at))
        records.append(
            ExportRecord(
                pharmacy=pharmacy,
                patient=patients[rx.patient_id],
                rx=rx,
                drug=drugs[rx.drug_id],
                events=rx_events,
            )
        )
    return apply_export_faults(records, plan)
