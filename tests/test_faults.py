"""Step 3 tests: planted faults and the answer key.

The answer key is what every later accuracy claim rests on, so these tests check
it is exact rather than approximately right: disjoint faults, exact counts at a
known scale, and each fault type actually visible in the export it names.
"""

from __future__ import annotations

import csv
import json

import pytest

from simulator.config import SimConfig
from simulator.exporters import write_exports
from simulator.faults import ANSWER_KEY_FILENAME, FAULT_TYPES, plant, write_answer_key
from simulator.world import build_world, group_events_by_rx

# Which PMS type each answer-key export_file belongs to.
_TYPE_OF_FILE = {"pms_a.csv": "A", "pms_b.json": "B", "pms_c.txt": "C"}


@pytest.fixture(scope="module")
def unplanted():
    return build_world(SimConfig.small())


@pytest.fixture(scope="module")
def planted(unplanted):
    """The world after truth-layer faults, plus the plan."""
    return plant(unplanted)


@pytest.fixture(scope="module")
def exports(tmp_path_factory, planted):
    world, fault_plan = planted
    out = tmp_path_factory.mktemp("faulty")
    paths = write_exports(world, out, fault_plan)
    return {key: path.read_text(encoding="utf-8") for key, path in paths.items()}


# --- reading the exports back ---------------------------------------------


def _rx_counts_in(exports: dict[str, str], pms_type: str) -> dict[str, int]:
    """How many times each rx_number appears in one export."""
    if pms_type == "A":
        listed = [row["RX_NO"] for row in csv.DictReader(exports["A"].splitlines())]
    elif pms_type == "B":
        listed = [entry["rx"]["number"] for entry in json.loads(exports["B"])]
    else:
        listed = [
            line.split("|")[1] for line in exports["C"].splitlines() if line.startswith("RXO|")
        ]
    counts: dict[str, int] = {}
    for rx_number in listed:
        counts[rx_number] = counts.get(rx_number, 0) + 1
    return counts


def _a_rows_for(exports, rx_number):
    return [row for row in csv.DictReader(exports["A"].splitlines()) if row["RX_NO"] == rx_number]


def _b_entries_for(exports, rx_number):
    return [e for e in json.loads(exports["B"]) if e["rx"]["number"] == rx_number]


def _c_blocks_for(exports, rx_number):
    """C has no record delimiter, so split on PID and keep blocks naming this rx."""
    blocks, current = [], []
    for line in exports["C"].splitlines():
        if line.startswith("PID|") and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return [
        block for block in blocks if any(line.startswith(f"RXO|{rx_number}|") for line in block)
    ]


def _faults_of(plan, fault_type):
    return plan.of_type(fault_type)


# --- the plan itself -------------------------------------------------------


def test_every_fault_type_is_planted(planted):
    _, plan = planted
    present = {fault.type for fault in plan.faults}
    assert present == set(FAULT_TYPES)


def test_counts_match_the_configured_rates(planted):
    world, plan = planted
    config = world.config
    expected = {
        "duplicate": config.fault_count(config.rate_duplicate),
        "dropped": config.fault_count(config.rate_dropped),
        "stale_status": config.fault_count(config.rate_stale_status),
        "phantom_schedule": config.fault_count(config.rate_phantom_schedule),
    }
    actual = {name: len(plan.of_type(name)) for name in expected}
    assert actual == expected


def test_faults_are_disjoint(planted):
    """No prescription carries two faults, so the answer key is unambiguous."""
    _, plan = planted
    numbers = [fault.rx_number for fault in plan.faults]
    assert len(numbers) == len(set(numbers))
    assert len(plan.by_rx()) == len(plan.faults)


def test_fault_ids_are_unique_and_sequential(planted):
    _, plan = planted
    ids = [fault.fault_id for fault in plan.faults]
    assert ids == [f"f{index:05d}" for index in range(1, len(ids) + 1)]


def test_faults_are_ordered_by_type_then_rx(planted):
    """Stable ordering is what makes fault_ids reproducible across runs."""
    _, plan = planted
    keys = [(fault.type, fault.rx_number) for fault in plan.faults]
    assert keys == sorted(keys)


def test_every_fault_names_a_real_prescription_and_pharmacy(planted):
    world, plan = planted
    prescriptions = {rx.rx_number: rx for rx in world.prescriptions}
    for fault in plan.faults:
        assert fault.rx_number in prescriptions
        assert prescriptions[fault.rx_number].pharmacy_id == fault.pharmacy_id
        assert fault.details["export_file"] in _TYPE_OF_FILE


def test_planting_is_deterministic(unplanted):
    first_world, first_plan = plant(unplanted)
    second_world, second_plan = plant(unplanted)
    assert first_plan == second_plan
    assert first_world.fill_events == second_world.fill_events


# --- the answer key file ---------------------------------------------------


