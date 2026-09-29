"""Tests for the investigation loop, driven by a scripted fake client.

No network, no API key, no cost. The fake returns whatever sequence of turns a
test wants, which lets us drive the loop through paths a real model would only
reach occasionally: a bad category, a refusal, a budget exhaustion, evidence the
model made up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from investigator import agent, tools
from investigator.agent import (
    CATEGORIES,
    DEFAULT_MODEL,
    HAIKU,
    SONNET,
    SUBMIT_TOOL,
    cost_usd,
    investigate,
    resolve_model,
)
from simulator.config import SimConfig
from simulator.generate import generate

# --- a scripted stand-in for anthropic.Anthropic --------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeToolUse:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str = "tool_use"
    usage: FakeUsage = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.usage is None:
            self.usage = FakeUsage()


class FakeMessages:
    def __init__(self, turns: list[FakeResponse]):
        self._turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        # Snapshot `messages`: the loop appends to the same list after the call, so
        # storing the kwargs as-is would record the end state, not this request.
        recorded = dict(kwargs)
        recorded["messages"] = [dict(message) for message in kwargs["messages"]]
        self.calls.append(recorded)
        if not self._turns:
            raise AssertionError("the loop asked for more turns than the test scripted")
        return self._turns.pop(0)


class FakeClient:
    def __init__(self, turns: list[FakeResponse]):
        self.messages = FakeMessages(turns)


def _submit(**overrides: Any) -> FakeToolUse:
    payload = {
        "category": "duplicate",
        "rx_number": None,
        "evidence": [],
        "confidence": 0.8,
        "draft_reply": "We found the problem and are fixing it.",
    }
    payload.update(overrides)
    return FakeToolUse(name=SUBMIT_TOOL["name"], input=payload, id="tu_submit")


# --- real data, so the investigative tools return real rows ----------------


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("agentdata")
    result = generate(SimConfig.small(), out)
    tools.clear_cache()
    tools.set_data_dir(out)
    faults = [json.loads(line) for line in result.answer_key.read_text().splitlines()]
    duplicate = next(f for f in faults if f["type"] == "duplicate")
    by_rx = {rx.rx_number: rx for rx in result.world.prescriptions}
    yield {
        "duplicate_rx": duplicate["rx_number"],
        "duplicate_patient": by_rx[duplicate["rx_number"]].patient_id,
        "world": result.world,
    }
    tools.clear_cache()
    tools.set_data_dir(tools.DEFAULT_DATA_DIR)


# --- models and pricing ---------------------------------------------------


def test_default_model_is_the_canonical_haiku_id():
    assert DEFAULT_MODEL == HAIKU == "claude-haiku-4-5"


def test_the_dated_haiku_spelling_is_accepted_as_an_alias():
    assert resolve_model("claude-haiku-4-5-20251001") == HAIKU


def test_sonnet_is_supported():
    assert resolve_model(SONNET) == "claude-sonnet-5"


def test_an_unsupported_model_is_rejected_with_the_options():
    with pytest.raises(ValueError, match="unsupported model"):
        resolve_model("claude-opus-5")


def test_cost_matches_the_published_rates():
    # Haiku 4.5: $1.00 in / $5.00 out per million tokens.
    assert cost_usd(HAIKU, 1_000_000, 0) == pytest.approx(1.00)
    assert cost_usd(HAIKU, 0, 1_000_000) == pytest.approx(5.00)
    # Sonnet 5: $2.00 / $10.00.
    assert cost_usd(SONNET, 1_000_000, 0) == pytest.approx(2.00)
    assert cost_usd(SONNET, 0, 1_000_000) == pytest.approx(10.00)


def test_cache_tokens_are_priced_off_the_input_rate():
    write_only = cost_usd(HAIKU, 0, 0, cache_write_tokens=1_000_000)
    read_only = cost_usd(HAIKU, 0, 0, cache_read_tokens=1_000_000)
    assert write_only == pytest.approx(1.25)
    assert read_only == pytest.approx(0.10)


# --- the happy path -------------------------------------------------------


def test_a_single_tool_call_then_a_diagnosis(world):
    patient_id = world["duplicate_patient"]
    rx_number = world["duplicate_rx"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse(
                [_submit(rx_number=rx_number, evidence=[rx_number], confidence=0.9)],
                stop_reason="tool_use",
            ),
        ]
    )

    result = investigate("It shows twice", patient_id, client=client)

    assert result.incomplete_reason is None
    assert result.diagnosis.category == "duplicate"
    assert result.diagnosis.rx_number == rx_number
    assert result.diagnosis.confidence == 0.9
    assert result.unverified_evidence == []
    assert [call.name for call in result.trace.tool_calls] == [
        "get_patient_view",
        "submit_diagnosis",
    ]


def test_the_trace_records_inputs_outputs_tokens_cost_and_latency(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("It shows twice", patient_id, client=client)
    trace = result.trace

    assert trace.model == HAIKU
    assert trace.api_calls == 2
    first = trace.tool_calls[0]
    assert first.input == {"patient_id": patient_id}
    assert isinstance(first.output, list) and first.output, "the real tool ran"
    assert first.is_error is False
    # Two turns of the fake's fixed usage.
    assert trace.input_tokens == 200
    assert trace.output_tokens == 100
    assert trace.total_tokens == 300
    assert trace.cost_usd == pytest.approx(cost_usd(HAIKU, 200, 100))
    assert trace.cost_usd > 0
    assert trace.latency_s >= 0
    assert result.to_dict()["trace"]["cost_usd"] == trace.cost_usd


def test_the_real_tools_are_actually_called(world):
    """The loop dispatches through TOOL_FUNCTIONS, not a stub."""
    rx_number = world["duplicate_rx"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_rx_history", {"rx_number": rx_number})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("what happened", world["duplicate_patient"], client=client)
    history = result.trace.tool_calls[0].output
    assert history["rx_number"] == rx_number
    assert history["timeline"][0]["status"] == "received"


def test_parallel_tool_calls_come_back_in_one_user_message(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            FakeResponse(
                [
                    FakeToolUse("get_patient_view", {"patient_id": patient_id}, id="a"),
                    FakeToolUse("get_pharmacy_records", {"patient_id": patient_id}, id="b"),
                ]
            ),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", patient_id, client=client)
    assert len(result.trace.tool_calls) == 3  # two tools plus the submit

    second_request = client.messages.calls[1]
    tool_result_messages = [
        message
        for message in second_request["messages"]
        if message["role"] == "user"
        and isinstance(message["content"], list)
        and message["content"]
        and message["content"][0].get("type") == "tool_result"
    ]
    assert len(tool_result_messages) == 1
    assert len(tool_result_messages[0]["content"]) == 2


# --- what the model is offered -------------------------------------------


def test_the_model_is_offered_the_four_tools_plus_submit(world):
    client = FakeClient([FakeResponse([_submit()])])
    investigate("hello", world["duplicate_patient"], client=client)
    offered = {tool["name"] for tool in client.messages.calls[0]["tools"]}
    assert offered == set(tools.TOOL_FUNCTIONS) | {"submit_diagnosis"}


def test_the_system_prompt_carries_the_three_rules(world):
    client = FakeClient([FakeResponse([_submit()])])
    investigate("hello", world["duplicate_patient"], client=client)
    system = client.messages.calls[0]["system"].lower()
    assert "only evidence that a tool actually returned" in system
    assert "never guess an rx_number" in system
    assert "no medical advice" in system
    assert "low confidence" in system


def test_the_ticket_and_patient_id_reach_the_model(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient([FakeResponse([_submit()])])
    investigate("My statin is listed twice!", patient_id, client=client)
    opening = client.messages.calls[0]["messages"][0]["content"]
    assert "My statin is listed twice!" in opening
    assert patient_id in opening


# --- the tool-call budget ------------------------------------------------


def test_the_budget_is_capped_and_only_submit_is_offered_once_spent(world):
    patient_id = world["duplicate_patient"]
    probe = FakeToolUse("get_patient_view", {"patient_id": patient_id})
    # Three probes, a budget of two: the third turn must be submit-only.
    client = FakeClient(
        [
            FakeResponse([probe]),
            FakeResponse([probe]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", patient_id, client=client, max_tool_calls=2)

    investigative = [c for c in result.trace.tool_calls if c.name != "submit_diagnosis"]
    assert len(investigative) == 2, "the cap was exceeded"
    assert {t["name"] for t in client.messages.calls[2]["tools"]} == {"submit_diagnosis"}
    assert result.diagnosis is not None


def test_default_budget_is_eight():
    assert agent.MAX_TOOL_CALLS == 8


def test_never_submitting_yields_no_diagnosis_and_a_reason(world):
    patient_id = world["duplicate_patient"]
    probe = FakeToolUse("get_patient_view", {"patient_id": patient_id})
    client = FakeClient([FakeResponse([probe]) for _ in range(4)])
    result = investigate("check", patient_id, client=client, max_tool_calls=1)
    assert result.diagnosis is None
    assert result.incomplete_reason
    assert result.trace.cost_usd > 0, "a failed run still cost money"


def test_a_text_only_turn_ends_the_run_with_a_reason(world):
    client = FakeClient([FakeResponse([FakeText("I think it is fine.")], stop_reason="end_turn")])
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.diagnosis is None
    assert "without calling submit_diagnosis" in result.incomplete_reason


def test_a_refusal_is_reported_not_raised(world):
    client = FakeClient([FakeResponse([], stop_reason="refusal")])
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.diagnosis is None
    assert "declined" in result.incomplete_reason
    assert result.trace.stop_reason == "refusal"


# --- validation of what the model submits --------------------------------


def test_an_invalid_category_is_fed_back_and_can_be_corrected(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            FakeResponse([_submit(category="made_up_thing")]),
            FakeResponse([_submit(category="no_issue_found")]),
        ]
    )
    result = investigate("check", patient_id, client=client)
    assert result.diagnosis.category == "no_issue_found"
    rejected = result.trace.tool_calls[0]
    assert rejected.is_error
    assert "category must be one of" in rejected.output


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1.5},
        {"confidence": "high"},
        {"draft_reply": "   "},
        {"evidence": "A00001"},
        {"rx_number": 123},
    ],
)
def test_malformed_diagnoses_are_rejected(world, bad):
    client = FakeClient([FakeResponse([_submit(**bad)]), FakeResponse([_submit()])])
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.trace.tool_calls[0].is_error
    assert result.diagnosis is not None  # the retry succeeded


def test_every_category_is_accepted(world):
    for category in CATEGORIES:
        client = FakeClient([FakeResponse([_submit(category=category)])])
        result = investigate("check", world["duplicate_patient"], client=client)
        assert result.diagnosis.category == category


# --- evidence the model did not actually see ------------------------------


def test_invented_evidence_is_flagged_rather_than_trusted(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit(evidence=["A00001", "RX9999999"])]),
        ]
    )
    result = investigate("check", patient_id, client=client)
    # RX9999999 was never returned by a tool, so it must be called out.
    assert "RX9999999" in result.unverified_evidence


def test_evidence_seen_in_a_tool_result_is_not_flagged(world):
    patient_id = world["duplicate_patient"]
    seen = tools.get_patient_view(patient_id)[0]["record_id"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit(evidence=[seen])]),
        ]
    )
    result = investigate("check", patient_id, client=client)
    assert result.unverified_evidence == []


# --- tool errors ---------------------------------------------------------


def test_a_tool_error_is_passed_back_and_the_run_continues(world):
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": "PT99999"})]),
            FakeResponse([_submit(category="no_issue_found", confidence=0.2)]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)
    failed = result.trace.tool_calls[0]
    assert failed.is_error
    assert "error" in failed.output
    assert result.diagnosis.category == "no_issue_found"


def test_an_unknown_tool_name_is_a_tool_error_not_a_crash(world):
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("delete_everything", {})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)
    failed = result.trace.tool_calls[0]
    assert failed.is_error
    # The allowed-set check runs first, so an invented name is reported as
    # unavailable rather than nonexistent. That is the better answer anyway: it
    # does not tell the model which tools exist but were withheld.
    assert "delete_everything" in failed.output["error"]
    assert "not available" in failed.output["error"]


def test_bad_tool_arguments_are_a_tool_error_not_a_crash(world):
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"wrong_kwarg": "x"})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.trace.tool_calls[0].is_error
    assert "bad arguments" in result.trace.tool_calls[0].output["error"]


# --- no network ----------------------------------------------------------


def test_no_api_key_is_needed_when_a_client_is_injected(world, monkeypatch):
    """The whole test module must never touch the network."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = FakeClient([FakeResponse([_submit()])])
    assert investigate("check", world["duplicate_patient"], client=client).diagnosis


