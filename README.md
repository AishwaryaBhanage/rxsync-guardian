# RxSync Investigator

## Goal

A patient opens their pharmacy app and something is wrong: the same prescription
is listed twice, or one they were promised isn't there at all, or it has said
"ready" for three days. RxSync Investigator is an **LLM agent that investigates
that complaint**. It queries a handful of small read-only tools over the pharmacy
data, works out what actually happened, **cites the specific records it relied
on**, and drafts a reply for a human to approve — it never answers the patient
directly and never changes anything.

The point of the project is that the agent can be **graded**. A simulator builds a
synthetic pharmacy world and plants four kinds of fault in it on purpose —
duplicated records, dropped records, stale statuses, and scheduled refills that
never happen — writing every one to an answer key at `data/faults.jsonl`. So
"did the agent diagnose this correctly?" has a real answer, not a vibe.

**All data is synthetic**, generated with Faker. No real patient or prescription
data ever enters this repository.

## Running it

Requires [uv](https://docs.astral.sh/uv/). No database, no Docker.

```bash
uv sync                                                    # Python 3.12 + deps
uv run python -m simulator.generate --seed 42 --out data   # build the dataset
uv run pytest                                              # fast suite
```

The generator takes about a second and prints a summary: record counts, median
time-to-ready by pharmacy size, the fault tally, and every file it wrote. Output is
byte-identical for a given `(seed, --as-of)` pair, so regenerating never
invalidates results you have already measured.

### Tests

```bash
uv run pytest            # fast suite; full-scale checks deselected
uv run pytest -m ""      # everything (141 tests) — run this before committing
uv run pytest -m slow    # only the full-scale checks
uv run ruff check . && uv run ruff format --check .
```

The default run skips tests marked `slow` — the ones that build the whole
5,000-prescription world — so the edit loop stays under a second.

## What the generator produces

Everything lands in `data/`, which is gitignored and reproducible from the seed.

| File | Contents |
| --- | --- |
| `data/truth/*.csv` | The clean world: pharmacies, patients, prescriptions, fill events |
| `data/raw/pms_a.csv` | Pharmacy system A's export — flat, uppercase, `MM/DD/YYYY`, printed drug strings |
| `data/raw/pms_b.json` | System B's — nested JSON, ISO dates, structured drug fields |
| `data/raw/pms_c.txt` | System C's — pipe-delimited HL7-like `PID`/`RXO`/`STS` segments |
| `data/app_view.csv` | The three exports flattened into what the patient app shows, faults included |
| `data/faults.jsonl` | The answer key: one line per planted fault |

The three formats disagree on purpose. The same patient's date of birth is
`03/12/1955` in A, `1955-03-12` in B and `19550312` in C; the same drug is
`FLUOXETINE HCL 20 MG CAP` in one place and `fluoxetine 20 mg capsule` in another.
That mess is the agent's problem to reason through.

## The four tools

The agent investigates through `investigator/tools.py`. Each tool is read-only,
returns a compact list or dict, identifies people by `patient_id` and **never
returns a patient name**. An unknown id comes back as `{"error": "..."}` rather
than raising.

| Tool | Answers |
| --- | --- |
| `get_patient_view(patient_id)` | "What did the patient actually see?" — carries the faults |
| `get_pharmacy_records(patient_id)` | "What do the pharmacy's records say?" — the ground truth |
| `get_rx_history(rx_number)` | "What happened to this prescription, and when?" |
| `get_pharmacy_speed(pharmacy_id)` | "Is this wait normal for this pharmacy?" |

The first two are the heart of it. Here is a real duplicate, found by comparing
them — same prescription, same pharmacy, two spellings, two record ids to cite:

```
get_patient_view("PT00832")
  {"record_id": "A00006", "rx_number": "RX1000017", "drug_display": "FLUOXETINE HCL 20 MG CAP", ...}
  {"record_id": "A00007", "rx_number": "RX1000017", "drug_display": "fluoxetine 20 mg capsule", ...}

get_pharmacy_records("PT00832")
  {"rx_number": "RX1000017", "drug": "fluoxetine 20 mg capsule", "days_supply": 90, ...}
```

Two rows in the app, one prescription on file. `TOOL_SCHEMAS` exports the same four
tools in Anthropic tool-use format, ready to hand to the model.

## Layout

| Path | Holds |
| --- | --- |
| `simulator/` | The synthetic world, the three exports, the app view, the answer key |
| `investigator/` | `tools.py` (built) and `agent.py` (the loop — not built yet) |
| `evals/` | Grading diagnoses against the answer key — not built yet |
| `specs/` | One spec per feature, written before the code |
| `notebooks/` | Scratch exploration |
| `tests/` | 141 tests |
| `docs/` | `agent-log.md` — a running record of what went wrong and why |

## Status

The simulator and the four tools are done and tested. The agent loop, the eval
harness, and a spec for the investigator are not written yet.
