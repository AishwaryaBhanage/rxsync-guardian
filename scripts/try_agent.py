"""Run the investigator against three real tickets and print the trace.

Picks one planted `duplicate`, one `stale_status` and one `dropped` out of
`data/faults.jsonl`, writes the complaint each one would produce, and sends it
through `investigate()`. The answer key says what the cause really was, so each
run prints whether the agent's category was right.

This one costs real money — it calls the API. Usage:

    uv run python scripts/try_agent.py
    uv run python scripts/try_agent.py --model claude-sonnet-5
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Allow `python scripts/try_agent.py` from the repo root without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from investigator import tools
from investigator.agent import DEFAULT_MODEL, PRICING, Investigation, investigate

# One complaint per fault type, phrased the way a patient would.
TICKETS = {
    "duplicate": (
        "I think I'm being charged twice. The same medication is showing up two "
        "times in my list and I only ever had one prescription for it. Can you "
        "check?"
    ),
    "stale_status": (
        "The app has said my prescription is waiting for me for days now. I went "
        "to the pharmacy and they looked confused. Is the app wrong?"
    ),
    "dropped": (
        "My doctor said they sent a prescription over but I can't see it anywhere "
        "in the app. Has it been lost?"
    ),
}


def pick_tickets(data_dir: Path) -> list[dict[str, str]]:
    """One fault of each type, paired with the patient who would complain."""
    faults = [
        json.loads(line)
        for line in (data_dir / "faults.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    patient_of = {
        row["rx_number"]: row["patient_id"]
        for row in csv.DictReader(
            (data_dir / "truth" / "prescriptions.csv").read_text(encoding="utf-8").splitlines()
        )
    }

    chosen = []
    for fault_type, ticket_text in TICKETS.items():
        fault = next((f for f in faults if f["type"] == fault_type), None)
        if fault is None:
            print(f"  ! no {fault_type} fault in the answer key, skipping")
            continue
        chosen.append(
            {
                "fault_type": fault_type,
                "rx_number": fault["rx_number"],
                "patient_id": patient_of[fault["rx_number"]],
                "ticket": ticket_text,
            }
        )
    return chosen


def print_investigation(ticket: dict[str, str], result: Investigation) -> None:
    rule = "=" * 78
    print(f"\n{rule}")
    print(f"TICKET  patient {ticket['patient_id']}   (planted: {ticket['fault_type']})")
    print(rule)
    print(f"  {ticket['ticket']}\n")

    print("TOOL CALLS")
    for index, call in enumerate(result.trace.tool_calls, start=1):
        marker = "ERR" if call.is_error else "ok "
        print(f"  {index}. [{marker}] {call.name}({_short(call.input)})")
        print(f"        -> {_short(call.output, limit=300)}")

    print("\nDIAGNOSIS")
    if result.diagnosis is None:
        print(f"  none — {result.incomplete_reason}")
    else:
        diagnosis = result.diagnosis
        correct = "CORRECT" if diagnosis.category == ticket["fault_type"] else "WRONG"
        print(f"  category      {diagnosis.category}   [{correct}]")
        print(f"  rx_number     {diagnosis.rx_number}")
        expected = ticket["rx_number"]
        if diagnosis.rx_number and diagnosis.rx_number != expected:
            print(f"                (answer key says {expected})")
        print(f"  confidence    {diagnosis.confidence}")
        print(f"  evidence      {', '.join(diagnosis.evidence) or '(none)'}")
        if result.unverified_evidence:
            print(
                f"  UNVERIFIED    {', '.join(result.unverified_evidence)}  <- not seen in any tool result"
            )
        print(f"\n  draft reply:\n    {diagnosis.draft_reply}")

    trace = result.trace
    print(
        f"\nTRACE  {trace.api_calls} api calls, {len(trace.tool_calls)} tool calls, "
        f"{trace.total_tokens:,} tokens "
        f"({trace.input_tokens:,} in / {trace.output_tokens:,} out), "
        f"${trace.cost_usd:.6f}, {trace.latency_s:.2f}s"
    )


def _short(value: object, limit: int = 160) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICING))
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args(argv)

    if not (args.data / "faults.jsonl").exists():
        print(
            f"No dataset in {args.data}/. Run:\n"
            f"  uv run python -m simulator.generate --seed 42 --out {args.data}",
            file=sys.stderr,
        )
        return 1

    tools.set_data_dir(args.data)
    tickets = pick_tickets(args.data)
    print(f"Investigating {len(tickets)} tickets with {args.model}")

    results = []
    for ticket in tickets:
        try:
            result = investigate(ticket["ticket"], ticket["patient_id"], args.model)
        except Exception as exc:  # noqa: BLE001 - a script: report and keep going
            print(f"\n!! {ticket['fault_type']} failed: {type(exc).__name__}: {exc}")
            continue
        print_investigation(ticket, result)
        results.append((ticket, result))

    if not results:
        return 1

    right = sum(1 for t, r in results if r.diagnosis and r.diagnosis.category == t["fault_type"])
    total_cost = sum(r.trace.cost_usd for _, r in results)
    print(f"\n{'=' * 78}")
    print(
        f"SUMMARY  {right}/{len(results)} categories correct   "
        f"total ${total_cost:.6f}   model {args.model}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