def test_submit_schema_is_in_the_anthropic_format():
    assert set(SUBMIT_TOOL) == {"name", "description", "input_schema"}
    schema = SUBMIT_TOOL["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "category",
        "rx_number",
        "evidence",
        "confidence",
        "draft_reply",
    }
    assert schema["properties"]["category"]["enum"] == list(CATEGORIES)


# --- evidence must be bare identifiers, not prose ------------------------


def test_the_evidence_schema_declares_the_id_pattern():
    items = SUBMIT_TOOL["input_schema"]["properties"]["evidence"]["items"]
    assert items["pattern"] == r"^(RX\d+|[ABC]\d{5})$"
    assert items["type"] == "string"


def test_the_evidence_description_forbids_sentences():
    description = SUBMIT_TOOL["input_schema"]["properties"]["evidence"]["description"]
    lowered = description.lower()
    assert "identifiers only, no sentences" in lowered
    assert "no commentary" in lowered
    # Shows the model the wrong and right shapes explicitly.
    assert "wrong:" in lowered and "right:" in lowered


@pytest.mark.parametrize("identifier", ["RX1000017", "RX1", "A00006", "B12345", "C00964"])
def test_valid_identifiers_are_accepted(world, identifier):
    client = FakeClient([FakeResponse([_submit(evidence=[identifier])])])
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.diagnosis.evidence == [identifier]


