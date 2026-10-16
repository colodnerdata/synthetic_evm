"""CSV export and the command-line runner."""

from __future__ import annotations

import re
import runpy
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import generate_evm
import synthetic_evm
from synthetic_evm import export_csvs
from synthetic_evm.io import OUTPUT_ORDER
from tests.helpers import DEFAULT_SEED

ROOT = Path(__file__).resolve().parents[1]


def run_cli(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["generate_evm.py", *args])
    generate_evm.main()


# --- export_csvs ----------------------------------------------------------------


def test_export_writes_the_three_tables_and_they_round_trip(default_portfolio, tmp_path):
    out = tmp_path / "nested" / "out"  # created on demand
    paths = export_csvs(default_portfolio, out)

    assert list(paths) == list(OUTPUT_ORDER) == ["projects", "wbs_totals", "monthly"]
    for name, path in paths.items():
        assert path == out / f"{name}.csv" and path.exists()
        back = pd.read_csv(path, dtype={"project_id": str, "wbs_id": str})
        pd.testing.assert_frame_equal(
            back, default_portfolio[name], check_dtype=False, atol=0.005, rtol=0
        )


def test_export_formats_ids_as_text_and_amounts_to_the_cent(default_portfolio, tmp_path):
    lines = export_csvs(default_portfolio, tmp_path)["monthly"].read_text("utf-8").splitlines()
    assert lines[0] == "project_id,wbs_id,month,PV,EV,AC"
    row = re.compile(r"P\d{3},\d{2},\d+,\d+\.\d{2},\d+\.\d{2},\d+\.\d{2}")
    assert all(row.fullmatch(line) for line in lines[1:])


def test_export_prefix_names_the_files(default_portfolio, tmp_path):
    paths = export_csvs(default_portfolio, tmp_path, prefix="run1_")
    assert [p.name for p in paths.values()] == [
        "run1_projects.csv",
        "run1_wbs_totals.csv",
        "run1_monthly.csv",
    ]


# --- generate_evm.py -------------------------------------------------------------


def test_cli_generates_the_selected_scenarios(monkeypatch, capsys, tmp_path):
    run_cli(monkeypatch, "--seed", "3", "--out", str(tmp_path), "--scenarios", "nominal", "cost_overrun")
    out = capsys.readouterr().out
    assert "Generated 2 projects with seed 3." in out
    assert "Dynamic reporting horizons:" in out
    projects = pd.read_csv(tmp_path / "projects.csv")
    assert list(projects["scenario"]) == ["nominal", "cost_overrun"]


def test_cli_defaults_to_the_documented_seed_and_output_directory(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    run_cli(monkeypatch, "--scenarios", "nominal")
    assert f"seed {DEFAULT_SEED}" in capsys.readouterr().out
    assert (tmp_path / "synthetic_evm_output" / "monthly.csv").exists()


def test_cli_rejects_a_scenarios_option_with_no_names(monkeypatch, capsys, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        run_cli(monkeypatch, "--out", str(tmp_path), "--scenarios")
    assert exit_info.value.code == 2
    assert "expected at least one argument" in capsys.readouterr().err


def test_cli_runs_as_a_script(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "generate_evm.py"), "--seed", "3", "--out", str(tmp_path)]
        + ["--scenarios", "nominal"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "Generated 1 projects with seed 3." in result.stdout


# --- synthetic_evm_generator.py ----------------------------------------------------


def test_the_alias_module_reexports_the_public_api():
    import synthetic_evm_generator as alias

    assert set(alias.__all__) <= set(synthetic_evm.__all__)
    for name in alias.__all__:
        assert getattr(alias, name) is getattr(synthetic_evm, name)


def test_the_alias_module_writes_the_default_sample_when_run(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # it writes relative to the working directory
    runpy.run_path(str(ROOT / "synthetic_evm_generator.py"), run_name="__main__")
    written = sorted(p.name for p in (tmp_path / "synthetic_evm_output").iterdir())
    assert written == ["monthly.csv", "projects.csv", "wbs_totals.csv"]
