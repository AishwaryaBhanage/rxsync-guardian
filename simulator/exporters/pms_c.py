"""PMS C: pipe-delimited, HL7-flavoured segments.

One prescription becomes a PID segment, an RXO segment, then one STS per event.
Dates lose their separators and names invert to LAST^FIRST, so C disagrees with
both other formats on how the same patient is written down.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from simulator.exporters.records import ExportRecord, build_records
from simulator.models import FaultPlan, Pharmacy, World
from simulator.writers import write_lines

FILENAME = "pms_c.txt"
SEGMENT_TAGS = ("PID", "RXO", "STS")


def hl7_date(day: date) -> str:
    return f"{day:%Y%m%d}"


def hl7_timestamp(moment: datetime) -> str:
    return f"{moment:%Y%m%d%H%M%S}"


def hl7_name(name: str) -> str:
    """ "Jane Doe" -> "DOE^JANE". The last whitespace-separated token is the
    surname; anything before it stays in the given-name field."""
    parts = name.rsplit(" ", 1)
    if len(parts) == 1:
        return parts[0].upper()
    given, family = parts
    return f"{family.upper()}^{given.upper()}"


def digits_only(phone: str) -> str:
    return "".join(character for character in phone if character.isdigit())


def segments_for(record: ExportRecord) -> list[str]:
    """PID, RXO, then one STS per event (none for a prescription with no fills)."""
    if record.is_duplicate_copy:
        # C's copy keeps the name in reading order, dashes the date, and uses the
        # canonical drug name — C's conventions, inverted.
        name = record.patient.name.upper()
        dob = record.patient.dob.isoformat()
        drug = record.drug.canonical_desc
    else:
        name = hl7_name(record.patient.name)
        dob = hl7_date(record.patient.dob)
        drug = record.drug.messy_desc
    segments = [
        "|".join(
            (
                "PID",
                record.patient.patient_id,
                name,
                dob,
                digits_only(record.patient.phone),
            )
        ),
        "|".join(
            (
                "RXO",
                record.rx.rx_number,
                record.pharmacy.pharmacy_id,
                drug,
                str(record.rx.days_supply),
                str(record.refills_left),
                record.rx.prescriber.upper(),
            )
        ),
    ]
    segments.extend(
        "|".join(
            (
                "STS",
                record.rx.rx_number,
                str(event.fill_number),
                event.status.upper(),
                hl7_timestamp(event.occurred_at),
            )
        )
        for event in record.events
    )
    return segments


def write(
    path: Path,
    world: World,
    pharmacies: tuple[Pharmacy, ...],
    plan: FaultPlan | None = None,
) -> None:
    lines: list[str] = []
    for record in build_records(world, pharmacies, plan):
        lines.extend(segments_for(record))
    write_lines(path, lines)
