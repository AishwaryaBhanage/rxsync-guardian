# Agent log

Notes on working with Claude Code on this project — what I steered, and what it got wrong.

## Phase 0

- Asked Claude Code to scaffold the project in plan mode.
- What I changed in its plan:
  - **Postgres host port 5432 → 5433, everywhere.** The plan specified `"5432:5432"` and a
    `DATABASE_URL` on 5432. I overrode that after the collision surfaced (write-in answer; none
    of the three options it offered were what I wanted). The container still listens on 5432
    internally — only the host mapping moved.
  - Chose full verification over files-only: it had to run `uv sync`, bring up Postgres, and get
    the test passing before handing back.
  - Chose a readable dev password (`rxsync_dev_pw`) in both `.env.example` and `.env` rather than
    a generated random one.
- Anything it got wrong:
  - **Missed the port collision during planning.** It checked `uv`, `docker`, and Python versions
    before writing the plan, but never checked whether port 5432 was already in use — a native
    Postgres (PID 966) already owned `127.0.0.1:5432` and `[::1]:5432` on this machine. One
    `lsof -iTCP:5432` would have caught it. The plan's verification section asserted the test
    would pass on 5432; the first `uv run pytest` failed instead.
  - **The failure mode was misleading, and it had to diagnose rather than predict it.** The error
    was `FATAL: role "rxsync" does not exist`, which reads like a broken container, not a port
    conflict. Docker bound `*:5432` while the native server held the loopback addresses, so both
    listeners coexisted and `localhost` silently resolved to the wrong one. To its credit it
    proved the container itself was fine (connected via the LAN IP, test passed) before proposing
    any change.
  - **Offered 55432 as the alternate port** instead of 5433, the conventional choice for a second
    Postgres. Had to be corrected to the obvious value.
  - **Created `specs/.gitkeep` and then `specs/_template.md`**, leaving the `.gitkeep` redundant.
    It flagged this afterwards instead of noticing while writing the template; the stray file is
    still committed. Same now applies to `docs/.gitkeep`, redundant as of this file.
  - **Dependency floors, not pins.** `faker>=30.0` and friends resolved to faker 40.39.0,
    pandas **3.0.6**, pytest 9.1.1. Fine for a skeleton, but pandas 3.x is a major jump from what
    the plan's floors implied; `uv.lock` is what actually pins this.
  - One non-issue for the record: the "restore the original hook command" step was a no-op — the
    sentinel had already been stripped in the prior turn, and dismissing `/hooks` does not modify
    settings.

## Phase 1

- Wrote `specs/01-simulator.md` myself first, then asked Claude Code to plan the build in plan mode.
- **It found a contradiction in my spec before writing a line of code.** I had asked for two things
  that cannot both be true:
  - *"5,000 prescriptions over the last 90 days"* — a window relative to the run date.
  - *"Same seed must always produce identical files."*

  If the 90-day window is anchored to `date.today()`, the generated world changes every day, so the
  same seed does **not** reproduce the same files, and any stored `faults.jsonl` answer key goes
  stale overnight.
- **The part that makes it a good story: my own acceptance test would have passed anyway.** "Same
  seed → byte-identical files" was going to be checked by generating twice and diffing — two runs
  seconds apart share the same `today()`, so it would have come up green. The defect would have
  shipped with a passing suite and only surfaced weeks later, when an old answer key silently
  stopped matching freshly generated data. A test can only catch what it is anchored against.
- Resolution: a `--as-of YYYY-MM-DD` flag with a fixed default (`2026-09-27`). Byte-identity is now
  a property of the `(seed, as-of)` pair, which is a claim that actually holds. Recorded in a new
  `## Decisions` section in the spec, not just in the plan.
- Two smaller conflicts it caught in the same pass:
  - The `.gitignore` covered `data/raw/` only, but the spec also writes `data/truth/` and
    `data/faults.jsonl` — generated data would have been committed on the next `git add .`.
  - The test hook I asked for runs the full suite on every edit, which collides with a
    5,000-prescription generation living in the tests. Led to splitting full-scale checks behind
    `@pytest.mark.slow` plus a pre-commit rule to run them.
- What I changed in its plan:
  - Test scale: took the small-world-by-default option and added the rule that I run
    `uv run pytest -m slow` before every commit, with CI to run them later.
  - Hook scope: guard it to `.py`, `.yaml`, `.yml` and `.toml`, not just `.py`.
