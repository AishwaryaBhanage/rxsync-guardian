"""The shape of the simulated world.

All records are frozen: the generator builds them once and the exporters only
read, so nothing downstream can quietly mutate the truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from simulator.config import SimConfig
from simulator.schedule import OpeningHours

# Canonical order a single fill moves through. Terminal states are mutually
# exclusive: a fill ends either picked_up or delivered, or not at all.
STATUS_SEQUENCE = ("received", "in_process", "ready")
TERMINAL_STATUSES = ("picked_up", "delivered")
ALL_STATUSES = STATUS_SEQUENCE + TERMINAL_STATUSES


@dataclass(frozen=True)
class Pharmacy:
    pharmacy_id: str
    name: str
    size: str  # small | medium | large
    area: str  # urban | rural
    pms_type: str  # A | B | C
    hours: OpeningHours

    @property
    def closed_sunday(self) -> bool:
        return not self.hours.windows[6]


@dataclass(frozen=True)
class Patient:
    patient_id: str
    name: str
    dob: date
    phone: str
    home_pharmacy_id: str


@dataclass(frozen=True)
class Drug:
    drug_id: str
    name: str  # canonical, lowercase: "atorvastatin"
    strength: str  # "20 mg"
    form: str  # "tablet"
    messy_desc: str  # how a PMS might actually print it

    @property
    def canonical_desc(self) -> str:
        return f"{self.name} {self.strength} {self.form}"


@dataclass(frozen=True)
class Prescription:
    rx_number: str
    patient_id: str
    pharmacy_id: str
    drug_id: str
    days_supply: int  # 30 or 90
    refills_authorized: int  # 0-5
    prescriber: str
    written_on: date
    refill_on_schedule: bool


@dataclass(frozen=True)
class FillEvent:
    event_id: str
    rx_number: str
    pharmacy_id: str
    fill_number: int  # 0 is the original fill, 1+ are refills
    status: str
    occurred_at: datetime


@dataclass(frozen=True)
class World:
    config: SimConfig
    pharmacies: tuple[Pharmacy, ...]
    patients: tuple[Patient, ...]
    drugs: tuple[Drug, ...]
    prescriptions: tuple[Prescription, ...]
    fill_events: tuple[FillEvent, ...]

    # Deliberately a plain data holder: lookups live in world.index_* helpers so
    # callers build an index once instead of rescanning per prescription, which
    # would turn the exporters into an O(n^2) walk over ~15k events.
