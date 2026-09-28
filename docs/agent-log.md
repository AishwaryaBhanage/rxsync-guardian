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
- Anything it got wrong:
  - Ordering friction, not an error: I asked mid-turn for the decisions to go into the spec *before*
    the plan was written, but plan mode only permits edits to the plan file, so it could not comply
    at that moment. It said so plainly and made the spec edit the first action after approval rather
    than dropping the instruction.
  - Implementation has not started yet — fill this in once the simulator is actually built.