- Anything it got wrong (planning):
  - Ordering friction, not an error: I asked mid-turn for the decisions to go into the spec *before*
    the plan was written, but plan mode only permits edits to the plan file, so it could not comply
    at that moment. It said so plainly and made the spec edit the first action after approval rather
    than dropping the instruction.

### Implementation

- What I asked for: the four steps one at a time — truth data, the three PMS exporters, fault
  planting plus `faults.jsonl`, then the CLI — each ending "add tests, stop when tests pass".
  Committed after step 1; the rest is still uncommitted as of writing.
- Result: `uv run python -m simulator.generate --seed 42 --out data` builds 50 pharmacies, 2,000
  patients, 5,000 prescriptions, 24,064 fill events and 350 planted faults in ~1.3 s. 101 tests.
  A concrete duplicate pair, from `pms_a.csv` — same Rx, three textual differences, which is what
  `dedup` will have to match through:

  ```
  RX1000017,JAMES WHITE,01/13/1931,FLUOXETINE HCL 20 MG CAP,90,5,READY,08/12/2026 14:12,PH034
  RX1000017,James White,1931-01-13,fluoxetine 20 mg capsule,90,5,READY,08/12/2026 14:12,PH034
  ```

- What I changed in its plan:
  - **Processing speeds are wall-clock, not working hours.** It read "small rural ~18 hours" as 18
    hours of *open* time, which came out at a 49.7 h elapsed median. I meant elapsed — ready the
    next day. It kept counting open hours internally (the Sunday rule depends on that) and lowered
    the bases instead.
  - **Stop tuning by reasoning; measure.** After the correction it argued from analysis about which
    values would work and proposed a number off the top of its head. I told it to sweep a few values
    with a script and show me a table. See below — the measurement disagreed with its reasoning.
  - Gave explicit targets with acceptable bands (large 1–4 h, medium 3–10 h, small 12–30 h) and
    asked for a slow test per band, rather than only the "large faster than small" comparison.
  - Questioned the fast/slow test split when `pytest` reported "7 deselected" and had it show me
    `uv run pytest -m ""`, which runs everything in one go.
- Anything it got wrong (implementation):
  - **The wall-clock misreading is the same class of bug as the spec contradiction, and again my own
    acceptance test would not have caught it.** The criterion was "large median < small median",
    which passed comfortably at 2.3 h vs 49.7 h. Only because it printed the actual medians during
    verification did the 49.7 h show up as absurd. Second time in this project that a passing test
    sat on top of wrong data.
  - **Reasoned instead of measuring, and its reasoning was wrong.** Once it actually swept the
    parameters, the shape of the problem turned out to be different from what it had argued: elapsed
    time is *bimodal* — a fill either finishes the same day or crosses a closure and absorbs the
    ~16 h overnight gap. The sweep showed small jumping 7.45 h → 21.10 h between bases 5.0 and 5.5
    with nothing in between, so a median of exactly 18 h does not exist at all. The 30-second script
    settled what several turns of argument had not, and it had also proposed a medium value that the
    table showed was not the best fit.
  - **Three signature/call-site mismatches.** It edited a function's call site without its signature
    (or the reverse) three times — `_build_prescriptions`, then `_received_at` twice. The PostToolUse
    hook blocked each one within a second or two. This is the hook paying for itself; without it
    each would have sat broken until the next manual test run.
  - **A find-and-replace script aborted halfway through.** One of its Python heredocs asserted on a
    code snippet that `ruff format` had already reflowed, so `schedule.py` got its edit and
    `world.py` did not — leaving a call to a function that did not exist yet. Cause: it wrote the
    replacement against the text as it had authored it, not as the formatter had left it on disk.
  - Minor: a `tuple(x,)` in a test that tried to iterate `x` instead of making a one-tuple (caught
    on the first run), and it assumed ruff's default rule set was narrower than it is — 0.16 also
    enforces DTZ/PLR/UP/ISC/RUF, so the first real lint run produced nine findings.
  - **Carried test debt for a few turns.** I said "implement step 3 only, add tests" and it wrote the
    fault-planting code but not the tests — I interrupted with a question just as it started them,
    and the debt survived until step 4. It did flag the gap itself when step 4 began, and wrote all
    27 fault tests then, but for a few turns the answer key had no test behind it.
