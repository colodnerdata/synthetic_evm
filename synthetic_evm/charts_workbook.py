"""Keep the charts workbook's Power Query parameter pointed at the latest ``monthly.csv``.

``monthly_charts.xlsx`` holds one Power Query *parameter*, ``pCsvPath`` (text). A query built on
``File.Contents(pCsvPath)``, and the pivot and charts built on that query, read whatever path the parameter
holds, so after a run only that one value has to change. :func:`update_charts_workbook` overwrites it in
place and touches nothing else in the workbook: queries, pivots and charts stay as they are, and nothing is
refreshed (use *Data > Refresh All* in Excel to load the new run).

The overwrite drives desktop Excel through COM (``pywin32``), so it runs only on Windows with Excel 2016 or
later. Anywhere else it reports that the workbook was not updated and the run carries on: the CSVs are
written first and never depend on this step.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CHARTS_WORKBOOK_NAME = "monthly_charts.xlsx"
PARAMETER_NAME = "pCsvPath"

# Marks a query as a required text parameter; the M text literal in front of it is its current value.
PARAMETER_META = 'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'


class ChartsWorkbookError(RuntimeError):
    """The charts workbook could not be updated; the message says why and what to do."""


def m_text_literal(value: str) -> str:
    """Return ``value`` as a Power Query M text literal.

    Inside an M text literal a double quote is written ``""`` and ``#(`` opens an escape sequence, so a
    literal ``#(`` is written ``#(#)(`` (``#(#)`` is the escape for ``#``). Backslashes are not special.
    """
    return '"' + value.replace('"', '""').replace("#(", "#(#)(") + '"'


def parameter_formula(csv_path: str | Path) -> str:
    """The M formula that makes ``pCsvPath`` a required text parameter holding ``csv_path``."""
    return f"{m_text_literal(str(csv_path))} {PARAMETER_META}"


def _dispatch_excel() -> Any:
    """Start a private Excel instance, separate from any Excel window the user has open."""
    import win32com.client  # pywin32; imported here because it exists on Windows only

    return win32com.client.DispatchEx("Excel.Application")


def _same_formula(current: object, wanted: str) -> bool:
    """Whether Excel's formula text equals ``wanted``, ignoring line breaks and spacing Excel may add."""
    return " ".join(str(current).split()) == " ".join(wanted.split())


def _find_parameter(book: Any) -> Any:
    for index in range(1, book.Queries.Count + 1):  # COM collections are 1-based
        query = book.Queries.Item(index)
        if query.Name == PARAMETER_NAME:
            return query
    raise ChartsWorkbookError(
        f"it has no query named {PARAMETER_NAME}; add that text parameter in Power Query (Manage Parameters)"
    )


def set_csv_path_parameter(
    workbook: str | Path,
    csv_path: str | Path,
    *,
    dispatch_excel: Callable[[], Any] = _dispatch_excel,
) -> bool:
    """Overwrite ``pCsvPath`` in ``workbook`` with ``csv_path``; return whether the value changed.

    The workbook is opened in its own hidden Excel and saved in place only if the value changed, so a run
    that writes to the same place leaves the file untouched. The workbook is always closed and Excel always
    quit, whatever happens. Raises :class:`ChartsWorkbookError` for anything that stops the overwrite.
    """
    workbook = Path(workbook).resolve()
    formula = parameter_formula(csv_path)

    try:
        excel = dispatch_excel()
    except ImportError as error:
        raise ChartsWorkbookError("pywin32 is not installed; run `uv sync` on Windows") from error
    except Exception as error:
        raise ChartsWorkbookError(f"Excel could not be started ({error})") from error

    try:
        excel.Visible = False
        excel.DisplayAlerts = False  # a dialog must never block a run
        book = excel.Workbooks.Open(str(workbook))
        try:
            if book.ReadOnly:  # Excel opens a file that is locked elsewhere read-only
                raise ChartsWorkbookError(
                    "it is open in another Excel window or is read-only; close it and run again"
                )
            query = _find_parameter(book)
            if _same_formula(query.Formula, formula):
                return False
            query.Formula = formula
            book.Save()
            return True
        finally:
            book.Close(SaveChanges=False)
    except ChartsWorkbookError:
        raise
    except Exception as error:
        raise ChartsWorkbookError(f"Excel reported an error ({error})") from error
    finally:
        with contextlib.suppress(Exception):
            excel.Quit()


def update_charts_workbook(
    monthly_csv: str | Path,
    *,
    platform: str = sys.platform,
    dispatch_excel: Callable[[], Any] = _dispatch_excel,
) -> str | None:
    """Point the charts workbook beside ``monthly_csv`` at that file; return a one-line report.

    Returns ``None`` when there is no ``monthly_charts.xlsx`` beside the CSV, so a run that writes somewhere
    else never touches the workbook. Otherwise the report says what happened: updated, already current, or why
    nothing was updated. Nothing that goes wrong with Excel raises, because the CSVs are already written.
    """
    csv_path = Path(monthly_csv).resolve()
    workbook = csv_path.parent / CHARTS_WORKBOOK_NAME
    if not workbook.exists():
        return None
    if platform != "win32":
        return f"{workbook.name} not updated: Excel automation needs Windows with desktop Excel"
    try:
        changed = set_csv_path_parameter(workbook, csv_path, dispatch_excel=dispatch_excel)
    except ChartsWorkbookError as error:
        return f"{workbook.name} not updated: {error}"
    outcome = "now points at" if changed else "already points at"
    return f"{PARAMETER_NAME} in {workbook.name} {outcome} {csv_path}"
