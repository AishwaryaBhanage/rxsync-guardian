"""Tests for the eval runner, driven by a scripted fake client.

No network. The fake lets each test dictate exactly what the "model" answers, so
the scoring, the budget stop and the report writing can all be checked against
known-correct and known-wrong answers.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from typing import Any

import pytest

from evals import run_eval as runner
from evals.make_tickets import NO_ISSUE, build_tickets
from evals.run_eval import (
    NO_DIAGNOSIS,
    RunConfig,
    load_tickets,
    run_eval,
    summarize,
    summary_table,
    write_report_json,
    write_report_md,
    write_results_csv,
)
from investigator import tools
from investigator.agent import HAIKU
from simulator.config import SimConfig
from simulator.generate import generate

# --- a scripted stand-in for anthropic.Anthropic --------------------------


@dataclass
class FakeUsage:
    input_tokens: int = 200
    output_tokens: int = 80
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


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


class _Messages:
    def __init__(self, answer):
        self._answer = answer
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append({"tools": [t["name"] for t in kwargs["tools"]]})
        return self._answer(kwargs)


class FakeClient:
    def __init__(self, answer):
        self.messages = _Messages(answer)


def _submit_block(**payload: Any) -> FakeToolUse:
    base = {
        "category": "duplicate",
        "rx_number": None,
        "evidence": [],
        "confidence": 0.8,
        "draft_reply": "We looked into it.",
    }
    base.update(payload)
    return FakeToolUse(name="submit_diagnosis", input=base, id="tu_submit")


def perfect_factory(tickets):
    """A model that always answers exactly right, citing the real rx number."""
    by_patient = {t["patient_id"]: t["ground_truth"] for t in tickets}

    def factory(model: str):
        def answer(kwargs):
            opening = kwargs["messages"][0]["content"]
            truth = next(gt for pid, gt in by_patient.items() if pid in opening)
            evidence = [truth["rx_number"]] if truth["rx_number"] else []
            return FakeResponse(
                [
                    _submit_block(
                        category=truth["category"],
                        rx_number=truth["rx_number"],
                        evidence=evidence,
                        confidence=0.95,
                    )
                ]
            )

        return FakeClient(answer)

    return factory


def always_factory(category: str, **payload: Any):
    """A model that always answers the same thing, right or wrong."""

    def factory(model: str):
        return FakeClient(
            lambda kwargs: FakeResponse([_submit_block(category=category, **payload)])
        )

    return factory


# --- fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("evaldata")
    generate(SimConfig(), out)
    tools.clear_cache()
    tools.set_data_dir(out)
    yield out
    tools.clear_cache()
    tools.set_data_dir(tools.DEFAULT_DATA_DIR)


@pytest.fixture(scope="module")
def tickets(dataset):
    return [t.as_record() for t in build_tickets(dataset)][:6]


TWO_RUNS = (
    RunConfig("haiku+tools", HAIKU, 8),
    RunConfig("haiku-no-tools", HAIKU, 0),
)


# --- the text-only baseline ------------------------------------------------


def test_the_baseline_is_offered_only_submit_diagnosis(dataset, tickets):
    """max_tool_calls=0 must mean the model never sees an investigative tool."""
    seen: list[list[str]] = []

    def factory(model: str):
        client = FakeClient(lambda kwargs: FakeResponse([_submit_block()]))
        seen.append(client.messages.calls)  # filled during the call
        return client

    run_eval(
        tickets[:2],
        dataset,
        runs=(RunConfig("baseline", HAIKU, 0),),
        client_factory=factory,
        progress=False,
    )
    offered = [names for calls in seen for call in calls for names in [call["tools"]]]
    assert offered
    for names in offered:
        assert names == ["submit_diagnosis"]


def test_the_tool_run_is_offered_every_tool(dataset, tickets):
    seen: list[list[str]] = []

    def factory(model: str):
        client = FakeClient(lambda kwargs: FakeResponse([_submit_block()]))
        seen.append(client.messages.calls)
        return client

    run_eval(
        tickets[:1],
        dataset,
        runs=(RunConfig("agent", HAIKU, 8),),
        client_factory=factory,
        progress=False,
    )
    names = seen[0][0]["tools"]
    assert set(names) == set(tools.TOOL_FUNCTIONS) | {"submit_diagnosis"}


def test_the_baseline_records_no_investigative_calls(dataset, tickets):
    results, _ = run_eval(
        tickets[:3],
        dataset,
        runs=(RunConfig("baseline", HAIKU, 0),),
        client_factory=always_factory("no_issue_found"),
        progress=False,
    )
    assert all(result.investigative_calls == 0 for result in results)
    assert all(result.tools_enabled is False for result in results)


# --- scoring --------------------------------------------------------------


def test_a_perfect_model_scores_one_hundred_percent(dataset, tickets):
    results, stopped = run_eval(
        tickets, dataset, runs=TWO_RUNS[:1], client_factory=perfect_factory(tickets), progress=False
    )
    assert stopped is None
    (summary,) = summarize(results)
    assert summary.category_accuracy == 1.0
    assert summary.rx_accuracy == 1.0
    assert summary.evidence_valid_rate is None or summary.evidence_valid_rate >= 0


def test_a_model_that_always_says_one_category_scores_that_share(dataset, tickets):
    results, _ = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS[:1],
        client_factory=always_factory("duplicate"),
        progress=False,
    )
    (summary,) = summarize(results)
    expected = sum(1 for t in tickets if t["ground_truth"]["category"] == "duplicate") / len(
        tickets
    )
    assert summary.category_accuracy == pytest.approx(expected, abs=0.001)


def test_rx_accuracy_skips_no_issue_tickets(dataset, tickets):
    results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS[:1], client_factory=perfect_factory(tickets), progress=False
    )
    (summary,) = summarize(results)
    expected_scored = sum(1 for t in tickets if t["ground_truth"]["category"] != NO_ISSUE)
    assert summary.rx_scored == expected_scored
    for result in results:
        if result.true_category == NO_ISSUE:
            assert result.rx_correct is None, "a clean ticket must not be scored on rx"


def test_a_wrong_rx_is_counted_wrong_but_category_can_still_be_right(dataset, tickets):
    fault = next(t for t in tickets if t["ground_truth"]["category"] != NO_ISSUE)
    results, _ = run_eval(
        [fault],
        dataset,
        runs=TWO_RUNS[:1],
        client_factory=always_factory(fault["ground_truth"]["category"], rx_number="RX0000000"),
        progress=False,
    )
    (result,) = results
    assert result.category_correct is True
    assert result.rx_correct is False


def test_no_diagnosis_counts_as_wrong(dataset, tickets):
    def factory(model: str):
        # A text-only turn: the loop ends with no diagnosis.
        return FakeClient(lambda kwargs: FakeResponse([], stop_reason="end_turn"))

    results, _ = run_eval(
        tickets[:2], dataset, runs=TWO_RUNS[:1], client_factory=factory, progress=False
    )
    for result in results:
        assert result.predicted_category == NO_DIAGNOSIS
        assert result.category_correct is False
        assert result.incomplete_reason
    (summary,) = summarize(results)
    assert summary.category_accuracy == 0.0
    assert summary.incomplete == 2


# --- evidence validity ---------------------------------------------------


def test_invented_evidence_makes_the_run_invalid(dataset, tickets):
    results, _ = run_eval(
        tickets[:2],
        dataset,
        runs=TWO_RUNS[:1],
        client_factory=always_factory("duplicate", evidence=["RX9999999"]),
        progress=False,
    )
    for result in results:
        assert result.evidence_ids == 1
        assert result.unverified_ids == 1
        assert result.evidence_valid is False
    (summary,) = summarize(results)
    assert summary.evidence_valid_rate == 0.0


def test_citing_nothing_is_recorded_separately_not_as_valid(dataset, tickets):
    """Vacuous validity would flatter the baseline, so it is counted apart."""
    results, _ = run_eval(
        tickets[:3],
        dataset,
        runs=(RunConfig("baseline", HAIKU, 0),),
        client_factory=always_factory("no_issue_found", evidence=[]),
        progress=False,
    )
    assert all(result.evidence_valid is None for result in results)
    (summary,) = summarize(results)
    assert summary.evidence_valid_rate is None
    assert summary.tickets_citing_nothing == 3
    assert summary.avg_evidence_ids == 0


# --- calibration and confusion -------------------------------------------


def test_calibration_buckets_by_confidence(dataset, tickets):
    results, _ = run_eval(
        tickets[:4],
        dataset,
        runs=TWO_RUNS[:1],
        client_factory=always_factory("duplicate", confidence=0.95),
        progress=False,
    )
    (summary,) = summarize(results)
    high = next(band for band in summary.calibration if band.low == 0.9)
    assert high.n == 4
    assert all(band.n == 0 for band in summary.calibration if band.low != 0.9)
    assert all(band.category_accuracy is None for band in summary.calibration if band.n == 0)


def test_the_confusion_matrix_totals_the_tickets(dataset, tickets):
    results, _ = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS[:1],
        client_factory=always_factory("dropped"),
        progress=False,
    )
    (summary,) = summarize(results)
    assert sum(sum(row.values()) for row in summary.confusion.values()) == len(tickets)
    # Everything was predicted "dropped", so only that column is populated.
    for truth, row in summary.confusion.items():
        for predicted, count in row.items():
            if count:
                assert predicted == "dropped", (truth, predicted)


# --- the budget stop ------------------------------------------------------


def test_the_eval_stops_once_the_budget_is_passed(dataset, tickets):
    results, stopped = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS,
        budget_usd=0.0005,  # one ticket costs more than this
        client_factory=always_factory("duplicate"),
        progress=False,
    )
    assert stopped is not None
    assert "cap $0.00" in stopped
    assert len(results) == 1, "it should stop immediately, not finish the run"


def test_partial_results_are_still_scored_and_reported(dataset, tickets, tmp_path):
    results, stopped = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS,
        budget_usd=0.002,
        client_factory=always_factory("duplicate"),
        progress=False,
    )
    assert stopped
    assert results
    summaries = summarize(results)
    report = write_report_md(summaries, stopped, tmp_path / "report.md")
    assert "Stopped early" in report.read_text()


def test_a_generous_budget_runs_everything(dataset, tickets):
    results, stopped = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS,
        budget_usd=100.0,
        client_factory=always_factory("duplicate"),
        progress=False,
    )
    assert stopped is None
    assert len(results) == len(tickets) * len(TWO_RUNS)


def test_the_default_budget_is_three_dollars():
    assert runner.BUDGET_USD == 3.0


# --- outputs -------------------------------------------------------------


def test_results_csv_has_one_row_per_run_and_ticket(dataset, tickets, tmp_path):
    results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS, client_factory=perfect_factory(tickets), progress=False
    )
    path = write_results_csv(results, tmp_path / "results.csv")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == len(tickets) * len(TWO_RUNS)
    assert {row["run"] for row in rows} == {run.label for run in TWO_RUNS}
    assert "ticket_id" in rows[0] and "cost_usd" in rows[0]
    # None renders as empty, not the string "None".
    assert "None" not in path.read_text()
    assert b"\r\n" not in path.read_bytes()


def test_report_json_carries_every_run_and_the_stop_reason(dataset, tickets, tmp_path):
    results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS, client_factory=perfect_factory(tickets), progress=False
    )
    path = write_report_json(summarize(results), "ran out of money", tmp_path / "report.json")
    payload = json.loads(path.read_text())
    assert payload["stopped_early"] == "ran out of money"
    assert [run["label"] for run in payload["runs"]] == [run.label for run in TWO_RUNS]
    first = payload["runs"][0]
    for key in ("category_accuracy", "rx_accuracy", "calibration", "confusion"):
        assert key in first


def test_report_md_has_a_table_matrix_and_comparison(dataset, tickets, tmp_path):
    results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS, client_factory=perfect_factory(tickets), progress=False
    )
    summaries = summarize(results)
    text = write_report_md(summaries, None, tmp_path / "report.md").read_text()
    assert "## Summary" in text
    assert "| run | model | tools |" in text
    assert "### Confusion matrix" in text
    assert "### Calibration" in text
    assert "text-only baseline" in text
    for run in TWO_RUNS:
        assert f"## {run.label}" in text


def test_the_comparison_paragraph_names_the_lift(dataset, tickets, tmp_path):
    """The baseline answers nothing right; the tool run answers everything right."""
    perfect = perfect_factory(tickets)

    def factory(model: str):
        return perfect(model)

    tool_results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS[:1], client_factory=factory, progress=False
    )
    base_results, _ = run_eval(
        tickets,
        dataset,
        runs=TWO_RUNS[1:],
        client_factory=always_factory("phantom_schedule", confidence=0.4),
        progress=False,
    )
    summaries = summarize(tool_results + base_results)
    text = write_report_md(summaries, None, tmp_path / "report.md").read_text()
    assert "lift of" in text
    assert "cited nothing" in text


def test_summary_table_renders_one_row_per_run(dataset, tickets):
    results, _ = run_eval(
        tickets, dataset, runs=TWO_RUNS, client_factory=perfect_factory(tickets), progress=False
    )
    table = summary_table(summarize(results))
    lines = table.splitlines()
    assert len(lines) == 2 + len(TWO_RUNS)
    for run in TWO_RUNS:
        assert any(run.label in line for line in lines)


# --- loading and the CLI -------------------------------------------------


def test_load_tickets_reads_the_jsonl(dataset, tmp_path):
    from evals.make_tickets import write_tickets

    path = write_tickets(build_tickets(dataset), tmp_path / "t.jsonl")
    loaded = load_tickets(path)
    assert len(loaded) == 48
    assert set(loaded[0]) == {"ticket_id", "text", "patient_id", "ground_truth"}


def test_the_cli_explains_itself_with_no_ticket_file(tmp_path, capsys):
    from evals.run_eval import main

    assert main(["--tickets", str(tmp_path / "nope.jsonl")]) == 1
    assert "No ticket set" in capsys.readouterr().err


def test_the_cli_rejects_an_unknown_run_label(dataset, tmp_path, capsys):
    from evals.make_tickets import write_tickets
    from evals.run_eval import main

    path = write_tickets(build_tickets(dataset), tmp_path / "t.jsonl")
    assert main(["--tickets", str(path), "--runs", "not-a-run"]) == 1
    assert "No run matched" in capsys.readouterr().err
