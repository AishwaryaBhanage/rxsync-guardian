"""Scale and timing knobs for the simulated world.

Every number the generator depends on lives here so tests can shrink the world
without touching generation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

# The 90-day window ends here, not at today(), so a given (seed, as_of) pair
# always reproduces the same files. See specs/01-simulator.md -> Decisions.
DEFAULT_AS_OF = date(2026, 9, 27)
DEFAULT_SEED = 42


@dataclass(frozen=True)
class SimConfig:
    """How big the world is and when it is observed."""

    n_pharmacies: int = 50
    n_patients: int = 2_000
    n_prescriptions: int = 5_000
    window_days: int = 90
    as_of: date = DEFAULT_AS_OF
    seed: int = DEFAULT_SEED

    # Share of prescriptions with "refill on schedule" turned on. Must stay
    # comfortably above the phantom_schedule fault rate (1%), since that fault
    # can only be planted on a prescription that has the flag set.
    refill_on_schedule_share: float = 0.20

    # How arrivals are spread across the open day. 1.0 is uniform; higher values
    # push them toward the morning (offset = day_length * random() ** bias).
    # This matters because a fill that cannot finish before closing resumes the
    # next morning, so the arrival hour decides the elapsed time.
    arrival_morning_bias: float = 2.5

    # Share of fills the patient never collects.
    never_collected_share: float = 0.15
    # Of the fills that are collected, the share delivered rather than picked up.
    delivered_share: float = 0.20

    # Planted fault rates, as a share of prescriptions. Faults are disjoint, so
    # these must sum to well under 1.0.
    rate_duplicate: float = 0.03
    rate_dropped: float = 0.01
    rate_stale_status: float = 0.02
    rate_phantom_schedule: float = 0.01

    def fault_count(self, rate: float) -> int:
        """How many prescriptions a given fault rate covers."""
        return round(rate * self.n_prescriptions)

    @property
    def window_start(self) -> date:
        """First day a prescription can be written."""
        from datetime import timedelta

        return self.as_of - timedelta(days=self.window_days)

    @classmethod
    def small(cls, **overrides: object) -> SimConfig:
        """A fast world with the same shape, for the default test run.

        Kept at >= 10 pharmacies so the index-based size/area/PMS assignment in
        world.py still covers every category (see _build_pharmacies).
        """
        base = cls(n_pharmacies=15, n_patients=120, n_prescriptions=300)
        return replace(base, **overrides)  # type: ignore[arg-type]
