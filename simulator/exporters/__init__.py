"""Per-PMS exports of the truth.

A pharmacy runs exactly one PMS, so each pharmacy's prescriptions appear in
exactly one export file. Between them the three files cover every prescription
once — until step 3 starts dropping and duplicating rows on purpose.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from simulator.exporters import pms_a, pms_b, pms_c
from simulator.models import FaultPlan, World

# pms_type -> the module that renders it. Each exposes FILENAME and write().
EXPORTERS: dict[str, ModuleType] = {"A": pms_a, "B": pms_b, "C": pms_c}

RAW_FILENAMES = tuple(module.FILENAME for module in EXPORTERS.values())


def write_exports(world: World, out_dir: Path, plan: FaultPlan | None = None) -> dict[str, Path]:
    """Write one file per PMS type under `out_dir/raw/`, keyed by that type.

    Without a `plan` the exports are a faithful view of the truth. With one, the
    export-layer faults (dropped, stale_status, duplicate) are applied as each
    file is rendered.
    """
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    for pms_type in sorted(EXPORTERS):  # sorted so the write order is stable
        module = EXPORTERS[pms_type]
        pharmacies = tuple(p for p in world.pharmacies if p.pms_type == pms_type)
        path = raw_dir / module.FILENAME
        module.write(path, world, pharmacies, plan)
        paths[pms_type] = path
    return paths
