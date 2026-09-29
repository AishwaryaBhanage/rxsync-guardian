"""Run the graded tickets through the agent and score the result.

Three runs: the agent on Haiku 4.5, the agent on Sonnet 5, and a **text-only
baseline** — Haiku with no investigative tools, answering from the complaint alone.
The baseline needs no special code path: `max_tool_calls=0` means the very first
turn is offered only `submit_diagnosis`, so the model must guess from the text.
That is the number the agent has to beat to justify its cost.

Spending is checked after every ticket and the whole eval stops the moment the
running total passes `--budget` (default $3), leaving partial results scored and
written rather than throwing them away.

What is measured, per run:

* **category accuracy** — against the answer key; a ticket with no diagnosis is wrong.
* **rx_number accuracy** — only where the true category implies one prescription,
  so `no_issue_found` tickets are excluded rather than counted as free wins.
* **evidence validity** — every cited id appeared in that run's own tool output.
  Reported over the tickets that cited anything, alongside how many cited nothing,
  because a run that cites nothing is vacuously valid and that would flatter the
  baseline.
* **calibration** — accuracy inside each confidence band.
* **average tool calls, cost per ticket, latency.**
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from evals.make_tickets import CATEGORIES
from investigator import tools
from investigator.agent import HAIKU, MAX_TOOL_CALLS, SONNET, investigate

BUDGET_USD = 3.0
NO_DIAGNOSIS = "none"

# Confidence bands for the calibration table.
CONFIDENCE_BANDS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


@dataclass(frozen=True)
class RunConfig:
    label: str
    model: str
    # 0 means the text-only baseline: only submit_diagnosis is ever offered.
    max_tool_calls: int

    @property
    def tools_enabled(self) -> bool:
        return self.max_tool_calls > 0


RUNS: tuple[RunConfig, ...] = (
    RunConfig("haiku+tools", HAIKU, MAX_TOOL_CALLS),
    RunConfig("sonnet+tools", SONNET, MAX_TOOL_CALLS),
    RunConfig("haiku-no-tools", HAIKU, 0),
)


class _ClientFactory(Protocol):
    def __call__(self, model: str) -> Any: ...


@dataclass(frozen=True)
class TicketResult:
    run: str
    model: str
    tools_enabled: bool
    ticket_id: str
    patient_id: str
    true_category: str
    true_rx: str | None
    predicted_category: str
    predicted_rx: str | None
    confidence: float | None
    category_correct: bool
    # None when the true category implies no single prescription.
    rx_correct: bool | None
    evidence_ids: int
    unverified_ids: int
    # None when the run cited nothing, so "valid" would be vacuous.
    evidence_valid: bool | None
    tool_calls: int
    investigative_calls: int
    total_tokens: int
    cost_usd: float
    latency_s: float
    incomplete_reason: str | None


@dataclass(frozen=True)
class Band:
    low: float
    high: float
    n: int
    category_accuracy: float | None


@dataclass(frozen=True)
class RunSummary:
    label: str
    model: str
    tools_enabled: bool
    tickets: int
    category_accuracy: float
    rx_accuracy: float | None
    rx_scored: int
    evidence_valid_rate: float | None
    tickets_citing_nothing: int
    avg_evidence_ids: float
    avg_tool_calls: float
    avg_investigative_calls: float
    total_cost_usd: float
    cost_per_ticket_usd: float
    avg_latency_s: float
    incomplete: int
    calibration: list[Band] = field(default_factory=list)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)


class BudgetExceeded(RuntimeError):
    """Raised once the running total passes the cap, to stop the whole eval."""


def load_tickets(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_eval(
    tickets: list[dict[str, Any]],
    data_dir: Path,
    runs: tuple[RunConfig, ...] = RUNS,
    budget_usd: float = BUDGET_USD,
    client_factory: _ClientFactory | None = None,
    progress: bool = True,
) -> tuple[list[TicketResult], str | None]:
    """Score every ticket under every run. Returns results and a stop reason."""
    tools.set_data_dir(data_dir)
    results: list[TicketResult] = []
    spent = 0.0
    stopped: str | None = None

    try:
        for run in runs:
            for index, ticket in enumerate(tickets, start=1):
                client = client_factory(run.model) if client_factory else None
                investigation = investigate(
                    ticket["text"],
                    ticket["patient_id"],
                    run.model,
                    client=client,
                    max_tool_calls=run.max_tool_calls,
                )
                result = _score(run, ticket, investigation)
                results.append(result)
                spent += result.cost_usd

                if progress:
                    mark = "ok " if result.category_correct else "MISS"
                    print(
                        f"  [{run.label}] {index:>2}/{len(tickets)} "
                        f"{result.ticket_id} {mark} "
                        f"{result.true_category} -> {result.predicted_category} "
                        f"(${spent:.3f})",
                        flush=True,
                    )

                if spent > budget_usd:
                    raise BudgetExceeded(
                        f"stopped after {len(results)} tickets: spent ${spent:.2f}, "
                        f"cap ${budget_usd:.2f}"
                    )
    except BudgetExceeded as exc:
        stopped = str(exc)

    return results, stopped


def _score(run: RunConfig, ticket: dict[str, Any], investigation: Any) -> TicketResult:
    truth = ticket["ground_truth"]
    true_category = str(truth["category"])
    true_rx = truth["rx_number"]
    diagnosis = investigation.diagnosis
    trace = investigation.trace

    predicted_category = diagnosis.category if diagnosis else NO_DIAGNOSIS
    predicted_rx = diagnosis.rx_number if diagnosis else None
    evidence = diagnosis.evidence if diagnosis else []
    unverified = investigation.unverified_evidence

    # Only score the prescription where the truth names one.
    rx_correct = None if true_rx is None else predicted_rx == true_rx
    evidence_valid = None if not evidence else not unverified

    investigative = sum(1 for call in trace.tool_calls if call.name != "submit_diagnosis")
    return TicketResult(
        run=run.label,
        model=run.model,
        tools_enabled=run.tools_enabled,
        ticket_id=str(ticket["ticket_id"]),
        patient_id=str(ticket["patient_id"]),
        true_category=true_category,
        true_rx=true_rx,
        predicted_category=predicted_category,
        predicted_rx=predicted_rx,
        confidence=diagnosis.confidence if diagnosis else None,
        category_correct=predicted_category == true_category,
        rx_correct=rx_correct,
        evidence_ids=len(evidence),
        unverified_ids=len(unverified),
        evidence_valid=evidence_valid,
        tool_calls=len(trace.tool_calls),
        investigative_calls=investigative,
        total_tokens=trace.total_tokens,
        cost_usd=trace.cost_usd,
        latency_s=trace.latency_s,
        incomplete_reason=investigation.incomplete_reason,
    )


def summarize(results: list[TicketResult]) -> list[RunSummary]:
    summaries = []
    for label in dict.fromkeys(result.run for result in results):
        rows = [result for result in results if result.run == label]
        summaries.append(_summarize_run(label, rows))
    return summaries


def _summarize_run(label: str, rows: list[TicketResult]) -> RunSummary:
    scored_rx = [row for row in rows if row.rx_correct is not None]
    cited = [row for row in rows if row.evidence_valid is not None]
    total_cost = sum(row.cost_usd for row in rows)
    return RunSummary(
        label=label,
        model=rows[0].model,
        tools_enabled=rows[0].tools_enabled,
        tickets=len(rows),
        category_accuracy=_rate(row.category_correct for row in rows),
        rx_accuracy=_rate(bool(row.rx_correct) for row in scored_rx) if scored_rx else None,
        rx_scored=len(scored_rx),
        evidence_valid_rate=(_rate(bool(row.evidence_valid) for row in cited) if cited else None),
        tickets_citing_nothing=len(rows) - len(cited),
        avg_evidence_ids=_mean(row.evidence_ids for row in rows),
        avg_tool_calls=_mean(row.tool_calls for row in rows),
        avg_investigative_calls=_mean(row.investigative_calls for row in rows),
        total_cost_usd=round(total_cost, 6),
        cost_per_ticket_usd=round(total_cost / len(rows), 6),
        avg_latency_s=round(_mean(row.latency_s for row in rows), 2),
        incomplete=sum(1 for row in rows if row.incomplete_reason),
        calibration=_calibration(rows),
        confusion=_confusion(rows),
    )


def _calibration(rows: list[TicketResult]) -> list[Band]:
    bands = []
    for low, high in CONFIDENCE_BANDS:
        inside = [
            row for row in rows if row.confidence is not None and low <= row.confidence < high
        ]
        bands.append(
            Band(
                low=low,
                high=min(high, 1.0),
                n=len(inside),
                category_accuracy=(
                    _rate(row.category_correct for row in inside) if inside else None
                ),
            )
        )
    return bands


def _confusion(rows: list[TicketResult]) -> dict[str, dict[str, int]]:
    """truth -> predicted -> count, including a column for no diagnosis at all."""
    columns = [*CATEGORIES, NO_DIAGNOSIS]
    matrix = {truth: dict.fromkeys(columns, 0) for truth in CATEGORIES}
    for row in rows:
        predicted = row.predicted_category if row.predicted_category in columns else NO_DIAGNOSIS
        matrix[row.true_category][predicted] += 1
    return matrix


def _rate(values) -> float:
    items = list(values)
    return round(sum(1 for value in items if value) / len(items), 4) if items else 0.0


def _mean(values) -> float:
    items = list(values)
    return round(statistics.mean(items), 3) if items else 0.0


# --- writing the outputs --------------------------------------------------

RESULT_COLUMNS = tuple(TicketResult.__dataclass_fields__)


def write_results_csv(results: list[TicketResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(RESULT_COLUMNS)
        for result in results:
            row = asdict(result)
            writer.writerow(["" if row[name] is None else row[name] for name in RESULT_COLUMNS])
    return path


def write_report_json(summaries: list[RunSummary], stopped: str | None, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "stopped_early": stopped,
        "runs": [asdict(summary) for summary in summaries],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def summary_table(summaries: list[RunSummary]) -> str:
    header = (
        "| run | model | tools | n | category acc | rx acc | evidence valid "
        "| avg tool calls | $/ticket | total $ | avg latency |"
    )
    divider = "| " + " | ".join(["---"] * 11) + " |"
    lines = [header, divider]
    for s in summaries:
        lines.append(
            f"| {s.label} | {s.model} | {'yes' if s.tools_enabled else 'no'} | {s.tickets} "
            f"| {_pct(s.category_accuracy)} | {_pct(s.rx_accuracy)} ({s.rx_scored}) "
            f"| {_pct(s.evidence_valid_rate)} | {s.avg_tool_calls} "
            f"| ${s.cost_per_ticket_usd:.4f} | ${s.total_cost_usd:.3f} | {s.avg_latency_s}s |"
        )
    return "\n".join(lines)


def write_report_md(summaries: list[RunSummary], stopped: str | None, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [
        "# Investigator eval",
        "",
        (
            f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} against "
            f"`evals/tickets.jsonl`."
        ),
        "",
    ]
    if stopped:
        parts += [f"> **Stopped early:** {stopped}", ""]

    parts += ["## Summary", "", summary_table(summaries), "", _comparison(summaries), ""]

    for summary in summaries:
        parts += [
            f"## {summary.label}",
            "",
            "### Confusion matrix",
            "",
            _confusion_table(summary.confusion),
            "",
            "### Calibration",
            "",
            "| confidence | n | category accuracy |",
            "| --- | --- | --- |",
        ]
        for band in summary.calibration:
            parts.append(
                f"| {band.low:.2f}–{band.high:.2f} | {band.n} | {_pct(band.category_accuracy)} |"
            )
        parts += [
            "",
            (
                f"Cited nothing on {summary.tickets_citing_nothing} of "
                f"{summary.tickets} tickets; average {summary.avg_evidence_ids} ids "
                f"cited. {summary.incomplete} run(s) produced no diagnosis."
            ),
            "",
        ]

    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _confusion_table(matrix: dict[str, dict[str, int]]) -> str:
    columns = [*CATEGORIES, NO_DIAGNOSIS]
    lines = [
        "| true \\ predicted | " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * (len(columns) + 1)) + " |",
    ]
    for truth in CATEGORIES:
        counts = matrix.get(truth, {})
        cells = [str(counts.get(column, 0)) for column in columns]
        lines.append(f"| **{truth}** | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _comparison(summaries: list[RunSummary]) -> str:
    """A short paragraph putting the tool runs against the text-only baseline."""
    baseline = next((s for s in summaries if not s.tools_enabled), None)
    with_tools = [s for s in summaries if s.tools_enabled]
    if baseline is None or not with_tools:
        return "No baseline/tool pair was run, so there is nothing to compare."

    best = max(with_tools, key=lambda s: s.category_accuracy)
    lift = best.category_accuracy - baseline.category_accuracy
    ratio = (
        best.cost_per_ticket_usd / baseline.cost_per_ticket_usd
        if baseline.cost_per_ticket_usd
        else float("inf")
    )
    sentences = [
        (
            f"The text-only baseline ({baseline.model}, no tools) gets "
            f"{_pct(baseline.category_accuracy)} of categories right from the "
            f"complaint alone, at ${baseline.cost_per_ticket_usd:.4f} per ticket."
        ),
        (
            f"The best tool-using run ({best.label}) reaches "
            f"{_pct(best.category_accuracy)}, a lift of {lift * 100:+.1f} points "
            f"for {ratio:.1f}x the cost per ticket."
        ),
    ]
    if baseline.rx_accuracy is not None and best.rx_accuracy is not None:
        sentences.append(
            f"On naming the right prescription the gap is starker: "
            f"{_pct(baseline.rx_accuracy)} for the baseline against "
            f"{_pct(best.rx_accuracy)} with tools — guessing an rx number from a "
            f"complaint that never mentions one is close to impossible."
        )
    sentences.append(
        f"The baseline cited nothing on {baseline.tickets_citing_nothing} of "
        f"{baseline.tickets} tickets, which is the honest outcome: with no tool "
        f"output there is no evidence to cite, so its evidence validity is not "
        f"comparable to a run that actually looked."
    )
    return " ".join(sentences)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent against the ticket set.")
    parser.add_argument("--tickets", type=Path, default=Path("evals") / "tickets.jsonl")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("evals"))
    parser.add_argument("--budget", type=float, default=BUDGET_USD)
    parser.add_argument("--limit", type=int, default=None, help="only the first N tickets")
    parser.add_argument("--runs", nargs="*", default=None, help="subset of run labels to execute")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if not args.tickets.exists():
        print(
            f"No ticket set at {args.tickets}. Run:\n  uv run python -m evals.make_tickets",
            file=sys.stderr,
        )
        return 1

    tickets = load_tickets(args.tickets)
    if args.limit:
        tickets = tickets[: args.limit]
    runs = RUNS if not args.runs else tuple(r for r in RUNS if r.label in args.runs)
    if not runs:
        print(f"No run matched {args.runs}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(
            f"{len(tickets)} tickets x {len(runs)} runs "
            f"({', '.join(r.label for r in runs)}), budget ${args.budget:.2f}"
        )

    results, stopped = run_eval(
        tickets, args.data, runs=runs, budget_usd=args.budget, progress=not args.quiet
    )
    if not results:
        print("No results produced.", file=sys.stderr)
        return 1

    summaries = summarize(results)
    write_results_csv(results, args.out_dir / "results.csv")
    write_report_json(summaries, stopped, args.out_dir / "report.json")
    write_report_md(summaries, stopped, args.out_dir / "report.md")

    if not args.quiet:
        print(f"\n{summary_table(summaries)}\n")
        if stopped:
            print(f"STOPPED EARLY: {stopped}\n")
        print(
            f"Wrote {args.out_dir / 'results.csv'}, {args.out_dir / 'report.json'}, "
            f"{args.out_dir / 'report.md'}"
        )
    # 2, not 0, when the budget cut the eval short: a caller should be able to
    # tell a complete run from a truncated one without parsing the report.
    return 0 if stopped is None else 2


if __name__ == "__main__":
    sys.exit(main())
