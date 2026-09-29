"""Tests for the investigator's four tools.

Every test runs against a freshly generated small world in a tmp directory, so
the planted faults are known from the answer key rather than guessed.
"""

from __future__ import annotations

import csv
import json

import pytest

from investigator import tools
from simulator.config import SimConfig
from simulator.generate import generate


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """Generate a small world, point the tools at it, return the useful handles."""
    out = tmp_path_factory.mktemp("toolsdata")
    result = generate(SimConfig.small(), out)
    tools.clear_cache()
    tools.set_data_dir(out)
    yield {
        "dir": out,
        "world": result.world,
        "plan": result.plan,
        "faults": [json.loads(line) for line in result.answer_key.read_text().splitlines()],
        "patients": list(csv.DictReader((out / "truth" / "patients.csv").read_text().splitlines())),
    }
    tools.clear_cache()
    tools.set_data_dir(tools.DEFAULT_DATA_DIR)


def _fault_of(dataset, fault_type):
    return [f for f in dataset["faults"] if f["type"] == fault_type]


def _any_patient(dataset) -> str:
    return dataset["world"].prescriptions[0].patient_id


# --- loading --------------------------------------------------------------


def test_data_is_loaded_once_not_per_call(dataset):
    first = tools.dataset()
    get_patient_view = tools.get_patient_view(_any_patient(dataset))
    assert get_patient_view is not None
    assert tools.dataset() is first, "each call re-read the CSVs"


def test_patients_csv_is_never_read(dataset):
    """The one truth file with names in it must stay unopened."""
    loaded = tools.dataset()
    for frame in (loaded.app_view, loaded.prescriptions, loaded.fill_events, loaded.pharmacies):
        assert "name" not in frame.columns or "pharmacy_id" in frame.columns
    # pharmacies.csv legitimately has a `name` column — a pharmacy, not a person.
    assert "patient_first_name" not in loaded.prescriptions.columns
    assert list(loaded.app_view.columns) == [
        "record_id",
        "patient_id",
        "pharmacy_id",
        "rx_number",
        "drug_display",
        "status",
        "status_ts",
        "refill_on_schedule",
        "source_pms",
    ]


def test_no_tool_output_contains_a_patient_name(dataset):
    """The hard privacy rule, checked against every real name in the world."""
    patient_id = _any_patient(dataset)
    rx_number = dataset["world"].prescriptions[0].rx_number
    pharmacy_id = dataset["world"].pharmacies[0].pharmacy_id

    everything = json.dumps(
        [
            tools.get_patient_view(patient_id),
            tools.get_pharmacy_records(patient_id),
            tools.get_rx_history(rx_number),
            tools.get_pharmacy_speed(pharmacy_id),
        ]
    )
    # The surname check excludes get_pharmacy_speed: pharmacy names are city-derived
    # ("Gardnerhaven Pharmacy") and can legitimately contain a token that happens to
    # match somebody's surname. Full names are still checked everywhere.
    without_pharmacy_name = json.dumps(
        [
            tools.get_patient_view(patient_id),
            tools.get_pharmacy_records(patient_id),
            tools.get_rx_history(rx_number),
        ]
    )
    for patient in dataset["world"].patients:
        assert patient.name not in everything
        assert patient.name.upper() not in everything
        surname = patient.name.rsplit(" ", 1)[-1]
        assert surname not in without_pharmacy_name, f"surname {surname!r} leaked"


# --- get_patient_view -----------------------------------------------------


def test_get_patient_view_returns_compact_rows(dataset):
    rows = tools.get_patient_view(_any_patient(dataset))
    assert isinstance(rows, list) and rows
    assert set(rows[0]) == {
        "record_id",
        "rx_number",
        "pharmacy_id",
        "drug_display",
        "status",
        "status_ts",
        "refill_on_schedule",
        "source_pms",
    }
    # patient_id is not echoed back — the caller already supplied it.
    assert "patient_id" not in rows[0]


