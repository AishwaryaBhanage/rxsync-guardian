"""Build `evals/tickets.jsonl` — the graded ticket set, from templates, no LLM.

48 tickets: 10 for each of the four planted fault types, plus 8 where nothing is
wrong. Each one is phrased the way a patient would phrase it and names the drug
("my fluoxetine"), but **never** contains the rx number, the app record id, or the
name of the category — otherwise the agent could pattern-match the answer out of
the complaint instead of investigating.

Two eligibility rules keep every ticket single-answered:

* the patient has exactly one planted fault, so the complaint cannot be about two
  things at once;
* the patient has exactly one prescription for that drug, so "my fluoxetine"
  identifies one prescription unambiguously.

Everything is seeded, so the same `(seed, dataset)` always produces the same file.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

FAULT_CATEGORIES = ("duplicate", "dropped", "stale_status", "phantom_schedule")
NO_ISSUE = "no_issue_found"
CATEGORIES = (*FAULT_CATEGORIES, NO_ISSUE)

TICKETS_PER_FAULT = 10
NO_ISSUE_TICKETS = 8
TOTAL_TICKETS = TICKETS_PER_FAULT * len(FAULT_CATEGORIES) + NO_ISSUE_TICKETS

DEFAULT_SEED = 42

# Words that would hand the agent the answer. Checked by the tests, and asserted
# here at build time so a careless new template cannot ship.
GIVEAWAY_WORDS = (
    "duplicate",
    "duplicated",
    "dropped",
    "stale",
    "phantom",
    *CATEGORIES,
)

# Patient-voice templates. Deliberately none of them name the fault: a duplicate
# is "twice", a dropped prescription is "missing", a stale status is "hasn't
# updated", a phantom refill is "hasn't refilled".
TEMPLATES: dict[str, tuple[str, ...]] = {
    "duplicate": (
        (
            "I'm looking at my medication list and my {drug} is in there two times. "
            "I've only ever had one prescription for it — am I going to be charged twice?"
        ),
        (
            "Why does my {drug} show up twice in the app? It's the same medicine, just "
            "written a bit differently each time. Is one of them a mistake?"
        ),
        (
            "There are two entries for my {drug} in my prescription list. Should I be "
            "worried I'll be given double the amount?"
        ),
        (
            "My app is showing {drug} two separate times. I only take the one. Can "
            "someone sort this out?"
        ),
        (
            "Quick question — I can see two lines for {drug} in my account. Did the "
            "pharmacy enter it twice by accident?"
        ),
        (
            "The same {drug} prescription appears twice on my screen, with the name "
            "spelled differently. Which one is the real one?"
        ),
    ),
    "dropped": (
        (
            "My doctor told me they sent over a prescription for {drug} but I can't "
            "find it anywhere in the app. Has it gone missing?"
        ),
        (
            "I was expecting to see {drug} in my list and it just isn't there. Did the "
            "pharmacy ever receive it?"
        ),
        (
            "Where is my {drug}? My prescriber says it was sent across. Nothing shows "
            "up for me at all."
        ),
        "I can't see my {drug} in the app. Everything else of mine is listed. Is something wrong?",
        (
            "My {drug} has disappeared from my medication list. I definitely had it "
            "before. What happened to it?"
        ),
        (
            "The app doesn't show my {drug} prescription anywhere. Could you check "
            "whether it came through?"
        ),
    ),
    "stale_status": (
        (
            "The app has been telling me my {drug} is ready for collection for days "
            "now. I went in and the staff couldn't find anything waiting. Is the app "
            "out of date?"
        ),
        "My {drug} still says it's waiting for me, but I already have it. Why hasn't that updated?",
        (
            "I keep being told my {drug} is ready. I've been to the pharmacy twice and "
            "there's nothing there. What's going on?"
        ),
        (
            "The status on my {drug} hasn't changed in ages and I don't think it's "
            "right anymore. Can you check it?"
        ),
        (
            "My app says {drug} is ready but that isn't what the pharmacy told me. "
            "Which one should I believe?"
        ),
        (
            "Is the status showing for my {drug} actually accurate? It's said the same "
            "thing for a long time and I think it's wrong."
        ),
    ),
    "phantom_schedule": (
        (
            "I have automatic refills turned on for my {drug} but nothing has come "
            "through. Shouldn't it have refilled by now?"
        ),
        "My {drug} is meant to refill on its own and it hasn't. Do I need to request it myself?",
        (
            "I signed up for automatic refills on my {drug} and I'm running low. No "
            "refill seems to have happened at all."
        ),
        "Nothing has happened with my automatic {drug} refill. Has it stopped working?",
        (
            "I thought my {drug} refilled automatically, but I've had no new supply. "
            "Could you check the setting?"
        ),
        (
            "My auto-refill for {drug} doesn't seem to be doing anything — no new "
            "refill has been processed."
        ),
    ),
    NO_ISSUE: (
        "Just checking — is my {drug} ready to collect yet?",
        "Can you tell me where my {drug} is up to? I'd like to know when to come in.",
        "Hi, when should I expect my {drug} to be ready?",
        "Is there any update on my {drug}? No rush, I'm just planning my week.",
        "I wanted to confirm my {drug} prescription is all set. Anything I need to do?",
        "How many refills do I have left on my {drug}?",
        "Quick check on my {drug} — is everything in order?",
        "Could you confirm my {drug} is on file with you? Thanks.",
    ),
}


@dataclass(frozen=True)
class GroundTruth:
    category: str
    # None for no_issue_found: no prescription is at fault.
    rx_number: str | None


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    text: str
    patient_id: str
    ground_truth: GroundTruth

    def as_record(self) -> dict[str, object]:
        return {
            "ticket_id": self.ticket_id,
            "text": self.text,
            "patient_id": self.patient_id,
            "ground_truth": asdict(self.ground_truth),
        }


@dataclass(frozen=True)
class _Dataset:
    prescriptions: list[dict[str, str]]
    faults: list[dict[str, object]]
    events_by_rx: dict[str, int]


def load_dataset(data_dir: Path) -> _Dataset:
    prescriptions = list(
        csv.DictReader(
            (data_dir / "truth" / "prescriptions.csv").read_text(encoding="utf-8").splitlines()
        )
    )
    faults = [
        json.loads(line)
        for line in (data_dir / "faults.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event_counts: Counter[str] = Counter()
    for row in csv.DictReader(
        (data_dir / "truth" / "fill_events.csv").read_text(encoding="utf-8").splitlines()
    ):
        event_counts[row["rx_number"]] += 1
    return _Dataset(prescriptions, faults, dict(event_counts))


def build_tickets(data_dir: Path, seed: int = DEFAULT_SEED) -> list[Ticket]:
    """The full ticket set for one dataset and seed."""
    data = load_dataset(data_dir)
    rx_by_number = {row["rx_number"]: row for row in data.prescriptions}

    faults_per_patient: Counter[str] = Counter()
    for fault in data.faults:
        row = rx_by_number.get(str(fault["rx_number"]))
        if row:
            faults_per_patient[row["patient_id"]] += 1

    # (patient, drug) -> how many prescriptions, so "my fluoxetine" is unambiguous.
    drug_counts: Counter[tuple[str, str]] = Counter(
        (row["patient_id"], row["drug_name"]) for row in data.prescriptions
    )

    def unambiguous(row: dict[str, str]) -> bool:
        return (
            faults_per_patient[row["patient_id"]] <= 1
            and drug_counts[(row["patient_id"], row["drug_name"])] == 1
        )

    eligible: dict[str, list[dict[str, str]]] = defaultdict(list)
    for fault in sorted(data.faults, key=lambda f: str(f["fault_id"])):
        category = str(fault["type"])
        row = rx_by_number.get(str(fault["rx_number"]))
        if row is None or category not in FAULT_CATEGORIES:
            continue
        if faults_per_patient[row["patient_id"]] != 1:
            continue  # more than one planted fault: no single right answer
        if not unambiguous(row):
            continue
        eligible[category].append(row)

    faulty_patients = {
        rx_by_number[str(f["rx_number"])]["patient_id"]
        for f in data.faults
        if str(f["rx_number"]) in rx_by_number
    }
    clean = [
        row
        for row in data.prescriptions
        if row["patient_id"] not in faulty_patients
        and unambiguous(row)
        # Something must have happened to it, or "is it ready?" has no answer.
        and data.events_by_rx.get(row["rx_number"], 0) > 0
    ]
    clean.sort(key=lambda row: row["rx_number"])

    rng = random.Random(f"{seed}:tickets")
    tickets: list[Ticket] = []
    used_patients: set[str] = set()

    for category in FAULT_CATEGORIES:
        for row in _pick(eligible[category], TICKETS_PER_FAULT, rng, used_patients, category):
            tickets.append(_make_ticket(category, row, rng, len(tickets) + 1))

    for row in _pick(clean, NO_ISSUE_TICKETS, rng, used_patients, NO_ISSUE):
        tickets.append(_make_ticket(NO_ISSUE, row, rng, len(tickets) + 1))

    return tickets


def write_tickets(tickets: list[Ticket], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        for ticket in tickets:
            handle.write(json.dumps(ticket.as_record(), sort_keys=True))
            handle.write("\n")
    return path


# --- internals ------------------------------------------------------------


def _pick(
    pool: list[dict[str, str]],
    count: int,
    rng: random.Random,
    used_patients: set[str],
    label: str,
) -> list[dict[str, str]]:
    """Seeded pick of `count` rows, at most one per patient across the whole set.

    One ticket per patient keeps the graded cases independent: two tickets about
    the same person would share the same tool output, so an error on one would
    correlate with an error on the other.
    """
    # At most one candidate per patient, both against patients already used by an
    # earlier category and within this pool — a patient with two clean
    # prescriptions would otherwise be picked twice in the same call.
    seen = set(used_patients)
    candidates: list[dict[str, str]] = []
    for row in pool:
        if row["patient_id"] in seen:
            continue
        seen.add(row["patient_id"])
        candidates.append(row)

    if len(candidates) < count:
        raise ValueError(
            f"only {len(candidates)} unambiguous {label} candidates on unused "
            f"patients, need {count}"
        )
    indexes = list(range(len(candidates)))
    rng.shuffle(indexes)
    chosen = [candidates[index] for index in sorted(indexes[:count])]
    used_patients.update(row["patient_id"] for row in chosen)
    return chosen


def _make_ticket(category: str, row: dict[str, str], rng: random.Random, number: int) -> Ticket:
    template = rng.choice(TEMPLATES[category])
    text = template.format(drug=row["drug_name"])
    _assert_no_giveaway(text, category)
    return Ticket(
        ticket_id=f"T{number:03d}",
        text=text,
        patient_id=row["patient_id"],
        ground_truth=GroundTruth(
            category=category,
            rx_number=None if category == NO_ISSUE else row["rx_number"],
        ),
    )


def _assert_no_giveaway(text: str, category: str) -> None:
    lowered = text.lower()
    for word in GIVEAWAY_WORDS:
        if word in lowered:
            raise AssertionError(f"the {category} template leaks the word {word!r}: {text!r}")


# --- CLI ------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the graded ticket set.")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("evals") / "tickets.jsonl")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--samples", type=int, default=5, help="how many to print")
    args = parser.parse_args(argv)

    if not (args.data / "faults.jsonl").exists():
        print(
            f"No dataset in {args.data}/. Run:\n"
            f"  uv run python -m simulator.generate --seed 42 --out {args.data}",
            file=sys.stderr,
        )
        return 1

    tickets = build_tickets(args.data, args.seed)
    path = write_tickets(tickets, args.out)

    tally = Counter(ticket.ground_truth.category for ticket in tickets)
    print(f"Wrote {len(tickets)} tickets to {path}")
    for category in CATEGORIES:
        print(f"  {category:<20}{tally[category]:>4}")

    if args.samples:
        rng = random.Random(f"{args.seed}:samples")
        shown = sorted(
            rng.sample(tickets, min(args.samples, len(tickets))), key=lambda t: t.ticket_id
        )
        print(f"\n{'=' * 78}\n{len(shown)} sample tickets\n{'=' * 78}")
        for ticket in shown:
            truth = ticket.ground_truth
            print(f"\n{ticket.ticket_id}  patient {ticket.patient_id}")
            print(f'  "{ticket.text}"')
            print(f"  -> truth: {truth.category}, rx={truth.rx_number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
