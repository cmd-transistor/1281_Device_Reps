#!/usr/bin/env python
"""Regenerate every output after a change and report any file in OUTPUT/ or VALIDATE/ that was not refreshed.

Atlantis is not pulled again (the LVS check and rename table use the saved pulls); run pull_atlantis_lvs.py or
atlantis_rename_table.py without --cached for fresh Atlantis data. Close the Excel/PowerPoint outputs first.

    python rebuild_all.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ['structure_name_validator.py'],
    ['update_bkm_pptx.py'],
    ['family_field_map.py'],
    ['row_owner_summary.py'],
    ['ros_matching_list.py'],
    ['lvs_name_check.py', '--lvs', 'OUTPUT/Atlantis_81Z_TapeIn_LVS.csv'],
    ['buildsheet_issues.py'],
    ['atlantis_rename_table.py', '--cached'],
    ['dft_check_audit.py'],
    ['buildsheet_update_files.py'],
]
# inputs or hand-maintained files, not produced by the steps above
NOT_GENERATED = {'Atlantis_81Z_TapeIn_LVS.csv', 'Atlantis_81Z_TapeIn_LVS.sql', 'Atlantis_81Z_TapeIn_Structures_raw.csv',
                 'Atlantis_81Z_MLR_OpenCal_raw.csv', 'Atlantis_81Z_ISO_Buildsheet_raw.csv',
                 'Structure_Nomenclature_Learnings.md'}


def main():
    start = time.time()
    failed = []
    for step in STEPS:
        r = subprocess.run([sys.executable, *step], cwd=HERE, capture_output=True, text=True)
        last = (r.stdout.strip().splitlines() or [''])[-1]
        print(f"{'OK  ' if r.returncode == 0 else 'FAIL'} {step[0]:30s} {last[:110]}")
        if r.returncode:
            failed.append(step[0])
            print(r.stderr.strip()[-600:])
    stale = [p for d in ('OUTPUT', 'VALIDATE', 'OUTPUT/Buildsheet_Updates') for p in sorted((HERE / d).glob('*'))
             if p.is_file() and p.name not in NOT_GENERATED and p.stat().st_mtime < start]
    for p in stale:
        print(f'STALE {p.relative_to(HERE)}')
    print(f"{len(STEPS) - len(failed)}/{len(STEPS)} steps OK, {len(stale)} stale files"
          + ('' if not stale and not failed else ' - check above'))
    print('Hand-maintained (update manually if rules changed): OUTPUT/Structure_Nomenclature_Learnings.md, '
          'split_structure_names.jsl, versions/CHANGELOG.md')
    sys.exit(1 if failed or stale else 0)


if __name__ == '__main__':
    main()
