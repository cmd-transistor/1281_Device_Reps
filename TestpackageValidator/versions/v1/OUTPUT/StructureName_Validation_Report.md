# X81Z Structure Name Validation - Summary

Superflow names: **14235** (7004 unique bases). Etest-report-only names (not corrected here): 4732. Detail: `StructureName_Validation.xlsx`.

## Result

- Auto-fixed **14199**, already compliant **36**, manual **0**
- Duplicates after correction: **0**; longest name **64** of 64 characters

## What changed

| Theme | Names | Example |
|---|---|---|
| Field order, VT and diffusion | 8430 | `DIFFOGD110-N27-L-A:FTIML:OGD:CD:N1_TC21_1` -> `DIFFOGD110-NZ1-NL-L-A:FTIML:OGD:CD:N1_TC21_1` |
| Delimiters and default padding | 5816 | `CTG132-NPZ5-L-H:SDR:OGD:DEN:1VG_PLY_TC22_1` -> `CTG132-NPZ5-NL-L-H:NOM:OGD:DEN:1VGPLY_TC22_1` |
| NOM / POR / DR | 6984 | `CTG110-NPZ1-E-A:SDR:OGD:REF:2TRK_TC22_1` -> `CTG110-NPZ1-NL-E-A:NOM:OGD:REF:2TRK_TC22_1` |
| TSDR skew grammar (incl. PLY/TCN) | 4256 | `CTG110-NPZ1-E-A:PLYFTCN:OGD:CD:0_TC22_1` -> `CTG110-NPZ1-NL-E-A:PLYFTCN:OGD:CD:NOM_TC22_1` |
| Environment, matching, WID, ADT, FK | 1038 | `FKNS44-110-Z1-L-TCN:OGD:CD:N1_TC22_1` -> `NS110-Z1-1L-L-CMOS-1X1-IXI-SH-A:FKTCN:OGD:CD:N1:44PP_TC22_1` |
| Back-end and QnR layout | 3223 | `M0_ECD_B_12_9_30UM_CD_+1_110H_TC22_1` -> `M0ECD110-B-12-9-30UM-A:M0:Z:CD:P1_TC22_1` |
| Other (ADD_FOLD, LEGS_NL, LIB_BITCELL_DIFF) | 3840 | |

## Per family

| Family | AUTO-FIX | OK | Total |
|---|---|---|---|
| BCLA | 0 | 36 | 36 |
| BE | 3031 | 0 | 3031 |
| BITCELL | 120 | 0 | 120 |
| FK | 60 | 0 | 60 |
| ISO | 3420 | 0 | 3420 |
| KLV | 308 | 0 | 308 |
| MLR | 2064 | 0 | 2064 |
| PFT | 3404 | 0 | 3404 |
| QREL | 614 | 0 | 614 |
| ROS | 766 | 0 | 766 |
| ROSPFT | 412 | 0 | 412 |

## Open items

- DR -> NOM: **261** structures (BE/KLV) keep DR until `Approve_Y_N` = Y in `DR_to_NOM_Decisions.csv`; 2 would collide with an existing NOM name
- ISO POR skews given a direction from their device series: **18** (`ISO_Skew_Direction_Decisions.csv`)
- Highlighted changes to confirm: **4682** (yellow rows in the Structures sheet)
- Bitcell widths differing from BKM slide 8: **20** (kept)
- QnR names are a draft until the code decoder is available

## Files

- `StructureName_Validation.xlsx` / `.csv`: every name with original, corrected, status and issue codes
- `DR_to_NOM_Decisions.csv`, `ISO_Skew_Direction_Decisions.csv`: decisions to approve
- `StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx`: revised BKM, changes highlighted
