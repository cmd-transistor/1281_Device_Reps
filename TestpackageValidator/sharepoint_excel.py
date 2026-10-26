"""Read SharePoint workbooks through a private, hidden Excel instance that uses the user's Office sign-in.

Workbooks are always opened ReadOnly and closed with SaveChanges=False; nothing is ever written back.
"""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote, urlsplit

import pandas as pd

WORKBOOK_RX = re.compile(r'\.(xlsx|xlsm|xls)$', re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Accept a direct workbook path or a '/:x:/r/' sharing link; return the plain https workbook URL."""
    parts = urlsplit(url.strip().strip('"'))
    if parts.scheme != 'https' or not parts.netloc.lower().endswith('.sharepoint.com'):
        raise ValueError('Expected an https://<tenant>.sharepoint.com workbook URL')
    path = re.sub(r'^/:x:/r/', '/', parts.path)
    if path.startswith('/:'):
        raise ValueError("Tokenised sharing links can't be resolved; in Excel use File > Info > Copy path")
    if not WORKBOOK_RX.search(unquote(path)):
        raise ValueError('The URL must point to an .xlsx, .xlsm or .xls workbook, not a folder or view')
    return f'https://{parts.netloc}{path}'


def display_name(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit('/', 1)[-1])


def _cell(v) -> str:
    if v is None:
        return ''
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat(sep=' ')
    return str(v).strip()


def _frame(used_range) -> pd.DataFrame:
    values = used_range.Value
    if values is None:
        return pd.DataFrame()
    if not isinstance(values, tuple):
        values = ((values,),)
    # pad so cell positions match A1-based sheet coordinates, like pandas.read_excel(header=None)
    top, left = used_range.Row - 1, used_range.Column - 1
    width = left + max(len(r) for r in values)
    rows = [[''] * width for _ in range(top)]
    rows += [[''] * left + [_cell(v) for v in r] + [''] * (width - left - len(r)) for r in values]
    return pd.DataFrame(rows)


def read_workbook(url: str) -> dict[str, pd.DataFrame]:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    excel = wb = None
    try:
        excel = win32com.client.DispatchEx('Excel.Application')
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        wb = excel.Workbooks.Open(url, UpdateLinks=0, ReadOnly=True, IgnoreReadOnlyRecommended=True,
                                  Notify=False, AddToMru=False)
        return {ws.Name: _frame(ws.UsedRange) for ws in wb.Worksheets}
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()