def test_get_patient_view_only_returns_that_patient(dataset):
    patient_id = _any_patient(dataset)
    app_view = tools.dataset().app_view
    expected = set(app_view[app_view["patient_id"] == patient_id]["record_id"])
    assert {row["record_id"] for row in tools.get_patient_view(patient_id)} == expected


def test_refill_on_schedule_is_none_when_the_source_omits_it(dataset):
    """Empty in the file means unknown; it must not surface as False."""
    seen = {}
    for patient in dataset["world"].patients:
        rows = tools.get_patient_view(patient.patient_id)
        if isinstance(rows, dict):
            continue
        for row in rows:
            seen.setdefault(row["source_pms"], set()).add(row["refill_on_schedule"])
    for pms in ("A", "C"):
        assert seen.get(pms) == {None}, f"PMS {pms} should report unknown, got {seen.get(pms)}"
    assert seen["B"] <= {True, False}
    assert None not in seen["B"]


# --- the planted duplicate: twice in the app, once in the records ---------


def test_a_planted_duplicate_shows_twice_in_the_app_but_once_in_the_records(dataset):
    duplicates = _fault_of(dataset, "duplicate")
    assert duplicates, "fixture must contain a planted duplicate"

    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    checked = 0
    for fault in duplicates:
        patient_id = by_rx[fault["rx_number"]].patient_id

        app_rows = [
            row
            for row in tools.get_patient_view(patient_id)
            if row["rx_number"] == fault["rx_number"]
        ]
        record_rows = [
            row
            for row in tools.get_pharmacy_records(patient_id)
            if row["rx_number"] == fault["rx_number"]
        ]

        assert len(app_rows) == 2, f"{fault['rx_number']} should appear twice in the app view"
        assert len(record_rows) == 1, f"{fault['rx_number']} should appear once in the records"
        # The two app rows disagree on spelling — that is the whole complaint.
        assert app_rows[0]["drug_display"] != app_rows[1]["drug_display"]
        assert app_rows[0]["record_id"] != app_rows[1]["record_id"]
        checked += 1
    assert checked


def test_a_dropped_prescription_is_in_the_records_but_not_the_app(dataset):
    dropped = _fault_of(dataset, "dropped")
    assert dropped
    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    for fault in dropped:
        patient_id = by_rx[fault["rx_number"]].patient_id
        app = tools.get_patient_view(patient_id)
        records = tools.get_pharmacy_records(patient_id)
        assert fault["rx_number"] not in {row["rx_number"] for row in app}
        assert fault["rx_number"] in {row["rx_number"] for row in records}


def test_a_stale_row_disagrees_with_its_history(dataset):
    stale = _fault_of(dataset, "stale_status")
    assert stale
    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    for fault in stale:
        patient_id = by_rx[fault["rx_number"]].patient_id
        (app_row,) = [
            row
            for row in tools.get_patient_view(patient_id)
            if row["rx_number"] == fault["rx_number"]
        ]
        history = tools.get_rx_history(fault["rx_number"])
        assert app_row["status"] == fault["details"]["shown_status"]
        assert history["timeline"][-1]["status"] == fault["details"]["actual_status"]
        assert app_row["status"] != history["timeline"][-1]["status"]


# --- get_pharmacy_records -------------------------------------------------


def test_get_pharmacy_records_returns_a_composed_drug_string(dataset):
    rows = tools.get_pharmacy_records(_any_patient(dataset))
    assert rows
    assert set(rows[0]) == {
        "rx_number",
        "pharmacy_id",
        "drug",
        "days_supply",
        "refills_authorized",
        "written_on",
        "refill_on_schedule",
    }
    assert isinstance(rows[0]["days_supply"], int)
    assert isinstance(rows[0]["refill_on_schedule"], bool)
    assert rows[0]["drug"] == rows[0]["drug"].lower()


def test_records_match_the_truth_for_that_patient(dataset):
    patient_id = _any_patient(dataset)
    expected = {
        rx.rx_number for rx in dataset["world"].prescriptions if rx.patient_id == patient_id
    }
    assert {row["rx_number"] for row in tools.get_pharmacy_records(patient_id)} == expected


# --- get_rx_history -------------------------------------------------------


