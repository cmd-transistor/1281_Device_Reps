# X81Z structure names - v2 summary for the team (2026-10-22)

Baseline: v1 (2026-10-21). Inputs: superflow `1X281Z_SUPER_FLOW_R2 - FE_26WW43p1_v2.csv` (14,235 structure names,
286,926 tests), Etest report, device tracker, Atlantis (read-only pull, project X81).
Full detail: `versions/CHANGELOG.md`; rules and reasoning: `OUTPUT/Structure_Nomenclature_Learnings.md`.

## 1. Result

| | |
|---|---|
| Superflow names validated | 14,235: 11,046 corrected automatically, 3,189 already compliant (BE / QnR / BCLA unchanged), 0 manual |
| Duplicates within a row | 0 |
| Longest corrected name | 64 characters incl. `_TCxx_y` |
| Atlantis structures to rename | 6,166 in 236 rows, 9 libraries |
| Empty structure comments filled | 1,802 suggestions, plus 80 buildsheet notes |
| Miller open-cal references to update | 610 |
| Buildsheet / Atlantis issues found | 89 |

Scope: only rows `Configured for Tape-in` that are in the superflow are changed. Back-end (BE) and QnR names are not
changed, except back-end-style front-end via chains in device rows.

## 2. Naming changes in v2 (final state)

| Topic | v2 rule | Example |
|---|---|---|
| Matching pairs | head `MNS` / `MPS`, normal environment (no `/NP`, `/PP`) | `MNS110-Z1-1L-E-CMOS-0X1-IXI-SH-A:NOM` |
| ADT and HV | double height `DH` (1GANG ADT stays `SH`); PCL95 POR follows NCL95 HV | `PS165-Z4-1L-L-UNI-10X10-IXI-DH-A:HV` |
| Bitcell | SR / RF cell type in the height position | `SRN192-30-1L-PD-HDC1-SNXSN-SNXSN-SR-A:NOM` |
| Within die | no `W` prefix; `A:WID` in the skew position | `MNS132-Z2-1L-L-CMOS-0X1-IXI-SH-A:WID` |
| PLY CD + poly pitch | layer `PLYPP`, kind `CDPP`; CD only stays `PLY:..:CD` | `A:PLYPP:OGD:CDPP:N2`, `A:PLY:OGD:CD:N0D4` |
| Full KTCN | `FK` head prefix, TCN skew, 44PP implied | `FKNS132-Z5-1L-L-CMOS-1X1-IXI-SH-A:TCN:OGD:CD:N1` |
| Lego type | every first additional token starts with `A:` | `A:HV`, `A:STK`, `A:1GANG` |
| LLE | no `:LLE` tag | |
| KLV environment | after VT: N / P = `UNI`, NP = `CMOS`, ND (no diffusion) = `/D` with polarity N | `GATEKLV110-NZ1-NL-S-/D-A:NOM` |
| Analog PFT environment | legacy `CMOS_A` goes in ENV as `ANALOG` | `NS110-Z1-1L-E-ANALOG-0X1-IXI-SH-A:NOM` |
| DR | design rule = POR, written `A:NOM` | `GATEKLV132-NPZ5-NL-L-CMOS-A:NOM` |
| LLE reference | `REF` is nominal for its LLE sub-family; all LLE references use `NOM` without `_LLE` | `NS165-Z7-1L-L-CMOS-0X0-IXI-SH-A:NOM`, `A:UDEG:NOM` |
| Layer removal | TSDR `A:{N<layer>/..}:Z:REM:ALL`, IMPLANT = IMP | `A:NPXL/NGLK:Z:REM:ALL`, `A:NDBI/NIMP:Z:REM:ALL` |
| XTRCAP only | VCR present is a variant, not a TSDR skew; gate count without NoImplant means implant kept | `A:NVCR`, `A:YIMP:Z:KEP:ALL:20250G` |
| XTRPFX TCNVCR Nub | Preserve `TCNVCR`; use stable `NONUB` marker and relative PGD/OGD sizes | `A:TCNVCR:PGDNUB:NONUB`, `A:TCNVCR:PGDNUB:P3D6` |
| Atlantis DFT name length | Apply checker recommendations to 94 unique XTRPFX names (96 IDs); max 55 characters excluding TC suffix | `PFERST` -> `PFERT`; `OFF:R1` -> `OFF1`; LLE references use `NOM` |
| Diffusion JOG | encode starting Z / active-device Z / ending Z | `A:JOG:2/Z5/2` (`2A52`), `A:JOG:2/Z5` (`2A5`) |
| ISO POR reference | series with several DIR / SKEW types -> `NOM` placeholders | `DIFFOGD132-NZ5-NL-L-A:FTIML:NOM:NOM:NOM` |
| Via chains | poly layer `PLY` (was `POL`) | `VGVCHN110-PLY/M0B-12/32-12/9-12X12-A:NOM` |
| WID row marker | row-specific final `A:NOM` becomes `A:WID` (19 Atlantis structures; shared BE-row names remain NOM) | `VTKLV132-TCN/M0C-12/32-15/9-12X15-A:WID` |
| Gate KLV duplicate | DR and SDR twins in separate rows share a NOM target; DR copy gets `_2` per BKM instance convention | row 02 `...A:NOM_2`; row 01 `...A:NOM` |
| Comments | empty Atlantis comments get a suggestion from the new name | `NMOS PFT, ELVT, 165H, Z4 (40nm), 1 leg, CMOS, nest 0X1, iso IXI, single height; POR` |

