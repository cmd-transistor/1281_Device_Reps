#!/usr/bin/env python
"""Audit DFT name-length and duplicate risks and reconcile approved name proposals."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'OUTPUT'
RAW = OUTPUT / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
RENAMES = OUTPUT / 'Atlantis_Structure_Rename.csv'
ROWS = OUTPUT / 'Atlantis_Row_Summary.csv'
DFT_EXPORT = HERE / 'X81Z_Catalog_R2_Structure_With_More_Than_55_Char.csv_WW43p5.csv'
LENGTH_OUT = OUTPUT / 'DFT_Name_Length_Audit.csv'
DUPLICATE_OUT = OUTPUT / 'DFT_Duplicate_Name_Audit.csv'
CONFIRMED_OUT = OUTPUT / 'DFT_Checker_Confirmed_Length_Audit.csv'
PROPOSALS_OUT = OUTPUT / 'DFT_Checker_Rename_Proposals.csv'
TC_SUFFIX = r'_TC\d+[A-Z]?_\d+$'
TYPE_NOTE = 'Confirm Test Type in Atlantis DFT checker; cached pull omits this field'


def propose_name(name: str, recommendation: str) -> str:
    if recommendation == 'Remove S in RST':
        proposed, count = re.subn(r'RST(?=:)', 'RT', name, count=1)
    elif recommendation == 'Remove _LLE from only these structures':
        proposed, count = (name[:-4], 1) if name.endswith('_LLE') else (name, 0)
    elif recommendation.startswith('Remove L and R in Lxx..CxRxx'):
        proposed, count = re.subn(r'L(\d+)C(\d+)R(\d+)$', r'\1C\2\3', name, count=1)
    elif recommendation == 'OFF:Rx -> OFFx':
        proposed, count = re.subn(r':OFF:R(\d+)(?=:|$)', r':OFF\1', name, count=1)
    else:
        raise ValueError(f'Unknown DFT rename recommendation: {recommendation!r}')
    if not count:
        raise ValueError(f'Recommendation {recommendation!r} did not match {name!r}')
    return proposed


def main() -> None:
    raw = pd.read_csv(RAW, dtype=str, keep_default_na=False)
    names = pd.read_csv(RENAMES, dtype=str, keep_default_na=False)
    rows = pd.read_csv(ROWS, dtype=str, keep_default_na=False)
    data = names.merge(raw[['TestStructId', 'RowStatus']], left_on='TESTSTRUCTID', right_on='TestStructId',
                       how='left', validate='many_to_one')
    data = data[data.RowStatus.eq('Configured for Tape-in')].copy()
    data = data.merge(rows[['LIBRARY', 'TESTROWNAME', 'IN_SUPERFLOW']], on=['LIBRARY', 'TESTROWNAME'],
                      how='left', validate='many_to_one')
    data['NAME_LENGTH_EXCL_TC'] = data.TESTSTRUCTNAME.str.replace(TC_SUFFIX, '', regex=True).str.len()
    data['OVER_BY'] = (data.NAME_LENGTH_EXCL_TC - 55).clip(lower=0)
    data['NAME_CHANGED'] = data.TESTSTRUCTNAME.ne(data.OLD_TESTSTRUCTNAME)

    length = data[data.NAME_LENGTH_EXCL_TC.gt(55)].copy()
    length['DFT_TYPE_CHECK'] = TYPE_NOTE
    length = length[['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTID', 'IN_SUPERFLOW', 'OLD_TESTSTRUCTNAME',
                     'TESTSTRUCTNAME', 'NAME_LENGTH_EXCL_TC', 'OVER_BY', 'NAME_CHANGED', 'DFT_TYPE_CHECK']]
    length = length.sort_values(['LIBRARY', 'TESTROWNAME', 'NAME_LENGTH_EXCL_TC', 'TESTSTRUCTNAME'])
    length.to_csv(LENGTH_OUT, index=False)

    duplicate = (data[data.TESTSTRUCTNAME.ne('') & ~data.TESTSTRUCTNAME.str.upper().str.startswith('GNAC')]
                 .groupby(['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTNAME'], as_index=False)
                 .agg(DUPLICATE_COUNT=('TESTSTRUCTID', 'size'),
                      TESTSTRUCTIDS=('TESTSTRUCTID', lambda values: ';'.join(values)),
                      IN_SUPERFLOW=('IN_SUPERFLOW', 'first')))
    duplicate = duplicate[duplicate.DUPLICATE_COUNT.gt(1)].copy()
    duplicate['DFT_TYPE_CHECK'] = TYPE_NOTE
    duplicate = duplicate[['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTNAME', 'DUPLICATE_COUNT', 'TESTSTRUCTIDS',
                           'IN_SUPERFLOW', 'DFT_TYPE_CHECK']]
    duplicate = duplicate.sort_values(['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTNAME'])
    duplicate.to_csv(DUPLICATE_OUT, index=False)

    checker = pd.read_csv(DFT_EXPORT, dtype=str, keep_default_na=False, encoding='cp1252')
    checker = checker.rename(columns={'RowName': 'TESTROWNAME', 'StructureName': 'CHECKER_TESTSTRUCTNAME',
                                      'Unnamed: 2': 'DFT_LENGTH_EXCL_TC', 'Unnamed: 3': 'DFT_RECOMMENDATION'})
    checker['DFT_LENGTH_EXCL_TC'] = pd.to_numeric(checker.DFT_LENGTH_EXCL_TC, errors='raise')
    checker = (checker.groupby(['TESTROWNAME', 'CHECKER_TESTSTRUCTNAME'], as_index=False)
               .agg(CHECKER_OCCURRENCES=('DFT_LENGTH_EXCL_TC', 'size'),
                    DFT_LENGTH_EXCL_TC=('DFT_LENGTH_EXCL_TC', 'first'),
                    DFT_RECOMMENDATION=('DFT_RECOMMENDATION', 'first')))
    checker['PROPOSED_TESTSTRUCTNAME'] = [propose_name(name, recommendation)
                                          for name, recommendation in zip(checker.CHECKER_TESTSTRUCTNAME,
                                                                          checker.DFT_RECOMMENDATION)]
    current_names = (names.groupby(['TESTROWNAME', 'TESTSTRUCTNAME'], as_index=False)
                     .agg(LIBRARY=('LIBRARY', 'first'),
                          TESTSTRUCTIDS=('TESTSTRUCTID', lambda values: ';'.join(dict.fromkeys(values))),
                          OLD_TESTSTRUCTNAMES=('OLD_TESTSTRUCTNAME', lambda values: ';'.join(dict.fromkeys(values)))))
    confirmed = checker.merge(current_names, left_on=['TESTROWNAME', 'PROPOSED_TESTSTRUCTNAME'],
                              right_on=['TESTROWNAME', 'TESTSTRUCTNAME'], how='left', validate='one_to_one')
    if confirmed.LIBRARY.isna().any():
        raise ValueError('One or more approved DFT names did not match the Atlantis rename table')
    confirmed = confirmed.merge(rows[['LIBRARY', 'TESTROWNAME', 'IN_SUPERFLOW']],
                                on=['LIBRARY', 'TESTROWNAME'], how='left', validate='many_to_one')
    confirmed['PROPOSED_LENGTH_EXCL_TC'] = confirmed.PROPOSED_TESTSTRUCTNAME.str.replace(
        TC_SUFFIX, '', regex=True).str.len()
    if confirmed.PROPOSED_LENGTH_EXCL_TC.gt(55).any():
        raise ValueError('One or more proposed names still exceed the DFT 55-character limit')

    names_by_row = names.groupby(['LIBRARY', 'TESTROWNAME'])
    collisions = []
    for proposal in confirmed.itertuples(index=False):
        current_row = names_by_row.get_group((proposal.LIBRARY, proposal.TESTROWNAME))
        candidate_ids = set(proposal.TESTSTRUCTIDS.split(';'))
        existing = current_row[current_row.TESTSTRUCTNAME.eq(proposal.PROPOSED_TESTSTRUCTNAME)]
        collision_ids = sorted(set(existing.TESTSTRUCTID) - candidate_ids)
        collisions.append(';'.join(collision_ids))
    confirmed['COLLISION_WITH_TESTSTRUCTIDS'] = collisions
    confirmed['PROPOSAL_ALIAS_COUNT_IN_ROW'] = confirmed.groupby(
        ['LIBRARY', 'TESTROWNAME', 'PROPOSED_TESTSTRUCTNAME']).TESTSTRUCTNAME.transform('size')
    confirmed['APPROVAL_STATUS'] = 'Applied per user approval'
    confirmed['OVER_BY'] = confirmed.DFT_LENGTH_EXCL_TC - 55
    confirmed = confirmed[['LIBRARY', 'TESTROWNAME', 'TESTSTRUCTIDS', 'IN_SUPERFLOW', 'CHECKER_OCCURRENCES',
                           'OLD_TESTSTRUCTNAMES', 'CHECKER_TESTSTRUCTNAME', 'TESTSTRUCTNAME',
                           'DFT_RECOMMENDATION', 'PROPOSED_TESTSTRUCTNAME', 'DFT_LENGTH_EXCL_TC',
                           'PROPOSED_LENGTH_EXCL_TC', 'OVER_BY', 'COLLISION_WITH_TESTSTRUCTIDS',
                           'PROPOSAL_ALIAS_COUNT_IN_ROW', 'APPROVAL_STATUS']]
    confirmed = confirmed.sort_values(['TESTROWNAME', 'CHECKER_TESTSTRUCTNAME'])
    confirmed.to_csv(CONFIRMED_OUT, index=False)
    confirmed.to_csv(PROPOSALS_OUT, index=False)

    by_library = length.groupby('LIBRARY').size().to_dict()
    collision_count = confirmed.COLLISION_WITH_TESTSTRUCTIDS.ne('').sum()
    print(f'{len(length)} configured structures exceed 55 characters (TC suffix excluded): {by_library}')
    print(f'{len(duplicate)} possible duplicate-name groups; confirm Test Type in the DFT checker')
    print(f'{len(checker)} unique DFT checker failures ({int(checker.CHECKER_OCCURRENCES.sum())} listed entries) '
          f'across {confirmed.LIBRARY.nunique()} library; {collision_count} name collisions; '
          'approved names reflected in the rename table')


if __name__ == '__main__':
    main()
