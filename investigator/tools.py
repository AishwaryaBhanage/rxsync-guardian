"""The four read-only tools the agent investigates with.

Design rules, all enforced by tests:

* **No patient names, ever.** Only `data/truth/patients.csv` holds names and this
  module never opens it. Every tool identifies people by `patient_id`.
* **Unknown ids return `{"error": ...}`**, never an exception — a tool error is
  something the agent should read and recover from, not a crash.
* **Compact payloads.** Only columns an investigation actually uses; no ids the
  caller already supplied, no internal surrogate keys.
* **Loaded once.** The CSVs are parsed on first use and cached per directory.

`get_patient_view` is what the *app showed the patient*, so it carries the planted
faults: a duplicate appears twice, a dropped prescription is missing, a stale row
shows an old status. `get_pharmacy_records` is what the pharmacy's own records
*actually say*. Comparing the two is the investigation.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_DATA_DIR = Path("data")

# Truth files this module is allowed to read. patients.csv is deliberately absent.
_APP_VIEW = "app_view.csv"
_PRESCRIPTIONS = Path("truth") / "prescriptions.csv"
_FILL_EVENTS = Path("truth") / "fill_events.csv"
_PHARMACIES = Path("truth") / "pharmacies.csv"


@dataclass(frozen=True)
class DataSet:
    """The four tables, parsed once."""

    app_view: pd.DataFrame
    prescriptions: pd.DataFrame
    fill_events: pd.DataFrame
    pharmacies: pd.DataFrame
    # The observation horizon: the newest event anywhere in the dataset. Nothing
    # records the generator's --as-of, and inferring "now" per patient is wrong —
    # a patient whose last event is two months old would look current. Derived
    # from the data so it stays right whatever --as-of built it.
    as_of: date


_active_dir: Path = DEFAULT_DATA_DIR
_cache: dict[Path, DataSet] = {}


def set_data_dir(path: Path | str) -> None:
    """Point the tools at a different dataset (used by tests)."""
    global _active_dir
    _active_dir = Path(path)


def dataset() -> DataSet:
    """The parsed tables for the active directory, read from disk only once."""
    if _active_dir not in _cache:
        _cache[_active_dir] = _read(_active_dir)
    return _cache[_active_dir]


def clear_cache() -> None:
    _cache.clear()


def as_of() -> date:
    """The date this dataset is current to. The agent has no other clock."""
    return dataset().as_of


def _read(data_dir: Path) -> DataSet:
    # Everything as strings except the few numeric columns we actually compute on,
    # so ids like "PT00067" keep their leading zeros.
    events = pd.read_csv(data_dir / _FILL_EVENTS, dtype=str, keep_default_na=False)
    return DataSet(
        app_view=pd.read_csv(data_dir / _APP_VIEW, dtype=str, keep_default_na=False),
        prescriptions=pd.read_csv(data_dir / _PRESCRIPTIONS, dtype=str, keep_default_na=False),
        fill_events=events,
        pharmacies=pd.read_csv(data_dir / _PHARMACIES, dtype=str, keep_default_na=False),
        as_of=date.fromisoformat(events["occurred_at"].max()[:10]),
    )


# --- tools ----------------------------------------------------------------


def get_patient_view(patient_id: str) -> list[dict[str, Any]] | dict[str, str]:
    """What the patient app shows this patient — faults included."""
    data = dataset()
    if not _patient_exists(data, patient_id):
        return {"error": f"unknown patient_id {patient_id!r}"}

    rows = data.app_view[data.app_view["patient_id"] == patient_id]
    return [
        {
            "record_id": row["record_id"],
            "rx_number": row["rx_number"],
            "pharmacy_id": row["pharmacy_id"],
            "drug_display": row["drug_display"],
            "status": row["status"] or None,
            "status_ts": row["status_ts"] or None,
            # Empty means the source never sent the field, which is not the same
            # as false; keep that distinction visible to the agent.
            "refill_on_schedule": _tri_state(row["refill_on_schedule"]),
            "source_pms": row["source_pms"],
        }
        for _, row in rows.iterrows()
    ]


def get_pharmacy_records(patient_id: str) -> list[dict[str, Any]] | dict[str, str]:
    """What the pharmacies' own records say this patient was prescribed."""
    data = dataset()
    if not _patient_exists(data, patient_id):
        return {"error": f"unknown patient_id {patient_id!r}"}

    rows = data.prescriptions[data.prescriptions["patient_id"] == patient_id]
    return [
        {
            "rx_number": row["rx_number"],
            "pharmacy_id": row["pharmacy_id"],
            "drug": f"{row['drug_name']} {row['drug_strength']} {row['drug_form']}",
            "days_supply": int(row["days_supply"]),
            "refills_authorized": int(row["refills_authorized"]),
            "written_on": row["written_on"],
            "refill_on_schedule": row["refill_on_schedule"] == "true",
        }
        for _, row in rows.iterrows()
    ]


