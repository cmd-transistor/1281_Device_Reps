#!/usr/bin/env python
"""Atlantis rename table for every X81Z structure in rows 'Configured for Tape-in' plus every device-tracker row
(any status; read-only pull). Only rows Configured for Tape-in that are in the superflow are renamed (owner WW45);
every other row keeps its names and is listed in the row summary with the reason.

Columns: TEMPLATENAME, DATABASE, TESTROWNAME, TESTSTRUCTNAME (corrected), TESTSTRUCTID, LIBRARY, OLD_TESTSTRUCTNAME,
OPENCAL_STRUCTURE (corrected), OLD_OPENCAL_STRUCTURE (Miller buildsheet parameter: the open-comp structure referenced;
the new value is the new name of that structure), TESTSTRUCTCOMMENT (suggested from the corrected name where the
Atlantis comment is empty, in-scope rows only), OLD_TESTSTRUCTCOMMENT.
TEMPLATENAME / DATABASE / TESTROWNAME / TESTSTRUCTID / LIBRARY / OLD_TESTSTRUCTNAME come from Atlantis; the
corrected name comes from OUTPUT/StructureName_Validation.csv (run structure_name_validator.py first), matched
through the Etest report (Atlantis row + Atlantis structure name -> name as tested). Structures without a
unique match keep their old name and are listed in OUTPUT/Atlantis_Structure_Rename_Unmapped.csv.

    python atlantis_rename_table.py [--libs bechnx81z_x81_lay ...] [--workers 6]
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'MetadataScalarISO'))
import structure_name_validator as snv  # noqa: E402
from atlantis_wcf import AtlantisWcfClient  # noqa: E402
from structure_name_validator import (DEFAULT_ETEST, DEFAULT_OUT, DEFAULT_SUPERFLOW, DEVICE_ROWS,  # noqa: E402
                                      FROZEN_FAMILIES, TAPE_IN_STATUS, TRACKER_FILE, load_device_rows, load_etest,
                                      load_superflow, row_key)

PROJECT = 'X81'
TAPE_IN = 9                                   # StatusCode of 'Configured for Tape-in'
OUT = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename.csv'
UNMAPPED = HERE / 'OUTPUT' / 'Atlantis_Structure_Rename_Unmapped.csv'
LIBS_OUT = HERE / 'OUTPUT' / 'Atlantis_Libraries_To_Edit.csv'
ROWS_OUT = HERE / 'OUTPUT' / 'Atlantis_Row_Summary.csv'
RAW = HERE / 'OUTPUT' / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
ISSUES = HERE / 'OUTPUT' / 'Buildsheet_Issues.csv'           # buildsheet_issues.py: notes added to the comments
OPENCAL_RAW = HERE / 'OUTPUT' / 'Atlantis_81Z_MLR_OpenCal_raw.csv'
MLR_TEMPLATE = 'MOS_MLR_X81Z'
COLS = ['TEMPLATENAME', 'DATABASE', 'TESTROWNAME', 'TESTSTRUCTNAME', 'TESTSTRUCTID', 'LIBRARY', 'OLD_TESTSTRUCTNAME',
        'OPENCAL_STRUCTURE', 'OLD_OPENCAL_STRUCTURE', 'TESTSTRUCTCOMMENT', 'OLD_TESTSTRUCTCOMMENT']
KEEP = '\0keep'
VT_NAME = {v: k for k, v in snv.VT_FULL_TO_CODE.items()}
POL_NAME = {'N': 'NMOS', 'P': 'PMOS', 'NP': 'NMOS+PMOS', 'X': 'open-cal', 'XN': 'NMOS open-cal', 'XP': 'PMOS open-cal'}
FAMILY_NAME = {'PFT': 'PFT', 'FK': 'full-KTCN PFT', 'MLR': 'Miller', 'ROS': 'ring oscillator', 'ROSPFT': 'ROS PFT',
               'BITCELL': 'SRAM bitcell', 'ISO': 'isolation', 'KLV': 'Kelvin'}
KIND_NAME = {'CD': 'CD', 'CDPP': 'CD + poly pitch', 'REG': 'registration', 'REF': 'reference', 'DEN': 'density',
             'ESD': 'ESD', 'ENC': 'enclosure', 'EC': 'end cap', 'NES': 'nested'}


def pull_atlantis(libs: list[str] | None, workers: int) -> pd.DataFrame:
    c = AtlantisWcfClient()
    libs = libs or [l for l in c.list_libraries(PROJECT) if l.lower().endswith('x81z_x81_lay')]
    load_device_rows()
    rows, status = [], {}
    for lib in libs:
        for r in c.list_rows(PROJECT, lib):
            if r['StatusCode'] == TAPE_IN or r['TestRowName'].lower() in DEVICE_ROWS:
                rows.append((lib, r['TestRowName']))
                status[(lib, r['TestRowName'])] = r.get('Status', '')
    print(f'{len(rows)} rows (configured for tape-in or in the device tracker) in {len(libs)} libraries', flush=True)
    out, failed = [], []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(c.list_structure_summaries, PROJECT, lib, row): (lib, row) for lib, row in rows}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                out += [{**s, 'RowStatus': status[futs[f]]} for s in f.result()]
            except Exception as e:
                failed.append((futs[f], str(e)[:120]))
            if n % 50 == 0:
                print(f'  {n}/{len(rows)} rows', flush=True)
    for it, msg in failed:
        print('FAILED', it, msg)
    return pd.DataFrame(out)


def pull_opencal(at: pd.DataFrame, workers: int) -> pd.DataFrame:
    """OPENCAL_STRUCTURE buildsheet parameter of every Miller (MLR) structure = the open-comp structure it references."""
    c = AtlantisWcfClient()
    m = at[at.TemplateName == MLR_TEMPLATE]
    rows = sorted(set(zip(m.Library, m.TestRowName)))
    out, failed = [], []
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(c.fetch_structures, PROJECT, lib, row, []): (lib, row) for lib, row in rows}
        for f in as_completed(futs):
            lib, row = futs[f]
            try:
                out += [{'Library': lib, 'TestRowName': row, 'TestStructName': s['StructureName'],
                         'OPENCAL_STRUCTURE': s.get('OPENCAL_STRUCTURE', '')} for s in f.result()[1]]
            except Exception as e:
                failed.append(((lib, row), str(e)[:120]))
    for it, msg in failed:
        print('FAILED open-cal', it, msg)
    print(f'{len(rows)} Miller rows: open-cal references read', flush=True)
    return pd.DataFrame(out, columns=['Library', 'TestRowName', 'TestStructName', 'OPENCAL_STRUCTURE'])


def opencal_columns(df: pd.DataFrame, oc: pd.DataFrame) -> tuple[list, list, int]:
    """New and old open-cal reference per structure; the new one follows the rename of the referenced structure."""
    ref = {(l, r.lower(), s.upper()): v.strip() for l, r, s, v in
           zip(oc.Library, oc.TestRowName, oc.TestStructName, oc.OPENCAL_STRUCTURE) if v.strip()}
    by_old = defaultdict(lambda: defaultdict(set))
    for lib, old, new in zip(df.LIBRARY, df.OLD_TESTSTRUCTNAME, df.TESTSTRUCTNAME):
        by_old[old.upper()][lib].add(new)
    olds, news, unresolved = [], [], 0
    for lib, row, old in zip(df.LIBRARY, df.TESTROWNAME, df.OLD_TESTSTRUCTNAME):
        o = ref.get((lib, row.lower(), old.upper()), '')
        n = o
        if o:
            libs = by_old.get(o.upper(), {})
            cands = (libs.get(lib) or set().union(*libs.values())) if libs else set()
            if len(cands) == 1:
                n = next(iter(cands))
            else:
                unresolved += 1
        olds.append(o)
        news.append(n)
    return news, olds, unresolved


def corrected_maps():
    """(atlantis row, atlantis structure) -> corrected bases, and original base -> corrected bases."""
    v = pd.read_csv(DEFAULT_OUT.with_suffix('.csv'), dtype=str, keep_default_na=False)
    manual = set(v.Original_Name[v.Corrected_Name == ''])
    v = v[v.Corrected_Name != '']
    tc = v.TC.str.len()
    v['new'] = [n[:len(n) - k] if k else n for n, k in zip(v.Corrected_Name, tc)]
    # the validator's row-specific A:WID is re-applied below per Atlantis row (shared BE-row names stay A:NOM)
    wid_row = v.Issue_Codes.str.contains(r'\bWID_ROW\b')
    v.loc[wid_row, 'new'] = v.loc[wid_row, 'new'].str.replace(r'-A:WID(?=$|[-_])', '-A:NOM', n=1, regex=True)
    v['old'] = [n[:len(n) - k] if k else n for n, k in zip(v.Original_Name, tc)]
    frozen = v.Family.isin(FROZEN_FAMILIES) & (v.new.str.upper() == v.old.str.upper())
    v.loc[frozen, 'new'] = KEEP          # BE / QnR kept: keep the Atlantis name exactly as it is
    new_of = dict(zip(v.Original_Name, v.new))

    by_key, by_name, et_keys, et_rows = defaultdict(set), defaultdict(set), {}, set()
    for old, new in zip(v.old, v.new):
        by_name[old.upper()].add(new)
    for rows, old, new in zip(v.Row[v.Source == 'AtlantisOnly'], v.old[v.Source == 'AtlantisOnly'],
                              v.new[v.Source == 'AtlantisOnly']):
        for row in rows.split('; '):
            by_key[(row.lower(), old.upper())].add(new)
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


def row_summary(at: pd.DataFrame, new_names: list, kept: list, um: pd.DataFrame, sf_rows: set):
    """One line per Atlantis row (and per device-tracker / superflow row not found in Atlantis) with what happens to it."""
    d = pd.DataFrame({'LIBRARY': at.Library, 'TESTROWNAME': at.TestRowName,
                      'ROW_STATUS': at.get('RowStatus', pd.Series('', index=at.index)),
                      'renamed': [n != o for n, o in zip(new_names, at.TestStructName)], 'frozen': kept,
                      'gnac': at.TestStructName.str.upper().str.startswith('GNAC')})
    s = d.groupby(['LIBRARY', 'TESTROWNAME']).agg(ROW_STATUS=('ROW_STATUS', 'first'), STRUCTURES=('renamed', 'size'),
                                                  RENAMED=('renamed', 'sum'), KEPT_BE_QNR=('frozen', 'sum'),
                                                  GNAC=('gnac', 'sum')).reset_index()
    u = um[~um.Reason.str.startswith('GNAC')].groupby('TESTROWNAME').Reason.agg(
        lambda r: '; '.join(f'{k} x{v}' for k, v in r.value_counts().items()))
    s['NOT_MAPPED'] = s.TESTROWNAME.map(u).fillna('')
    owner = {}
    if TRACKER_FILE.exists():
        t = pd.read_excel(TRACKER_FILE, sheet_name='X81Z_Row_Tracker', dtype=str).fillna('')
        owner = {r.strip().lower(): o for r, o in zip(t['Test Row Name'], t['Test Row Owner']) if r.strip()}
    s['DEVICE_ROW'] = s.TESTROWNAME.str.lower().isin(owner)
    s['OWNER'] = s.TESTROWNAME.str.lower().map(owner).fillna('')
    s['IN_SUPERFLOW'] = s.TESTROWNAME.str.lower().isin(sf_rows)
    named = s.STRUCTURES - s.GNAC
    s['RESULT'] = 'no change needed'
    s.loc[s.NOT_MAPPED != '', 'RESULT'] = 'not mapped (see NOT_MAPPED)'
    s.loc[(s.KEPT_BE_QNR > 0) & (s.KEPT_BE_QNR == named), 'RESULT'] = 'kept unchanged: BE / QnR (frozen)'
    s.loc[s.RENAMED > 0, 'RESULT'] = 'renamed'
    s.loc[(named == 0), 'RESULT'] = 'GNAC only'
    s.loc[~s.IN_SUPERFLOW, 'RESULT'] = 'not changed: row not in superflow'
    s.loc[s.ROW_STATUS != TAPE_IN_STATUS, 'RESULT'] = 'not changed: row not Configured for Tape-in'
    missing = sorted(set(owner) - set(s.TESTROWNAME.str.lower()))
    s = pd.concat([s, pd.DataFrame({'TESTROWNAME': missing, 'DEVICE_ROW': True, 'OWNER': [owner[r] for r in missing],
                                    'IN_SUPERFLOW': [r in sf_rows for r in missing],
                                    'RESULT': 'device row not found in Atlantis'})], ignore_index=True)
    tape_in = set(s.TESTROWNAME[s.ROW_STATUS == TAPE_IN_STATUS].str.lower())
    sf_missing = sorted(sf_rows - tape_in)
    s = pd.concat([s, pd.DataFrame({'TESTROWNAME': sf_missing, 'IN_SUPERFLOW': True,
                                    'RESULT': 'superflow row not Configured for Tape-in in Atlantis'})], ignore_index=True)
    s.to_csv(ROWS_OUT, index=False)
    print(f'{len(s)} rows -> {ROWS_OUT.name}: ' + ', '.join(f'{k} {v}' for k, v in s.RESULT.value_counts().items()))


def mag_text(m: str) -> str:
    if m == 'NOM':
        return 'nominal'
    if re.fullmatch(r'[NP]\d+(?:D\d+)?', m):
        return ('-' if m[0] == 'N' else '+') + m[1:].replace('D', '.')
    return m


def skew_text(tok: str) -> str:
    """Plain-text meaning of a NOM / TSDR / variant token (A:NOM, A:PLY:OGD:CD:N2, A:NDBI:Z:REM:ALL, A:HV)."""
    c = tok.split(':')
    if not re.fullmatch(r'[A-Z]', c[0]):
        return tok
    extra = lambda k: f" ({':'.join(c[k:])})" if len(c) > k else ''
    if len(c) >= 5 and c[3] == 'REM':
        out = []
        for lay in c[1].split('/'):
            if lay.startswith('TAPN'):
                out.append(f'{lay[4:]} removed in TAP')
            else:
                out.append(f"{lay[1:]} {'removed' if lay[0] == 'N' else 'present'}")
        return ', '.join(out) + extra(5)
    if len(c) >= 5 and c[3] == 'KEP':
        return 'implant kept' + extra(5)
    if len(c) >= 5 and snv._is_dir(c[2]):
        lay = 'POR layers' if c[1] == 'NOM' else c[1]
        return f'{lay} {KIND_NAME.get(c[3], c[3])} skew {c[2]} {mag_text(c[4])}' + extra(5)
    if c[-1] == 'NOM_LLE':
        return 'LLE reference' + (f" ({':'.join(c[1:-1])})" if len(c) > 2 else '')
    if c[1:2] == ['NOM']:
        return 'POR' + extra(2)
    if c[1:2] == ['WID']:
        return 'POR within-die' + extra(2)
    return 'variant ' + ':'.join(c[1:])


def suggest_comment(name: str) -> str:
    """Structure comment built from the fields of the (corrected) name; '' when the family is not decoded."""
    res = snv.parse(name.upper())
    f, fam = res.fields, res.family
    if fam not in FAMILY_NAME:
        return ''
    desc = FAMILY_NAME[fam]
    if fam in ('ISO', 'KLV'):
        desc = f"{f.get('CAT', '')} {desc}"
    elif 'M' in f.get('PFX', ''):
        desc = 'matching pair PFT'
    parts = [f"{POL_NAME.get(f.get('POL', ''), '')} {desc}".strip()]
    if fam == 'BITCELL':
        parts += [f"{f.get('LIB', '')} {f.get('DUT', '')}", f"diff {f.get('DIFF', '')}nm"]
    else:
        parts.append('/'.join(VT_NAME.get(v, v) for v in f.get('VT', '').split(':') if v))
    parts.append(f"{f.get('CH', '')}H")
    if fam != 'BITCELL' and f.get('DIFF'):
        parts.append(f"{f['DIFF']} ({f['DIFF_W']}nm)" if f.get('DIFF_W') else f['DIFF'])
    if f.get('ADF'):
        parts.append(f"{f['ADF'][:-3]} ADF")
    elif re.fullmatch(r'\d+L', f.get('LEGS', '')):
        parts.append(f"{f['LEGS'][:-1]} leg{'s' if f['LEGS'] != '1L' else ''}")
    parts += [f.get(k, '') for k in ('ENV', 'STYLE', 'STG', 'FO')]
    parts += [f"{k.lower()} {f[k]}" for k in ('NEST', 'ISO') if f.get(k)]
    parts.append({'SH': 'single height', 'DH': 'double height'}.get(f.get('HEIGHT', ''), ''))
    toks = [f['SKEW']] if fam in ('ISO', 'KLV') and f.get('SKEW') else f.get('ADD', '').split('-')
    skew = ', '.join(skew_text(t) for t in toks if t)
    return ', '.join(p for p in parts if p) + (f'; {skew}' if skew else '')


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
    sf_rows = {row_key(r) for r in load_superflow(DEFAULT_SUPERFLOW).row}
    load_device_rows()

    new_names, unmapped, kept, scope = [], [], [], []
    for r in at.itertuples(index=False):
        old = r.TestStructName
        scope.append(r.RowStatus == TAPE_IN_STATUS and r.TestRowName.lower() in sf_rows)
        if not scope[-1]:
            new_names.append(old)
            kept.append(False)
            continue
        cands = by_key.get((r.TestRowName.lower(), old.upper())) or by_name.get(old.upper(), set())
        if len(cands) == 2 and KEEP in cands:
            # seat name shared by a device row and a BE row: frozen only outside device rows
            cands = cands - {KEEP} if r.TestRowName.lower() in DEVICE_ROWS else {KEEP}
        kept.append(cands == {KEEP})
        if len(cands) == 1:
            new = next(iter(cands))
            if new != KEEP and re.fullmatch(r'x81zs_wid\d+', r.TestRowName.lower()):
                new = re.sub(r'-A:NOM(?=$|[-_])', '-A:WID', new, count=1)
            new_names.append(old if new == KEEP else new)
            continue
        new_names.append(old)
        unmapped.append({'LIBRARY': r.Library, 'TESTROWNAME': r.TestRowName, 'TESTSTRUCTID': r.TestStructId,
                         'OLD_TESTSTRUCTNAME': old,
                         'Reason': unmapped_reason(r.TestRowName, old, et_keys, et_rows) if not cands
                         else f'ambiguous: {sorted(cands)}'})

    df = pd.DataFrame({'TEMPLATENAME': at.TemplateName, 'DATABASE': at.Database, 'TESTROWNAME': at.TestRowName,
                       'TESTSTRUCTNAME': new_names, 'TESTSTRUCTID': at.TestStructId, 'LIBRARY': at.Library,
                       'OLD_TESTSTRUCTNAME': at.TestStructName, '_seq': at.TestStructSeq})
    if args.cached and OPENCAL_RAW.exists():
        oc = pd.read_csv(OPENCAL_RAW, dtype=str, keep_default_na=False)
    else:
        oc = pull_opencal(at, args.workers)
        oc.to_csv(OPENCAL_RAW, index=False)
    df['OPENCAL_STRUCTURE'], df['OLD_OPENCAL_STRUCTURE'], oc_unresolved = opencal_columns(df, oc)
    # empty comments in the rename scope get a suggestion (BE / QnR kept structures and GNAC excluded)
    df['TESTSTRUCTCOMMENT'] = [
        cm if cm.strip() or not sc or kp or not old.strip() or old.upper().startswith('GNAC')
        else (suggest_comment(new) or cm)
        for new, old, cm, sc, kp in zip(new_names, at.TestStructName, at.TestStructComment, scope, kept)]
    df['OLD_TESTSTRUCTCOMMENT'] = at.TestStructComment
    notes = {}
    if ISSUES.exists():
        iss = pd.read_csv(ISSUES, dtype=str, keep_default_na=False)
        for r, s, n in zip(iss.TESTROWNAME, iss.STRUCTURE, iss.get('COMMENT_NOTE', pd.Series(dtype=str))):
            if n:
                notes.setdefault((r, s), []).append(n)
    df['TESTSTRUCTCOMMENT'] = [' | '.join(notes[(r, o)] + ([c] if c.strip() else [])) if (r, o) in notes else c
                               for r, o, c in zip(df.TESTROWNAME, df.OLD_TESTSTRUCTNAME, df.TESTSTRUCTCOMMENT)]
    df = df.sort_values(['LIBRARY', 'TESTROWNAME', '_seq'])[COLS]
    OUT.parent.mkdir(exist_ok=True)
    df.to_csv(OUT, index=False)
    ren = df[(df.TESTSTRUCTNAME != df.OLD_TESTSTRUCTNAME) | (df.OPENCAL_STRUCTURE != df.OLD_OPENCAL_STRUCTURE)
             | (df.TESTSTRUCTCOMMENT != df.OLD_TESTSTRUCTCOMMENT)]
    libs = (ren.groupby('LIBRARY').agg(Rows_To_Edit=('TESTROWNAME', 'nunique'),
                                       Structures_To_Rename=('TESTSTRUCTNAME', lambda s: (s != ren.OLD_TESTSTRUCTNAME[s.index]).sum()),
                                       OpenCal_To_Update=('OPENCAL_STRUCTURE', lambda s: (s != ren.OLD_OPENCAL_STRUCTURE[s.index]).sum()),
                                       Comments_To_Add=('TESTSTRUCTCOMMENT', lambda s: (s != ren.OLD_TESTSTRUCTCOMMENT[s.index]).sum()))
            .join(df.groupby('LIBRARY').TESTROWNAME.nunique().rename('Rows_In_Library'))
            .join(ren.groupby('LIBRARY').TEMPLATENAME.agg(lambda s: ', '.join(sorted(set(s)))).rename('Templates'))
            .reset_index().sort_values('Structures_To_Rename', ascending=False))
    libs.to_csv(LIBS_OUT, index=False)
    print(f'{len(libs)} libraries need editing -> {LIBS_OUT.name}')
    um = pd.DataFrame(unmapped, columns=['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTID', 'OLD_TESTSTRUCTNAME', 'Reason'])
    um.to_csv(UNMAPPED, index=False)
    row_summary(at, new_names, kept, um, sf_rows)
    changed = (df.TESTSTRUCTNAME != df.OLD_TESTSTRUCTNAME).sum()
    print(f'{len(df)} structures -> {OUT.name}: {changed} renamed, {len(df) - changed - len(unmapped)} unchanged, '
          f'{len(unmapped)} unmapped (old name kept) -> {UNMAPPED.name}')
    oc_refs = (df.OLD_OPENCAL_STRUCTURE != '').sum()
    oc_changed = (df.OPENCAL_STRUCTURE != df.OLD_OPENCAL_STRUCTURE).sum()
    print(f'Miller open-cal references: {oc_refs} structures, {oc_changed} to update, '
          f'{oc_unresolved} referenced names not found / ambiguous (kept)')
    print(f'Structure comments: {(df.TESTSTRUCTCOMMENT != df.OLD_TESTSTRUCTCOMMENT).sum()} comments added '
          f'(suggestions for empty comments, buildsheet notes)')


if __name__ == '__main__':
    main()