def test_answer_key_has_one_line_per_fault(planted, tmp_path):
    _, plan = planted
    path = write_answer_key(plan, tmp_path)
    assert path.name == ANSWER_KEY_FILENAME
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(plan.faults)
    for line in lines:
        record = json.loads(line)
        assert set(record) == {"fault_id", "type", "rx_number", "pharmacy_id", "details"}
        assert record["type"] in FAULT_TYPES
        assert record["details"], "every fault should carry some detail"


def test_answer_key_is_byte_identical_across_runs(unplanted, tmp_path):
    first = write_answer_key(plant(unplanted)[1], tmp_path / "one")
    second = write_answer_key(plant(unplanted)[1], tmp_path / "two")
    assert first.read_bytes() == second.read_bytes()


def test_answer_key_uses_unix_newlines(planted, tmp_path):
    _, plan = planted
    blob = write_answer_key(plan, tmp_path).read_bytes()
    assert b"\r\n" not in blob


# --- dropped ---------------------------------------------------------------


def test_dropped_prescriptions_are_absent_from_their_export(planted, exports):
    _, plan = planted
    faults = _faults_of(plan, "dropped")
    assert faults
    for fault in faults:
        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        counts = _rx_counts_in(exports, pms_type)
        assert fault.rx_number not in counts, f"{fault.rx_number} should have been dropped"


def test_undropped_prescriptions_are_still_exported(planted, exports):
    world, plan = planted
    dropped = {fault.rx_number for fault in plan.of_type("dropped")}
    by_pharmacy = {p.pharmacy_id: p.pms_type for p in world.pharmacies}
    counts = {pms: _rx_counts_in(exports, pms) for pms in ("A", "B", "C")}
    for rx in world.prescriptions:
        if rx.rx_number in dropped:
            continue
        assert rx.rx_number in counts[by_pharmacy[rx.pharmacy_id]]


# --- duplicate -------------------------------------------------------------


def test_duplicates_appear_exactly_twice_in_their_export(planted, exports):
    _, plan = planted
    faults = _faults_of(plan, "duplicate")
    assert faults
    for fault in faults:
        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        counts = _rx_counts_in(exports, pms_type)
        assert counts.get(fault.rx_number) == 2, (
            f"{fault.rx_number} appears {counts.get(fault.rx_number)} times in {pms_type}"
        )


def test_everything_else_appears_at_most_once(planted, exports):
    _, plan = planted
    duplicated = {fault.rx_number for fault in plan.of_type("duplicate")}
    for pms_type in ("A", "B", "C"):
        for rx_number, count in _rx_counts_in(exports, pms_type).items():
            expected = 2 if rx_number in duplicated else 1
            assert count == expected, f"{rx_number} appears {count} times in {pms_type}"


def test_the_duplicate_copy_differs_from_the_original(planted, exports):
    """The spec's "small differences": drug text, name casing, date format."""
    _, plan = planted
    checked = {"A": 0, "B": 0, "C": 0}
    for fault in plan.of_type("duplicate"):
        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        if pms_type == "A":
            first, second = _a_rows_for(exports, fault.rx_number)
            assert first["PT_NAME"] != second["PT_NAME"]
            assert first["PT_DOB"] != second["PT_DOB"]
            assert first["DRUG_DESC"] != second["DRUG_DESC"]
        elif pms_type == "B":
            first, second = _b_entries_for(exports, fault.rx_number)
            assert first["patient"]["name"] != second["patient"]["name"]
            assert first["rx"]["drug"]["name"] != second["rx"]["drug"]["name"]
        else:
            first, second = _c_blocks_for(exports, fault.rx_number)
            assert first[0] != second[0], "PID segment should differ"
            assert first[1] != second[1], "RXO segment should differ"
        checked[pms_type] += 1
    assert all(count > 0 for count in checked.values()), (
        f"no duplicate landed in some format: {checked}"
    )


def test_the_duplicate_pair_still_shares_the_rx_number(planted, exports):
    """Different text, same prescription — otherwise dedup has nothing to match."""
    _, plan = planted
    for fault in plan.of_type("duplicate"):
        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        if pms_type == "A":
            rows = _a_rows_for(exports, fault.rx_number)
            assert {row["RX_NO"] for row in rows} == {fault.rx_number}
            assert {row["PHARMACY_ID"] for row in rows} == {fault.pharmacy_id}


# --- stale_status ----------------------------------------------------------


