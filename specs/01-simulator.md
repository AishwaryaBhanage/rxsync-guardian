# Spec 01: Pharmacy simulator

## User story

As the builder of RxSync Guardian, I want a fake but realistic pharmacy world, with mistakes planted on purpose and an answer key, so I can measure whether my detectors catch them.

## Inputs

- CLI: `uv run python -m simulator.generate --seed 42 --out data [--as-of YYYY-MM-DD]`
- Same seed **and same `--as-of`** must always produce identical files.
- `--as-of` defaults to `2026-09-27` and anchors the 90-day window.

## Decisions

Recorded 2026-09-27, before implementation.

- **Date anchor.** The 90-day window ends at `--as-of`, default `2026-09-27` — not `today()`.
  Anchoring to the run date would contradict "same seed → identical files" and would stale the
  answer key overnight.
- **Test scale.** `tests/test_simulator.py` runs a reduced world by default. The checks that need
  full volume — fault rates within ±20%, large-vs-small median time to ready, the 30-second
  ceiling — carry `@pytest.mark.slow` and are deselected unless `-m slow` is passed. Rationale: the
  PostToolUse hook runs the suite on every code edit, and a 5,000-prescription generation per edit
  is too slow a loop.
- **Pre-commit.** `uv run pytest -m slow` before every commit, so the full-scale criteria are never
  skipped silently. CI will run them later.
- **Hook scope.** The test hook fires only for `.py`, `.yaml`, `.yml` and `.toml` edits; editing
  specs or docs no longer triggers a generation.

## The fake world (truth)

- 50 pharmacies: id, name, size (small/medium/large), area (urban/rural), opening hours (some closed Sundays), PMS type (A, B or C).
- Processing speed depends on the pharmacy. All figures below are **wall-clock
  median hours from `received` to `ready`** (elapsed time, not working time):

  | size | target | acceptable | measured |
  | --- | --- | --- | --- |
  | large | ~2 h | 1-4 h | 2.40 h |
  | medium | ~6 h | 3-10 h | 6.23 h |
  | small | ~18 h, ready next day | 12-30 h | 22.77 h |

  Internally the clock advances only while the pharmacy is open, so the Sunday
  closure rule still holds; the configured *open*-hour bases are therefore smaller
  than the wall-clock figures (`BASE_PROCESS_HOURS` in `simulator/schedule.py`:
  large 2.0, medium 4.5, small 6.0, times 1.0 urban / 1.3 rural, with lognormal
  jitter). Arrivals are weighted toward the morning
  (`SimConfig.arrival_morning_bias = 2.5`), which is the other lever on elapsed
  time: a fill that cannot finish before closing resumes the next morning.

  **Elapsed time is bimodal, so not every target is reachable.** A fill either
  finishes the same day (≈ its open-hour base) or crosses one closure and absorbs
  the ~15-16 h overnight gap. A parameter sweep showed small jumping from 7.45 h
  to 21.10 h between bases 5.0 and 5.5 with nothing in between, so a median of
  exactly 18 h does not exist. Small is set to 22.77 h: inside the acceptable
  band, genuinely "ready next day", and far enough from the 5.5 cliff that the
  test is not flaky.
- 2,000 patients (fake names, birth dates, phones from Faker), each with one home pharmacy.
- About 30 common drugs, hard-coded list with name, strength and form (e.g. atorvastatin 20 mg tablet).
- 5,000 prescriptions over the last 90 days: Rx number, patient, drug, days' supply (30 or 90), refills (0–5), fake prescriber.
- Fill events: received → in_process → ready → picked_up or delivered, timed by the pharmacy's speed and hours. Refills repeat when the supply runs low. Some patients pick up late or never.
- Some prescriptions have "refill on schedule" turned on.

## Outputs

- `data/truth/` — the clean truth as CSVs (pharmacies, patients, prescriptions, fill_events).
- `data/raw/pms_a.csv` — flat CSV, columns like RX_NO, PT_NAME, PT_DOB, DRUG_DESC, DAYS_SUPPLY, REFILLS_LEFT, STATUS, STATUS_TS, PHARMACY_ID.
- `data/raw/pms_b.json` — nested JSON: pharmacy, patient, rx, and a list of events.
- `data/raw/pms_c.txt` — pipe-delimited HL7-like lines (PID|…, RXO|…, STS|…).
- Each pharmacy exports only in its own PMS format.
- `data/faults.jsonl` — the answer key: one line per planted fault with fault_id, type, rx_number, pharmacy_id and details.

## Planted faults (approximate rates of prescriptions)

- duplicate (~3%): the same prescription exported twice with small differences (drug text like "ATORVASTATIN CALCIUM 20 MG TAB", name casing, date format).
- dropped (~1%): exists in truth but missing from the export.
- stale_status (~2%): export shows an older status than the truth (e.g. still "received" when it's really "ready").
- phantom_schedule (~1%): "refill on schedule" is on, but no refill event ever happens.

## Acceptance criteria

- [ ] One command creates all output files.
- [ ] Same seed → byte-identical files.
- [ ] Every fault type appears, within ±20% of its target rate.
- [ ] Every duplicate appears twice in its export; every dropped prescription is absent from its export.
- [ ] Large pharmacies' median time to "ready" is lower than small pharmacies'.
- [ ] Large median received->ready is 1-4 wall-clock hours.
- [ ] Medium median received->ready is 3-10 wall-clock hours.
- [ ] Small median received->ready is 12-30 wall-clock hours.
- [ ] Runs in under 30 seconds.
- [ ] tests/test_simulator.py covers all of the above.

## Edge cases

- A pharmacy closed on Sunday must not mark anything "ready" on Sunday.
- A prescription with 0 refills gets no refill events.

## Out of scope

- Cleaning or matching the data (Phase 2 and 3).
- Insurance, prices, real patient data.
