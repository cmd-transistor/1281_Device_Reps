#!/usr/bin/env python
"""Row-owner summary: which structure names did not follow the original BKM, and what else was standardized.

Joins the test-row tracker (X81Z_Device_Testrow_Tracker.xlsx, sheet X81Z_Row_Tracker: row -> owner) with the
validator result (OUTPUT/StructureName_Validation.csv, superflow rows) and writes OUTPUT/Row_Owner_Summary.xlsx.

Two groups of corrections (edit BKM_VIOLATION_CODES to move a code between them):
  * BKM violation   - a rule that exists in the original WW19'26 BKM deck was not followed
  * Standardization - further optimization / consistency from the WW43-WW45 owner decisions (not in the original deck)
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

import structure_name_validator as snv

HERE = Path(__file__).resolve().parent
TRACKER = HERE / 'X81Z_Device_Testrow_Tracker.xlsx'
VALIDATION = snv.OUTPUT_DIR / 'StructureName_Validation.csv'
OUT = snv.OUTPUT_DIR / 'Row_Owner_Summary.xlsx'
TC = re.compile(r'_TC\d+[A-Z]?_\d+$')

BKM_VIOLATION_CODES = {
    'ORDER', 'VT_SPLIT', 'UNDERSCORE_IN_BODY', 'UNDERSCORE_DELIM', 'CAT_CH_SPLIT', 'DUP_FIELD', 'WID_PREFIX',
    'MATCHING_PREFIX', 'DIFF_NUMERIC', 'LIB_CH_Z', 'LIB_CH', 'LIB_BITCELL_DIFF', 'LIB_BITCELL_CH', 'LEN_LIMIT', 'FIX_LEN',
    'CHARSET', 'EMPTY_FIELD', 'SLASH', 'MISSING_FIELD', 'HEAD', 'NO_TC_SUFFIX', 'LOWERCASE', 'COLLISION',
}


def load_tracker() -> pd.DataFrame:
    t = pd.read_excel(TRACKER, sheet_name='X81Z_Row_Tracker', header=0, dtype=str).fillna('')
    t.columns = [c.strip() for c in t.columns]
    t = t[t['Test Row Name'].str.strip() != '']
    t['RowBase'] = t['Test Row Name'].str.strip().str.lower()
    t['Owner'] = t['Test Row Owner'].str.strip().replace('', '(no owner)')
    return t.drop_duplicates('RowBase')


def load_structures() -> pd.DataFrame:
    v = pd.read_csv(VALIDATION, dtype=str, keep_default_na=False)
    v = v[v.Source == 'Superflow'].copy()
    v['RowOne'] = v.Row.str.split('; ')
    v = v.explode('RowOne')
    v['RowBase'] = v.RowOne.str.replace(TC, '', regex=True).str.lower()
    codes = v.Issue_Codes.str.split(', ')
    v['BKM_Codes'] = codes.apply(lambda cs: ', '.join(c for c in cs if c in BKM_VIOLATION_CODES))
    v['Standardization_Codes'] = codes.apply(lambda cs: ', '.join(c for c in cs if c and c not in BKM_VIOLATION_CODES))
    v['Category'] = v.apply(lambda r: 'BKM violation' if r.BKM_Codes else ('Standardized only' if r.Standardization_Codes
                                                                         else 'Compliant'), axis=1)
    return v


def main():
    snv.OUTPUT_DIR.mkdir(exist_ok=True)
    t, s = load_tracker(), load_structures()
    d = s.merge(t[['RowBase', 'Owner', 'Library', 'TCE Template', 'Area', 'Structure Type', 'Row Description',
                   'Content Owner']], on='RowBase', how='left')
    matched = d[d.Owner.notna()].copy()
    matched['RB'] = matched.RowBase

    def breakdown(df):
        u = df.drop_duplicates('Original_Name')
        order_only = u.BKM_Codes.eq('ORDER')
        return pd.Series({
            'Rows': df.RB.nunique(), 'Structures': len(u),
            'Compliant (unchanged)': int((u.Category == 'Compliant').sum()),
            'BKM violation (original BKM)': int((u.Category == 'BKM violation').sum()),
            '  of which only VT/legs field order': int(order_only.sum()),
            'Standardized only (WW43-45)': int((u.Category == 'Standardized only').sum()),
            'Manual': int((u.Status == 'MANUAL').sum())})

    own = matched.groupby('Owner').apply(breakdown, include_groups=False).reset_index()
    own['% not following original BKM'] = (100 * own['BKM violation (original BKM)'] / own.Structures).round(1)
    own['% renamed (any change)'] = (100 * (own.Structures - own['Compliant (unchanged)']) / own.Structures).round(1)
    top = {}
    for o, g in matched.groupby('Owner'):
        c = g.BKM_Codes.str.split(', ').explode()
        top[o] = ', '.join(f'{k} ({n})' for k, n in c[c != ''].value_counts().head(5).items())
    own['Top BKM violations'] = own.Owner.map(top)
    top = {}
    for o, g in matched.groupby('Owner'):
        c = g.Standardization_Codes.str.split(', ').explode()
        top[o] = ', '.join(f'{k} ({n})' for k, n in c[c != ''].value_counts().head(5).items())
    own['Top standardizations'] = own.Owner.map(top)
    own = own.sort_values('Structures', ascending=False)

    rows = matched.groupby(['Owner', 'RowBase']).apply(breakdown, include_groups=False).reset_index()
    meta = t.set_index('RowBase')[['Library', 'TCE Template', 'Area', 'Structure Type', 'Row Description', 'Content Owner']]
    rows = rows.join(meta, on='RowBase').rename(columns={'RowBase': 'Row'}).sort_values(['Owner', 'Row'])

    long = []
    for col, grp in (('BKM_Codes', 'BKM violation'), ('Standardization_Codes', 'Standardization')):
        x = matched[matched[col] != ''].assign(Code=lambda f: f[col].str.split(', ')).explode('Code')
        for (o, c), g in x.groupby(['Owner', 'Code']):
            ex = g.sort_values('Original_Name').iloc[0]
            what, how = snv.CODE_DOC.get(c, ('', ''))
            long.append({'Owner': o, 'Group': grp, 'Code': c, 'Structures': g.Original_Name.nunique(),
                         'Rows': g.RowBase.nunique(), 'What was wrong / changed': what, 'Correction': how,
                         'Example': f'{ex.Original_Name} -> {ex.Corrected_Name or ex.Draft_Name_Manual}'})
    breakdown_df = pd.DataFrame(long).sort_values(['Owner', 'Group', 'Structures'], ascending=[True, False, False])

    detail = matched[['Owner', 'RowBase', 'Family', 'Original_Name', 'Corrected_Name', 'Status', 'Category', 'BKM_Codes',
                      'Standardization_Codes', 'Issues']].rename(columns={'RowBase': 'Row'})
    detail = detail[detail.Category != 'Compliant'].sort_values(['Owner', 'Row', 'Original_Name'])

    miss_tracker = sorted(set(t.RowBase) - set(s.RowBase))
    miss_owner = (d[d.Owner.isna()].groupby('RowBase').Original_Name.nunique().reset_index()
                  .rename(columns={'RowBase': 'Superflow row without tracker owner', 'Original_Name': 'Structures'}))
    not_in_flow = t[t.RowBase.isin(miss_tracker)][['Test Row Name', 'Owner', 'Library', 'Area', 'Structure Type']]

    rules = pd.DataFrame([
        ('BKM violation', 'Original WW19 BKM rule not followed. Codes: ' + ', '.join(sorted(BKM_VIOLATION_CODES))),
        ('Standardization', 'Further consistency from the WW43-WW45 owner decisions (single-letter VT, A:NOM, TSDR skew grammar, '
                            'PLY/TCN vocabulary, routing removal, NL/SR fields, :LLE tag, BE uniform layout, ...).'),
        ('Compliant', 'Original name unchanged by the validator.'),
        ('Counting', 'Structures = unique superflow names (incl. _TCxx_y) listed in the owner\'s rows; a name in two rows counts '
                     'in both. Owner = "Test Row Owner" of sheet X81Z_Row_Tracker.'),
    ], columns=['Topic', 'Rule'])

    with pd.ExcelWriter(OUT, engine='openpyxl') as xw:
        for name, df in [('Owner_Summary', own), ('Row_Summary', rows), ('Owner_Issue_Breakdown', breakdown_df),
                         ('Structure_Detail', detail), ('Tracker_Rows_Not_In_Superflow', not_in_flow),
                         ('Superflow_Rows_No_Owner', miss_owner), ('Rules', rules)]:
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = 'A2'
            if len(df):
                ws.auto_filter.ref = ws.dimensions
            for i, c in enumerate(df.columns, start=1):
                w = min(max([len(str(c))] + [len(str(x)) for x in df[c].head(200)]) + 2, 70)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    print(f'{len(own)} owners, {len(rows)} rows, {len(detail)} non-compliant owner/structure pairs -> {OUT}')
    print(own[['Owner', 'Rows', 'Structures', 'Compliant (unchanged)', 'BKM violation (original BKM)',
               '  of which only VT/legs field order', 'Standardized only (WW43-45)',
               '% not following original BKM']].to_string(index=False))
    print('tracker rows not in superflow:', len(not_in_flow), '| superflow rows without owner:', len(miss_owner))


if __name__ == '__main__':
    main()
