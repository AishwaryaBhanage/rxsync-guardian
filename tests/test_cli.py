"""Step 4 tests: the CLI, and the acceptance criteria that span the whole run.

"One command creates all output files" and "same seed -> byte-identical files" are
properties of the command, not of any one stage, so they are checked here by
driving `main()` the way a shell would.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest

from simulator.config import DEFAULT_AS_OF, DEFAULT_SEED, SimConfig
from simulator.faults import ANSWER_KEY_FILENAME
from simulator.generate import build_parser, format_summary, generate, human_size, main
from simulator.writers import TRUTH_FILES

EXPECTED_FILES = (
    *(f"truth/{name}.csv" for name in TRUTH_FILES),
    "raw/pms_a.csv",
    "raw/pms_b.json",
    "raw/pms_c.txt",
    ANSWER_KEY_FILENAME,
)

# The CLI defaults to 5,000 prescriptions; tests drive a small world directly
# except where the point is the command-line surface itself.
SMALL = SimConfig.small()


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli")
    return generate(SMALL, out), out


# --- the command line surface ---------------------------------------------


def test_parser_defaults_match_the_documented_ones():
    args = build_parser().parse_args([])
    assert args.seed == DEFAULT_SEED
    assert args.as_of == DEFAULT_AS_OF
    assert args.out == Path("data")
    assert args.quiet is False


def test_parser_accepts_seed_out_and_as_of(tmp_path):
    args = build_parser().parse_args(
        ["--seed", "7", "--out", str(tmp_path), "--as-of", "2026-06-30"]
    )
    assert args.seed == 7
    assert args.out == tmp_path
    assert args.as_of == date(2026, 6, 30)


def test_parser_rejects_a_malformed_as_of():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--as-of", "30/06/2026"])


def test_main_returns_zero_and_prints_a_summary(tmp_path, capsys):
    # A real command-line invocation, at full default scale.
    assert main(["--out", str(tmp_path), "--seed", "1"]) == 0
    printed = capsys.readouterr().out
    assert "rxsync-guardian" in printed
    assert "planted faults" in printed
    assert "median hours received -> ready" in printed


def test_quiet_prints_nothing_but_still_writes(tmp_path, capsys):
    assert main(["--out", str(tmp_path), "--seed", "1", "--quiet"]) == 0
    assert capsys.readouterr().out == ""
    for name in EXPECTED_FILES:
        assert (tmp_path / name).exists()


# --- one command creates every file ---------------------------------------


def test_one_command_creates_all_output_files(small_run):
    _, out = small_run
    for name in EXPECTED_FILES:
        path = out / name
        assert path.exists(), f"{name} was not written"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_result_lists_exactly_those_files(small_run):
    result, out = small_run
    assert {path.relative_to(out).as_posix() for path in result.all_paths} == set(EXPECTED_FILES)


def test_the_answer_key_sits_beside_the_data_directories(small_run):
    result, out = small_run
    assert result.answer_key == out / ANSWER_KEY_FILENAME


# --- determinism across the whole run ------------------------------------


def _fingerprint(out: Path) -> dict[str, bytes]:
    return {name: (out / name).read_bytes() for name in EXPECTED_FILES}


def test_same_seed_and_as_of_give_byte_identical_files(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    generate(SMALL, first)
    generate(SMALL, second)
    assert _fingerprint(first) == _fingerprint(second)


def test_a_different_seed_changes_the_output(tmp_path):
    from dataclasses import replace

    generate(SMALL, tmp_path / "s42")
    generate(replace(SMALL, seed=43), tmp_path / "s43")
    first, second = _fingerprint(tmp_path / "s42"), _fingerprint(tmp_path / "s43")
    assert first != second
    # The answer key must move with the data, not just the data.
    assert first[ANSWER_KEY_FILENAME] != second[ANSWER_KEY_FILENAME]


def test_a_different_as_of_changes_the_output(tmp_path):
    from dataclasses import replace

    generate(SMALL, tmp_path / "sept")
    generate(replace(SMALL, as_of=date(2026, 6, 30)), tmp_path / "june")
    assert _fingerprint(tmp_path / "sept") != _fingerprint(tmp_path / "june")


def test_rerunning_into_the_same_directory_overwrites_cleanly(tmp_path):
    generate(SMALL, tmp_path)
    before = _fingerprint(tmp_path)
    generate(SMALL, tmp_path)
    assert _fingerprint(tmp_path) == before


# --- the summary ----------------------------------------------------------


def test_summary_reports_counts_faults_and_files(small_run):
    result, out = small_run
    summary = format_summary(result, out)
    assert f"{len(result.world.prescriptions):,}" in summary
    assert f"{len(result.plan.faults):,}" in summary
    for name in EXPECTED_FILES:
        assert Path(name).name in summary


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [(0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB")],
)
def test_human_size_formats_each_magnitude(num_bytes, expected):
    assert human_size(num_bytes) == expected


# --- the answer key describes the files that were written -----------------


def test_answer_key_rows_all_reference_exported_prescriptions(small_run):
    result, out = small_run
    known = {rx.rx_number for rx in result.world.prescriptions}
    lines = (out / ANSWER_KEY_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.plan.faults)
    for line in lines:
        record = json.loads(line)
        assert record["rx_number"] in known


# --- full scale ----------------------------------------------------------


@pytest.mark.slow
def test_the_whole_command_runs_within_thirty_seconds(tmp_path):
    started = time.perf_counter()
    assert main(["--out", str(tmp_path), "--quiet"]) == 0
    elapsed = time.perf_counter() - started
    assert elapsed < 30, f"generation took {elapsed:.1f}s"


@pytest.mark.slow
def test_full_scale_run_is_byte_identical(tmp_path):
    main(["--out", str(tmp_path / "one"), "--quiet"])
    main(["--out", str(tmp_path / "two"), "--quiet"])
    assert _fingerprint(tmp_path / "one") == _fingerprint(tmp_path / "two")
