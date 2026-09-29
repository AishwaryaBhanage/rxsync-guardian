"""`data/app_view.csv` — the fault-bearing view a patient app would show.

This is the file the investigator's tools read. It is the three PMS exports
flattened into one table, built from the *same* faulted record lists the exports
are built from (`build_records(world, pharmacies, plan)`), so the app view can
never drift from them: a dropped prescription is missing here too, a duplicate
appears as two rows with their own spelling, and a stale record shows its old
status.

What the app knows vs. what the feed carries:

* `patient_id` is resolved for every row — an app knows which patient is logged
  in even when the PMS feed identifies them only by name (PMS A carries no id).
* `refill_on_schedule` is left **empty** for A and C, which never send the field.
  Empty means unknown, not false.
* `drug_display` is exactly the string that source wrote, which is the messiness
  the agent has to reason about.
"""

from __future__ import annotations

from pathlib import Path

from simulator.exporters import EXPORTERS
from simulator.exporters.records import build_records
from simulator.models import FaultPlan, World
from simulator.writers import iso_timestamp, write_csv

FILENAME = "app_view.csv"

COLUMNS = (
    "record_id",
    "patient_id",
    "pharmacy_id",
    "rx_number",
    "drug_display",
    "status",
    "status_ts",
    "refill_on_schedule",
    "source_pms",
)

# Only PMS B sends this field; A and C leave it unknown.
_CARRIES_REFILL_FLAG = {"B"}


def rows_for(world: World, plan: FaultPlan | None = None) -> list[tuple[object, ...]]:
    """Every app-visible row, in PMS order then export order within each PMS."""
    rows: list[tuple[object, ...]] = []
    for pms_type in sorted(EXPORTERS):
        module = EXPORTERS[pms_type]
        pharmacies = tuple(p for p in world.pharmacies if p.pms_type == pms_type)
        records = build_records(world, pharmacies, plan)
        for index, record in enumerate(records, start=1):
            current = record.current
            rows.append(
                (
                    f"{pms_type}{index:05d}",
                    record.patient.patient_id,
                    record.pharmacy.pharmacy_id,
                    record.rx.rx_number,
                    module.drug_display(record),
                    # Canonical lowercase status; a staled record shows the older
                    # one, because its event list was truncated upstream.
                    current.status if current else "",
                    iso_timestamp(current.occurred_at) if current else "",
                    _refill_flag(pms_type, record.rx.refill_on_schedule),
                    pms_type,
                )
            )
    return rows


def write_app_view(world: World, out_dir: Path, plan: FaultPlan | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / FILENAME
    write_csv(path, COLUMNS, rows_for(world, plan))
    return path


def _refill_flag(pms_type: str, value: bool) -> str:
    """ "true"/"false" where the source sends the field, empty where it does not."""
    if pms_type not in _CARRIES_REFILL_FLAG:
        return ""
    return "true" if value else "false"
