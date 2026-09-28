"""Step 2 tests: the three PMS exports.

No faults are planted yet, so between them the exports must account for every
prescription exactly once. Step 3 will deliberately break that.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import replace
from datetime import date, datetime

import pytest

from simulator.config import SimConfig
from simulator.drugs import DRUGS
from simulator.exporters import EXPORTERS, pms_a, pms_b, pms_c, write_exports
from simulator.exporters.records import ExportRecord, build_records
from simulator.models import FillEvent, Patient, Pharmacy, Prescription
from simulator.schedule import OpeningHours
from simulator.world import build_world, group_events_by_rx


@pytest.fixture(scope="module")
def world():
    return build_world(SimConfig.small())


@pytest.fixture(scope="module")
def exports(tmp_path_factory, world):
    """The three raw files written once, returned as {pms_type: text}."""
    out = tmp_path_factory.mktemp("exports")
    paths = write_exports(world, out)
    return {key: path.read_text(encoding="utf-8") for key, path in paths.items()}


def _pharmacies_of(world, pms_type):
    return tuple(p for p in world.pharmacies if p.pms_type == pms_type)


def _rx_numbers_for(world, pms_type):
    wanted = {p.pharmacy_id for p in _pharmacies_of(world, pms_type)}
    return {rx.rx_number for rx in world.prescriptions if rx.pharmacy_id in wanted}


# --- a hand-built record, for the cases a generated world may not contain ----


def _bare_record(events: tuple[FillEvent, ...] = ()) -> ExportRecord:
    pharmacy = Pharmacy(
        pharmacy_id="PH001",
        name="Testville Pharmacy",
        size="small",
        area="rural",
        pms_type="A",
        hours=OpeningHours(windows=((9, 17),) * 5 + ((10, 14), None)),
    )
    patient = Patient(
        patient_id="PT00001",
        name="Jane Doe",
        dob=date(1955, 3, 12),
        phone="555-123-4567",
        home_pharmacy_id="PH001",
    )
    rx = Prescription(
        rx_number="RX1000000",
        patient_id="PT00001",
        pharmacy_id="PH001",
        drug_id="D01",
        days_supply=30,
        refills_authorized=3,
        prescriber="Dr. Black",
        written_on=date(2026, 7, 1),
        refill_on_schedule=False,
    )
    return ExportRecord(pharmacy=pharmacy, patient=patient, rx=rx, drug=DRUGS[0], events=events)


def test_a_prescription_with_no_events_renders_blank_status():
    """A prescription can be on file with nothing recorded against it yet."""
    record = _bare_record()
    assert record.current is None
    assert record.refills_left == 3
    row = dict(zip(pms_a.HEADER, pms_a.row_for(record), strict=True))
    assert row["STATUS"] == ""
    assert row["STATUS_TS"] == ""
    assert row["RX_NO"] == "RX1000000"


def test_b_and_c_render_an_empty_event_list():
    record = _bare_record()
    assert pms_b.record_payload(record)["events"] == []
    segments = pms_c.segments_for(record)
    assert [segment.split("|")[0] for segment in segments] == ["PID", "RXO"]


def test_refills_left_counts_down_with_fills():
    events = tuple(
        FillEvent(
            event_id=f"EV{index}",
            rx_number="RX1000000",
            pharmacy_id="PH001",
            fill_number=index,
            status="ready",
            occurred_at=datetime(2026, 7, 1 + index, 12, 0),
        )
        for index in range(3)
    )
    assert _bare_record(events).refills_left == 1  # 3 authorized, fill 2 reached


def test_refills_left_never_goes_negative():
    over = (
        FillEvent(
            event_id="EV9",
            rx_number="RX1000000",
            pharmacy_id="PH001",
            fill_number=9,
            status="ready",
            occurred_at=datetime(2026, 7, 9, 12, 0),
        ),
    )
    assert _bare_record(over).refills_left == 0


def test_hl7_name_inverts_to_family_first():
    assert pms_c.hl7_name("Jane Doe") == "DOE^JANE"
    assert pms_c.hl7_name("Mary Anne Smith") == "SMITH^MARY ANNE"
    assert pms_c.hl7_name("Cher") == "CHER"


# --- file level -------------------------------------------------------------


def test_all_three_files_are_written(world, tmp_path):
    paths = write_exports(world, tmp_path)
    assert set(paths) == {"A", "B", "C"}
    assert {path.name for path in paths.values()} == {
        "pms_a.csv",
        "pms_b.json",
        "pms_c.txt",
    }
    for path in paths.values():
        assert path.parent.name == "raw"
        assert path.stat().st_size > 0


def test_each_pharmacy_appears_only_in_its_own_format(world, exports):
    """The spec's core export rule: one pharmacy, one PMS file."""
    for pms_type in EXPORTERS:
        own = {p.pharmacy_id for p in _pharmacies_of(world, pms_type)}
        others = {p.pharmacy_id for p in world.pharmacies} - own
        assert own, f"no pharmacies for PMS {pms_type}"
        text = exports[pms_type]
        for pharmacy_id in sorted(others):
            assert pharmacy_id not in text, f"{pharmacy_id} leaked into the PMS {pms_type} export"


