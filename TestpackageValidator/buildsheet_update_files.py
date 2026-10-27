#!/usr/bin/env python
"""One Atlantis buildsheet update workbook per library, to share with the device owners.

Reads OUTPUT/Atlantis_Structure_Rename.csv (run atlantis_rename_table.py first), OUTPUT/Atlantis_Row_Summary.csv and
OUTPUT/Buildsheet_Issues.csv (buildsheet_issues.py) and writes OUTPUT/Buildsheet_Updates/<LIBRARY>.xlsx with:
  Summary  - rows of the library with owner and counts
  Updates  - every structure to edit: new name, suggested comment (where Atlantis had none),
             new open-cal reference (Miller, only when the referenced structure was renamed)
  Issues   - buildsheet / Atlantis data issues of the library (when any)
plus OUTPUT/Buildsheet_Updates/_Index.csv.

    python buildsheet_update_files.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / 'OUTPUT' / 'Buildsheet_Updates'
RENAME = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename.csv'
ROWS = HERE / 'OUTPUT' / 'Atlantis_Row_Summary.csv'
ISSUES = HERE / 'OUTPUT' / 'Buildsheet_Issues.csv'
UPDATE_COLS = ['TESTROWNAME', 'OWNER', 'TEMPLATENAME', 'TESTSTRUCTID', 'OLD_TESTSTRUCTNAME', 'TESTSTRUCTNAME',
               'CHANGE', 'TESTSTRUCTCOMMENT', 'OLD_OPENCAL_STRUCTURE', 'OPENCAL_STRUCTURE']


def write_book(path: Path, sheets: dict[str, pd.DataFrame]):
    with pd.ExcelWriter(path, engine='xlsxwriter') as xw:
        head = xw.book.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': 'white', 'border': 1})
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, head)
                width = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)])
                ws.set_column(i, i, min(width + 2, 70))
            ws.freeze_panes(1, 0)
            if len(df):
                ws.autofilter(0, 0, len(df), len(df.columns) - 1)


def main():
    ren = pd.read_csv(RENAME, dtype=str, keep_default_na=False)
    rows = pd.read_csv(ROWS, dtype=str, keep_default_na=False)
    owner = dict(zip(rows.TESTROWNAME, rows.OWNER))
    iss = pd.read_csv(ISSUES, dtype=str, keep_default_na=False) if ISSUES.exists() else pd.DataFrame(columns=['LIBRARY'])

    name = ren.TESTSTRUCTNAME != ren.OLD_TESTSTRUCTNAME
    comment = ren.TESTSTRUCTCOMMENT != ren.OLD_TESTSTRUCTCOMMENT
    opencal = ren.OPENCAL_STRUCTURE != ren.OLD_OPENCAL_STRUCTURE
    upd = ren[name | comment | opencal].copy()
    upd['CHANGE'] = [', '.join(c for c, f in (('rename', a), ('comment', b), ('open-cal', d)) if f)
                     for a, b, d in zip(name[upd.index], comment[upd.index], opencal[upd.index])]
    upd.loc[~comment[upd.index], 'TESTSTRUCTCOMMENT'] = ''
    upd.loc[~opencal[upd.index], ['OPENCAL_STRUCTURE', 'OLD_OPENCAL_STRUCTURE']] = ''
    upd['OWNER'] = upd.TESTROWNAME.map(owner).fillna('')

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for lib in sorted(set(upd.LIBRARY) | set(iss.LIBRARY) - {''}):
        u = upd[upd.LIBRARY == lib][UPDATE_COLS]
        i = iss[iss.LIBRARY == lib].drop(columns=['LIBRARY'], errors='ignore')
        summ = (u.groupby(['TESTROWNAME', 'OWNER'])
                .agg(Structures=('TESTSTRUCTID', 'size'),
                     Renamed=('CHANGE', lambda s: s.str.contains('rename').sum()),
                     Comments_Added=('CHANGE', lambda s: s.str.contains('comment').sum()),
                     OpenCal_Updated=('CHANGE', lambda s: s.str.contains('open-cal').sum()))
                .reset_index())
        if len(i):
            summ = summ.merge(i.groupby('TESTROWNAME').size().rename('Buildsheet_Issues').reset_index(),
                              on='TESTROWNAME', how='outer').fillna({'OWNER': '', 'Buildsheet_Issues': 0})
        sheets = {'Summary': summ, 'Updates': u}
        if len(i):
            sheets['Issues'] = i
        path = OUT_DIR / f'{lib}.xlsx'
        write_book(path, sheets)
        index.append({'LIBRARY': lib, 'File': path.name, 'Rows': u.TESTROWNAME.nunique(),
                      'Owners': ', '.join(sorted(set(u.OWNER) - {''})), 'Structures_To_Update': len(u),
                      'Renamed': int(u.CHANGE.str.contains('rename').sum()),
                      'Comments_Added': int(u.CHANGE.str.contains('comment').sum()),
                      'OpenCal_Updated': int(u.CHANGE.str.contains('open-cal').sum()), 'Buildsheet_Issues': len(i)})
    idx = pd.DataFrame(index)
    idx.to_csv(OUT_DIR / '_Index.csv', index=False)
    print(f'{len(idx)} library workbooks -> {OUT_DIR.name}/: {int(idx.Structures_To_Update.sum())} structures, '
          f'{int(idx.Buildsheet_Issues.sum())} issues')


if __name__ == '__main__':
    main()
