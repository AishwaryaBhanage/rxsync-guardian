# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

- Synthetic data only. Never use or generate real patient information.
- Every feature starts from a spec in `specs/`. Read the spec before planning.
- Plan first, then code in small steps. One feature per change.
- Run `uv run pytest` after changes; never leave failing tests.
- Run `uv run pytest -m slow` before every commit — full-scale checks are
  deselected from the default run, so they are easy to skip silently.
- Never commit `.env` or any secrets.
- Explain any non-obvious code with a short comment.

## Project state

`simulator` is partly built: step 1 of `specs/01-simulator.md` (the truth — 50
pharmacies, 2,000 patients, 5,000 prescriptions, ~24k fill events) generates and
writes `data/truth/*.csv`. Still to come: the three PMS exports (step 2), the
planted faults and `data/faults.jsonl` (step 3), and the CLI (step 4) — so
`python -m simulator.generate` does not exist yet; call `build_world` and
`write_truth` directly until it does.

Every other package under Architecture is still a one-line docstring in
`__init__.py`.

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

## Commands

```bash
docker compose up -d                 # PostgreSQL 16; must be running for tests
uv sync                              # install deps into .venv (Python 3.12)
uv run pytest                        # fast suite (slow tests deselected)
uv run pytest -m slow                # full-scale checks only; run before committing
uv run pytest tests/test_db.py::test_select_one   # single test
uv run python -m simulator.generate --seed 42 --out data   # regenerate data/ (step 4)
uv run ruff check .                  # lint
uv run ruff format .                 # format
docker compose exec postgres psql -U rxsync -d rxsync   # psql shell
docker compose down                  # stop db, keep the named volume
docker compose down -v               # stop db and delete stored data
```

Always invoke Python through `uv run`; there is no activated-venv workflow here.

## Environment

`DATABASE_URL` and `POSTGRES_PASSWORD` come from `.env` (gitignored, copied from
`.env.example`). Compose reads `.env` itself and fails loudly via
`${POSTGRES_PASSWORD:?...}` if it is missing; test code loads it with
`dotenv.load_dotenv()`, which does not override variables already set in the
environment.

**The host port is 5433, not 5432.** A PostgreSQL server runs natively on this
machine and owns `127.0.0.1:5432` / `[::1]:5432`, so `localhost:5432` reaches
that server, not the container — connecting there fails with
`role "rxsync" does not exist`. Inside the container and on the compose network
the port is still 5432; only the host mapping is 5433. If a connection error
mentions a missing role, check the port before suspecting the container.

## Architecture

Packages are stages of one pipeline, in dependency order:

`simulator` → `normalizer` → `dedup` / `desync` → `explainer` → `api` → `dashboard`

- **simulator** — generates synthetic prescription records with Faker, deliberately
  including the messiness the detectors exist to catch: near-duplicate spellings,
  and the same prescription diverging across sources (quantity, directions, fill
  status, prescriber).
- **normalizer** — canonicalizes drug, patient, prescriber, and pharmacy fields so
  records from different sources become comparable. Both detectors depend on its
  output shape; changing it affects them together.
- **dedup** — finds the same prescription recorded more than once.
- **desync** — finds one prescription whose attributes disagree across sources.
- **explainer** — turns a flag into a plain-language reason a pharmacist can act on.
  Every flag is expected to carry one.
- **copilot** — LLM-assisted review helpers layered on the detectors' output.
- **evals** — accuracy and regression harness for the detectors.
- **specs** — detection rules and data contracts (the rules live here, not inline
  in detector code).
- **infra** — deployment and database setup.

**All data is synthetic.** Real patient or prescription data must never enter this
repository. `data/raw/` is gitignored except for `.gitkeep`, so generated datasets
stay local.

## Layout conventions

Flat package layout at the repo root, not `src/`. `pyproject.toml` intentionally
has no `[build-system]`, so uv treats the root as a virtual project and installs
dependencies without installing the local packages. Tests can import those
packages only because `[tool.pytest.ini_options] pythonpath = ["."]` puts the repo
root on `sys.path` — `tests/` has no `__init__.py`, so pytest would otherwise
insert `tests/` instead of the root. Keep that setting when adding test
directories.