@pytest.mark.parametrize(
    "not_an_id",
    [
        # The exact shape both real models drifted into on the stale ticket.
        'C00008: patient view shows RX1000024 status "ready" at 2026-09-23T10:02:00',
        "pharmacy records show escitalopram 10 mg tablet prescribed 2026-07-25",
        "A00006 and A00007",
        "A123",  # too few digits
        "A000066",  # too many
        "PT00832",  # a patient id is not evidence
        "rx1000017",  # wrong case
        "",
    ],
)
def test_evidence_that_is_not_a_bare_identifier_is_rejected(world, not_an_id):
    client = FakeClient(
        [
            FakeResponse([_submit(evidence=[not_an_id])]),
            FakeResponse([_submit(evidence=["A00006"])]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)

    rejected = result.trace.tool_calls[0]
    assert rejected.is_error
    assert "must be one bare identifier" in rejected.output
    assert "not identifiers" in rejected.output
    # The retry with a real id succeeds, so the feedback is actionable.
    assert result.diagnosis.evidence == ["A00006"]


def test_the_rejection_names_the_offending_entry_but_clips_it(world):
    long_prose = "C00008: " + "x" * 200
    client = FakeClient(
        [
            FakeResponse([_submit(evidence=["A00006", long_prose])]),
            FakeResponse([_submit(evidence=["A00006"])]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)
    message = result.trace.tool_calls[0].output
    assert "C00008: xxx" in message  # named
    assert "..." in message  # clipped
    assert len(message) < 400  # not the whole 200-char blob
    assert "A00006" not in message.split("not identifiers:")[1]  # only offenders listed


def test_a_mix_of_good_and_bad_entries_is_rejected_wholesale(world):
    client = FakeClient(
        [
            FakeResponse([_submit(evidence=["A00006", "explanation text"])]),
            FakeResponse([_submit(evidence=["A00006"])]),
        ]
    )
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.trace.tool_calls[0].is_error
    assert result.diagnosis.evidence == ["A00006"]


def test_empty_evidence_is_still_allowed(world):
    """A no_issue_found verdict may legitimately cite nothing."""
    client = FakeClient([FakeResponse([_submit(category="no_issue_found", evidence=[])])])
    result = investigate("check", world["duplicate_patient"], client=client)
    assert result.diagnosis.evidence == []


def test_prose_evidence_no_longer_produces_a_false_unverified_flag(world):
    """The regression this fix exists for.

    Before the pattern check, a correct observation wrapped around a real id was
    accepted and then flagged unverified — a false positive that would corrupt an
    eval scoring evidence precision. Now it is rejected at the door.
    """
    patient_id = world["duplicate_patient"]
    seen = tools.get_patient_view(patient_id)[0]["record_id"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit(evidence=[f"{seen}: the app shows it twice"])]),
            FakeResponse([_submit(evidence=[seen])]),
        ]
    )
    result = investigate("check", patient_id, client=client)
    assert result.diagnosis.evidence == [seen]
    assert result.unverified_evidence == [], "a real id must not be flagged"


# --- the offered set is the authorization boundary ------------------------


def test_a_tool_that_was_not_offered_is_refused_not_executed(world):
    """The bug that silently gave the "no tools" eval baseline real data.

    A model can emit a tool_use block for a tool it was never given. Dispatching
    from TOOL_FUNCTIONS without checking what was offered executes it anyway.
    """
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            # max_tool_calls=0, so only submit_diagnosis was offered — yet the
            # model asks for patient data.
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", patient_id, client=client, max_tool_calls=0)

    refused = result.trace.tool_calls[0]
    assert refused.name == "get_patient_view"
    assert refused.is_error is True
    assert "not available" in refused.output["error"]
    # Crucially, no data came back.
    assert "record_id" not in str(refused.output)


def test_an_offered_tool_still_runs_normally(world):
    patient_id = world["duplicate_patient"]
    client = FakeClient(
        [
            FakeResponse([FakeToolUse("get_patient_view", {"patient_id": patient_id})]),
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", patient_id, client=client, max_tool_calls=8)
    ran = result.trace.tool_calls[0]
    assert ran.is_error is False
    assert isinstance(ran.output, list) and ran.output


def test_tools_are_refused_once_the_budget_is_spent(world):
    """After the budget, only submit is offered — so a late tool call is refused."""
    patient_id = world["duplicate_patient"]
    probe = FakeToolUse("get_patient_view", {"patient_id": patient_id})
    client = FakeClient(
        [
            FakeResponse([probe]),  # allowed: budget of 1
            FakeResponse([probe]),  # refused: budget spent
            FakeResponse([_submit()]),
        ]
    )
    result = investigate("check", patient_id, client=client, max_tool_calls=1)
    assert result.trace.tool_calls[0].is_error is False
    assert result.trace.tool_calls[1].is_error is True
    assert "not available" in result.trace.tool_calls[1].output["error"]


# --- the text-only run is told it has no tools ---------------------------


def test_a_text_only_run_gets_the_no_tools_prompt(world):
    client = FakeClient([FakeResponse([_submit()])])
    investigate("check", world["duplicate_patient"], client=client, max_tool_calls=0)
    system = client.messages.calls[0]["system"]
    assert system == agent.SYSTEM_PROMPT_TEXT_ONLY
    lowered = system.lower()
    assert "no tools and no access to any records" in lowered
    assert "`evidence` must be empty" in lowered
    assert "keep confidence low" in lowered
    # It must not advertise tools it cannot use.
    for name in tools.TOOL_FUNCTIONS:
        assert name not in system


def test_a_tool_run_gets_the_investigating_prompt(world):
    client = FakeClient([FakeResponse([_submit()])])
    investigate("check", world["duplicate_patient"], client=client, max_tool_calls=8)
    assert client.messages.calls[0]["system"] == agent.SYSTEM_PROMPT
    assert "get_patient_view" in client.messages.calls[0]["system"]
