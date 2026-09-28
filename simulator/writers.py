"""Deterministic file writing.

Byte-identical output for a given (seed, as_of) pair is an acceptance criterion,
so the formatting choices here are deliberate rather than incidental.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from simulator.models import World
from simulator.world import index_drugs

TRUTH_FILES = ("pharmacies", "patients", "prescriptions", "fill_events")


def iso_timestamp(moment: datetime) -> str:
    """Second-resolution ISO 8601. One helper so no float ever reaches a file."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def iso_date(day: date) -> str:
    return day.isoformat()


def flag(value: bool) -> str:
    """Booleans as stable lowercase text rather than Python's True/False."""
    return "true" if value else "false"


def write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    # lineterminator defaults to \r\n, which would make output platform-dependent.
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    # sort_keys and a fixed indent keep the bytes stable across runs; newline=""
    # stops the platform rewriting the \n that json.dump emits.
    with path.open("w", newline="", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def write_jsonl(path: Path, records: list[Any]) -> None:
    """One compact JSON object per line, keys sorted for stable bytes."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            )
            handle.write("\n")


def write_lines(path: Path, lines: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def write_truth(world: World, out_dir: Path) -> dict[str, Path]:
    """Write the four truth CSVs under `out_dir/truth/`."""
    truth_dir = out_dir / "truth"
    truth_dir.mkdir(parents=True, exist_ok=True)
    drugs = index_drugs(world)

    paths = {name: truth_dir / f"{name}.csv" for name in TRUTH_FILES}

    write_csv(
        paths["pharmacies"],
        ("pharmacy_id", "name", "size", "area", "pms_type", "closed_sunday", "opening_hours"),
        [
            (
                pharmacy.pharmacy_id,
                pharmacy.name,
                pharmacy.size,
                pharmacy.area,
                pharmacy.pms_type,
                flag(pharmacy.closed_sunday),
                pharmacy.hours.encode(),
            )
            for pharmacy in world.pharmacies
        ],
    )

    write_csv(
        paths["patients"],
        ("patient_id", "name", "dob", "phone", "home_pharmacy_id"),
        [
            (
                patient.patient_id,
                patient.name,
                iso_date(patient.dob),
                patient.phone,
                patient.home_pharmacy_id,
            )
            for patient in world.patients
        ],
    )

    write_csv(
        paths["prescriptions"],
        (
            "rx_number",
            "patient_id",
            "pharmacy_id",
            "drug_id",
            # Drug fields are denormalized here because the spec names exactly four
            # truth files, so there is no drugs.csv to join against.
            "drug_name",
            "drug_strength",
            "drug_form",
            "days_supply",
            "refills_authorized",
            "prescriber",
            "written_on",
            "refill_on_schedule",
        ),
        [
            (
                rx.rx_number,
                rx.patient_id,
                rx.pharmacy_id,
                rx.drug_id,
                drugs[rx.drug_id].name,
                drugs[rx.drug_id].strength,
                drugs[rx.drug_id].form,
                rx.days_supply,
                rx.refills_authorized,
                rx.prescriber,
                iso_date(rx.written_on),
                flag(rx.refill_on_schedule),
            )
            for rx in world.prescriptions
        ],
    )

    write_csv(
        paths["fill_events"],
        ("event_id", "rx_number", "pharmacy_id", "fill_number", "status", "occurred_at"),
        [
            (
                event.event_id,
                event.rx_number,
                event.pharmacy_id,
                event.fill_number,
                event.status,
                iso_timestamp(event.occurred_at),
            )
            for event in world.fill_events
        ],
    )

    return paths
