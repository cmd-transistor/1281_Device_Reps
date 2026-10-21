#!/usr/bin/env python
"""Pull structure names + LVS device data of all X81Z rows 'Configured for Tape-in' from Atlantis (read-only).

Same result as OUTPUT/Atlantis_81Z_TapeIn_LVS.sql, but through the Windows-authenticated WCF client of
MetadataScalarISO (no Oracle login needed). Output: OUTPUT/Atlantis_81Z_TapeIn_LVS.csv with columns
Library, TestRow, RowStatus, StructureName, lvsDeviceData, shortedPinPairs - the input of lvs_name_check.py.

    python pull_atlantis_lvs.py [--libs xtrpfx81z_x81_lay isox81z_x81_lay ...] [--workers 6]
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'MetadataScalarISO'))
from atlantis_wcf import AtlantisWcfClient  # noqa: E402

PROJECT = 'X81'
TAPE_IN = 9                                   # StatusCode of 'Configured for Tape-in'
OUT = HERE / 'OUTPUT' / 'Atlantis_81Z_TapeIn_LVS.csv'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--libs', nargs='*', help='only these libraries (default: every *x81z_x81_lay library)')
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    c = AtlantisWcfClient()
    libs = args.libs or [l for l in c.list_libraries(PROJECT) if l.lower().endswith('x81z_x81_lay')]
    rows = []
    for lib in libs:
        rows += [(lib, r['TestRowName']) for r in c.list_rows(PROJECT, lib) if r['StatusCode'] == TAPE_IN]
    print(f'{len(rows)} rows configured for tape-in in {len(libs)} libraries', flush=True)

    def pull(item):
        lib, row = item
        header, recs = c.fetch_structures(PROJECT, lib, row, [])
        return [{'Library': lib, 'TestRow': row, 'RowStatus': 'Configured for Tape-in',
                 'StructureName': r.get('StructureName', ''), 'lvsDeviceData': r.get('lvsDeviceData', ''),
                 'shortedPinPairs': r.get('shortedPinPairs', '')} for r in recs]

    out, failed = [], []
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(pull, it): it for it in rows}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                out += f.result()
            except Exception as e:                      # keep going, report at the end
                failed.append((futs[f], str(e)[:120]))
            if n % 25 == 0:
                print(f'  {n}/{len(rows)} rows', flush=True)
    df = pd.DataFrame(out).sort_values(['Library', 'TestRow', 'StructureName'])
    OUT.parent.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f'{len(df)} structures ({(df.lvsDeviceData != "").sum()} with LVS data) -> {OUT}')
    for it, msg in failed:
        print('FAILED', it, msg)


if __name__ == '__main__':
    main()
