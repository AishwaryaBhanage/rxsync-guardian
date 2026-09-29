"""PMS A: a flat CSV with one row per prescription, current state only.

A carries no event history, which is precisely why it is the easiest place to
hide a stale status later: there is no earlier row to contradict STATUS.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from simulator.exporters.records import ExportRecord, build_records
from simulator.models import FaultPlan, Pharmacy, World
from simulator.writers import write_csv

FILENAME = "pms_a.csv"
HEADER = (
    "RX_NO",
    "PT_NAME",
    "PT_DOB",
    "DRUG_DESC",
    "DAYS_SUPPLY",
    "REFILLS_LEFT",
    "STATUS",
    "STATUS_TS",
    "PHARMACY_ID",
)


def us_date(day: date) -> str:
    """MM/DD/YYYY — A's house style, and deliberately not B's or C's."""
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


def us_timestamp(moment: datetime) -> str:
    return f"{us_date(moment.date())} {moment:%H:%M}"


def drug_display(record: ExportRecord) -> str:
    """The drug string exactly as A prints it. Shared with `data/app_view.csv`."""
    return record.drug.canonical_desc if record.is_duplicate_copy else record.drug.messy_desc


def row_for(record: ExportRecord) -> tuple[object, ...]:
    current = record.current
    if record.is_duplicate_copy:
        # The second copy of a duplicate flips A's conventions: title case instead
        # of upper, ISO instead of US dates, the canonical drug name instead of
        # the printed one. Same prescription, three small textual differences.
        name = record.patient.name.title()
        dob = record.patient.dob.isoformat()
    else:
        name = record.patient.name.upper()  # A shouts; B and C do not
        dob = us_date(record.patient.dob)
    drug = drug_display(record)
    return (
        record.rx.rx_number,
        name,
        dob,
        drug,
        record.rx.days_supply,
        record.refills_left,
        current.status.upper() if current else "",
        us_timestamp(current.occurred_at) if current else "",
        record.pharmacy.pharmacy_id,
    )


def write(
    path: Path,
    world: World,
    pharmacies: tuple[Pharmacy, ...],
    plan: FaultPlan | None = None,
) -> None:
    rows = [row_for(record) for record in build_records(world, pharmacies, plan)]
    write_csv(path, HEADER, rows)
