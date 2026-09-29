"""`python -m simulator.generate` — build the whole synthetic dataset.

One command writes the truth, the three PMS exports and the answer key. Output is
byte-identical for a given (seed, as-of) pair, so regenerating never invalidates
an answer key you have already measured against.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from simulator.app_view import write_app_view
from simulator.config import DEFAULT_AS_OF, DEFAULT_SEED, SimConfig
from simulator.exporters import write_exports
from simulator.faults import FAULT_TYPES, plant, write_answer_key
from simulator.models import FaultPlan, World
from simulator.world import build_world, ready_hours_by_size
from simulator.writers import write_truth


@dataclass(frozen=True)
class GenerationResult:
    """Everything one run produced, for the summary and for tests."""

    world: World
    plan: FaultPlan
    truth_paths: dict[str, Path]
    raw_paths: dict[str, Path]
    app_view: Path
    answer_key: Path

    @property
    def all_paths(self) -> list[Path]:
        return [
            *self.truth_paths.values(),
            *self.raw_paths.values(),
            self.app_view,
            self.answer_key,
        ]


def generate(config: SimConfig, out_dir: Path) -> GenerationResult:
    """Build the world, plant the faults, write every file."""
    world = build_world(config)
    # Planting rewrites the truth (phantom_schedule strips refills), so the truth
    # CSVs must be written from the planted world, not the original.
    world, fault_plan = plant(world)

    return GenerationResult(
        world=world,
        plan=fault_plan,
        truth_paths=write_truth(world, out_dir),
        raw_paths=write_exports(world, out_dir, fault_plan),
        app_view=write_app_view(world, out_dir, fault_plan),
        answer_key=write_answer_key(fault_plan, out_dir),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m simulator.generate",
        description="Generate the synthetic pharmacy dataset (truth, exports, answer key).",
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help=f"random seed (default {DEFAULT_SEED})"
    )
    parser.add_argument(
        "--out", type=Path, default=Path("data"), help="output directory (default data)"
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=DEFAULT_AS_OF,
        metavar="YYYY-MM-DD",
        help=f"end of the 90-day window (default {DEFAULT_AS_OF.isoformat()})",
    )
    parser.add_argument("--quiet", action="store_true", help="write the files, print nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = replace(SimConfig(), seed=args.seed, as_of=args.as_of)
    result = generate(config, args.out)
    if not args.quiet:
        print(format_summary(result, args.out))
    return 0


# --- summary --------------------------------------------------------------


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def format_summary(result: GenerationResult, out_dir: Path) -> str:
    config = result.world.config
    lines = [
        "rxsync-investigator — synthetic dataset",
        (
            f"  seed {config.seed}   as-of {config.as_of.isoformat()}   "
            f"window {config.window_days} days   "
            f"({config.window_start.isoformat()} to {config.as_of.isoformat()})"
        ),
        "",
        "truth",
        f"  {'pharmacies':<20}{len(result.world.pharmacies):>10,}",
        f"  {'patients':<20}{len(result.world.patients):>10,}",
        f"  {'drugs':<20}{len(result.world.drugs):>10,}",
        f"  {'prescriptions':<20}{len(result.world.prescriptions):>10,}",
        f"  {'fill_events':<20}{len(result.world.fill_events):>10,}",
        "",
        "median hours received -> ready (wall clock)",
    ]

    durations = ready_hours_by_size(result.world)
    for size in ("large", "medium", "small"):
        samples = durations[size]
        median = statistics.median(samples) if samples else float("nan")
        lines.append(f"  {size:<20}{median:>10.2f}   (n={len(samples):,})")

    tally = Counter(fault.type for fault in result.plan.faults)
    lines += ["", f"planted faults{len(result.plan.faults):>16,}"]
    for fault_type in FAULT_TYPES:
        share = tally[fault_type] / len(result.world.prescriptions)
        lines.append(f"  {fault_type:<20}{tally[fault_type]:>10,}   ({share:.1%})")

    lines += ["", "files"]
    for path in result.all_paths:
        shown = path.relative_to(out_dir) if path.is_relative_to(out_dir) else path
        lines.append(f"  {out_dir / shown!s:<34}{human_size(path.stat().st_size):>10}")

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