def test_stale_status_shows_an_older_status_than_the_truth(planted, exports):
    world, plan = planted
    grouped = group_events_by_rx(world)
    faults = _faults_of(plan, "stale_status")
    assert faults
    for fault in faults:
        truth = sorted(grouped[fault.rx_number], key=lambda e: e.occurred_at)
        assert fault.details["shown_status"] != fault.details["actual_status"] or (
            fault.details["shown_at"] != fault.details["actual_at"]
        )
        assert fault.details["actual_status"] == truth[-1].status
        assert int(fault.details["events_hidden"]) >= 1

        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        if pms_type == "A":
            (row,) = _a_rows_for(exports, fault.rx_number)
            assert row["STATUS"] == str(fault.details["shown_status"]).upper()
            assert row["STATUS"] != truth[-1].status.upper() or len(truth) == 1
        elif pms_type == "B":
            (entry,) = _b_entries_for(exports, fault.rx_number)
            assert len(entry["events"]) == int(fault.details["events_kept"])
            assert entry["events"][-1]["status"] == fault.details["shown_status"]
        else:
            (block,) = _c_blocks_for(exports, fault.rx_number)
            sts = [line for line in block if line.startswith("STS|")]
            assert len(sts) == int(fault.details["events_kept"])


def test_stale_status_hides_events_rather_than_inventing_them(planted, exports):
    """The export must be a prefix of the truth, never a contradiction."""
    world, plan = planted
    grouped = group_events_by_rx(world)
    for fault in plan.of_type("stale_status"):
        if _TYPE_OF_FILE[str(fault.details["export_file"])] != "B":
            continue
        (entry,) = _b_entries_for(exports, fault.rx_number)
        truth = sorted(grouped[fault.rx_number], key=lambda e: e.occurred_at)
        shown = [event["status"] for event in entry["events"]]
        assert shown == [event.status for event in truth][: len(shown)]


# --- phantom_schedule -----------------------------------------------------


def test_phantom_schedule_removes_every_refill_from_the_truth(planted, unplanted):
    world, plan = planted
    after = group_events_by_rx(world)
    before = group_events_by_rx(unplanted)
    faults = _faults_of(plan, "phantom_schedule")
    assert faults
    for fault in faults:
        rx_number = fault.rx_number
        # It genuinely had refills before planting, so the fault is detectable.
        assert any(event.fill_number > 0 for event in before[rx_number])
        # And none afterwards.
        assert all(event.fill_number == 0 for event in after.get(rx_number, ()))
        assert int(fault.details["refill_events_removed"]) >= 1


def test_phantom_schedule_only_targets_scheduled_refills(planted):
    world, plan = planted
    prescriptions = {rx.rx_number: rx for rx in world.prescriptions}
    for fault in plan.of_type("phantom_schedule"):
        rx = prescriptions[fault.rx_number]
        assert rx.refill_on_schedule is True
        assert rx.refills_authorized > 0


def test_phantom_schedule_is_visible_in_the_export_too(planted, exports):
    """Truth-layer, so every format shows it: no refill, full refills remaining."""
    world, plan = planted
    prescriptions = {rx.rx_number: rx for rx in world.prescriptions}
    for fault in plan.of_type("phantom_schedule"):
        pms_type = _TYPE_OF_FILE[str(fault.details["export_file"])]
        rx = prescriptions[fault.rx_number]
        if pms_type == "A":
            (row,) = _a_rows_for(exports, fault.rx_number)
            assert int(row["REFILLS_LEFT"]) == rx.refills_authorized
        elif pms_type == "B":
            (entry,) = _b_entries_for(exports, fault.rx_number)
            assert entry["rx"]["refill_on_schedule"] is True
            assert all(event["fill"] == 0 for event in entry["events"])
        else:
            (block,) = _c_blocks_for(exports, fault.rx_number)
            fills = {line.split("|")[2] for line in block if line.startswith("STS|")}
            assert fills <= {"0"}


def test_planting_only_removes_events(planted, unplanted):
    """Truth-layer planting is a filter: it never adds or rewrites an event."""
    world, _ = planted
    assert set(world.fill_events) <= set(unplanted.fill_events)
    assert len(world.fill_events) < len(unplanted.fill_events)


# --- full scale -----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("fault_type", FAULT_TYPES)
def test_fault_rates_are_within_twenty_percent_of_target(fault_type):
    config = SimConfig()
    _, plan = plant(build_world(config))
    target_rate = getattr(config, f"rate_{fault_type}")
    actual_rate = len(plan.of_type(fault_type)) / config.n_prescriptions
    low, high = target_rate * 0.8, target_rate * 1.2
    assert low <= actual_rate <= high, (
        f"{fault_type} at {actual_rate:.4%}, target {target_rate:.2%} (±20%)"
    )


@pytest.mark.slow
def test_full_scale_plan_is_disjoint_and_complete():
    config = SimConfig()
    _, plan = plant(build_world(config))
    numbers = [fault.rx_number for fault in plan.faults]
    assert len(numbers) == len(set(numbers))
    assert len(plan.faults) == sum(
        config.fault_count(getattr(config, f"rate_{name}")) for name in FAULT_TYPES
    )
