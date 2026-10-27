#!/usr/bin/env python
"""Carry test package selections from an older superflow (with package columns) onto a newer superflow.

A line is matched on row (a trailing '_A' row split is folded back), updated structure name, test name and config;
config renames are detected from lines that only differ in config. Everything that does not carry over cleanly is
listed in the report.

    python carry_packages.py [--old OLD.csv] [--new NEW.csv]
"""
from __future__ import annotations

import argparse
import difflib
import re
from pathlib import Path

import numpy as np
import pandas as pd

from testpackage_model import BASE_COLS, HERE, SUPERFLOW

NEW_DEFAULT = HERE / '1X281Z_SUPER_FLOW_R3.csv'
VALIDATION = HERE / 'OUTPUT' / 'StructureName_Validation.csv'
RENAME = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename.csv'
TC = re.compile(r'(_TC\d+[A-Z]?_\d+)$')
KEY = ['RowKey', 'Structure', 'Test Configuration Name', 'Test']


def name_candidates(old: pd.DataFrame) -> pd.DataFrame:
    """Possible new names per old line: Atlantis rename table (row-specific), validator correction, unchanged."""
    val = pd.read_csv(VALIDATION, dtype=str, keep_default_na=False)
    val = val[val.Source == 'Superflow'].drop_duplicates('Original_Name')
    corrected = dict(zip(val.Original_Name, val.Corrected_Name))
    row_names = {(r, o): n for o, s in zip(val.Original_Name, val.Row_Specific_Names)
                 for r, n in (p.split('=', 1) for p in s.split('; ') if p)}
    ren = pd.read_csv(RENAME, dtype=str, keep_default_na=False)
    ren_map = dict(zip(zip(ren.TESTROWNAME.str.lower(), ren.OLD_TESTSTRUCTNAME), ren.TESTSTRUCTNAME))
    atl_row = old['Test Row Alias'].str.replace(TC, '', regex=True).str.lower()
    m = old['Test Structure Alias'].str.extract(r'^(.*?)(_TC\d+[A-Z]?_\d+)$')
    base, suffix = m[0].fillna(old['Test Structure Alias']), m[1].fillna('')
    renamed = [ren_map.get((r, b)) for r, b in zip(atl_row, base)]
    return pd.DataFrame({'renamed': [f'{n}{s}' if n else '' for n, s in zip(renamed, suffix)],
                         'corrected': [row_names.get((r, n), corrected.get(n, '')) for r, n in
                                       zip(old['Test Row Alias'], old['Test Structure Alias'])],
                         'original': old['Test Structure Alias']}, index=old.index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old', type=Path, default=SUPERFLOW)
    ap.add_argument('--new', type=Path, default=NEW_DEFAULT)
    args = ap.parse_args()

    old = pd.read_csv(args.old, dtype=str, keep_default_na=False)
    new = pd.read_csv(args.new, dtype=str, keep_default_na=False)
    packages = [c for c in old.columns if c not in BASE_COLS]
    if not packages:
        raise SystemExit(f'{args.old.name} has no package columns to carry over')
    if [c for c in new.columns if c not in BASE_COLS]:
        print(f'note: {args.new.name} already has package columns; they are replaced in the output')
    new = new[BASE_COLS].copy()
    for d in (old, new):
        d['RowKey'] = d['Test Row Alias'].str.replace(r'_A$', '', regex=True)
        d['Test'] = d['Test Names'].str.split('|').str[0]
    new['Structure'] = new['Test Structure Alias']

    # choose the candidate new name that exists in the new superflow for the same row
    cand = name_candidates(old)
    present = set(zip(new.RowKey, new.Structure))
    old['Structure'], old['Name_Rule'] = old['Test Structure Alias'], 'not in new superflow'
    for rule in ('original', 'corrected', 'renamed'):
        hit = [(rk, n) in present for rk, n in zip(old.RowKey, cand[rule])]
        old.loc[hit, 'Structure'], old.loc[hit, 'Name_Rule'] = cand.loc[hit, rule], rule

    # pair identical lines occurrence by occurrence (both superflows contain repeated lines)
    for d in (old, new):
        d['Occ'] = d.groupby(KEY).cumcount()
    old['NewIndex'] = old.merge(new.reset_index()[KEY + ['Occ', 'index']], on=KEY + ['Occ'], how='left')['index'].values
    old['Config_Change'] = ''
    # second pass: same row / structure / test under a renamed config
    gone = old.NewIndex.isna()
    free = new.loc[~new.index.isin(old.NewIndex.dropna().astype(int))].copy()
    k2 = ['RowKey', 'Structure', 'Test']
    free['Occ2'] = free.groupby(k2).cumcount()
    rest = old.loc[gone].copy()
    rest['Occ2'] = rest.groupby(k2).cumcount()
    m2 = rest.reset_index().merge(free.reset_index()[k2 + ['Occ2', 'index', 'Test Configuration Name']],
                                  on=k2 + ['Occ2'], how='inner', suffixes=('', '_new'))
    old.loc[m2['index'], 'NewIndex'] = m2['index_new'].values
    old.loc[m2['index'], 'Config_Change'] = (m2['Test Configuration Name'] + ' -> '
                                             + m2['Test Configuration Name_new']).values

    # third pass: same row / structure / config but the new superflow lists different test names (whole group)
    k3 = ['RowKey', 'Structure', 'Test Configuration Name']
    claimed = set(old.NewIndex.dropna().astype(int))
    free3 = new.loc[~new.index.isin(claimed)]
    free_groups = free3.groupby(k3).groups
    old['Test_Change'] = False
    for key, idx in old.loc[old.NewIndex.isna()].groupby(k3).groups.items():
        if key in free_groups:
            old.loc[idx, 'Test_Change'] = True
    group_lines = {key: free_groups[key] for key in
                   set(map(tuple, old.loc[old.Test_Change, k3].to_numpy()))}
    claimed |= {i for idx in group_lines.values() for i in idx}

    # fourth pass: same row / config / test, structure renamed in the row (closest remaining name)
    pools = {k: list(v) for k, v in new.loc[~new.index.isin(claimed)]
             .groupby(['RowKey', 'Test Configuration Name', 'Test']).groups.items()}
    for idx in old.index[old.NewIndex.isna() & ~old.Test_Change]:
        r = old.loc[idx]
        pool = pools.get((r.RowKey, r['Test Configuration Name'], r.Test), [])
        names = [new.at[i, 'Structure'] for i in pool]
        best = difflib.get_close_matches(r.Structure, names, n=1, cutoff=0.85)
        if best:
            i = pool.pop(names.index(best[0]))
            old.loc[idx, ['NewIndex', 'Structure', 'Name_Rule']] = [i, best[0], 'closest name in row']
            claimed.add(i)

    matched = old.NewIndex.notna() | old.Test_Change
    out = new[BASE_COLS].copy()
    new_keys = pd.Series(list(map(tuple, new[k3].to_numpy())), index=new.index)
    for p in packages:
        in_p = old[p].str.strip().ne('')
        out[p] = ''
        out.loc[old.loc[in_p & old.NewIndex.notna(), 'NewIndex'].astype(int).unique(), p] = 'X'
        keys = set(map(tuple, old.loc[in_p & old.Test_Change, k3].to_numpy()))
        out.loc[[i for key in keys for i in group_lines[key]], p] = 'X'

    # ----- report -------------------------------------------------------------------------------------------------
    in_pkg = old[packages].apply(lambda c: c.str.strip().ne('')).any(axis=1)
    new_only = new.loc[~new.index.isin(claimed), BASE_COLS]
    summary = pd.DataFrame([{
        'Package': p, 'Old_Tests': int(old[p].str.strip().ne('').sum()),
        'Carried_To_New': int(out[p].eq('X').sum()),
        'Not_In_New': int((old[p].str.strip().ne('') & ~matched).sum()),
        'Carried_Via_Config_Rename': int((old[p].str.strip().ne('') & old.Config_Change.ne('')).sum()),
        'Carried_Via_Test_Name_Change': int((old[p].str.strip().ne('') & old.Test_Change).sum()),
        'Carried_Via_Name_Change': int((old[p].str.strip().ne('') & matched & old.Name_Rule.ne('original')).sum()),
        'Old_Structures': old.loc[old[p].str.strip().ne(''), 'Test Structure Alias'].nunique(),
        'New_Structures': out.loc[out[p].eq('X'), 'Test Structure Alias'].nunique(),
    } for p in packages])

    issues = []

    def flag(category, detail, count, example=''):
        issues.append({'Category': category, 'Detail': detail, 'Count': int(count), 'Example': example})

    split_rows = sorted(set(new['Test Row Alias'][new['Test Row Alias'].str.endswith('_A')]))
    if split_rows:
        flag('Row split', 'New rows ending in _A hold structures that were in the base row before',
             len(split_rows), ', '.join(split_rows[:4]))
    cc = old.loc[old.Config_Change.ne('')].drop_duplicates('Config_Change')
    for r in cc.itertuples():
        n = int(old.Config_Change.eq(r.Config_Change).sum())
        carried = old.loc[old.Config_Change.eq(r.Config_Change), 'NewIndex'].astype(int)
        tests = sorted(set(new.loc[carried, 'Test']))
        pts = re.search(r'(\d+)PT$', r.Config_Change.split(' -> ')[1])
        extra = f' (config name says {pts.group(1)} points but these lines carry {len(tests)} tests)' \
            if pts and int(pts.group(1)) != len(tests) else ''
        flag('Config renamed', r.Config_Change + extra, n, r.Test)
    volt = new[['Test Configuration Name', 'Test']].drop_duplicates()
    cv = volt['Test Configuration Name'].str.extract(r'(\d+\.\d+)V', expand=False)
    tv = volt.Test.str.extract(r'(\d+\.\d+)V', expand=False)
    for r in volt[cv.notna() & tv.notna() & cv.ne(tv)].itertuples():
        flag('Config/test voltage mismatch', f'Config {r[1]} runs test {r.Test}',
             int(((new['Test Configuration Name'] == r[1]) & (new.Test == r.Test)).sum()))
    tc = old.loc[old.Test_Change].drop_duplicates(k3)
    if len(tc):
        flag('Test names changed', 'Same row / structure / config, but the new superflow lists different test names; '
             'the package keeps the structure + config with the new test list', len(tc),
             f'{tc.iloc[0]["Test Configuration Name"]}: {tc.iloc[0].Test} | {tc.iloc[0].Structure}')
    cl = old.loc[old.Name_Rule.eq('closest name in row')].drop_duplicates(['Test Structure Alias', 'Structure'])
    if len(cl):
        flag('Structure renamed in row', 'Old name not in the new row; carried to the closest remaining name',
             len(cl), f'{cl.iloc[0]["Test Structure Alias"]} -> {cl.iloc[0].Structure}')
    gone_cfg = sorted(set(old['Test Configuration Name']) - set(new['Test Configuration Name'])
                      - {c.split(' -> ')[0] for c in cc.Config_Change})
    for c in gone_cfg:
        flag('Config removed', f'{c} has no counterpart in the new superflow',
             int((old['Test Configuration Name'] == c).sum()))
    new_cfg = sorted(set(new['Test Configuration Name']) - set(old['Test Configuration Name'])
                     - {c.split(' -> ')[1] for c in cc.Config_Change})
    for c in new_cfg:
        flag('Config added', f'{c} is new; not part of any carried package',
             int((new['Test Configuration Name'] == c).sum()))
    renamed = old.loc[matched & old.Name_Rule.eq('renamed') & (old.Structure != cand.corrected)]
    if len(renamed):
        flag('Name differs from validator', 'New superflow uses the row-specific Atlantis name (e.g. A:WID in WID '
             'rows) instead of the validator correction', renamed.Structure.nunique(),
             f'{renamed.iloc[0]["Test Structure Alias"]} -> {renamed.iloc[0].Structure}')
    unm = old.loc[~matched]
    if len(unm):
        flag('Old lines not in new', 'Old superflow lines with no new counterpart', len(unm),
             unm.iloc[0]['Test Names'])
        flag('Old package lines lost', 'Of those, lines that were in at least one package',
             int((~matched & in_pkg).sum()))
    flag('New lines not in old', 'New superflow lines with no old counterpart (not in any package)', len(new_only),
         new_only.iloc[0]['Test Names'] if len(new_only) else '')
    dup = new.duplicated(BASE_COLS, keep=False)
    if dup.any():
        top = new.loc[dup, 'Test Configuration Name'].value_counts().head(3).to_dict()
        flag('Duplicate lines', f'Identical lines repeated in the new superflow (old had '
             f'{int(old.duplicated(BASE_COLS, keep=False).sum()):,}); top configs {top}', int(dup.sum()))

    stem = HERE / 'OUTPUT' / f'{args.new.stem}_packages'
    out.to_csv(stem.with_suffix('.csv'), index=False)
    lost = unm.loc[in_pkg[~matched].values if len(unm) else [], BASE_COLS + packages]
    detail = old.loc[matched & (old.Name_Rule.ne('original') | old.Config_Change.ne('')),
                     ['Test Row Alias', 'Test Structure Alias', 'Structure', 'Name_Rule',
                      'Test Configuration Name', 'Config_Change', 'Test']] \
        .drop_duplicates(['Test Structure Alias', 'Structure', 'Config_Change']) \
        .rename(columns={'Test Structure Alias': 'Old_Structure', 'Structure': 'New_Structure'})
    with pd.ExcelWriter(HERE / 'OUTPUT' / f'{args.old.stem[:24]}_to_{args.new.stem}_Carryover.xlsx'.replace(' ', '_'),
                        engine='xlsxwriter') as xw:
        head = xw.book.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': 'white'})
        for sheet, df in [('Summary', summary), ('Inconsistencies', pd.DataFrame(issues)),
                          ('Lost_Package_Lines', lost), ('New_Lines', new_only), ('Name_Config_Changes', detail)]:
            df.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, head)
                ws.set_column(i, i, min(max([len(str(col))] + [len(str(x)) for x in df[col].head(300)]) + 2, 70))
            ws.freeze_panes(1, 0)
            if len(df):
                ws.autofilter(0, 0, len(df), len(df.columns) - 1)
    print(f'{len(packages)} packages carried from {args.old.name} to {args.new.name} -> {stem.with_suffix(".csv").name}')
    print(summary.to_string(index=False))
    print()
    print(pd.DataFrame(issues).to_string(index=False, max_colwidth=110))


if __name__ == '__main__':
    main()