Unchanged on purpose: layout descriptors (`VTMX`, `NEST`, `STKFTI`, `UDEG`, `DRST`, `GNAC*`,
`nnnnG`, `5T`, `STK`, `SHRD` ...), BE and QnR names.

The XTRCAP rule is scoped by Atlantis library and superflow row. The 36 `TCNVCR` structures in `xtrpfx81z` LLE rows
are normalized with the `TCNVCR` label preserved: `NONUB:6` becomes `A:TCNVCR:PGDNUB:NONUB`, and PGD/OGD Nub sizes
become relative magnitudes. The four ADT `1GANG` structures and YTEST free-text gate-count text are not MLR gate-count
cases. No exact NOVCR/NOVCRNUB or MLR numeric gate-count names matched outside XTRCAP.

## 3. Issues caught

Buildsheet (in `OUTPUT/Buildsheet_Issues.xlsx` and in each library workbook):

| Check | Count | Where | What to do |
|---|---|---|---|
| `FTI_SKEW_FORMAT` | 72 | `x81zs_diffiso01_e/02/03`, `x81zs_gategate05/06/07_b` (PGD CD N1-N4) | `FTICdMisregSkew` has 5 numbers (`0,-0.004,0,0,0,Y`); skew built as named - name kept, comment `Buildsheet needs correction` |
| `FTI_SKEW_NAME` | 8 | `x81zs_diffchn01/02/03` | `FTI:OGD:REG:N4` built -6 nm -> renamed `FTI:OGD:REG:N6` + `_2`; `FTI:OGD:REG:P6` built as PGD REG -> renamed `FTI:PGD:REG:P6`; comment `Needs structure correction ... intended name` |
| `LVS_DATA` | 9 | `x81zs_nlle35` | diffusion in `lvsDeviceData` does not match the name; LVS data to correct (PLY CD mismatches are a known LVS bug, not listed) |

Data / process findings:
- 59 device-tracker rows do not exist in Atlantis; 8 device rows are not `Configured for Tape-in` (`x81zs_ctg03`,
  `x81zs_nm_lle01`, `x81zs_xm_lle01`, `x81zs_nm_ply01/02`, `x81zs_rospft03/11/12`) and were not renamed.
- `x81zs_sram07` is configured for tape-in but not in the superflow (free-text names).
- 34 superflow rows have no owner in the device tracker (32 BE / QnR plus `x81zs_tcn_gate_klv_03`, `x81zs_tcn_klv_01`).
- ISO SDR series verified against the buildsheet: each SDR is the zero point of a 31-32 skew series with all four
  FTI directions; some series miss one point (e.g. `DIFFOGD110-N27-L-A:FTISL:PGD:REG:P4` in `x81zs_diffiso02`).
- 36 of the 123 superflow test configs are not in the P1280 test config document (e.g. `KEY_LOGIC_0.3V_DRN`,
  `CGT_NOCOV_4PT`, `IG_PRE_4PT`, `FKTCN_0.55V`, `BTI`, `HCI`).

## 4. Files to share

| File | For |
|---|---|
| `OUTPUT/Buildsheet_Updates/<LIBRARY>.xlsx` (9) + `_Index.csv` | device owners: renames, comments, open-cal references and issues per library |
| `OUTPUT/Atlantis_Structure_Rename.csv` | full rename table (all libraries) |
| `OUTPUT/Buildsheet_Issues.xlsx` | all buildsheet / Atlantis issues |
| `OUTPUT/StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx` | revised BKM deck |
| `OUTPUT/ROS_Matching_Structures.xlsx` | ROS -> ROSPFT / PFT / MLR / FK -> supporting structures |
| `OUTPUT/Structure_Field_Map.xlsx` | field decoder per family + vocabulary |
| `OUTPUT/DFT_Checker_Rename_Proposals.csv` | checker-confirmed XTRPFX names, approved replacements, length/collision verification |
| `TestPackageBuilder.bat` | test package selection GUI (superflow format export) |

| Library | Owners | Structures to update | Renamed | Comments | Open-cal | Issues |
|---|---|---|---|---|---|---|
| isox81z_x81_lay | Aaryan, Gilad, Shubhajit | 2,180 | 2,180 | 404 | 0 | 80 |
| xtrpfx81z_x81_lay | Amey, Darpan, Han, Jaesung, Jiun, Roberto | 2,016 | 2,016 | 432 | 0 | 9 |
| xtrcapx81z_x81_lay | Amrit, Jason | 882 | 882 | 574 | 602 | 0 |
| rosx81z_x81_lay | Jaladhi | 383 | 383 | 0 | 0 | 0 |
| xtrmiscx81z_x81_lay | Jaladhi | 282 | 282 | 282 | 0 | 0 |
| conx81z_x81_lay | Domingo, Han, Jason | 170 | 170 | 0 | 0 | 0 |
| xtrmatx81z_x81_lay | Amey, Roberto | 170 | 170 | 170 | 0 | 0 |
| widx81z_x81_lay | Jaladhi | 47 | 47 | 12 | 8 | 0 |
| bitsx81z_x81_lay | Divya | 36 | 36 | 0 | 0 | 0 |

## 5. Open questions

- `UDEG:NOTCNCUT` contains a removal inside a layout descriptor; kept as-is.
- The ND gate KLV polarity is written N (`/D` environment); confirm.
- Bitcell diffusion widths that differ from BKM slide 8 (20 names) are kept; confirm against the PDK.
