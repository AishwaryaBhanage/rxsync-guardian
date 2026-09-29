"""PMS B: nested JSON carrying the full event history.

B is the well-behaved source: ISO dates, structured drug fields, every event
retained. It is the yardstick the other two are messy against.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from simulator.exporters.records import ExportRecord, build_records
from simulator.models import FaultPlan, Pharmacy, World
from simulator.writers import iso_date, iso_timestamp, write_json

FILENAME = "pms_b.json"


def drug_display(record: ExportRecord) -> str:
    """What B puts in its drug `name` field. Shared with `data/app_view.csv`.

    B normally writes the bare canonical name and keeps strength and form in
    sibling fields; the duplicate copy stuffs the whole messy string in here, so
    even the tidy source contradicts itself across the pair.
    """
    return record.drug.messy_desc if record.is_duplicate_copy else record.drug.name


def record_payload(record: ExportRecord) -> dict[str, Any]:
    patient_name = record.patient.name.upper() if record.is_duplicate_copy else record.patient.name
    drug_name = drug_display(record)
    return {
        "pharmacy": {
            "id": record.pharmacy.pharmacy_id,
            "name": record.pharmacy.name,
            "pms": record.pharmacy.pms_type,
        },
        "patient": {
            "id": record.patient.patient_id,
            "name": patient_name,
            "dob": iso_date(record.patient.dob),
            "phone": record.patient.phone,
        },
        "rx": {
            "number": record.rx.rx_number,
            # Structured rather than one printed string: B knows the fields.
            "drug": {
                "name": drug_name,
                "strength": record.drug.strength,
                "form": record.drug.form,
            },
            "days_supply": record.rx.days_supply,
            "refills_authorized": record.rx.refills_authorized,
            "refills_left": record.refills_left,
            "prescriber": record.rx.prescriber,
            "written_on": iso_date(record.rx.written_on),
            "refill_on_schedule": record.rx.refill_on_schedule,
        },
        "events": [
            {
                "seq": index,
                "fill": event.fill_number,
                "status": event.status,
                "at": iso_timestamp(event.occurred_at),
            }
            for index, event in enumerate(record.events, start=1)
        ],
    }


def write(
    path: Path,
    world: World,
    pharmacies: tuple[Pharmacy, ...],
    plan: FaultPlan | None = None,
) -> None:
    payload = [record_payload(record) for record in build_records(world, pharmacies, plan)]
    write_json(path, payload)