def test_every_prescription_appears_exactly_once_across_the_exports(world, exports):
    rows = csv.DictReader(exports["A"].splitlines())
    in_a = [row["RX_NO"] for row in rows]
    in_b = [entry["rx"]["number"] for entry in json.loads(exports["B"])]
    in_c = [line.split("|")[1] for line in exports["C"].splitlines() if line.startswith("RXO|")]

    seen = in_a + in_b + in_c
    assert len(seen) == len(set(seen)), "a prescription was exported twice"
    assert set(seen) == {rx.rx_number for rx in world.prescriptions}
    for pms_type, listed in (("A", in_a), ("B", in_b), ("C", in_c)):
        assert set(listed) == _rx_numbers_for(world, pms_type)


def test_exports_use_unix_newlines(exports):
    for pms_type, text in exports.items():
        assert "\r\n" not in text, f"PMS {pms_type} export has CRLF"


def test_exports_are_byte_identical_across_runs(tmp_path):
    config = SimConfig.small()
    first = write_exports(build_world(config), tmp_path / "run1")
    second = write_exports(build_world(config), tmp_path / "run2")
    for pms_type in first:
        assert first[pms_type].read_bytes() == second[pms_type].read_bytes()


# --- format A ---------------------------------------------------------------


def test_a_has_the_expected_header_and_row_count(world, exports):
    lines = exports["A"].splitlines()
    assert tuple(lines[0].split(",")) == pms_a.HEADER
    assert len(lines) - 1 == len(_rx_numbers_for(world, "A"))


def test_a_status_is_the_latest_event(world, exports):
    grouped = group_events_by_rx(world)
    for row in csv.DictReader(exports["A"].splitlines()):
        events = sorted(grouped.get(row["RX_NO"], []), key=lambda e: e.occurred_at)
        if not events:
            assert row["STATUS"] == ""
            continue
        assert row["STATUS"] == events[-1].status.upper()
        assert row["STATUS_TS"] == pms_a.us_timestamp(events[-1].occurred_at)


def test_a_uses_us_dates_and_upper_case_names(exports):
    for row in csv.DictReader(exports["A"].splitlines()):
        assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", row["PT_DOB"]), row["PT_DOB"]
        assert row["PT_NAME"] == row["PT_NAME"].upper()
        assert row["DRUG_DESC"] == row["DRUG_DESC"].upper()


# --- format B ---------------------------------------------------------------


def test_b_is_valid_json_with_the_expected_shape(world, exports):
    payload = json.loads(exports["B"])
    assert len(payload) == len(_rx_numbers_for(world, "B"))
    entry = payload[0]
    assert set(entry) == {"pharmacy", "patient", "rx", "events"}
    assert set(entry["rx"]["drug"]) == {"name", "strength", "form"}
    assert entry["pharmacy"]["pms"] == "B"


def test_b_uses_iso_dates(exports):
    for entry in json.loads(exports["B"]):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["patient"]["dob"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["rx"]["written_on"])