def test_get_rx_history_is_chronological_and_matches_the_truth(dataset):
    world = dataset["world"]
    counts: dict[str, int] = {}
    for event in world.fill_events:
        counts[event.rx_number] = counts.get(event.rx_number, 0) + 1
    rx_number = next(rx for rx, n in counts.items() if n >= 3)

    history = tools.get_rx_history(rx_number)
    assert history["rx_number"] == rx_number
    assert len(history["timeline"]) == counts[rx_number]
    stamps = [entry["at"] for entry in history["timeline"]]
    assert stamps == sorted(stamps)
    assert history["timeline"][0]["status"] == "received"


def test_get_rx_history_is_empty_for_a_prescription_with_no_fills(dataset):
    world = dataset["world"]
    with_events = {event.rx_number for event in world.fill_events}
    bare = [rx for rx in world.prescriptions if rx.rx_number not in with_events]
    if not bare:
        pytest.skip("this small world has no prescription without fill events")
    history = tools.get_rx_history(bare[0].rx_number)
    assert history["timeline"] == []
    assert "error" not in history


# --- get_pharmacy_speed ---------------------------------------------------


def test_get_pharmacy_speed_reports_a_median_and_context(dataset):
    pharmacy = dataset["world"].pharmacies[0]
    speed = tools.get_pharmacy_speed(pharmacy.pharmacy_id)
    assert set(speed) == {
        "pharmacy_id",
        "name",
        "size",
        "area",
        "closed_sunday",
        "median_hours_to_ready",
        "fills_measured",
    }
    assert speed["size"] == pharmacy.size
    assert speed["area"] == pharmacy.area
    assert speed["closed_sunday"] is pharmacy.closed_sunday
    assert speed["fills_measured"] > 0
    assert speed["median_hours_to_ready"] > 0


def test_large_pharmacies_report_a_lower_median_than_small_ones(dataset):
    world = dataset["world"]
    medians = {"large": [], "small": []}
    for pharmacy in world.pharmacies:
        if pharmacy.size not in medians:
            continue
        speed = tools.get_pharmacy_speed(pharmacy.pharmacy_id)
        if speed["median_hours_to_ready"] is not None:
            medians[pharmacy.size].append(speed["median_hours_to_ready"])
    assert medians["large"] and medians["small"]
    assert min(medians["large"]) < min(medians["small"])


# --- unknown ids ----------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "bad_id"),
    [
        ("get_patient_view", "PT99999"),
        ("get_pharmacy_records", "PT99999"),
        ("get_rx_history", "RX0000000"),
        ("get_pharmacy_speed", "PH999"),
    ],
)
def test_unknown_ids_return_an_error_dict_rather_than_raising(dataset, tool_name, bad_id):
    result = tools.TOOL_FUNCTIONS[tool_name](bad_id)
    assert isinstance(result, dict)
    assert "error" in result
    assert bad_id in result["error"]


def test_an_empty_id_is_an_error_not_an_empty_list(dataset):
    assert "error" in tools.get_patient_view("")
    assert "error" in tools.get_pharmacy_speed("")


# --- schemas --------------------------------------------------------------


def test_tool_schemas_cover_every_tool_exactly_once():
    names = [schema["name"] for schema in tools.TOOL_SCHEMAS]
    assert len(names) == 4
    assert set(names) == set(tools.TOOL_FUNCTIONS)


def test_tool_schemas_are_in_the_anthropic_format():
    for schema in tools.TOOL_SCHEMAS:
        assert set(schema) == {"name", "description", "input_schema"}
        assert schema["description"].strip()
        params = schema["input_schema"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        assert params["required"]
        for field in params["required"]:
            assert field in params["properties"]
            assert params["properties"][field]["type"] == "string"
            assert params["properties"][field]["description"].strip()


def test_every_schema_argument_matches_the_function_signature():
    import inspect

    for schema in tools.TOOL_SCHEMAS:
        signature = inspect.signature(tools.TOOL_FUNCTIONS[schema["name"]])
        assert list(signature.parameters) == list(schema["input_schema"]["properties"])
