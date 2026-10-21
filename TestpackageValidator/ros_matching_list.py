"""List ROS / ROSPFT structures with the matching PFT, MLR and FKTCN structures next to them.

Reads StructureName_Validation.csv (superflow rows only) and writes ROS_Matching_Structures.xlsx / _Wide.csv.
Match key: cell height + Z + VT (+ polarity for ROSPFT, + legs for PFT/FK; MLR has no legs, it uses #ADF).
By default only POR (A:NOM) partners are listed; use --all to include skewed / variant partners.
"""
import argparse
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
SRC = HERE / 'OUTPUT' / 'StructureName_Validation.csv'
TC = re.compile(r'_TC[0-9A-Z]+_\d+$')
PARTNERS = ['PFT', 'MLR', 'FK']


def load(all_variants: bool) -> pd.DataFrame:
    st = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    sf = st[(st.Source == 'Superflow') & (st.Corrected_Name != '')].copy()
    sf['Base'] = sf.Corrected_Name.str.replace(TC, '', regex=True)
    sf = sf.drop_duplicates(['Family', 'Base'])
    sf = sf[(sf.Family != 'PFT') | sf.Base.str.match(r'[NP]S\d')]
    if not all_variants:
        poor = sf.Family.isin(['PFT', 'MLR']) & ~sf.ADD.str.fullmatch(r'A:NOM(?::LLE)?')
        sf = sf[~poor]
    return sf


def partners_for(ros: pd.Series, pool: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    vts = set(ros.VT.split(':'))
    out = {}
    for fam in PARTNERS:
        p = pool[fam]
        m = (p.CH == ros.CH) & (p.DIFF == ros.DIFF) & p.VT.isin(vts)
        if ros.POL:
            m &= p.POL == ros.POL
        if fam != 'MLR':
            m &= p.LEGS == ros.LEGS
        out[fam] = sorted(p[m].Base)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--all', action='store_true', help='include skewed / variant PFT, MLR, FK partners')
    args = ap.parse_args()

    sf = load(args.all)
    pool = {f: sf[sf.Family == f] for f in PARTNERS}
    ros = sf[sf.Family.isin(['ROS', 'ROSPFT'])].sort_values(['CH', 'DIFF', 'LEGS', 'VT', 'POL', 'Base'])

    wide, long = [], []
    for gid, (_, r) in enumerate(ros.iterrows(), start=1):
        mt = partners_for(r, pool)
        wide.append({'Group': gid, 'Family': r.Family, 'Structure': r.Base, 'POL': r.POL, 'CH': r.CH,
                     'Z': r.DIFF, 'LEGS': r.LEGS, 'VT': r.VT,
                     **{f'{f}_Count': len(mt[f]) for f in PARTNERS},
                     **{f'{f}_Matches': '; '.join(mt[f]) for f in PARTNERS}})
        long.append({'Group': gid, 'Role': r.Family, 'Structure': r.Base})
        long += [{'Group': gid, 'Role': f, 'Structure': n} for f in PARTNERS for n in mt[f]]

    wide = pd.DataFrame(wide)
    long = pd.DataFrame(long)
    keycols = ['Family', 'Structure', 'POL', 'CH', 'Z', 'LEGS', 'VT']
    unmatched = wide[(wide[[f'{f}_Count' for f in PARTNERS]] == 0).all(axis=1)][keycols]
    with pd.ExcelWriter(HERE / 'OUTPUT' / 'ROS_Matching_Structures.xlsx', engine='openpyxl') as xw:
        wide.to_excel(xw, sheet_name='Wide', index=False)
        long.to_excel(xw, sheet_name='Long', index=False)
        unmatched.to_excel(xw, sheet_name='No_Partner', index=False)
        for ws in xw.sheets.values():
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or '')) for c in col[:200]) + 2, 70)
    wide.to_csv(HERE / 'OUTPUT' / 'ROS_Matching_Structures_Wide.csv', index=False)
    print(f'{len(wide)} ROS/ROSPFT structures; without any partner: {len(unmatched)}')
    print(wide.groupby('Family')[[f'{f}_Count' for f in PARTNERS]].agg(lambda s: f'{(s > 0).sum()}/{len(s)}').to_string())


if __name__ == '__main__':
    main()
