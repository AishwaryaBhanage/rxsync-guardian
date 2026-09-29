"""Tests for the graded ticket set.

The ticket text is the agent's only input, so the important property is negative:
it must not contain the answer. If a complaint said "duplicate" or quoted the rx
number, a model could score well by pattern-matching instead of investigating,
and the eval would measure nothing.
"""

from __future__ import annotations

import json

import pytest

from evals.make_tickets import (
    CATEGORIES,
    FAULT_CATEGORIES,
    GIVEAWAY_WORDS,
    NO_ISSUE,
    NO_ISSUE_TICKETS,
    TEMPLATES,
    TICKETS_PER_FAULT,
    TOTAL_TICKETS,
    build_tickets,
    main,
    write_tickets,
)
from simulator.config import SimConfig
from simulator.generate import generate


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    """A full-scale world: the small one has too few unambiguous faults for 10 each."""
    out = tmp_path_factory.mktemp("ticketdata")
    result = generate(SimConfig(), out)
    return {
        "dir": out,
        "world": result.world,
        "faults": [json.loads(line) for line in result.answer_key.read_text().splitlines()],
    }


@pytest.fixture(scope="module")
def tickets(dataset):
    return build_tickets(dataset["dir"])


# --- counts ---------------------------------------------------------------


def test_there_are_forty_eight_tickets(tickets):
    assert len(tickets) == TOTAL_TICKETS == 48


def test_ten_of_each_fault_type_and_eight_clean(tickets):
    tally: dict[str, int] = {}
    for ticket in tickets:
        tally[ticket.ground_truth.category] = tally.get(ticket.ground_truth.category, 0) + 1
    for category in FAULT_CATEGORIES:
        assert tally[category] == TICKETS_PER_FAULT == 10, category
    assert tally[NO_ISSUE] == NO_ISSUE_TICKETS == 8


def test_ticket_ids_are_unique_and_sequential(tickets):
    ids = [ticket.ticket_id for ticket in tickets]
    assert ids == [f"T{index:03d}" for index in range(1, len(ids) + 1)]


# --- the text must not leak the answer ------------------------------------


def test_no_ticket_text_contains_its_rx_number(tickets):
    for ticket in tickets:
        rx_number = ticket.ground_truth.rx_number
        if rx_number:
            assert rx_number not in ticket.text


def test_no_ticket_text_contains_any_rx_number(tickets, dataset):
    """Not just its own — no rx number at all, in any casing."""
    all_rx = {rx.rx_number for rx in dataset["world"].prescriptions}
    for ticket in tickets:
        upper = ticket.text.upper()
        assert "RX" not in upper, f"{ticket.ticket_id} mentions an rx-looking token"
        assert not any(rx in upper for rx in all_rx)


def test_no_ticket_text_contains_an_app_record_id(tickets):
    """Record ids look like A00006 / B12345 / C00964."""
    import re

    pattern = re.compile(r"\b[ABC]\d{5}\b")
    for ticket in tickets:
        assert not pattern.search(ticket.text), f"{ticket.ticket_id} leaks a record id"


def test_no_ticket_text_contains_a_category_name(tickets):
    for ticket in tickets:
        lowered = ticket.text.lower()
        for category in CATEGORIES:
            assert category not in lowered, f"{ticket.ticket_id} names {category}"


def test_no_ticket_text_contains_a_giveaway_word(tickets):
    """ "duplicate", "dropped", "stale", "phantom" would each hand over the answer."""
    for ticket in tickets:
        lowered = ticket.text.lower()
        for word in GIVEAWAY_WORDS:
            assert word not in lowered, f"{ticket.ticket_id} leaks {word!r}: {ticket.text!r}"


def test_no_ticket_text_contains_a_patient_id(tickets):
    import re

    # Not a bare "PT" search: "prescription" contains those letters.
    pattern = re.compile(r"\bPT\d{5}\b")
    for ticket in tickets:
        assert ticket.patient_id not in ticket.text
        assert not pattern.search(ticket.text)


def test_every_template_is_clean():
    """Checked directly, so an unused template cannot hide a leak."""
    for category, templates in TEMPLATES.items():
        for template in templates:
            lowered = template.lower()
            for word in GIVEAWAY_WORDS:
                assert word not in lowered, f"{category} template leaks {word!r}"
            assert "{drug}" in template, f"{category} template never names the drug"


# --- the text must name the drug ------------------------------------------


def test_every_ticket_names_the_drug_the_patient_would_say(tickets, dataset):
    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    drugs = {drug.drug_id: drug for drug in dataset["world"].drugs}
    for ticket in tickets:
        rx_number = ticket.ground_truth.rx_number
        if rx_number is None:
            # A clean ticket still names some drug of that patient's.
            patient_drugs = {
                drugs[rx.drug_id].name
                for rx in dataset["world"].prescriptions
                if rx.patient_id == ticket.patient_id
            }
            assert any(name in ticket.text for name in patient_drugs)
        else:
            expected = drugs[by_rx[rx_number].drug_id].name
            assert expected in ticket.text, f"{ticket.ticket_id} should mention {expected}"


# --- ground truth is real -------------------------------------------------


def test_every_ground_truth_rx_number_exists_in_the_truth_data(tickets, dataset):
    known = {rx.rx_number for rx in dataset["world"].prescriptions}
    for ticket in tickets:
        rx_number = ticket.ground_truth.rx_number
        if rx_number is not None:
            assert rx_number in known, f"{ticket.ticket_id} cites unknown {rx_number}"