def test_b_keeps_the_full_event_history_in_order(world, exports):
    grouped = group_events_by_rx(world)
    for entry in json.loads(exports["B"]):
        events = grouped.get(entry["rx"]["number"], [])
        assert len(entry["events"]) == len(events)
        stamps = [event["at"] for event in entry["events"]]
        assert stamps == sorted(stamps), "events out of order"
        assert [event["seq"] for event in entry["events"]] == list(range(1, len(events) + 1))


# --- format C ---------------------------------------------------------------


def test_c_lines_all_carry_a_known_segment_tag(exports):
    for line in exports["C"].splitlines():
        assert line.split("|")[0] in pms_c.SEGMENT_TAGS, line


def test_c_has_one_pid_and_one_rxo_per_prescription(world, exports):
    lines = exports["C"].splitlines()
    expected = len(_rx_numbers_for(world, "C"))
    assert sum(1 for line in lines if line.startswith("PID|")) == expected
    assert sum(1 for line in lines if line.startswith("RXO|")) == expected


def test_c_sts_count_matches_the_truth_event_count(world, exports):
    grouped = group_events_by_rx(world)
    per_rx: dict[str, int] = {}
    for line in exports["C"].splitlines():
        if line.startswith("STS|"):
            rx_number = line.split("|")[1]
            per_rx[rx_number] = per_rx.get(rx_number, 0) + 1
    for rx_number in _rx_numbers_for(world, "C"):
        assert per_rx.get(rx_number, 0) == len(grouped.get(rx_number, []))


def test_c_uses_compact_dates_and_inverted_names(exports):
    for line in exports["C"].splitlines():
        fields = line.split("|")
        if fields[0] == "PID":
            assert re.fullmatch(r"\d{8}", fields[3]), fields[3]
            assert fields[2] == fields[2].upper()
        elif fields[0] == "STS":
            assert re.fullmatch(r"\d{14}", fields[4]), fields[4]


# --- the formats really do disagree ----------------------------------------


def test_the_three_formats_write_the_same_dob_differently():
    """The messiness `normalizer` will later have to reconcile."""
    record = _bare_record()
    assert pms_a.row_for(record)[2] == "03/12/1955"
    assert pms_b.record_payload(record)["patient"]["dob"] == "1955-03-12"
    assert pms_c.segments_for(record)[0].split("|")[3] == "19550312"


def test_a_and_c_print_the_drug_messily_while_b_keeps_fields():
    record = _bare_record()
    assert pms_a.row_for(record)[3] == record.drug.messy_desc
    assert pms_c.segments_for(record)[1].split("|")[3] == record.drug.messy_desc
    assert pms_b.record_payload(record)["rx"]["drug"]["name"] == record.drug.name


# --- records helper --------------------------------------------------------


def test_build_records_covers_only_the_requested_pharmacies(world):
    for pms_type in EXPORTERS:
        pharmacies = _pharmacies_of(world, pms_type)
        records = build_records(world, pharmacies)
        assert {record.rx.rx_number for record in records} == _rx_numbers_for(world, pms_type)
        assert all(record.pharmacy.pms_type == pms_type for record in records)


def test_build_records_preserves_world_prescription_order(world):
    pharmacies = _pharmacies_of(world, "A")
    records = build_records(world, pharmacies)
    wanted = {p.pharmacy_id for p in pharmacies}
    expected = [rx.rx_number for rx in world.prescriptions if rx.pharmacy_id in wanted]
    assert [record.rx.rx_number for record in records] == expected


@pytest.mark.slow
def test_full_scale_exports_are_deterministic_and_complete(tmp_path):
    config = replace(SimConfig())
    world = build_world(config)
    first = write_exports(world, tmp_path / "full1")
    second = write_exports(build_world(config), tmp_path / "full2")

    exported = []
    for row in csv.DictReader(first["A"].read_text().splitlines()):
        exported.append(row["RX_NO"])
    exported += [e["rx"]["number"] for e in json.loads(first["B"].read_text())]
    exported += [
        line.split("|")[1]
        for line in first["C"].read_text().splitlines()
        if line.startswith("RXO|")
    ]
    assert len(exported) == len(world.prescriptions) == 5_000
    for pms_type in first:
        assert first[pms_type].read_bytes() == second[pms_type].read_bytes()
