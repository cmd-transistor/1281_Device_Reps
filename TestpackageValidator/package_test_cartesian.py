#!/usr/bin/env python
"""Cartesian join of each test config's test names with the structures of one superflow test package.

Every row = one test name x one structure (updated name) that uses the config in the package, plus the split
structure-name fields from the family field map (family_field_map.FIELDS, also used by split_structure_names.jsl).
Test names of MOS / MAT configs come from the test config workbooks (the superflow only lists the config there);
test types (I2, R4, ...) keep the test names listed in the superflow. '<TARGET>' placeholders are filled with the
OPT_LVT leakage target (UL0 = uplift-0, single-transistor leakage; not UL1 full uplift) of the structure's model:
model (polarity, uhp for BCMOS, hv for HV, cell letter, VT) -> OPT_LVT model (p1281_OPT_LVT_model_mapping.csv) ->
<MODEL>_<diff width nm>_UL0 in p1281_idoff_VCC_0p75V_25C_tttt.csv (A, written in nA). Multi-leg scaling is left to the
official Ieff@opt target generation. --targets values take precedence.

    python package_test_cartesian.py [--package M1IL_01] [--superflow FILE.csv] [--targets TARGETS.csv]

TARGETS.csv columns: Structure_Name, Target and optionally Test_Configuration_Name / Placeholder (default TARGET).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

from family_field_map import FAMILY_ORDER, FIELDS, named, snv, split_name
from lvs_name_check import CH_LETTER, VT_TO_LVS
from testpackage_model import HERE, SUPERFLOW, ConfigDocument, discover_config_docs

VALIDATION = HERE / 'OUTPUT' / 'StructureName_Validation.csv'
R3_PACKAGES = HERE / 'OUTPUT' / '1X281Z_SUPER_FLOW_R3_packages.csv'
LEAKAGE = HERE / 'p1281_idoff_VCC_0p75V_25C_tttt.csv'
OPT_LVT_MAP = HERE / 'p1281_OPT_LVT_model_mapping.csv'
TARGET_UPLIFT = 'UL0'
PLACEHOLDER = re.compile(r'<([^<>]+)>')
TEST_COLS = ('Test Name', 'Test Names (Atlantis)', 'Test Name Output', 'Test Names (Device)')
VARIANT_SHEET_WORDS = ('PRE', 'CNODE', 'COMPARISON', 'FUTURE', 'EXTRA')


def sheet_rank(sheet: str) -> int:
    s = sheet.upper()
    if any(w in s for w in VARIANT_SHEET_WORDS):
        return 2
    return 0 if s.endswith('TEST CONFIG') else 1


def config_tests(cfg: str, sf_tests: list[str], docs: dict[str, ConfigDocument]) -> tuple[list[str], str]:
    found = False
    for doc_name, doc in docs.items():
        hit, blocks = doc.lookup(cfg)
        if not blocks:
            continue
        found = True
        blocks = sorted(blocks, key=lambda kv: (sheet_rank(kv[0]), -len(kv[1])))
        sheet, block = blocks[0]
        if 'TEST TYPES' in sheet.upper():
            return sf_tests, f'superflow (test type {hit} in {doc_name})'
        col = next((c for c in TEST_COLS if c in block.columns), None)
        tests = list(dict.fromkeys(t for t in block[col].astype(str).str.strip() if t)) if col else []
        if tests:
            how = 'exact' if hit == cfg.upper() else f'closest entry {hit}'
            return tests, f'{doc_name} / {sheet} / {col} ({how})'
    return sf_tests, 'superflow (no test list in config documents)' if found else \
        'superflow (config not in test config documents)'


def name_fields(corrected: str, family: str) -> dict:
    if not corrected:
        return {}
    base, inst, tc = split_name(corrected)
    d = named(family, base.split('-')) if family in FIELDS else {}
    skew = d.get('Skew', '')
    if skew.startswith('A:'):
        for k, chunk in enumerate(skew.split(':')[:6], start=1):
            d[f'Skew{k}'] = chunk
    d['Instance'], d['TC'] = inst, tc
    return d


def opt_lvt_targets(names) -> pd.DataFrame:
    """Per structure: device model, OPT_LVT model, diffusion width and the UL0 leakage target in nA."""
    leak = pd.read_csv(LEAKAGE, dtype=str)
    ul0 = {n.lower(): float(v) for n, v in zip(leak.NAME, leak.VALUE) if n.upper().endswith(f'_{TARGET_UPLIFT}')}
    mp = pd.read_csv(OPT_LVT_MAP, dtype=str)
    opt = dict(zip(mp.NAME.str.lower(), mp.VALUE.str.lower()))
    recs = []
    for name in names:
        base = split_name(name)[0]
        f = snv.parse(base).fields
        rec = {'Structure_Name': name, 'Model': '', 'OPT_LVT_Model': '', 'Diff_Width_nm': str(f.get('DIFF_W') or ''),
               'Target': '', 'Target_Note': ''}
        letter, vt = CH_LETTER.get(int(f['CH'])) if str(f.get('CH', '')).isdigit() else None, VT_TO_LVS.get(f.get('VT'))
        if f.get('POL') not in ('N', 'P') or not letter or not vt or not f.get('DIFF_W'):
            rec['Target_Note'] = 'model / diffusion width not derivable from the name'
            recs.append(rec)
            continue
        hv = 'hv' if re.search(r'(?:^|[-:])HV(?:$|[-:_])', base) else ''
        bal = 'u' if str(f.get('ENV') or '').startswith('BCMOS') else ''
        model = f"{f['POL'].lower()}{bal}hp{hv}{letter}{vt}"
        rec['Model'], rec['OPT_LVT_Model'] = model, opt.get(model, '')
        key = f"{rec['OPT_LVT_Model']}_{f['DIFF_W']}_{TARGET_UPLIFT}".lower()
        if not rec['OPT_LVT_Model']:
            rec['Target_Note'] = f'{model} not in {OPT_LVT_MAP.name}'
        elif key not in ul0:
            rec['Target_Note'] = f'{key.upper()} not in {LEAKAGE.name}'
        else:
            rec['Target'] = f'{ul0[key] * 1e9:.5g}'
        recs.append(rec)
    return pd.DataFrame(recs, columns=['Structure_Name', 'Model', 'OPT_LVT_Model', 'Diff_Width_nm', 'Target',
                                       'Target_Note'])


def apply_targets(out: pd.DataFrame, t: pd.DataFrame, source: str) -> int:
    missing = {'Structure_Name', 'Target'} - set(t.columns)
    if missing:
        raise SystemExit(f'{source} is missing columns: {", ".join(sorted(missing))}')
    t = t[t.Target.astype(str).str.strip().ne('')].copy()
    if 'Placeholder' not in t.columns:
        t['Placeholder'] = 'TARGET'
    keys = ['Structure_Name'] + (['Test_Configuration_Name'] if 'Test_Configuration_Name' in t.columns else [])
    filled = 0
    for ph, g in t.groupby('Placeholder'):
        tok = f'<{ph.upper()}>'
        lookup = g.drop_duplicates(keys).set_index(keys).Target
        idx = out.index[out.Test_Name.str.upper().str.contains(tok, regex=False)]
        vals = lookup.reindex(pd.MultiIndex.from_frame(out.loc[idx, keys]) if len(keys) > 1
                              else out.loc[idx, keys[0]]).to_numpy()
        for i, v in zip(idx, vals):
            if isinstance(v, str) and v:
                out.at[i, 'Test_Name'] = re.sub(re.escape(tok), v, out.at[i, 'Test_Name'], flags=re.IGNORECASE)
                out.at[i, 'Target_Source'] = source
                filled += 1
    return filled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', default='M1IL_01')
    ap.add_argument('--superflow', type=Path, default=R3_PACKAGES if R3_PACKAGES.exists() else SUPERFLOW)
    ap.add_argument('--targets', type=Path, help='CSV with Structure_Name, Target [, Test_Configuration_Name]')
    args = ap.parse_args()

    sf = pd.read_csv(args.superflow, dtype=str, keep_default_na=False)
    if args.package not in sf.columns:
        raise SystemExit(f'{args.package} is not a package column in {args.superflow.name}')
    pkg = sf[sf[args.package].str.strip().ne('')].copy()
    pkg['Test'] = pkg['Test Names'].str.split('|').str[0]

    val = pd.read_csv(VALIDATION, dtype=str, keep_default_na=False)
    val = val[val.Source == 'Superflow'].drop_duplicates('Original_Name').set_index('Original_Name')
    docs = {name: ConfigDocument(path) for name, path in discover_config_docs(HERE).items()}

    structs = pkg[['Test Configuration Name', 'Test Structure Alias', 'Test Row Alias']].drop_duplicates()
    parts, cfg_rows = [], []
    for cfg, g in structs.groupby('Test Configuration Name', sort=True):
        sf_tests = list(dict.fromkeys(pkg.loc[pkg['Test Configuration Name'] == cfg, 'Test']))
        tests, source = config_tests(cfg, sf_tests, docs)
        parts.append(g.merge(pd.DataFrame({'Test_Name': tests, 'Test_Name_Source': source}), how='cross'))
        cfg_rows.append({'Test_Configuration_Name': cfg, 'Test_Names': len(tests),
                         'Structures': g['Test Structure Alias'].nunique(), 'Rows': len(g) * len(tests),
                         'Test_Name_Source': source, 'Test_Name_List': ', '.join(tests)})
    out = pd.concat(parts, ignore_index=True)

    names = out['Test Structure Alias'].drop_duplicates()
    # superflow names may be originals (R2) or already-updated names (R3 and later)
    orig_to_new = {o: c for o, c in zip(val.index, val.Corrected_Name) if c}
    new_to_orig = {}
    for o, c in orig_to_new.items():
        new_to_orig.setdefault(c, o)
    for o, s in zip(val.index, val.Row_Specific_Names):
        for p in filter(None, s.split('; ')):
            new_to_orig.setdefault(p.split('=', 1)[1], o)
    fam_by_name = {**dict(zip(val.Corrected_Name, val.Family)), **dict(zip(val.index, val.Family))}
    updated = pd.Series([orig_to_new.get(n, n) for n in names], index=names)
    original = pd.Series([n if n in orig_to_new else new_to_orig.get(n, '') for n in names], index=names)
    family = pd.Series([fam_by_name.get(n) or fam_by_name.get(u) or fam_by_name.get(new_to_orig.get(n, ''))
                        or snv.detect_family(split_name(u)[0]) for n, u in zip(names, updated)], index=names)
    fields = pd.DataFrame([name_fields(u, f) for u, f in zip(updated, family)], index=names).fillna('')
    field_cols = list(dict.fromkeys([c for fam in FAMILY_ORDER for c in FIELDS[fam]]
                                    + [f'Skew{k}' for k in range(1, 7)] + ['Instance', 'TC']))
    field_cols = [c for c in field_cols if c in fields.columns] + \
        [c for c in fields.columns if c not in field_cols]

    out = out.rename(columns={'Test Configuration Name': 'Test_Configuration_Name', 'Test Row Alias': 'Test_Row_Alias'})
    out['Structure_Name'] = out['Test Structure Alias'].map(updated)
    out['Original_Structure_Name'] = out['Test Structure Alias'].map(original)
    out['Family'] = out['Test Structure Alias'].map(family)
    out = out.join(fields[field_cols], on='Test Structure Alias')
    out['Test_Name_Template'] = out.Test_Name
    out['Target_Source'] = ''
    filled = apply_targets(out, pd.read_csv(args.targets, dtype=str, keep_default_na=False), args.targets.name) \
        if args.targets else 0
    need = out.Test_Name_Template.str.upper().str.contains('<TARGET>', regex=False)
    opt = opt_lvt_targets(out.loc[need, 'Structure_Name'].drop_duplicates())
    filled_opt = apply_targets(out, opt[['Structure_Name', 'Target']], f'OPT_LVT {TARGET_UPLIFT} ({LEAKAGE.name})')
    out = out.merge(opt.drop(columns='Target').rename(columns={'Target_Note': 'OPT_LVT_Note'}),
                    on='Structure_Name', how='left')
    opt_cols = ['Model', 'OPT_LVT_Model', 'Diff_Width_nm', 'OPT_LVT_Note']
    out[opt_cols] = out[opt_cols].astype(object).fillna('')
    out[f'OPT_LVT_{TARGET_UPLIFT}_nA'] = out.Structure_Name.map(dict(zip(opt.Structure_Name, opt.Target))).fillna('')
    pending = out.Test_Name.str.findall(PLACEHOLDER).str.join(', ')
    out['Target_Status'] = [f'pending target generation ({p}{": " + n if n else ""})' if p else ''
                            for p, n in zip(pending, out.get('OPT_LVT_Note', pd.Series('', index=out.index)))]
    out = out[['Test_Name', 'Structure_Name', 'Original_Structure_Name', 'Test_Configuration_Name',
               'Test_Row_Alias', 'Family', *field_cols, 'Test_Name_Template', 'Target_Status', 'Target_Source',
               'Model', 'OPT_LVT_Model', 'Diff_Width_nm', f'OPT_LVT_{TARGET_UPLIFT}_nA', 'Test_Name_Source']]

    rev = re.search(r'_(R\d+)', args.superflow.stem)
    stem = HERE / 'OUTPUT' / f'{args.package}{"_" + rev.group(1) if rev else ""}_Test_Structure_Cartesian'
    out.to_csv(stem.with_suffix('.csv'), index=False)
    with pd.ExcelWriter(stem.with_suffix('.xlsx'), engine='xlsxwriter') as xw:
        head = xw.book.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': 'white'})
        for sheet, df in [('Cartesian', out), ('Config_Tests', pd.DataFrame(cfg_rows))]:
            df.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            for i, col in enumerate(df.columns):
                ws.write(0, i, col, head)
                ws.set_column(i, i, min(max([len(col)] + [len(str(x)) for x in df[col].head(300)]) + 2, 60))
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, len(df), len(df.columns) - 1)
    print(f'{args.package} from {args.superflow.name}: {len(out):,} test x structure rows, '
          f'{out.Structure_Name.nunique():,} structures, {len(cfg_rows)} configs -> {stem.with_suffix(".xlsx").name}')
    print(f'rows with pending placeholders: {int(out.Target_Status.ne("").sum()):,} | filled from OPT_LVT '
          f'{TARGET_UPLIFT}: {filled_opt:,}' + (f' | filled from {args.targets.name}: {filled:,}' if args.targets else ''))
    print(pd.DataFrame(cfg_rows).drop(columns='Test_Name_List').to_string(index=False))


if __name__ == '__main__':
    main()
