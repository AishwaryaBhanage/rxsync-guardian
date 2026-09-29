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
    NEUTRAL_TEMPLATES,
    NO_ISSUE,
    NO_ISSUE_TICKETS,
    TEMPLATES,
    TICKETS_PER_FAULT,
    TOTAL_TICKETS,
    build_tickets,
    main,
    neutralize,
    read_tickets,
    template_of,
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


@pytest.fixture(scope="module")
def neutral(tickets):
    """v2: same patients and ground truth, neutral wording."""
    return neutralize(tickets)


@pytest.fixture(params=["labelled", "neutral"])
def any_tickets(request, tickets, neutral):
    """Every leakage test runs against both versions."""
    return tickets if request.param == "labelled" else neutral


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


def test_no_ticket_text_contains_its_rx_number(any_tickets):
    for ticket in any_tickets:
        rx_number = ticket.ground_truth.rx_number
        if rx_number:
            assert rx_number not in ticket.text


def test_no_ticket_text_contains_any_rx_number(any_tickets, dataset):
    """Not just its own — no rx number at all, in any casing."""
    all_rx = {rx.rx_number for rx in dataset["world"].prescriptions}
    for ticket in any_tickets:
        upper = ticket.text.upper()
        assert "RX" not in upper, f"{ticket.ticket_id} mentions an rx-looking token"
        assert not any(rx in upper for rx in all_rx)


def test_no_ticket_text_contains_an_app_record_id(any_tickets):
    """Record ids look like A00006 / B12345 / C00964."""
    import re

    pattern = re.compile(r"\b[ABC]\d{5}\b")
    for ticket in any_tickets:
        assert not pattern.search(ticket.text), f"{ticket.ticket_id} leaks a record id"


def test_no_ticket_text_contains_a_category_name(any_tickets):
    for ticket in any_tickets:
        lowered = ticket.text.lower()
        for category in CATEGORIES:
            assert category not in lowered, f"{ticket.ticket_id} names {category}"


def test_no_ticket_text_contains_a_giveaway_word(any_tickets):
    """ "duplicate", "dropped", "stale", "phantom" would each hand over the answer."""
    for ticket in any_tickets:
        lowered = ticket.text.lower()
        for word in GIVEAWAY_WORDS:
            assert word not in lowered, f"{ticket.ticket_id} leaks {word!r}: {ticket.text!r}"


def test_no_ticket_text_contains_a_patient_id(any_tickets):
    import re

    # Not a bare "PT" search: "prescription" contains those letters.
    pattern = re.compile(r"\bPT\d{5}\b")
    for ticket in any_tickets:
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


# --- v2: neutral wording --------------------------------------------------


def test_neutral_keeps_the_patients_and_ground_truth_exactly(tickets, neutral):
    """The only variable between versions must be the wording."""
    assert len(neutral) == len(tickets)
    for before, after in zip(tickets, neutral, strict=True):
        assert after.ticket_id == before.ticket_id
        assert after.patient_id == before.patient_id
        assert after.ground_truth == before.ground_truth
        assert after.text != before.text


def test_every_neutral_template_is_used_by_at_least_two_categories(neutral):
    """The point of v2: wording must not correlate with the label."""
    by_template: dict[str, set[str]] = {}
    for ticket in neutral:
        template = template_of(ticket.text)
        assert template is not None, f"{ticket.ticket_id}: {ticket.text!r} matches no template"
        by_template.setdefault(template, set()).add(ticket.ground_truth.category)

    assert by_template, "no templates were used"
    for template, categories in by_template.items():
        assert len(categories) >= 2, (
            f"{template!r} only ever appears for {categories} — that is a label signal"
        )


def test_no_category_has_wording_of_its_own(neutral):
    """No template is exclusive to one category, checked from the other direction."""
    by_category: dict[str, set[str]] = {}
    for ticket in neutral:
        by_category.setdefault(ticket.ground_truth.category, set()).add(
            template_of(ticket.text) or ""
        )
    shapes = list(by_category.values())
    for index, first in enumerate(shapes):
        for second in shapes[index + 1 :]:
            assert first & second, "two categories share no wording at all"


