"""Tests for `data/app_view.csv` — the fault-bearing view the agent's tools read.

The app view must agree with the three PMS exports exactly, because both are
built from the same faulted record lists. These tests check that agreement and
each fault's visible signature in the flattened table.
"""

from __future__ import annotations

import csv
import json

import pytest

from simulator.app_view import COLUMNS, FILENAME, rows_for, write_app_view
from simulator.config import SimConfig
from simulator.exporters import EXPORTERS, write_exports
from simulator.exporters.records import build_records
from simulator.faults import plant
from simulator.world import build_world, group_events_by_rx


@pytest.fixture(scope="module")
def planted():
    return plant(build_world(SimConfig.small()))


@pytest.fixture(scope="module")
def view(tmp_path_factory, planted):
    """The written file, parsed back as a list of dicts."""
    world, fault_plan = planted
    path = write_app_view(world, tmp_path_factory.mktemp("view"), fault_plan)
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _rows_for_rx(view, rx_number):
    return [row for row in view if row["rx_number"] == rx_number]


# --- shape ----------------------------------------------------------------


def test_file_is_named_and_has_the_specified_columns(planted, tmp_path):
    world, fault_plan = planted
    path = write_app_view(world, tmp_path, fault_plan)
    assert path.name == FILENAME
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert tuple(header.split(",")) == COLUMNS


def test_row_count_matches_the_three_exports_combined(planted, view):
    world, fault_plan = planted
    expected = sum(
        len(
            build_records(
                world, tuple(p for p in world.pharmacies if p.pms_type == pms), fault_plan
            )
        )
        for pms in EXPORTERS
    )
    assert len(view) == expected


def test_record_ids_are_unique_and_prefixed_by_source(view):
    ids = [row["record_id"] for row in view]
    assert len(ids) == len(set(ids))
    for row in view:
        assert row["record_id"].startswith(row["source_pms"])


def test_every_source_is_represented(view):
    assert {row["source_pms"] for row in view} == {"A", "B", "C"}


def test_statuses_are_canonical_lowercase(view):
    allowed = {"received", "in_process", "ready", "picked_up", "delivered", ""}
    assert {row["status"] for row in view} <= allowed


def test_no_patient_names_appear_in_the_file(planted, view):
    """The app view carries ids only — names never reach the agent's tools."""
    world, _ = planted
    blob = "\n".join(",".join(row.values()) for row in view)
    for patient in world.patients:
        assert patient.name not in blob
        assert patient.name.upper() not in blob


def test_written_file_is_byte_identical_across_runs(planted, tmp_path):
    world, fault_plan = planted
    first = write_app_view(world, tmp_path / "one", fault_plan)
    second = write_app_view(world, tmp_path / "two", fault_plan)
    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()


# --- refill_on_schedule: empty means unknown, never false -----------------


def test_refill_flag_is_empty_for_sources_that_never_send_it(view):
    for row in view:
        if row["source_pms"] in {"A", "C"}:
            assert row["refill_on_schedule"] == "", (
                "A and C do not carry the field; empty means unknown, not false"
            )


def test_refill_flag_is_populated_for_pms_b(planted, view):
    world, _ = planted
    truth = {rx.rx_number: rx.refill_on_schedule for rx in world.prescriptions}
    b_rows = [row for row in view if row["source_pms"] == "B"]
    assert b_rows
    assert {row["refill_on_schedule"] for row in b_rows} <= {"true", "false"}
    for row in b_rows:
        assert row["refill_on_schedule"] == ("true" if truth[row["rx_number"]] else "false")


def test_pms_b_shows_both_true_and_false(view):
    """Guards against a constant being written instead of the real flag."""
    flags = {row["refill_on_schedule"] for row in view if row["source_pms"] == "B"}
    assert flags == {"true", "false"}


# --- faults are visible ---------------------------------------------------


def test_dropped_prescriptions_are_missing(planted, view):
    _, fault_plan = planted
    faults = fault_plan.of_type("dropped")
    assert faults
    for fault in faults:
        assert _rows_for_rx(view, fault.rx_number) == []