def test_fault_tickets_match_the_answer_key(tickets, dataset):
    """The ground truth is copied from faults.jsonl, not re-derived."""
    key = {str(f["rx_number"]): str(f["type"]) for f in dataset["faults"]}
    for ticket in tickets:
        if ticket.ground_truth.category == NO_ISSUE:
            continue
        rx_number = ticket.ground_truth.rx_number
        assert key[rx_number] == ticket.ground_truth.category


def test_clean_tickets_have_no_planted_fault(tickets, dataset):
    faulty = {str(f["rx_number"]) for f in dataset["faults"]}
    by_patient = {}
    for rx in dataset["world"].prescriptions:
        by_patient.setdefault(rx.patient_id, []).append(rx.rx_number)
    for ticket in tickets:
        if ticket.ground_truth.category != NO_ISSUE:
            continue
        assert ticket.ground_truth.rx_number is None
        assert not any(rx in faulty for rx in by_patient[ticket.patient_id])


def test_the_patient_id_belongs_to_the_ground_truth_prescription(tickets, dataset):
    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    for ticket in tickets:
        rx_number = ticket.ground_truth.rx_number
        if rx_number:
            assert by_rx[rx_number].patient_id == ticket.patient_id


# --- exactly one correct answer ------------------------------------------


def test_no_patient_appears_with_more_than_one_planted_fault(tickets, dataset):
    by_rx = {rx.rx_number: rx for rx in dataset["world"].prescriptions}
    faults_per_patient: dict[str, int] = {}
    for fault in dataset["faults"]:
        row = by_rx.get(str(fault["rx_number"]))
        if row:
            faults_per_patient[row.patient_id] = faults_per_patient.get(row.patient_id, 0) + 1
    for ticket in tickets:
        assert faults_per_patient.get(ticket.patient_id, 0) <= 1, (
            f"{ticket.ticket_id}: patient has two faults, so the ticket is ambiguous"
        )


def test_the_named_drug_identifies_one_prescription_for_that_patient(tickets, dataset):
    drugs = {drug.drug_id: drug for drug in dataset["world"].drugs}
    holdings: dict[tuple[str, str], int] = {}
    for rx in dataset["world"].prescriptions:
        key = (rx.patient_id, drugs[rx.drug_id].name)
        holdings[key] = holdings.get(key, 0) + 1
    for ticket in tickets:
        named = [
            name
            for (patient, name), count in holdings.items()
            if patient == ticket.patient_id and name in ticket.text
        ]
        assert named, f"{ticket.ticket_id} names no drug of that patient's"
        for name in named:
            assert holdings[(ticket.patient_id, name)] == 1, (
                f"{ticket.ticket_id}: patient has two {name} prescriptions"
            )


def test_each_ticket_is_a_distinct_patient(tickets):
    """One ticket per patient keeps the graded cases independent."""
    patients = [ticket.patient_id for ticket in tickets]
    assert len(patients) == len(set(patients))


# --- determinism ----------------------------------------------------------


def test_the_same_seed_gives_the_same_tickets(dataset):
    first = build_tickets(dataset["dir"], seed=42)
    second = build_tickets(dataset["dir"], seed=42)
    assert [t.as_record() for t in first] == [t.as_record() for t in second]


def test_a_different_seed_gives_different_tickets(dataset):
    baseline = build_tickets(dataset["dir"], seed=42)
    other = build_tickets(dataset["dir"], seed=7)
    assert [t.as_record() for t in baseline] != [t.as_record() for t in other]
    # Still a valid set, though.
    assert len(other) == TOTAL_TICKETS


def test_the_written_file_is_byte_identical_across_runs(dataset, tmp_path):
    first = write_tickets(build_tickets(dataset["dir"]), tmp_path / "a.jsonl")
    second = write_tickets(build_tickets(dataset["dir"]), tmp_path / "b.jsonl")
    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()


# --- the file and the CLI -------------------------------------------------


def test_the_file_is_one_json_object_per_line(dataset, tmp_path):
    path = write_tickets(build_tickets(dataset["dir"]), tmp_path / "tickets.jsonl")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == TOTAL_TICKETS
    for line in lines:
        record = json.loads(line)
        assert set(record) == {"ticket_id", "text", "patient_id", "ground_truth"}
        assert set(record["ground_truth"]) == {"category", "rx_number"}
        assert record["ground_truth"]["category"] in CATEGORIES


def test_the_cli_writes_the_file_and_prints_a_tally(dataset, tmp_path, capsys):
    out = tmp_path / "tickets.jsonl"
    assert main(["--data", str(dataset["dir"]), "--out", str(out), "--samples", "2"]) == 0
    printed = capsys.readouterr().out
    assert "Wrote 48 tickets" in printed
    assert "sample tickets" in printed
    for category in CATEGORIES:
        assert category in printed
    assert out.exists()


def test_the_cli_explains_itself_when_there_is_no_dataset(tmp_path, capsys):
    assert main(["--data", str(tmp_path / "missing")]) == 1
    assert "No dataset" in capsys.readouterr().err


# --- template variety -----------------------------------------------------


def test_each_category_has_several_distinct_templates():
    for category, templates in TEMPLATES.items():
        assert len(templates) >= 6, f"{category} has too few variants"
        assert len(set(templates)) == len(templates)


def test_the_generated_tickets_use_more_than_one_wording(tickets):
    for category in CATEGORIES:
        texts = [ticket.text for ticket in tickets if ticket.ground_truth.category == category]
        # Strip the drug so only the template shape remains.
        shapes = {text.split(" ", 3)[-1][:40] for text in texts}
        assert len(shapes) > 1, f"every {category} ticket reads the same"