def get_rx_history(rx_number: str) -> dict[str, Any]:
    """The status timeline for one prescription, from the pharmacy's fill events."""
    data = dataset()
    known = data.prescriptions[data.prescriptions["rx_number"] == rx_number]
    if known.empty:
        return {"error": f"unknown rx_number {rx_number!r}"}

    rx = known.iloc[0]
    events = data.fill_events[data.fill_events["rx_number"] == rx_number]
    events = events.sort_values("occurred_at")
    timeline = [
        {
            "fill": int(row["fill_number"]),
            "status": row["status"],
            "at": row["occurred_at"],
        }
        for _, row in events.iterrows()
    ]

    last_fill = max((entry["fill"] for entry in timeline), default=0)
    due, overdue = _refill_due(timeline, int(rx["days_supply"]), data.as_of)
    return {
        "rx_number": rx_number,
        "pharmacy_id": rx["pharmacy_id"],
        # The clock and the arithmetic, done here so the model never has to.
        "as_of": data.as_of.isoformat(),
        "refill_on_schedule": rx["refill_on_schedule"] == "true",
        "refills_remaining": max(0, int(rx["refills_authorized"]) - last_fill),
        "days_supply": int(rx["days_supply"]),
        # None when nothing has been collected: a supply that was never picked up
        # has not started, so no refill can be due.
        "refill_due_date": due.isoformat() if due else None,
        # Negative means not due yet. None when there is no due date.
        "days_overdue": overdue,
        # Empty for a prescription on file with nothing dispensed yet.
        "timeline": timeline,
    }


def get_pharmacy_speed(pharmacy_id: str) -> dict[str, Any]:
    """How long this pharmacy typically takes from `received` to `ready`."""
    data = dataset()
    row = data.pharmacies[data.pharmacies["pharmacy_id"] == pharmacy_id]
    if row.empty:
        return {"error": f"unknown pharmacy_id {pharmacy_id!r}"}
    pharmacy = row.iloc[0]

    hours = _ready_hours(data, pharmacy_id)
    return {
        "pharmacy_id": pharmacy_id,
        "name": pharmacy["name"],
        "size": pharmacy["size"],
        "area": pharmacy["area"],
        "closed_sunday": pharmacy["closed_sunday"] == "true",
        # None when nothing has reached `ready` yet — say so rather than imply 0.
        "median_hours_to_ready": round(statistics.median(hours), 1) if hours else None,
        "fills_measured": len(hours),
    }


# --- helpers --------------------------------------------------------------


def _patient_exists(data: DataSet, patient_id: str) -> bool:
    """Known if the pharmacy records mention them.

    Deliberately not "appears in the app view": a patient whose only prescription
    was dropped is a real patient with an empty app view, and that gap is exactly
    what an investigation should notice.
    """
    return bool((data.prescriptions["patient_id"] == patient_id).any())


def _refill_due(
    timeline: list[dict[str, Any]], days_supply: int, today: date
) -> tuple[date | None, int | None]:
    """When the next refill was due, and how overdue it is.

    Supply starts when the patient actually takes the medicine away, so the clock
    runs from the last collection (picked_up or delivered) — not from `ready`,
    which may still be sitting on the shelf.
    """
    collections = [entry for entry in timeline if entry["status"] in ("picked_up", "delivered")]
    if not collections:
        return None, None
    collected = date.fromisoformat(collections[-1]["at"][:10])
    due = collected + timedelta(days=days_supply)
    return due, (today - due).days