def test_duplicates_appear_twice_with_their_own_spelling(planted, view):
    _, fault_plan = planted
    faults = fault_plan.of_type("duplicate")
    assert faults
    for fault in faults:
        rows = _rows_for_rx(view, fault.rx_number)
        assert len(rows) == 2, f"{fault.rx_number} appears {len(rows)} times"
        assert rows[0]["drug_display"] != rows[1]["drug_display"], (
            "the copy should carry its own spelling"
        )
        # Same prescription underneath, or dedup has nothing to match on.
        assert rows[0]["patient_id"] == rows[1]["patient_id"]
        assert rows[0]["pharmacy_id"] == rows[1]["pharmacy_id"]
        assert rows[0]["record_id"] != rows[1]["record_id"]


def test_stale_records_show_the_old_status(planted, view):
    world, fault_plan = planted
    grouped = group_events_by_rx(world)
    faults = fault_plan.of_type("stale_status")
    assert faults
    for fault in faults:
        (row,) = _rows_for_rx(view, fault.rx_number)
        assert row["status"] == fault.details["shown_status"]
        latest = max(grouped[fault.rx_number], key=lambda e: e.occurred_at)
        assert row["status"] != latest.status or len(grouped[fault.rx_number]) == 1


def test_phantom_schedule_is_visible_as_a_flagged_rx_with_no_refill(planted, view):
    """Truth-layer fault, so it shows in whichever format the pharmacy uses."""
    world, fault_plan = planted
    grouped = group_events_by_rx(world)
    faults = fault_plan.of_type("phantom_schedule")
    assert faults
    for fault in faults:
        rows = _rows_for_rx(view, fault.rx_number)
        assert rows, "a phantom prescription is still exported"
        assert all(event.fill_number == 0 for event in grouped.get(fault.rx_number, ()))


def test_records_with_no_fill_activity_show_empty_status(planted, view):
    world, _ = planted
    grouped = group_events_by_rx(world)
    bare = {rx.rx_number for rx in world.prescriptions if not grouped.get(rx.rx_number)}
    for row in view:
        if row["rx_number"] in bare:
            assert row["status"] == ""
            assert row["status_ts"] == ""


# --- agreement with the exports ------------------------------------------


def test_app_view_and_exports_agree_on_which_rx_appear(planted, tmp_path):
    """The whole point of sharing build_records: the two views cannot diverge."""
    world, fault_plan = planted
    raw = write_exports(world, tmp_path, fault_plan)
    rows = rows_for(world, fault_plan)
    by_source: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    for row in rows:
        by_source[row[-1]].append(row[3])

    in_a = [r["RX_NO"] for r in csv.DictReader(raw["A"].read_text().splitlines())]
    in_b = [e["rx"]["number"] for e in json.loads(raw["B"].read_text())]
    in_c = [
        line.split("|")[1] for line in raw["C"].read_text().splitlines() if line.startswith("RXO|")
    ]
    assert by_source["A"] == in_a
    assert by_source["B"] == in_b
    assert by_source["C"] == in_c


def test_drug_display_matches_what_each_exporter_wrote(planted, view):
    world, fault_plan = planted
    for pms_type, module in EXPORTERS.items():
        pharmacies = tuple(p for p in world.pharmacies if p.pms_type == pms_type)
        records = build_records(world, pharmacies, fault_plan)
        shown = [row["drug_display"] for row in view if row["source_pms"] == pms_type]
        assert shown == [module.drug_display(record) for record in records]


# --- full scale ----------------------------------------------------------


@pytest.mark.slow
def test_full_scale_row_count_is_prescriptions_minus_dropped_plus_duplicates():
    config = SimConfig()
    world, fault_plan = plant(build_world(config))
    dropped = len(fault_plan.of_type("dropped"))
    duplicated = len(fault_plan.of_type("duplicate"))
    rows = rows_for(world, fault_plan)
    assert len(rows) == config.n_prescriptions - dropped + duplicated == 5_100
