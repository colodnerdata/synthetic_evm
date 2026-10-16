"""The charts workbook: overwriting the Power Query parameter ``pCsvPath``.

Concepts
--------
``monthly_charts.xlsx`` holds one Power Query text parameter. Each run overwrites its value with the absolute
path of the ``monthly.csv`` it wrote, in place, and nothing else in the workbook changes. The parameter is an
M expression, a text literal followed by a ``meta`` record. In an M text literal a double quote is written
``""`` and ``#(`` opens an escape sequence, so a literal ``#(`` is written ``#(#)(``; backslashes are not special.

The overwrite drives desktop Excel through COM, which cannot run in CI, so it is tested against a fake object
model that mimics the slice of Excel the module uses: collections are 1-based, a workbook locked elsewhere opens
read-only, and Excel may re-wrap a formula's whitespace. These checks establish the call sequence (hidden Excel,
no dialogs, save only on a change, always close and quit) and every skip and error path. Whether real Excel
accepts the calls is outside what the suite can establish.
"""

from __future__ import annotations

import base64
import io
import re
import struct
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

import generate_evm
from synthetic_evm.charts_workbook import (
    CHARTS_WORKBOOK_NAME,
    PARAMETER_META,
    PARAMETER_NAME,
    ChartsWorkbookError,
    m_text_literal,
    parameter_formula,
    set_csv_path_parameter,
    update_charts_workbook,
)

ROOT = Path(__file__).resolve().parents[1]
COMMITTED_WORKBOOK = ROOT / "synthetic_evm_output" / CHARTS_WORKBOOK_NAME

CSV = Path("/data/run/monthly.csv")  # only ever formatted into a formula, never touched on disk
OLD_FORMULA = parameter_formula(r"C:\old\run\monthly.csv")
OTHER_QUERY = "let Source = 1 in Source"


# --- a fake slice of the Excel object model -----------------------------------------------------------------


class FakeQuery:
    def __init__(self, name: str, formula: str):
        self.Name = name
        self.Formula = formula


class FakeQueries:
    def __init__(self, queries):
        self._queries = list(queries)

    @property
    def Count(self) -> int:
        return len(self._queries)

    def Item(self, index: int) -> FakeQuery:
        assert 1 <= index <= self.Count, "COM collections are 1-based"
        return self._queries[index - 1]


class FakeBook:
    def __init__(self, queries, *, read_only: bool = False, save_error: Exception | None = None):
        self.Queries = FakeQueries(queries)
        self.ReadOnly = read_only
        self.save_error = save_error
        self.saves = 0
        self.close_calls: list[dict] = []

    def Save(self) -> None:
        if self.save_error:
            raise self.save_error
        self.saves += 1

    def Close(self, **kwargs) -> None:
        self.close_calls.append(kwargs)

    def formula_of(self, name: str) -> str:
        return next(q.Formula for q in self.Queries._queries if q.Name == name)


class FakeWorkbooks:
    def __init__(self, book: FakeBook | None, open_error: Exception | None):
        self._book = book
        self._open_error = open_error
        self.opened: list[str] = []

    def Open(self, path: str) -> FakeBook:
        if self._open_error:
            raise self._open_error
        self.opened.append(path)
        return self._book


class FakeExcel:
    def __init__(self, book: FakeBook | None = None, *, open_error: Exception | None = None):
        self.Visible = True  # Excel's defaults, so the test sees them being switched off
        self.DisplayAlerts = True
        self.Workbooks = FakeWorkbooks(book, open_error)
        self.quit_calls = 0

    def Quit(self) -> None:
        self.quit_calls += 1


def default_book(**kwargs) -> FakeBook:
    return FakeBook([FakeQuery("monthly", OTHER_QUERY), FakeQuery(PARAMETER_NAME, OLD_FORMULA)], **kwargs)


def never_started():
    raise AssertionError("Excel must not be started")


# --- M text literals and the parameter formula ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        (r"C:\data\monthly.csv", r'"C:\data\monthly.csv"'),  # backslashes are not special
        ('say "hi"', '"say ""hi"""'),
        (r"C:\#(archive)\m.csv", r'"C:\#(#)(archive)\m.csv"'),
        ("a#b", '"a#b"'),  # a bare # is fine; only #( opens an escape
        ("", '""'),
    ],
)
def test_m_text_literal_escapes_quotes_and_escape_openers(value, literal):
    assert m_text_literal(value) == literal


