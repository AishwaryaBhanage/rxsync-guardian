# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Synthetic data only. Never use or generate real patient information.
- Every feature starts from a spec in `specs/`. Read the spec before planning.
- Plan first, then code in small steps. One feature per change.
- Run `uv run pytest` after changes; never leave failing tests.
- Run `uv run pytest -m ""` before every commit — that runs everything, including
  the full-scale checks the default run deselects.
- Never commit `.env` or any secrets.
- Explain any non-obvious code with a short comment.
- Never return patient names from an investigator tool. Identify people by
  `patient_id` only.

## Goal

**RxSync Investigator** is an LLM agent that investigates a patient complaint
("my prescription shows up twice", "the app says ready but it isn't") using small
pandas tools over synthetic pharmacy data, cites the records it used, and drafts a
reply for a human to approve. It never acts on the patient's behalf.

The data comes with an answer key. `simulator` plants four kinds of fault on
purpose and records each one in `data/faults.jsonl`, so the agent's diagnoses are
graded against ground truth rather than judged by eye.

## Project state

- **`simulator` is complete** — `specs/01-simulator.md`, all four steps.
  `uv run python -m simulator.generate --seed 42 --out data` writes the truth, the
  three PMS exports, `data/app_view.csv` and the answer key in about a second.
- **`investigator/tools.py` is complete** — the four tools below, plus
  `TOOL_SCHEMAS` (Anthropic tool-use format) and a `TOOL_FUNCTIONS` dispatch map.
- **`investigator/agent.py` is a placeholder.** No agent loop, no model choice, no
  API calls yet. The `anthropic` SDK is a dependency but unused.
- **`evals/` is an empty package.** Grading against `data/faults.jsonl` is not built.
- There is no spec for the investigator yet. Write `specs/03-investigator.md`
  before building the agent, per the Rules above.

## Architecture

```
simulator  ──>  data/app_view.csv  ──>  investigator  ──>  evals
   │            data/truth/*.csv         tools.py           graded against
   └──> data/faults.jsonl ─────────────────────────────────>  the answer key
```

- **simulator** — builds a synthetic pharmacy world (50 pharmacies, 2,000
  patients, 5,000 prescriptions, ~24k fill events), exports it in three
  disagreeing PMS formats, plants faults, and writes both the flattened
  `app_view.csv` and the answer key.
- **investigator** — `tools.py` reads the generated CSVs; `agent.py` will hold the
  loop that drives Claude over those tools.
- **evals** — will grade diagnoses against `data/faults.jsonl`.
- **specs** — one spec per feature; written before the code.
- **notebooks** — scratch exploration, not imported by anything.
- **docs** — `agent-log.md`, a running record of what went wrong and why.

### The four tools

All in `investigator/tools.py`. All read-only, all identified by id, never by name.

| Tool | Returns |
| --- | --- |
| `get_patient_view(patient_id)` | The rows the patient app showed — **faults included**: a duplicate appears twice with different spellings, a dropped prescription is missing, a stale row shows an old status. |
| `get_pharmacy_records(patient_id)` | What the pharmacies' own records actually say. Comparing this against the app view *is* the investigation. |
| `get_rx_history(rx_number)` | The status timeline from fill events; empty means on file but nothing dispensed. |
| `get_pharmacy_speed(pharmacy_id)` | Median wall-clock hours received → ready, with size, area and Sunday closure for context. |

Tool contracts, each covered by a test in `tests/test_tools.py`:

- **No patient names.** `data/truth/patients.csv` is the only file with names and
  `tools.py` never opens it.
- **Unknown ids return `{"error": ...}`**, never raise. A tool error is something
  the agent should read and recover from.
- **`refill_on_schedule` is `None` when the source never sent it** (PMS A and C),
  not `False`. Unknown and false are different answers.
- **Data is loaded once**, cached per directory; `set_data_dir()` re-points it for
  tests.
- A patient counts as known if the *pharmacy records* mention them, not the app
  view — so a patient whose only prescription was dropped returns `[]`, and that
  empty view is itself the finding.

### Simulator invariants worth knowing before editing it

- **Determinism is an acceptance criterion.** Output must be byte-identical for a
  given `(seed, as_of)` pair. Draws come from stage-scoped RNGs
  (`_rng(config, "stage")` in `simulator/world.py`) so adding a stage cannot
  reshuffle earlier ones; Faker is locale-pinned and instance-seeded; CSVs are
  written with `lineterminator="\n"`. Breaking any of these breaks a test.
- **The 90-day window ends at `config.as_of`, never `today()`** — default
  `2026-09-27`, see `simulator/config.py`.
- **All timing goes through `simulator/schedule.py`.** `advance_business_hours`
  consumes open hours only, which is the single reason a Sunday-closed pharmacy
  never produces a Sunday timestamp. Do not compute event times elsewhere.
- Processing-time jitter is lognormal with mu=0 (median exactly 1.0), so the
  large-faster-than-small ordering holds by construction.
- `World` is a plain frozen data holder; lookups belong in the `index_*` helpers
  in `world.py` so exporters do not rescan ~24k events per prescription.
- **`app_view.csv` is built from the same `build_records(world, pharmacies, plan)`
  the three exports use**, so it cannot drift from them. Add a column there, not a
  second code path.

## Commands

```bash
uv sync                              # install deps into .venv (Python 3.12)
uv run python -m simulator.generate --seed 42 --out data   # regenerate data/
uv run pytest                        # fast suite (slow tests deselected)
uv run pytest -m ""                  # everything; run before committing
uv run pytest -m slow                # only the full-scale checks
uv run pytest tests/test_tools.py    # one file
uv run pytest -k duplicate           # one topic
uv run ruff check .                  # lint
uv run ruff format .                 # format
```

Always invoke Python through `uv run`; there is no activated-venv workflow here.

There is no database and no Docker. Everything reads and writes CSV, JSON and
JSONL files under `data/`, all of which are gitignored and regenerable from
`(seed, as_of)`.

## Environment

`.env` (gitignored, copied from `.env.example`) holds `ANTHROPIC_API_KEY` for when
the agent is built. Nothing reads it yet. `python-dotenv` is installed for that
purpose.

## Tests

141 tests. The default `uv run pytest` deselects `@pytest.mark.slow` — the
full-scale checks that build the whole 5,000-prescription world — to keep the edit
loop fast, because a PostToolUse hook runs the fast suite on every `.py`, `.yaml`,
`.yml` or `.toml` edit. `uv run pytest -m ""` runs all of them and is the
pre-commit rule.

Tests that need generated data build their own small world into `tmp_path` with
`simulator.generate.generate(SimConfig.small(), out)` rather than depending on
`data/` existing.

## Layout conventions

Flat package layout at the repo root, not `src/`. `pyproject.toml` intentionally
has no `[build-system]`, so uv treats the root as a virtual project and installs
dependencies without installing the local packages. Tests can import those
packages only because `[tool.pytest.ini_options] pythonpath = ["."]` puts the repo
root on `sys.path` — `tests/` has no `__init__.py`, so pytest would otherwise
insert `tests/` instead of the root. Keep that setting when adding test
directories.
