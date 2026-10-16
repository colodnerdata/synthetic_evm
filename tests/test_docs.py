"""Documentation that can drift from the code is checked against it.

Links and heading anchors must resolve, the README's example must run, and the
facts the README states (options, defaults, the mean work-package duration, the
configuration constants and functions it names) must match the code. Like any
check that reads prose, these pin their inputs: a new documented name, option or
document is a conscious edit to the lists below, not something sniffed.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

import synthetic_evm
from synthetic_evm import SCENARIOS, config, engine, generate_portfolio
from tests.helpers import DEFAULT_SEED

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DOCS = [README, *sorted((ROOT / "docs").glob("*.md"))]
# The workbook review quotes Excel workbook names and the Lambda Catalog's style
# constants, and the test plan names the suite's own constants (G_KINDS, ...).
# Neither is configuration, so the name checks skip them.
CODE_DOCS = [doc for doc in DOCS if doc.name not in {"WORKBOOK_REVIEW.md", "TESTING.md"}]

FENCE = re.compile(r"^```.*?^```", re.S | re.M)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)
CONFIG_CONSTANT = re.compile(r"`((?:[A-Z][A-Z0-9]*_)+[A-Z0-9]+|SCENARIOS)`")

# Names the documentation points readers at, so each must exist.
DOCUMENTED_API = (
    "generate_portfolio",
    "validate_portfolio",
    "export_csvs",
    "partition_bac",
    "make_wbs_windows",
)
DOCUMENTED_SCENARIOS = ("schedule_slip", "combined_overrun", "ahead_of_plan", "efficient_execution")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """The text with fenced code blocks removed."""
    return FENCE.sub("", text)


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    heading = heading.replace("`", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors = set()
    for title in HEADING.findall(_prose(_text(path))):
        slug = _slug(title)
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def test_the_link_checker_sees_the_readmes_links():
    links = LINK.findall(_prose(_text(README)))
    assert len(links) >= 4
    assert any(link.startswith("#") for link in links)
    assert any(link.endswith(".md") for link in links)


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: path.name)
def test_relative_links_and_anchors_resolve(doc):
    problems = []
    for target in LINK.findall(_prose(_text(doc))):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        destination = doc if not file_part else (doc.parent / file_part).resolve()
        if not destination.exists():
            problems.append(f"{target}: no such file")
        elif anchor and destination.suffix == ".md" and anchor not in _anchors(destination):
            problems.append(f"{target}: {destination.name} has no heading '#{anchor}'")
    assert not problems, problems


def test_the_docs_are_uv_only():
    """Every shell command the docs show goes through uv, and nothing mentions pip."""
    commands = []
    for doc in DOCS:
        for block in re.findall(r"^```(?:bash|sh|shell|console)\n(.*?)^```", _text(doc), re.S | re.M):
            for line in block.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    commands.append((doc.name, line))
    assert commands, "the docs no longer show any shell commands"
    assert [command for command in commands if not command[1].startswith("uv ")] == []

    for path in [*DOCS, ROOT / "generate_evm.py"]:
        text = _text(path)
        assert not re.search(r"\bpip\b", text), f"{path.name} mentions pip"
        assert not re.search(r"(?<!uv run )python generate_evm\.py", text), (
            f"{path.name} runs the generator without uv"
        )


def test_the_readme_python_example_runs(monkeypatch, tmp_path):
    blocks = re.findall(r"^```python\n(.*?)^```", _text(README), re.S | re.M)
    assert blocks, "the README no longer shows a Python example"
    monkeypatch.chdir(tmp_path)  # the example exports to ./output
    for code in blocks:
        exec(compile(code, "README.md", "exec"), {"__name__": "readme_example"})
    assert (tmp_path / "output" / "monthly.csv").exists()


def test_the_readme_documents_exactly_the_cli_options():
    help_text = subprocess.run(
        [sys.executable, str(ROOT / "generate_evm.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    options = set(re.findall(r"^\s{2}(--[a-z][a-z-]*)", help_text, re.M)) - {"--help"}
    documented = set(re.findall(r"^\| `(--[a-z][a-z-]*)`", _text(README), re.M))
    assert options, "the --help output no longer lists options"
    assert options == documented


def test_the_readmes_stated_defaults_match_the_code():
    readme = _text(README)
    assert str(DEFAULT_SEED) in readme
    assert inspect.signature(generate_portfolio).parameters["seed"].default == DEFAULT_SEED
    assert "synthetic_evm_output" in readme
    assert f"${config.DEFAULT_TOTAL_BAC / 1e6:.0f}M" in readme
    assert f"{config.DEFAULT_PLAN_MONTHS}-month" in readme


def test_the_readmes_mean_work_package_duration_matches_the_configured_mix():
    mean = sum(
        months * p
        for months, p in zip(config.WP_DURATION_MONTHS, config.WP_DURATION_PROBS, strict=True)
    )
    stated = re.search(r"mean ≈ ([0-9.]+)", _text(README))
    assert stated, "the README no longer states the mean work-package duration"
    assert mean == pytest.approx(float(stated.group(1)), abs=0.05)


def test_configuration_constants_named_in_the_docs_exist():
    names = set(CONFIG_CONSTANT.findall("\n".join(_text(doc) for doc in CODE_DOCS)))
    assert {"WP_DURATION_MONTHS", "G_RIGHT_TAIL", "SCENARIOS"} <= names, "the pattern misses known names"
    assert [name for name in sorted(names) if not hasattr(config, name)] == []


def test_functions_and_scenarios_named_in_the_docs_exist():
    docs = "\n".join(_text(doc) for doc in CODE_DOCS)
    for name in DOCUMENTED_API:
        assert f"`{name}`" in docs, f"{name} is no longer documented; drop it from DOCUMENTED_API"
        assert hasattr(synthetic_evm, name) or hasattr(engine, name), name
    for name in DOCUMENTED_SCENARIOS:
        assert f"`{name}`" in docs, f"{name} is no longer documented; drop it from DOCUMENTED_SCENARIOS"
        assert name in SCENARIOS, name