def _tri_state(value: str) -> bool | None:
    if value == "":
        return None
    return value == "true"


def _ready_hours(data: DataSet, pharmacy_id: str) -> list[float]:
    """Wall-clock hours received -> ready, one entry per completed fill."""
    events = data.fill_events[data.fill_events["pharmacy_id"] == pharmacy_id]
    if events.empty:
        return []

    wanted = events[events["status"].isin(["received", "ready"])].copy()
    wanted["occurred_at"] = pd.to_datetime(wanted["occurred_at"])
    pivot = wanted.pivot_table(
        index=["rx_number", "fill_number"],
        columns="status",
        values="occurred_at",
        aggfunc="first",
    )
    if "received" not in pivot or "ready" not in pivot:
        return []
    both = pivot.dropna(subset=["received", "ready"])
    return [delta.total_seconds() / 3600 for delta in (both["ready"] - both["received"])]


# --- schemas --------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_patient_view",
        "description": (
            "Return the prescription rows the patient app shows for one patient, "
            "exactly as the pharmacy systems reported them. This is the patient's "
            "own view and may be wrong: a prescription can appear twice with "
            "different drug spellings, be missing entirely, or show an out-of-date "
            "status. Compare it against get_pharmacy_records to find the "
            "discrepancy. refill_on_schedule is null when the source system does "
            "not report that field, which is not the same as false."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "Patient identifier, e.g. 'PT01058'.",
                }
            },
            "required": ["patient_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_pharmacy_records",
        "description": (
            "Return what the pharmacies' own records say this patient was "
            "prescribed: one entry per real prescription, with the canonical drug "
            "name, strength and form. Treat this as the source of truth to check "
            "the patient's app view against."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "string",
                    "description": "Patient identifier, e.g. 'PT01058'.",
                }
            },
            "required": ["patient_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_rx_history",
        "description": (
            "Return the status timeline for one prescription from the pharmacy's "
            "fill events: received, in_process, ready, then picked_up or "
            "delivered, with a fill number per refill. An empty timeline means the "
            "prescription is on file but nothing has been dispensed. Use this to "
            "check whether a status the patient saw was simply stale, and whether "
            "an expected refill ever happened.\n\n"
            "It also returns the refill arithmetic already done for you: `as_of` "
            "(today's date for this data), `refill_on_schedule`, "
            "`refills_remaining`, `days_supply`, `refill_due_date` (when the "
            "current supply runs out) and `days_overdue` (positive means a refill "
            "is late, negative means it is not due yet, null means nothing has "
            "been collected so no refill can be due). A refill that never happened "
            "shows as refill_on_schedule true, refills_remaining above zero, "
            "days_overdue positive, and no fill above 0 in the timeline. Do not "
            "recompute these dates yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "rx_number": {
                    "type": "string",
                    "description": "Prescription number, e.g. 'RX1000017'.",
                }
            },
            "required": ["rx_number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_pharmacy_speed",
        "description": (
            "Return how long one pharmacy typically takes from receiving a "
            "prescription to marking it ready, as a median in wall-clock hours, "
            "with its size, area and whether it closes on Sundays. Use this to "
            "judge whether a wait the patient complained about is normal for that "
            "pharmacy or genuinely unusual. median_hours_to_ready is null when "
            "nothing has reached ready yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pharmacy_id": {
                    "type": "string",
                    "description": "Pharmacy identifier, e.g. 'PH036'.",
                }
            },
            "required": ["pharmacy_id"],
            "additionalProperties": False,
        },
    },
]

# name -> callable, so the agent loop can dispatch by the name Claude returns.
TOOL_FUNCTIONS = {
    "get_patient_view": get_patient_view,
    "get_pharmacy_records": get_pharmacy_records,
    "get_rx_history": get_rx_history,
    "get_pharmacy_speed": get_pharmacy_speed,
}
