#!/usr/bin/env python
"""Atlantis rename table for every X81Z structure in rows 'Configured for Tape-in' (read-only pull).

Columns: TEMPLATENAME, DATABASE, TESTROWNAME, TESTSTRUCTNAME (corrected), TESTSTRUCTID, LIBRARY, OLD_TESTSTRUCTNAME.
TEMPLATENAME / DATABASE / TESTROWNAME / TESTSTRUCTID / LIBRARY / OLD_TESTSTRUCTNAME come from Atlantis; the
corrected name comes from OUTPUT/StructureName_Validation.csv (run structure_name_validator.py first), matched
through the Etest report (Atlantis row + Atlantis structure name -> name as tested). Structures without a
unique match keep their old name and are listed in OUTPUT/Atlantis_Structure_Rename_Unmapped.csv.

    python atlantis_rename_table.py [--libs bechnx81z_x81_lay ...] [--workers 6]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'MetadataScalarISO'))
from atlantis_wcf import AtlantisWcfClient  # noqa: E402
from structure_name_validator import DEFAULT_ETEST, DEFAULT_OUT, load_etest  # noqa: E402

PROJECT = 'X81'
TAPE_IN = 9                                   # StatusCode of 'Configured for Tape-in'
OUT = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename.csv'
UNMAPPED = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename_Unmapped.csv'
RAW = HERE / 'OUTPUT' / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
COLS = ['TEMPLATENAME', 'DATABASE', 'TESTROWNAME', 'TESTSTRUCTNAME', 'TESTSTRUCTID', 'LIBRARY', 'OLD_TESTSTRUCTNAME']


def pull_atlantis(libs: list[str] | None, workers: int) -> pd.DataFrame:
    c = AtlantisWcfClient()
    libs = libs or [l for l in c.list_libraries(PROJECT) if l.lower().endswith('x81z_x81_lay')]
    rows = [(lib, r['TestRowName']) for lib in libs for r in c.list_rows(PROJECT, lib) if r['StatusCode'] == TAPE_IN]
    print(f'{len(rows)} rows configured for tape-in in {len(libs)} libraries', flush=True)
    out, failed = [], []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(c.list_structure_summaries, PROJECT, lib, row): (lib, row) for lib, row in rows}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                out += f.result()
            except Exception as e:
                failed.append((futs[f], str(e)[:120]))
            if n % 50 == 0:
                print(f'  {n}/{len(rows)} rows', flush=True)
    for it, msg in failed:
        print('FAILED', it, msg)
    return pd.DataFrame(out)


def corrected_maps():
    """(atlantis row, atlantis structure) -> corrected bases, and original base -> corrected bases."""
    v = pd.read_csv(DEFAULT_OUT.with_suffix('.csv'), dtype=str, keep_default_na=False)
    manual = set(v.Original_Name[v.Corrected_Name == ''])
    v = v[v.Corrected_Name != '']
    tc = v.TC.str.len()
    v['new'] = [n[:len(n) - k] if k else n for n, k in zip(v.Corrected_Name, tc)]
    v['old'] = [n[:len(n) - k] if k else n for n, k in zip(v.Original_Name, tc)]
    new_of = dict(zip(v.Original_Name, v.new))

    by_key, by_name, et_keys, et_rows = defaultdict(set), defaultdict(set), {}, set()
    for old, new in zip(v.old, v.new):
        by_name[old.upper()].add(new)
    et = load_etest(DEFAULT_ETEST)
    for row, sname, tested in zip(et.ATLANTIS_ROW_NAME, et.ATLANTIS_STRUCTURE_NAME, et.STRUCTURE_NAME_AS_TESTED):
        key = (row.strip().lower(), sname.strip().upper())
        et_rows.add(key[0])
        if tested in new_of:
            by_key[key].add(new_of[tested])
        elif tested in manual:
            et_keys[key] = 'name has no automatic correction (MANUAL in validator, free-text Etest-only name)'
    return by_key, by_name, et_keys, et_rows


def unmapped_reason(row: str, name: str, et_keys: dict, et_rows: set) -> str:
    if not name.strip():
        return 'blank structure name in Atlantis'
    if name.upper().startswith('GNAC'):
        return 'GNAC structure (not a named test structure)'
    if (row.lower(), name.upper()) in et_keys:
        return et_keys[(row.lower(), name.upper())]
    if row.lower() not in et_rows:
        return 'row not in superflow / Etest report'
    return 'structure not in superflow / Etest report'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--libs', nargs='*', help='only these libraries (default: every *x81z_x81_lay library)')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--cached', action='store_true', help=f'reuse {RAW.name} instead of pulling Atlantis again')
    args = ap.parse_args()

    by_key, by_name, et_keys, et_rows = corrected_maps()
    if args.cached:
        at = pd.read_csv(RAW, dtype=str, keep_default_na=False)
    else:
        at = pull_atlantis(args.libs, args.workers).fillna('').astype(str)
        RAW.parent.mkdir(exist_ok=True)
        at.to_csv(RAW, index=False)
    at['TestStructSeq'] = pd.to_numeric(at.TestStructSeq, errors='coerce')

    new_names, unmapped = [], []
    for r in at.itertuples(index=False):
        old = r.TestStructName
        cands = by_key.get((r.TestRowName.lower(), old.upper())) or by_name.get(old.upper(), set())
        if len(cands) == 1:
            new_names.append(next(iter(cands)))
            continue
        new_names.append(old)
        unmapped.append({'LIBRARY': r.Library, 'TESTROWNAME': r.TestRowName, 'TESTSTRUCTID': r.TestStructId,
                         'OLD_TESTSTRUCTNAME': old,
                         'Reason': unmapped_reason(r.TestRowName, old, et_keys, et_rows) if not cands
                         else f'ambiguous: {sorted(cands)}'})

    df = pd.DataFrame({'TEMPLATENAME': at.TemplateName, 'DATABASE': at.Database, 'TESTROWNAME': at.TestRowName,
                       'TESTSTRUCTNAME': new_names, 'TESTSTRUCTID': at.TestStructId, 'LIBRARY': at.Library,
                       'OLD_TESTSTRUCTNAME': at.TestStructName, '_seq': at.TestStructSeq})
    df = df.sort_values(['LIBRARY', 'TESTROWNAME', '_seq'])[COLS]
    OUT.parent.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    pd.DataFrame(unmapped, columns=['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTID', 'OLD_TESTSTRUCTNAME', 'Reason']) \
        .to_csv(UNMAPPED, index=False)
    changed = (df.TESTSTRUCTNAME != df.OLD_TESTSTRUCTNAME).sum()
    print(f'{len(df)} structures -> {OUT.name}: {changed} renamed, {len(df) - changed - len(unmapped)} unchanged, '
          f'{len(unmapped)} unmapped (old name kept) -> {UNMAPPED.name}')


if __name__ == '__main__':
    main()