def test_every_neutral_template_is_clean_and_names_the_drug():
    for template in NEUTRAL_TEMPLATES:
        lowered = template.lower()
        for word in GIVEAWAY_WORDS:
            assert word not in lowered, f"{template!r} leaks {word!r}"
        assert "{drug}" in template
        # Nothing that hints at a particular fault.
        for hint in ("twice", "two times", "missing", "refill", "ready", "status"):
            assert hint not in lowered, f"{template!r} hints at a fault via {hint!r}"


def test_neutral_templates_have_distinct_prefixes():
    """template_of matches on prefix and suffix, so they must be unambiguous."""
    prefixes = [t.partition("{drug}")[0] for t in NEUTRAL_TEMPLATES]
    assert len(set(prefixes)) == len(prefixes)


def test_neutralize_is_deterministic(tickets):
    assert [t.text for t in neutralize(tickets, 42)] == [t.text for t in neutralize(tickets, 42)]


def test_a_different_seed_rotates_the_templates_differently(tickets):
    assert [t.text for t in neutralize(tickets, 42)] != [t.text for t in neutralize(tickets, 7)]


def test_read_tickets_round_trips(tickets, tmp_path):
    path = write_tickets(tickets, tmp_path / "rt.jsonl")
    assert [t.as_record() for t in read_tickets(path)] == [t.as_record() for t in tickets]


def test_the_cli_neutral_style_needs_a_source(tmp_path, capsys):
    assert main(["--style", "neutral", "--out", str(tmp_path / "o.jsonl")]) == 1
    assert "--from" in capsys.readouterr().err


def test_the_cli_writes_a_neutral_set_from_a_source(tickets, tmp_path, capsys):
    source = write_tickets(tickets, tmp_path / "v1.jsonl")
    out = tmp_path / "v2.jsonl"
    assert main(["--style", "neutral", "--from", str(source), "--out", str(out)]) == 0
    assert "neutral tickets" in capsys.readouterr().out
    rewritten = read_tickets(out)
    assert [t.ground_truth for t in rewritten] == [t.ground_truth for t in tickets]


# --- no clean ticket may be answerable as a fault ------------------------


def test_no_clean_ticket_patient_holds_a_phantom_lookalike(tickets, dataset):
    """A no_issue_found ticket must have one defensible answer.

    An unplanted prescription with auto-refill on, refills left, a supply that
    ran out and no refill recorded is indistinguishable from a planted phantom.
    Grading a patient who holds one against "nothing is wrong" would punish a
    correct reading of the data.
    """
    from evals.make_tickets import load_dataset, looks_like_phantom

    data = load_dataset(dataset["dir"])
    lookalike_patients = {
        row["patient_id"] for row in data.prescriptions if looks_like_phantom(row, data)
    }
    assert lookalike_patients, "the dataset should contain some look-alikes to exclude"

    clean = [t for t in tickets if t.ground_truth.category == NO_ISSUE]
    assert clean
    for ticket in clean:
        assert ticket.patient_id not in lookalike_patients, (
            f"{ticket.ticket_id}: patient {ticket.patient_id} holds a prescription that "
            f"reads as a phantom refill, so the key is ambiguous"
        )


def test_the_lookalike_rule_matches_the_simulators_phantom_shape(dataset):
    """The exclusion must use the same four conditions the simulator plants on."""
    from evals.make_tickets import load_dataset, looks_like_phantom

    data = load_dataset(dataset["dir"])
    for row in data.prescriptions:
        if not looks_like_phantom(row, data):
            continue
        assert row["refill_on_schedule"] == "true"
        assert int(row["refills_authorized"]) > 0
        assert data.max_fill.get(row["rx_number"], 0) == 0
        assert data.last_collection[row["rx_number"]] is not None


def test_a_refilled_prescription_is_not_a_lookalike(dataset):
    from evals.make_tickets import load_dataset, looks_like_phantom

    data = load_dataset(dataset["dir"])
    refilled = [r for r in data.prescriptions if data.max_fill.get(r["rx_number"], 0) > 0]
    assert refilled
    assert not any(looks_like_phantom(row, data) for row in refilled)


def test_fault_tickets_are_unaffected_by_the_exclusion(tickets):
    """The exclusion only narrows the clean pool; the 40 fault tickets stand."""
    faults = [t for t in tickets if t.ground_truth.category != NO_ISSUE]
    assert len(faults) == TICKETS_PER_FAULT * len(FAULT_CATEGORIES) == 40
