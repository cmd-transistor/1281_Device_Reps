#!/usr/bin/env python
"""Compare structure names with their LVS strings (lvsDeviceData) and flag names that disagree.

LVS string format (LVS_String_generation.pptx), one device per ';':
    <Drain>,<Gate>,<Source>,<Bulk>,<Model+Vt>,<Diff width nm>,<Poly CD nm>,<Gangs>,<Legs stacked>,<Instances>,<Legs shared>
Active device = no FLOAT on drain/source; non-active = gate on the pad, a source/drain floating.

Checks (name -> expected LVS value):
    polarity   active device model n/p            = polarity in the name (PFT, ROSPFT, MLR, FK)
    cell       model letter c/b/a                  = cell height 110/132/165
    balanced   'u' in nuhp/puhp                    = BCMOS environment (PFT, FK, MLR)
    VT         model vt (hvt..elvt)                = VT code H/S/L/U/UL/E
    diff       diffusion width nm                  = Z code at that cell height (slide 7 library)
    gangs      number of gangs                     = SH -> 1, DH in CMOS -> 2, BCMOS DH -> 1 (PFT, FK)
    poly CD    poly CD nm                          = 12, or 12 + PLY CD skew (PLY:CD, PLYPP:CDPP, PLYITCN); PP skews not checked

Usage:
    python lvs_name_check.py --lvs <LVS data .xlsx/.xlsm/.csv>      (default: any *LVS* data file next to this script)
Input needs columns StructureName and lvsDeviceData (shortedPinPairs optional), e.g. the 'Find_LVS_string' sheet.
Writes OUTPUT/LVS_Name_Check.xlsx (+ .csv) and VALIDATE/LVS_Flagged_Structures.csv (names to verify).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

import structure_name_validator as snv

HERE = Path(__file__).resolve().parent
OUT_XLSX = snv.OUTPUT_DIR / 'LVS_Name_Check.xlsx'
OUT_CSV = snv.OUTPUT_DIR / 'LVS_Name_Check.csv'
FLAG_CSV = snv.VALIDATE_DIR / 'LVS_Flagged_Structures.csv'

VT_TO_LVS = {'H': 'hvt', 'S': 'svt', 'L': 'lvt', 'U': 'ulvt', 'UL': 'ulvtll', 'E': 'elvt'}
CH_LETTER = {110: 'c', 132: 'b', 165: 'a'}                     # slide 5 model table
MODEL_RX = re.compile(r'^(?P<pol>[np])(?P<u>u?)hp(?P<cell>[abc])(?P<vt>hvt|svt|lvt|ulvtll|ulvt|elvt)$', re.I)
DEV_FIELDS = ['D', 'G', 'S', 'B', 'model', 'diff', 'poly', 'gangs', 'stacked', 'inst', 'shared']
POLARITY_FAMILIES = {'PFT', 'ROSPFT', 'MLR', 'FK'}
MODEL_FAMILIES = {'PFT', 'ROSPFT', 'MLR', 'FK', 'ROS', 'ISO', 'KLV'}
GANG_FAMILIES = {'PFT', 'FK'}
POLY_CD_NOMINAL = 12
SKEW_RX = re.compile(r'A:([A-Z0-9]+):(OGD|PGD|Z):([A-Z]+):(NOM|[NP]\d+(?:D\d+)?)')
PLY_CD_LAYERS = {'PLY', 'PLYPP', 'PLYITCN'}
CHECKS = ['POL', 'CELL', 'BAL', 'VT', 'DIFF', 'GANGS', 'POLYCD']


def norm(x) -> str:
    return re.sub(r'[^a-z]', '', str(x).lower())


def load_lvs(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in ('.xlsx', '.xlsm', '.xls'):
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    else:
        sheets = {path.stem: pd.read_csv(path, header=None, dtype=str, keep_default_na=False)}
    frames = []
    for sheet, df in sheets.items():
        df = df.fillna('')
        for i in range(min(10, len(df))):
            hdr = [norm(c) for c in df.iloc[i]]
            s = next((j for j, h in enumerate(hdr) if h in ('structurename', 'structure', 'teststructname')), None)
            l = next((j for j, h in enumerate(hdr) if 'lvsdevicedata' in h or h == 'lvsstring'), None)
            if s is None or l is None:
                continue
            p = next((j for j, h in enumerate(hdr) if 'shortedpin' in h), None)
            body = df.iloc[i + 1:]
            frames.append(pd.DataFrame({
                'Sheet': sheet, 'StructureName': body.iloc[:, s].str.strip(),
                'shortedPinPairs': body.iloc[:, p].str.strip() if p is not None else '',
                'lvsDeviceData': body.iloc[:, l].str.strip()}))
            break
    if not frames:
        sys.exit(f'No sheet with StructureName / lvsDeviceData columns found in {path.name}')
    out = pd.concat(frames, ignore_index=True)
    return out[(out.StructureName != '') & (out.lvsDeviceData != '')].reset_index(drop=True)


def parse_lvs(s: str) -> list[dict]:
    devs = []
    for part in s.strip().strip(';').split(';'):
        f = [x.strip() for x in part.split(',')]
        if len(f) != len(DEV_FIELDS):
            devs.append({'bad': part})
            continue
        d = dict(zip(DEV_FIELDS, f))
        d['active'] = not any(d[k].lower() == 'float' or d[k].upper().startswith('TAP') for k in ('D', 'S'))
        m = MODEL_RX.match(d['model'])
        d['m'] = m.groupdict() if m else None
        for k in ('diff', 'poly', 'gangs', 'stacked', 'inst', 'shared'):
            try:
                d[k] = float(d[k])
            except ValueError:
                d['bad'] = part
        devs.append(d)
    return devs


def signed(mag: str) -> float:
    if mag == 'NOM':
        return 0.0
    return (-1 if mag[0] == 'N' else 1) * float(mag[1:].replace('D', '.'))


def expected_poly(corrected: str) -> float | None:
    m = SKEW_RX.search(corrected)
    if not m:
        return POLY_CD_NOMINAL
    layer, _, kind, mag = m.groups()
    if layer in PLY_CD_LAYERS and kind in ('CD', 'CDPP'):
        return POLY_CD_NOMINAL + signed(mag)
    if layer.startswith('PLY'):
        return None                      # PP / other poly skews: not defined in the LVS document
    return POLY_CD_NOMINAL


def uniq(vals) -> str:
    out = []
    for v in vals:
        v = f'{v:g}' if isinstance(v, float) else str(v)
        if v not in out:
            out.append(v)
    return '/'.join(out)


def check(name: str, lvs: str) -> dict:
    base = snv.TC_SUFFIX.match(name)
    base = base.group('base') if base else name
    res = snv.parse(base.upper())
    f = res.fields
    devs = parse_lvs(lvs)
    rec = {'Family': res.family, 'Corrected_Base': res.corrected or '', 'N_Devices': len(devs)}
    notes = []
    if any('bad' in d for d in devs):
        notes.append('LVS_STRING_UNPARSED')
    good = [d for d in devs if 'bad' not in d and d['m']]
    if not good:
        notes.append('MODEL_UNPARSED')
    active = [d for d in good if d['active']]
    st = dict.fromkeys(CHECKS, 'NA')

    pol = f.get('POL', '')[:1] if res.family in POLARITY_FAMILIES else ''
    rec.update(Name_POL=pol, LVS_Active_POL=uniq(d['m']['pol'].upper() for d in active))
    if pol in ('N', 'P') and active:
        st['POL'] = 'OK' if all(d['m']['pol'].upper() == pol for d in active) else 'MISMATCH'

    ch = int(f['CH']) if f.get('CH', '').isdigit() else None
    exp_letter = CH_LETTER.get(ch)
    rec.update(Name_CH=ch or '', Exp_Cell=exp_letter or '', LVS_Cell=uniq(d['m']['cell'].lower() for d in good))
    if res.family in MODEL_FAMILIES and exp_letter and good:
        st['CELL'] = 'OK' if all(d['m']['cell'].lower() == exp_letter for d in good) else 'MISMATCH'

    env = f.get('ENV', '')
    exp_u = 'u' if env.startswith('BCMOS') else ''
    rec.update(Name_ENV=env, LVS_Balanced=uniq(d['m']['u'].lower() or '-' for d in good))
    if res.family in ('PFT', 'FK', 'MLR') and env and good:
        st['BAL'] = 'OK' if all(d['m']['u'].lower() == exp_u for d in good) else 'MISMATCH'

    vt = f.get('VT', '')
    exp_vt = VT_TO_LVS.get(vt)
    rec.update(Name_VT=vt, LVS_VT=uniq(d['m']['vt'].lower() for d in good))
    if res.family in MODEL_FAMILIES and exp_vt and good:
        lvs_vts = {d['m']['vt'].lower() for d in good}
        if 'VTMX' in f.get('ADD', ''):          # VT-mix structures: the name VT is one of the device VTs
            st['VT'] = 'OK' if exp_vt in lvs_vts else 'MISMATCH'
        else:
            st['VT'] = 'OK' if lvs_vts == {exp_vt} else 'MISMATCH'

    z = f.get('DIFF', '')
    width = snv.LOGIC_LIB.get(ch or 0, {}).get(z)
    extra_z = [snv.LOGIC_LIB.get(ch or 0, {}).get(c) for c in re.split(r'[:\-]', f.get('ADD', ''))
               if re.fullmatch(r'Z\d', c)]
    allowed = {width, *extra_z} - {None}
    rec.update(Name_Z=z, Exp_Diff_nm=width or '', LVS_Diff_nm=uniq(d['diff'] for d in good if 'bad' not in d))
    if res.family in MODEL_FAMILIES and width and good:
        widths = {d['diff'] for d in good}
        st['DIFF'] = 'OK' if (width in widths and widths <= allowed) else 'MISMATCH'

    gangs = None
    if res.family in GANG_FAMILIES and re.fullmatch(r'(A:(NOM|WID|HV|1GANG))?', f.get('ADD', '')):
        h = f.get('HEIGHT', '')
        gangs = 1 if h == 'SH' else (1 if h == 'DH' and env.startswith('BCMOS') else (2 if h == 'DH' else None))
    rec.update(Name_Height=f.get('HEIGHT', ''), Exp_Gangs=gangs or '', LVS_Gangs=uniq(d['gangs'] for d in good))
    if gangs and good:
        st['GANGS'] = 'OK' if all(d['gangs'] == gangs for d in good) else 'MISMATCH'

    poly = expected_poly(res.corrected) if res.corrected else None
    rec.update(Exp_PolyCD=poly if poly is not None else '', LVS_PolyCD=uniq(d['poly'] for d in good))
    if poly is not None and good:
        st['POLYCD'] = 'OK' if all(abs(d['poly'] - poly) < 0.01 for d in good) else 'MISMATCH'

    for k, v in st.items():
        rec[f'{k}_Check'] = v
    bad = [k for k, v in st.items() if v == 'MISMATCH']
    rec['Mismatches'] = ', '.join(bad)
    rec['Name_Flagged'] = bool(bad)
    rec['Name_Not_Parsed'] = res.family == 'UNKNOWN' or res.corrected is None
    rec['Notes'] = '; '.join(notes)
    rec['Legs_Name'] = f.get('LEGS', '') or f.get('ADF', '')
    a = active[0] if active else (good[0] if good else {})
    rec['Active_Gangs_Stacked_Inst_Shared'] = (
        '-'.join(f'{a[k]:g}' for k in ('gangs', 'stacked', 'inst', 'shared')) if a else '')
    return rec


RULES = [
    ('LVS string', 'Per device ";"-separated: Drain,Gate,Source,Bulk,Model+Vt,DiffWidth(nm),PolyCD(nm),Gangs,LegsStacked,'
                   'Instances,LegsShared. FLOAT = floating node; FAKE_NMOS_BULK / FAKE_PMOS_BULK when bulk unused.'),
    ('Active / non-active', 'Active = all terminals on pads (no FLOAT on drain/source). Non-active = gate on pad, at least one '
                            'S/D floating; usually opposite polarity (GNAC and unipolar devices: same polarity).'),
    ('Model', 'n|p + hp (or uhp for BCMOS balanced) + cell letter (110H c, 132H b, 165H a) + vt (hvt, svt, lvt, ulvt, ulvtll, elvt).'),
    ('Diff width', 'From Z and cell height (slide 7 library): 110: Z1 27 Z2 31 Z3 35 Z4 40 Z8 72; 132: Z2 31 Z3 35 Z4 40 Z5 46 Z6 49 '
                   'Z8 72 Z9 86; 165: Z2 31 Z4 40 Z6 52 Z7 62 Z9 86 (nm).'),
    ('Gangs', 'SH cell 1, DH cell 2 (BCMOS DH-balanced 1). Checked for POR / WID / HV / 1GANG names (slide 5 table).'),
    ('Poly CD', '12 nm for most skews; PLY CD skews (A:PLY:OGD:CD / A:PLYPP:OGD:CDPP, PLYITCN) 12 + signed skew; PP and other poly skews are not '
                'defined in the document and are not checked.'),
    ('Legs / instances', 'Name legs (#L) versus stacked / shared / instances are NOT checked (rule not in the document); see '
                         'Legs_Crosstab to derive it from the data.'),
    ('Flag meaning', 'A mismatch flags the structure name; the LVS string may also be the wrong one - verify in VALIDATE.'),
]


def find_lvs_file() -> Path | None:
    skip = ('string_generation', 'name_check', 'flagged')
    for d in (HERE, snv.VALIDATE_DIR):
        for p in sorted(d.glob('*')):
            if ('lvs' in p.name.lower() and p.suffix.lower() in ('.xlsx', '.xlsm', '.csv')
                    and not any(s in p.name.lower() for s in skip)):
                return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--lvs', type=Path, help='LVS data file with StructureName and lvsDeviceData columns')
    args = ap.parse_args()
    path = args.lvs or find_lvs_file()
    if not path or not path.exists():
        sys.exit('LVS data file not found - pass --lvs <file> (columns StructureName, lvsDeviceData).')
    snv.OUTPUT_DIR.mkdir(exist_ok=True)
    snv.VALIDATE_DIR.mkdir(exist_ok=True)

    lvs = load_lvs(path)
    if snv.DEFAULT_SUPERFLOW.exists():
        snv.compute_pair_hv(snv.load_superflow(snv.DEFAULT_SUPERFLOW))   # P-row POR names that are HV (owner WW45)
    lvs = lvs[~lvs.StructureName.str.upper().str.startswith('GNAC')].reset_index(drop=True)   # GNAC names carry no device parameters
    rows = []
    for r in lvs.itertuples(index=False):
        rec = {'Sheet': r.Sheet, 'StructureName': r.StructureName}
        rec.update(check(r.StructureName, r.lvsDeviceData))
        rec['shortedPinPairs'] = r.shortedPinPairs
        rec['lvsDeviceData'] = r.lvsDeviceData
        rows.append(rec)
    df = pd.DataFrame(rows)

    val = snv.OUTPUT_DIR / 'StructureName_Validation.csv'
    if val.exists():
        v = pd.read_csv(val, dtype=str, keep_default_na=False)
        v = v[v.Source == 'Superflow']
        strip = lambda s: s.str.replace(r'_TC\d+[A-Z]?_\d+$', '', regex=True)
        known = set(strip(v.Original_Name)) | set(strip(v.Corrected_Name))
        df['In_Superflow'] = df.StructureName.str.upper().isin(known)

    flagged = df[df.Name_Flagged]
    summary = pd.DataFrame([{'Check': c, 'OK': int((df[f'{c}_Check'] == 'OK').sum()),
                             'MISMATCH': int((df[f'{c}_Check'] == 'MISMATCH').sum()),
                             'NA (not checkable)': int((df[f'{c}_Check'] == 'NA').sum())} for c in CHECKS])
    summary.loc[len(summary)] = ['Structures', len(df) - len(flagged), len(flagged), '']
    legs = (df[df.Active_Gangs_Stacked_Inst_Shared != ''].groupby(['Family', 'Legs_Name', 'Active_Gangs_Stacked_Inst_Shared'])
            .size().reset_index(name='Count').sort_values(['Family', 'Legs_Name', 'Count'], ascending=[True, True, False]))
    cols_first = ['StructureName', 'Corrected_Base', 'Family', 'Name_Flagged', 'Mismatches']
    df = df[cols_first + [c for c in df.columns if c not in cols_first]]
    flagged = df[df.Name_Flagged]

    with pd.ExcelWriter(OUT_XLSX, engine='openpyxl') as xw:
        for name, d in [('Summary', summary), ('Flagged', flagged), ('All_Checked', df),
                        ('Not_Parsed', df[df.Name_Not_Parsed | (df.Notes != '')]), ('Legs_Crosstab', legs),
                        ('Rules', pd.DataFrame(RULES, columns=['Topic', 'Rule']))]:
            d.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            ws.freeze_panes = 'A2'
            for i, c in enumerate(d.columns, start=1):
                w = min(max([len(str(c))] + [len(str(x)) for x in d[c].head(300)]) + 2, 70)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    df.to_csv(OUT_CSV, index=False)
    review = flagged[['StructureName', 'Corrected_Base', 'Family', 'Mismatches', 'Name_POL', 'LVS_Active_POL', 'Name_CH',
                      'LVS_Cell', 'Name_VT', 'LVS_VT', 'Name_Z', 'Exp_Diff_nm', 'LVS_Diff_nm', 'Exp_Gangs', 'LVS_Gangs',
                      'Exp_PolyCD', 'LVS_PolyCD', 'lvsDeviceData']].copy()
    review['Name_Is_Wrong_Y_N'] = ''
    review['Correct_Name_If_Wrong'] = ''
    lvs_wrong = review.Mismatches.str.fullmatch(r'(POLYCD|DIFF)(, (POLYCD|DIFF))*')
    review['Name_Is_Wrong_Y_N'] = lvs_wrong.map({True: 'N', False: ''})
    review['LVS_Data_To_Correct'] = lvs_wrong.map({True: 'Y - PLY CD / UDEG LVS data to be corrected separately (owner WW45)',
                                                  False: ''})
    review['Correct_Name_If_Wrong'] = ''
    review.to_csv(FLAG_CSV, index=False)
    print(f'{len(df)} LVS strings checked from {path.name}: {len(flagged)} structures flagged')
    print(summary.to_string(index=False))


if __name__ == '__main__':
    main()
