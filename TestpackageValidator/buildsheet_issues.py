#!/usr/bin/env python
"""Buildsheet / Atlantis data issues found while validating the X81Z names, one list for the device owners.

Checks (rows Configured for Tape-in that are in the superflow):
  FTI_SKEW_FORMAT   ISO FTICdMisregSkew must hold 4 numbers: OGD CD, PGD CD, OGD REG, PGD REG (um) [+ flag];
                    the skew is still built as named, the buildsheet needs correction
  FTI_SKEW_NAME     ISO FTICdMisregSkew value / position does not match the TSDR skew in the Atlantis name
                    (A:FTIML:PGD:CD:N4 -> 0,-0.004,0,0); the new name follows the build, the structure needs correction
  LVS_DATA          lvsDeviceData diffusion does not match the name (PLY CD mismatches are a known LVS bug: skipped)
  BLANK_NAME        structure without a name in Atlantis
ISO buildsheets are pulled read-only once into OUTPUT/Atlantis_81Z_ISO_Buildsheet_raw.csv (--refresh pulls again).
Runs before atlantis_rename_table.py, which adds the buildsheet notes to the structure comments.

    python buildsheet_issues.py [--refresh]
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from structure_name_validator import (DEFAULT_ETEST, DEFAULT_OUT, DEFAULT_SUPERFLOW, TRACKER_FILE, load_etest,
                                      load_superflow, row_key)

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / 'OUTPUT'
RAW = OUT_DIR / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
ISO_RAW = OUT_DIR / 'Atlantis_81Z_ISO_Buildsheet_raw.csv'
LVS_RAW = OUT_DIR / 'Atlantis_81Z_TapeIn_LVS.csv'
LVS_FLAGGED = HERE / 'VALIDATE' / 'LVS_Flagged_Structures.csv'
OUT = OUT_DIR / 'Buildsheet_Issues.xlsx'
TAPE_IN = 'Configured for Tape-in'
FTI_POS = {('OGD', 'CD'): 0, ('PGD', 'CD'): 1, ('OGD', 'REG'): 2, ('PGD', 'REG'): 3}
FTI_RX = re.compile(r'(?<=[-:_])([A-Z]*FTI[A-Z]*):(OGD|PGD|NOM):(CD|REG|NOM):([NP]\d+(?:D\d+)?|NOM|0)')
COLS = ['Category', 'Check', 'LIBRARY', 'TESTROWNAME', 'OWNER', 'TESTSTRUCTID', 'STRUCTURE', 'NEW_NAME', 'PARAMETER',
        'VALUE', 'EXPECTED', 'ISSUE', 'ACTION', 'COMMENT_NOTE']


def new_names() -> dict:
    """(Atlantis row, Atlantis structure) -> corrected name, through the Etest report and the validator output."""
    v = pd.read_csv(DEFAULT_OUT.with_suffix('.csv'), dtype=str, keep_default_na=False)
    cor = {o: c[:len(c) - len(t)] if t else c for o, c, t in zip(v.Original_Name, v.Corrected_Name, v.TC)}
    et = load_etest(DEFAULT_ETEST)
    return {(r.strip().lower(), s.strip().upper()): cor[t] for r, s, t in
            zip(et.ATLANTIS_ROW_NAME, et.ATLANTIS_STRUCTURE_NAME, et.STRUCTURE_NAME_AS_TESTED) if cor.get(t)}


def pull_iso(at: pd.DataFrame, workers: int = 6) -> pd.DataFrame:
    sys.path.insert(0, str(HERE.parent / 'MetadataScalarISO'))
    from atlantis_wcf import AtlantisWcfClient
    c = AtlantisWcfClient()
    rows = sorted(set(zip(at.Library, at.TestRowName)))
    out = []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(c.fetch_structures, 'X81', lib, row, []): (lib, row) for lib, row in rows}
        for f in as_completed(futs):
            lib, row = futs[f]
            for s in f.result()[1]:
                out.append({'Library': lib, 'TestRowName': row, 'StructureName': s['StructureName'],
                            **{k: v for k, v in s.items() if 'Skew' in k or k == 'Group'}})
    print(f'{len(rows)} ISO rows pulled', flush=True)
    return pd.DataFrame(out)


def fti_issues(iso: pd.DataFrame, new: dict, ids: dict) -> list[dict]:
    out = []
    for lib, row, name, val in zip(iso.Library, iso.TestRowName, iso.StructureName, iso.FTICdMisregSkew.fillna('')):
        if not val:
            continue
        nn = new.get((row.lower(), name.upper()), '')
        base = {'LIBRARY': lib, 'TESTROWNAME': row, 'TESTSTRUCTID': ids.get((row, name), ''), 'STRUCTURE': name,
                'NEW_NAME': nn, 'PARAMETER': 'FTICdMisregSkew', 'VALUE': val, 'Category': 'Buildsheet'}
        v = [x for x in val.rstrip(';').split(',')]
        num = [x for x in v if re.fullmatch(r'[+-]?\d*\.?\d+', x.strip())]
        if len(num) != 4:
            out.append({**base, 'Check': 'FTI_SKEW_FORMAT', 'EXPECTED': '4 values (OGD CD, PGD CD, OGD REG, PGD REG) '
                                                                      '+ optional flag',
                        'ISSUE': f'{len(num)} numeric values', 'ACTION': 'Buildsheet needs correction: remove the '
                                                                        'extra value (skew is built as named)',
                        'COMMENT_NOTE': f'Buildsheet needs correction: FTICdMisregSkew has {len(num)} numeric values '
                                        f'(expected 4: OGD CD, PGD CD, OGD REG, PGD REG)'})
        nums = [float(x) for x in num[:4]]
        m = FTI_RX.search(name)
        exp = [0.0] * 4
        if m and m.group(4) not in ('NOM', '0') and m.group(2) != 'NOM':
            mag = float(m.group(4)[1:].replace('D', '.')) / 1000 * (-1 if m.group(4)[0] == 'N' else 1)
            exp[FTI_POS[(m.group(2), m.group(3))]] = mag
        elif not m and not any(nums):
            continue
        if any(abs(a - b) > 1e-9 for a, b in zip(nums, exp)):
            built = '; '.join(f'{d} {k} {x * 1000:+g} nm' for (d, k), x in zip(FTI_POS, nums) if x) or 'no skew'
            out.append({**base, 'Check': 'FTI_SKEW_NAME', 'EXPECTED': ','.join(f'{x:g}' for x in exp),
                        'ISSUE': f'Name says {m.group(2)} {m.group(3)} {m.group(4)}, buildsheet builds {built}' if m
                                 else f'FTI skew in the buildsheet ({built}) but no FTI skew in the name',
                        'ACTION': 'Name follows the build; structure needs correction to the intended skew',
                        'COMMENT_NOTE': f'Needs structure correction: built as {built}; intended name {name}'})
    return out


def lvs_issues(new: dict, ids: dict) -> list[dict]:
    if not LVS_FLAGGED.exists():
        return []
    fl = pd.read_csv(LVS_FLAGGED, dtype=str, keep_default_na=False)
    fl = fl[fl.LVS_Data_To_Correct.str.startswith('Y') & fl.Mismatches.str.contains('DIFF')]
    lvs = pd.read_csv(LVS_RAW, dtype=str, keep_default_na=False)
    where = lvs.groupby('StructureName').apply(lambda g: sorted(set(zip(g.Library, g.TestRow))), include_groups=False)
    out = []
    for r in fl.itertuples(index=False):
        for lib, row in where.get(r.StructureName, [('', '')]):
            out.append({'Category': 'Buildsheet', 'Check': 'LVS_DATA', 'LIBRARY': lib, 'TESTROWNAME': row,
                        'TESTSTRUCTID': ids.get((row, r.StructureName), ''), 'STRUCTURE': r.StructureName,
                        'NEW_NAME': new.get((row.lower(), r.StructureName.upper()), r.Corrected_Base),
                        'PARAMETER': 'lvsDeviceData', 'VALUE': r.lvsDeviceData, 'EXPECTED': f'diff {r.Exp_Diff_nm} nm',
                        'ISSUE': f'LVS has diff {r.LVS_Diff_nm} nm', 'ACTION': 'Correct lvsDeviceData (name is right)'})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--refresh', action='store_true', help='pull the ISO buildsheets from Atlantis again')
    args = ap.parse_args()
    at = pd.read_csv(RAW, dtype=str, keep_default_na=False)
    sf_rows = {row_key(r) for r in load_superflow(DEFAULT_SUPERFLOW).row}
    scope = set(at.TestRowName[(at.RowStatus == TAPE_IN) & at.TestRowName.str.lower().isin(sf_rows)])
    owner = {}
    if TRACKER_FILE.exists():
        t = pd.read_excel(TRACKER_FILE, sheet_name='X81Z_Row_Tracker', dtype=str).fillna('')
        owner = {r.strip().lower(): o for r, o in zip(t['Test Row Name'], t['Test Row Owner']) if r.strip()}
    ids = dict(zip(zip(at.TestRowName, at.TestStructName), at.TestStructId))
    new = new_names()

    iso_rows = at[(at.TemplateName == 'ISO_X81Z') & at.TestRowName.isin(scope)]
    if args.refresh or not ISO_RAW.exists():
        iso = pull_iso(iso_rows)
        iso.to_csv(ISO_RAW, index=False)
    iso = pd.read_csv(ISO_RAW, dtype=str, keep_default_na=False)
    iso = iso[iso.TestRowName.isin(scope)]

    issues = fti_issues(iso, new, ids) + lvs_issues(new, ids)
    blank = at[(at.TestStructName.str.strip() == '') & at.TestRowName.isin(scope)]
    issues += [{'Category': 'Atlantis', 'Check': 'BLANK_NAME', 'LIBRARY': r.Library, 'TESTROWNAME': r.TestRowName,
                'TESTSTRUCTID': r.TestStructId, 'ISSUE': 'structure without a name',
                'ACTION': 'Name the structure or remove it'} for r in blank.itertuples(index=False)]
    df = pd.DataFrame(issues, columns=COLS).fillna('')
    df['OWNER'] = df.TESTROWNAME.str.lower().map(owner).fillna('')
    df = df.sort_values(['Check', 'LIBRARY', 'TESTROWNAME', 'STRUCTURE'])
    summ = df.groupby(['Check', 'LIBRARY']).agg(Rows=('TESTROWNAME', 'nunique'), Structures=('STRUCTURE', 'size')) \
        .reset_index()
    with pd.ExcelWriter(OUT, engine='openpyxl') as xw:
        summ.to_excel(xw, sheet_name='Summary', index=False)
        df.to_excel(xw, sheet_name='Issues', index=False)
        for ws in xw.sheets.values():
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or '')) for c in col[:300]) + 2, 60)
    df.to_csv(OUT.with_suffix('.csv'), index=False)
    print(f'{len(df)} buildsheet / Atlantis issues -> {OUT.name}: ' +
          ', '.join(f'{k} {v}' for k, v in df.Check.value_counts().items()))


if __name__ == '__main__':
    main()