def test_the_parameter_formula_is_a_required_text_parameter_holding_the_path():
    assert parameter_formula(r"C:\data\monthly.csv") == (
        r'"C:\data\monthly.csv" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    )
    assert parameter_formula(Path("/x/monthly.csv")).endswith(PARAMETER_META)


# --- driving Excel -------------------------------------------------------------------------------------------


def test_the_parameter_is_overwritten_and_saved_in_place(tmp_path):
    book = default_book()
    excel = FakeExcel(book)
    workbook = tmp_path / CHARTS_WORKBOOK_NAME

    changed = set_csv_path_parameter(workbook, CSV, dispatch_excel=lambda: excel)

    assert changed is True
    assert book.formula_of(PARAMETER_NAME) == parameter_formula(CSV)
    assert book.formula_of("monthly") == OTHER_QUERY  # nothing else is touched
    assert excel.Workbooks.opened == [str(workbook.resolve())]
    assert (excel.Visible, excel.DisplayAlerts) == (False, False)
    assert book.saves == 1
    assert book.close_calls == [{"SaveChanges": False}]  # already saved; never prompt
    assert excel.quit_calls == 1


def test_an_unchanged_value_is_not_saved_even_if_excel_rewraps_the_formula(tmp_path):
    rewrapped = parameter_formula(CSV).replace(", ", ",\n    ")
    book = FakeBook([FakeQuery(PARAMETER_NAME, rewrapped)])
    excel = FakeExcel(book)

    changed = set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=lambda: excel)

    assert changed is False
    assert book.saves == 0
    assert book.close_calls == [{"SaveChanges": False}]
    assert excel.quit_calls == 1


def test_a_workbook_without_the_parameter_is_reported_and_left_alone(tmp_path):
    book = FakeBook([FakeQuery("monthly", OTHER_QUERY)])
    excel = FakeExcel(book)

    with pytest.raises(ChartsWorkbookError, match=PARAMETER_NAME):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=lambda: excel)

    assert book.saves == 0
    assert book.formula_of("monthly") == OTHER_QUERY
    assert book.close_calls == [{"SaveChanges": False}]
    assert excel.quit_calls == 1


def test_a_workbook_open_elsewhere_is_reported_and_left_alone(tmp_path):
    book = default_book(read_only=True)
    excel = FakeExcel(book)

    with pytest.raises(ChartsWorkbookError, match="open in another Excel window"):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=lambda: excel)

    assert book.formula_of(PARAMETER_NAME) == OLD_FORMULA
    assert book.saves == 0
    assert excel.quit_calls == 1


def test_excel_failing_to_open_the_workbook_still_quits_excel(tmp_path):
    excel = FakeExcel(open_error=OSError("boom"))

    with pytest.raises(ChartsWorkbookError, match="boom"):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=lambda: excel)

    assert excel.quit_calls == 1


def test_a_failed_save_is_reported_and_the_workbook_and_excel_are_still_closed(tmp_path):
    book = default_book(save_error=RuntimeError("disk full"))
    excel = FakeExcel(book)

    with pytest.raises(ChartsWorkbookError, match="disk full"):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=lambda: excel)

    assert book.close_calls == [{"SaveChanges": False}]
    assert excel.quit_calls == 1


def test_a_missing_pywin32_is_reported_with_the_fix(tmp_path):
    def no_pywin32():
        raise ImportError("No module named 'win32com'")

    with pytest.raises(ChartsWorkbookError, match=r"pywin32 is not installed; run `uv sync`"):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=no_pywin32)


def test_excel_that_cannot_start_is_reported(tmp_path):
    def no_excel():
        raise OSError("Invalid class string")

    with pytest.raises(ChartsWorkbookError, match=r"Excel could not be started \(Invalid class string\)"):
        set_csv_path_parameter(tmp_path / CHARTS_WORKBOOK_NAME, CSV, dispatch_excel=no_excel)


# --- the run-level step --------------------------------------------------------------------------------------


def test_no_workbook_beside_the_csv_means_nothing_to_do_and_excel_is_never_started(tmp_path):
    assert update_charts_workbook(tmp_path / "monthly.csv", platform="win32", dispatch_excel=never_started) is None


def test_other_platforms_skip_with_a_message(tmp_path):
    (tmp_path / CHARTS_WORKBOOK_NAME).write_bytes(b"")
    message = update_charts_workbook(tmp_path / "monthly.csv", platform="linux", dispatch_excel=never_started)
    assert message == f"{CHARTS_WORKBOOK_NAME} not updated: Excel automation needs Windows with desktop Excel"


def test_the_workbook_is_pointed_at_the_absolute_path_of_the_csv_beside_it(tmp_path, monkeypatch):
    (tmp_path / CHARTS_WORKBOOK_NAME).write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    book = default_book()

    message = update_charts_workbook(Path("monthly.csv"), platform="win32", dispatch_excel=lambda: FakeExcel(book))

    csv_path = (tmp_path / "monthly.csv").resolve()
    assert book.formula_of(PARAMETER_NAME) == parameter_formula(csv_path)
    assert message == f"{PARAMETER_NAME} in {CHARTS_WORKBOOK_NAME} now points at {csv_path}"


