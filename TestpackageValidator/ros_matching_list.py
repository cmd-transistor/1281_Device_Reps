"""ROS structure tree: every ROS / ROSPFT with its matching ROSPFT, standard PFT, MLR and FK (full KTCN) POR structures,
and the supporting structures of the same device (PFT / MLR / FK skews and variants, ISO and KLV of the same CH, Z, VT).

Reads StructureName_Validation.csv (superflow rows only) and writes ROS_Matching_Structures.xlsx (Wide, Tree,
No_Partner), ROS_Matching_Structures_Wide.csv and ROS_Structure_Tree.csv (used by testpackage_gui.py).
Match key: cell height + Z + VT (+ polarity for ROSPFT, + legs for ROSPFT / PFT / FK; MLR uses #ADF, ISO / KLV have
no legs). LNR (nanoribbon length) = diffusion width of the Z code. Rows, libraries and test configs are listed per
structure.
"""
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / 'OUTPUT' / 'StructureName_Validation.csv'
RAW = HERE / 'OUTPUT' / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
TC = re.compile(r'_TC[0-9A-Z]+_\d+$')
ROW_TC = re.compile(r'_TC\d+[A-Z]?_\d+$')
PARTNERS = ['ROSPFT', 'PFT', 'MLR', 'FK']
SUPPORT = ['PFT', 'MLR', 'FK', 'ISO', 'KLV']
INFO = ['Family', 'CH', 'Z', 'LNR_nm', 'VT', 'POL', 'LEGS', 'ADD', 'Rows', 'Libraries', 'Configs']


def load() -> pd.DataFrame:
    st = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    sf = st[(st.Source == 'Superflow') & (st.Corrected_Name != '')].copy()
    sf['Base'] = sf.Corrected_Name.str.replace(TC, '', regex=True)
    lib = {}
    if RAW.exists():
        a = pd.read_csv(RAW, dtype=str, keep_default_na=False)
        lib = dict(zip(a.TestRowName.str.lower(), a.Library))
    join = lambda s: '; '.join(sorted({x for v in s for x in v.split('; ') if x}))
    g = sf.groupby(['Family', 'Base'])
    out = g.first()[['CH', 'DIFF', 'DIFF_W', 'VT', 'POL', 'LEGS', 'ADF', 'ADD', 'SKEW']]
    out['Rows'] = g.Row.agg(lambda s: '; '.join(sorted({ROW_TC.sub('', r).lower() for v in s for r in v.split('; ')})))
    out['Configs'] = g.Configs.agg(join)
    out = out.reset_index().rename(columns={'DIFF': 'Z', 'DIFF_W': 'LNR_nm'})
    out['Libraries'] = out.Rows.apply(lambda s: '; '.join(sorted({lib.get(r, '') for r in s.split('; ')} - {''})))
    out['LEGS'] = out.LEGS.where(out.LEGS != '', out.ADF)
    out['POR'] = out.ADD.str.match(r'^[A-Z]:NOM(?:-|$)') | out.SKEW.str.fullmatch(r'[A-Z]:NOM')
    out.loc[out.Family == 'FK', 'POR'] = out.ADD.str.fullmatch(r'[A-Z]:TCN:[A-Z]+:CD:NOM')   # FK: TCN CD series centre
    out['STD'] = ~((out.Family == 'PFT') & ~out.Base.str.match(r'[NP]S\d'))
    return out


def match(root: pd.Series, pool: pd.DataFrame, fam: str, legs: bool) -> pd.DataFrame:
    p = pool[pool.Family == fam]
    m = (p.CH == root.CH) & (p.Z == root.Z) & p.VT.isin(set(root.VT.split(':')))
    if root.POL in ('N', 'P') and fam not in ('ISO', 'KLV'):
        m &= p.POL == root.POL
    if legs:
        m &= p.LEGS == root.LEGS
    return p[m].sort_values('Base')


def main():
    sf = load()
    roots = sf[sf.Family.isin(['ROS', 'ROSPFT'])].sort_values(['CH', 'Z', 'LEGS', 'VT', 'POL', 'Base'])
    wide, tree = [], []
    for gid, (_, r) in enumerate(roots.iterrows(), start=1):
        node = lambda lvl, role, x: {'Group': gid, 'Root': r.Base, 'Level': lvl, 'Role': role, 'Structure': x.Base,
                                     **{k: x[k] for k in INFO}}
        tree.append(node(0, r.Family, r))
        por, sup = {}, {}
        for fam in PARTNERS:
            if fam == r.Family:
                continue
            cand = match(r, sf, fam, legs=fam != 'MLR')
            por[fam] = cand[cand.POR & cand.STD]
            tree += [node(1, fam, x) for _, x in por[fam].iterrows()]
        for fam in SUPPORT:
            cand = match(r, sf, fam, legs=fam in ('PFT', 'FK'))
            sup[fam] = cand[~cand.Base.isin(por[fam].Base)] if fam in por else cand
            tree += [node(2, f'{fam} supporting', x) for _, x in sup[fam].iterrows()]
        wide.append({'Group': gid, 'Family': r.Family, 'Structure': r.Base, 'POL': r.POL, 'CH': r.CH, 'Z': r.Z,
                     'LNR_nm': r.LNR_nm, 'LEGS': r.LEGS, 'VT': r.VT, 'Rows': r.Rows, 'Libraries': r.Libraries,
                     **{f'{f}_Count': len(por[f]) if f in por else '' for f in PARTNERS},
                     **{f'{f}_Supporting': len(sup[f]) for f in SUPPORT},
                     **{f'{f}_Matches': '; '.join(por[f].Base) if f in por else '' for f in PARTNERS}})

    wide = pd.DataFrame(wide)
    tree = pd.DataFrame(tree)
    counts = [f'{f}_Count' for f in PARTNERS]
    num = wide[counts].apply(pd.to_numeric, errors='coerce').fillna(0)
    unmatched = wide[(num == 0).all(axis=1)][['Family', 'Structure', 'POL', 'CH', 'Z', 'LEGS', 'VT']]
    with pd.ExcelWriter(HERE / 'OUTPUT' / 'ROS_Matching_Structures.xlsx', engine='openpyxl') as xw:
        wide.to_excel(xw, sheet_name='Wide', index=False)
        tree.to_excel(xw, sheet_name='Tree', index=False)
        unmatched.to_excel(xw, sheet_name='No_Partner', index=False)
        for ws in xw.sheets.values():
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or '')) for c in col[:200]) + 2, 70)
    wide.to_csv(HERE / 'OUTPUT' / 'ROS_Matching_Structures_Wide.csv', index=False)
    tree.to_csv(HERE / 'OUTPUT' / 'ROS_Structure_Tree.csv', index=False)
    print(wide.assign(**num).groupby('Family')[counts].agg(lambda s: f'{(s > 0).sum()}/{len(s)}').to_string())
    print(f'{len(wide)} ROS/ROSPFT roots, {len(tree)} tree nodes; without any POR partner: {len(unmatched)}')


if __name__ == '__main__':
    main()
