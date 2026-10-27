# X81Z Structure Name Validation - Summary

Superflow names: **14235** (7004 unique bases). Etest-report-only names (not corrected here): 4732. Detail: `StructureName_Validation.xlsx`.

## Result

- Auto-fixed **11046**, already compliant **3189**, manual **0**
- Duplicates within a row after correction: **0**; the same corrected name used by different original names in different rows: 12; longest name **62** of 64 characters

## What changed

| Theme | Names | Example |
|---|---|---|
| Field order, VT and diffusion | 8430 | `DIFFOGD110-N27-L-A:FTIML:OGD:CD:N1_TC21_1` -> `DIFFOGD110-NZ1-NL-L-A:FTIML:OGD:CD:N1_TC21_1` |
| Delimiters and default padding | 5756 | `CTG132-NPZ5-L-H:SDR:OGD:DEN:1VG_PLY_TC22_1` -> `CTG132-NPZ5-NL-L-H:NOM:OGD:DEN:1VGPLY_TC22_1` |
| NOM / POR / DR | 7466 | `CTG110-NPZ1-E-A:SDR:OGD:REF:2TRK_TC22_1` -> `CTG110-NPZ1-NL-E-A:NOM:OGD:REF:2TRK_TC22_1` |
| TSDR skew grammar (incl. PLY/TCN) | 4364 | `CTG110-NPZ1-E-A:PLYFTCN:OGD:CD:0_TC22_1` -> `CTG110-NPZ1-NL-E-A:PLYPP:OGD:CDPP:NOM_TC22_1` |
| Environment, matching, WID, ADT, FK | 1653 | `FKNS44-110-Z1-L-TCN:OGD:CD:N1_TC22_1` -> `FKNS110-Z1-1L-L-CMOS-1X1-IXI-SH-A:TCN:OGD:CD:N1_TC22_1` |
| Layout path descriptors | 98 | `NS110-Z3-L-1L-CMOS-0X1-IXI-SH-TCNVCR:NONUB:6_TC22_1` -> `NS110-Z3-1L-L-CMOS-0X1-IXI-SH-A:TCNVCR:PGDNUB:NONUB_TC22_1` |
| Other (ADD_FOLD, BE_PLY, BE_SCHEME, BE_UNIFORM, BUILDSHEET_NAME, DESCRIPTOR_ABBR, DFT_NAME_LENGTH, GATE_KEEP, KLV_ENV, LEGS_NL, LIB_BITCELL_DIFF, LLE_REF, NVCR_VARIANT) | 4680 | |

## Per family

| Family | AUTO-FIX | OK | Total |
|---|---|---|---|
| BCLA | 0 | 36 | 36 |
| BE | 492 | 2539 | 3031 |
| BITCELL | 120 | 0 | 120 |
| FK | 60 | 0 | 60 |
| ISO | 3420 | 0 | 3420 |
| KLV | 308 | 0 | 308 |
| MLR | 2064 | 0 | 2064 |
| PFT | 3404 | 0 | 3404 |
| QREL | 0 | 614 | 614 |
| ROS | 766 | 0 | 766 |
| ROSPFT | 412 | 0 | 412 |

## Open items

- DR -> NOM: **43** structures (KLV, BE in device rows) - DR = design rule = POR (owner WW45); listed in `DR_to_NOM_Decisions.csv`
- ISO POR skews given a direction from their device series: **18** (`ISO_Skew_Direction_Decisions.csv`)
- Highlighted changes to confirm: **4085** (yellow rows in the Structures sheet)
- Bitcell widths differing from BKM slide 8: **20** (kept)
- BE and QnR names are not changed (owner WW45); rule findings for them are INFO only

## Files

- `StructureName_Validation.xlsx` / `.csv`: every name with original, corrected, status and issue codes
- `DR_to_NOM_Decisions.csv`: DR names written NOM; `ISO_Skew_Direction_Decisions.csv`: decisions to approve
- `StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx`: revised BKM, changes highlighted