def test_a_second_run_to_the_same_place_reports_already_current(tmp_path):
    (tmp_path / CHARTS_WORKBOOK_NAME).write_bytes(b"")
    csv_path = (tmp_path / "monthly.csv").resolve()
    book = FakeBook([FakeQuery(PARAMETER_NAME, parameter_formula(csv_path))])

    message = update_charts_workbook(csv_path, platform="win32", dispatch_excel=lambda: FakeExcel(book))

    assert message == f"{PARAMETER_NAME} in {CHARTS_WORKBOOK_NAME} already points at {csv_path}"
    assert book.saves == 0


def test_excel_problems_become_a_report_not_an_exception(tmp_path):
    (tmp_path / CHARTS_WORKBOOK_NAME).write_bytes(b"")
    book = FakeBook([FakeQuery("monthly", OTHER_QUERY)])

    message = update_charts_workbook(
        tmp_path / "monthly.csv", platform="win32", dispatch_excel=lambda: FakeExcel(book)
    )

    assert message.startswith(f"{CHARTS_WORKBOOK_NAME} not updated: it has no query named {PARAMETER_NAME}")


# --- the command line -----------------------------------------------------------------------------------------


def run_cli(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["generate_evm.py", *args])
    generate_evm.main()


def test_the_cli_updates_the_charts_workbook_for_the_csv_it_wrote(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_update(monthly_csv):
        calls.append(Path(monthly_csv))
        return "stub report"

    monkeypatch.setattr(generate_evm, "update_charts_workbook", fake_update)
    run_cli(monkeypatch, "--seed", "3", "--out", str(tmp_path), "--scenarios", "nominal")

    assert calls == [tmp_path / "monthly.csv"]
    assert "Charts:     stub report" in capsys.readouterr().out


def test_the_cli_says_nothing_about_charts_when_there_is_no_workbook(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(generate_evm, "update_charts_workbook", lambda monthly_csv: None)
    run_cli(monkeypatch, "--seed", "3", "--out", str(tmp_path), "--scenarios", "nominal")
    assert "Charts:" not in capsys.readouterr().out


# --- the committed workbook and the dependency ------------------------------------------------------------


def read_section_m(workbook: Path) -> str:
    """The M source stored in a workbook's Power Query DataMashup (read as a zip; Excel is not needed)."""
    with zipfile.ZipFile(workbook) as z:
        # Excel stores the DataMashup as UTF-16 XML in one of the workbook's customXml items.
        items = [
            z.read(name).decode("utf-16")
            for name in z.namelist()
            if re.fullmatch(r"customXml/item\d+\.xml", name)
        ]
        item = next((text for text in items if "<DataMashup" in text), None)
        assert item, f"{workbook.name} has no Power Query DataMashup"
        relationships = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        assert "relationships/customXml" in relationships, "the workbook no longer relates to its customXml"
    blob = base64.b64decode(re.sub(r"<[^>]+>", "", item).strip().lstrip("\ufeff"))

    # version, then four length-prefixed sections: package parts, permissions, metadata, permission bindings
    version, offset = struct.unpack_from("<I", blob)[0], 4
    assert version == 0
    sections = []
    for _ in range(4):
        (length,) = struct.unpack_from("<I", blob, offset)
        sections.append(blob[offset + 4 : offset + 4 + length])
        offset += 4 + length
    assert offset == len(blob), "the DataMashup has bytes outside its four sections"

    with zipfile.ZipFile(io.BytesIO(sections[0])) as package:
        return package.read("Formulas/Section1.m").decode("utf-8-sig")


def test_the_committed_charts_workbook_holds_the_parameter_the_code_overwrites():
    section = read_section_m(COMMITTED_WORKBOOK)
    match = re.search(rf'shared\s+{PARAMETER_NAME}\s*=\s*"(?:[^"]|"")*"\s+meta\s*\[([^\]]*)\]', section)
    assert match, f"{COMMITTED_WORKBOOK.name} has no {PARAMETER_NAME} parameter"
    fields = {re.sub(r"\s+", "", field) for field in match.group(1).split(",")}
    assert {"IsParameterQuery=true", 'Type="Text"', "IsParameterQueryRequired=true"} <= fields


def test_pywin32_is_windows_only_and_installed_by_default():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = pyproject["dependency-groups"]["excel"]
    assert len(requirements) == 1 and requirements[0].startswith("pywin32")
    assert "sys_platform == 'win32'" in requirements[0]
    assert not any("pywin32" in dependency for dependency in pyproject["project"]["dependencies"])
    assert {"dev", "excel"} <= set(pyproject["tool"]["uv"]["default-groups"])
