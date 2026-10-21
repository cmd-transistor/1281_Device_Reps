#!/usr/bin/env python
"""Structure name field map: how every family's name splits on '-' and how the fields line up across families.

Reads OUTPUT/StructureName_Validation.csv (superflow, corrected names) and writes OUTPUT/Structure_Field_Map.xlsx:
  Field_Map    one row per field position, one column per family (what that position holds), colour = meaning
  Examples     one example name per family split into the same position columns
  Split_All    every corrected name split by '-' into F1..F12 (+ Family, instance, TC, seat)
  <FAMILY>     one sheet per family with the named fields (and the skew split into its 5 chunks)
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

import structure_name_validator as snv

SRC = snv.OUTPUT_DIR / 'StructureName_Validation.csv'
OUT = snv.OUTPUT_DIR / 'Structure_Field_Map.xlsx'
TC = re.compile(r'(_TC\d+[A-Z]?_\d+)$')

PFT = ['Head', 'Z', 'Legs', 'VT', 'Env', 'Nest', 'Iso', 'Height', 'Skew']
FIELDS = {
    'PFT': PFT, 'FK': PFT,
    'MLR': ['Head', 'Z', 'ADF', 'VT', 'Env', 'Nest', 'Iso', 'Height', 'Skew'],
    'ROS': ['Head', 'Z', 'Legs', 'VT', 'Style', 'STG', 'FO', 'Height', 'Skew', 'Routing', 'Add'],
    'ROSPFT': ['Head', 'Z', 'Legs', 'VT', 'Style', 'Nest', 'Iso', 'Height', 'Skew', 'Routing', 'Add1', 'Add2'],
    'BITCELL': ['Head', 'Diff', 'Legs', 'DUT', 'Lib', 'Nest', 'Iso', 'Height', 'Skew'],
    'ISO': ['Head', 'Z', 'Legs', 'VT', 'Skew'],
    'KLV': ['Head', 'Z', 'Legs', 'VT', 'Skew'],
    'BCLA': ['Head', 'Lib', 'Add'],
    'QREL': [f'F{i}' for i in range(1, 13)],
    'BE': ['Head', 'Core2', 'Core3', 'Core4', 'Core5', 'Skew', 'Add'],
}
FAMILY_ORDER = ['PFT', 'MLR', 'FK', 'ROS', 'ROSPFT', 'BITCELL', 'ISO', 'KLV', 'BE', 'QREL', 'BCLA']
MEANING = {      # field name -> colour group
    'Head': 'Head', 'Z': 'Z', 'Diff': 'Z', 'Legs': 'Legs', 'ADF': 'Legs', 'VT': 'VT', 'DUT': 'VT', 'Skew': 'Skew',
}
COLOURS = {'Head': 'BDD7EE', 'Z': 'C6E0B4', 'Legs': 'FFE699', 'VT': 'F8CBAD', 'Skew': 'F4B6D6', 'Other': 'E7E6E6'}
MAXF = 12


def split_name(name: str):
    m = TC.search(name)
    tc = m.group(1) if m else ''
    base = name[:m.start()] if m else name
    inst = ''
    if re.search(r'_\d+$', base):
        base, inst = base.rsplit('_', 1)
    return base, inst, tc


def named(fam: str, f: list[str]) -> dict:
    names = list(FIELDS.get(fam, []))
    if fam == 'BE':                       # core fields vary by type: put the skew field (A:.. / DR) in 'Skew'
        sk = next((i for i, t in enumerate(f) if i and (t.startswith('A:') or t == 'DR')), None)
        out = {'Head': f[0]}
        for i in range(1, len(f)):
            out['Skew' if i == sk else ('Add' if sk is not None and i > sk else f'Core{i + 1}')] = f[i]
        return out
    return {n: (f[i] if i < len(f) else '') for i, n in enumerate(names)} | (
        {f'Extra{i + 1}': v for i, v in enumerate(f[len(names):], start=len(names))} if len(f) > len(names) else {})


def colour(n: str) -> str:
    return COLOURS[MEANING.get(n, 'Other')]


def main():
    snv.OUTPUT_DIR.mkdir(exist_ok=True)
    st = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    sf = st[(st.Source == 'Superflow') & (st.Corrected_Name != '')].copy()
    parts = sf.Corrected_Name.apply(split_name)
    sf['Base'], sf['Instance'], sf['TC'] = zip(*parts)
    sf = sf.drop_duplicates(['Family', 'Base', 'Instance'])
    sf['F'] = sf.Base.str.split('-')

    # position x family matrix
    matrix = pd.DataFrame(index=[f'F{i}' for i in range(1, MAXF + 1)], columns=FAMILY_ORDER).fillna('')
    for fam in FAMILY_ORDER:
        for i, n in enumerate(FIELDS[fam][:MAXF]):
            matrix.loc[f'F{i + 1}', fam] = n
    counts = sf.groupby('Family').F.apply(lambda s: s.str.len().value_counts().to_dict())
    matrix.loc['Names'] = [int((sf.Family == f).sum()) for f in FAMILY_ORDER]
    matrix.loc['Fields per name'] = [', '.join(f'{k}' for k in sorted(sf[sf.Family == f].F.str.len().unique()))
                                     for f in FAMILY_ORDER]
    matrix = matrix.reset_index().rename(columns={'index': 'Position'})

    examples = []
    for fam in FAMILY_ORDER:
        g = sf[sf.Family == fam]
        if g.empty:
            continue
        top = g.F.str.len().mode()[0]
        r = g[g.F.str.len() == top].iloc[0]
        row = {'Family': fam, 'Example': r.Corrected_Name}
        row.update({f'F{i + 1}': (r.F[i] if i < len(r.F) else '') for i in range(MAXF)})
        examples.append(row)
    examples = pd.DataFrame(examples)

    wide = sf[['Family', 'Corrected_Name', 'Base']].copy()
    for i in range(MAXF):
        wide[f'F{i + 1}'] = sf.F.apply(lambda f, i=i: f[i] if i < len(f) else '')
    wide['Instance'], wide['TC'] = sf.Instance, sf.TC
    wide = wide.drop(columns='Base').sort_values(['Family', 'Corrected_Name'])

    sheets = {'Field_Map': matrix, 'Examples': examples, 'Split_All': wide}
    for fam in FAMILY_ORDER:
        g = sf[sf.Family == fam]
        if g.empty:
            continue
        rows = []
        for r in g.itertuples():
            d = {'Corrected_Name': r.Corrected_Name}
            d.update(named(fam, r.F))
            sk = d.get('Skew', '')
            if sk.startswith('A:'):
                for k, c in enumerate(sk.split(':')[:6], start=1):
                    d[f'Skew{k}'] = c
            d['Instance'], d['TC'] = r.Instance, r.TC
            rows.append(d)
        sheets[fam] = pd.DataFrame(rows).fillna('')

    with pd.ExcelWriter(OUT, engine='openpyxl') as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = 'B2' if name != 'Field_Map' else 'B2'
            for i, c in enumerate(df.columns, start=1):
                w = min(max([len(str(c))] + [len(str(x)) for x in df[c].head(300)]) + 2, 60)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = w
                head = ws.cell(1, i)
                head.font = Font(bold=True)
                head.alignment = Alignment(horizontal='center')
                if name in sheets and name not in ('Field_Map', 'Examples', 'Split_All'):
                    head.fill = PatternFill('solid', fgColor=colour(str(c)))
            if name == 'Field_Map':
                for row in ws.iter_rows(min_row=2, max_row=MAXF + 1, min_col=2):
                    for cell in row:
                        if cell.value:
                            cell.fill = PatternFill('solid', fgColor=colour(str(cell.value)))
                            cell.alignment = Alignment(horizontal='center')
            if name in ('Examples', 'Split_All'):
                first = 3 if name == 'Examples' else 3
                for i in range(MAXF):
                    ws.cell(1, first + i).fill = PatternFill('solid', fgColor='D9D9D9')
            ws.auto_filter.ref = ws.dimensions
        legend = pd.DataFrame({'Colour meaning': list(COLOURS)})
        legend.to_excel(xw, sheet_name='Legend', index=False)
        ws = xw.sheets['Legend']
        for r, k in enumerate(COLOURS, start=2):
            ws.cell(r, 1).fill = PatternFill('solid', fgColor=COLOURS[k])
        ws.column_dimensions['A'].width = 20
    print(f'{len(sf)} names, {len(FAMILY_ORDER)} families -> {OUT}')
    print(matrix.head(10).to_string(index=False))


if __name__ == '__main__':
    main()
