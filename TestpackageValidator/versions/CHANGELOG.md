# Structure-name validator - version history

Each version is frozen in `versions/vN/` (`scripts/`, `OUTPUT/`, `VALIDATE/`). The top-level `OUTPUT/` and
`VALIDATE/` folders always hold the latest version. Inputs (not copied): `1X281Z_SUPER_FLOW_R2 - FE_26WW43p1_v2.csv`,
`X81Z_EtestStructuresReport.csv`, `1X281Z_X81Z_CurrentAliasing.csv`, Atlantis pull of 2026-10-21.

## v1 - 2026-10-21 (baseline)

Result: 14,235 superflow names, 14,199 auto-fixed, 36 already compliant, 0 manual, 0 duplicates, longest 64 chars.

Naming rules applied:
- Field order CH-DIFF-LEGS-VT; single-letter VT (H/S/L/U/UL/E); 64-character limit incl. `_TCxx_y`.
- POR structures carry `A:NOM` (POR/REF/SDR -> NOM); DR -> NOM only when approved in `DR_to_NOM_Decisions.csv`.
- TSDR skew grammar `TYPE:LAYER:DIR:SKEW:MAG[:extra]` for all families; PLY/TCN vocabulary PLYFTCN / PLYFPP /
  PLYITCN / TCNFPP (ISO/KLV keep `A:TCN`).
- Defaults: PFT/ROSPFT `0X0-IXI`, MLR `0X0-IXI-SH`; metal routing removed from PFT/MLR/BITCELL, kept after the
  skew for ROS/ROSPFT.
- Matching pairs `M{N|P}S` + `CMOS/NP|PP`; WID = `W` head prefix; ADT environment `ADTUNI`; `/D` = no diffusion.
- FK re-expressed in PFT style; LLE-row names with shared descriptors get `:LLE`.
- ISO/KLV: `NL` (no legs) in field 3, polarity with the diffusion, Z codes only; ISO POR direction from the device
  series (`ISO_Skew_Direction_Decisions.csv`).
- BITCELL: DUT (PD/PG/PU/RP/IR) in field 4, no `SR` VT and no height placeholder, skew in field 8; two diffusions
  joined with `:`.
- BE: `-` delimiter, `{VIA}{TEST}{CH}` head, uniform core-skew-extra layout. QnR: delimiter/VT fixes only.

Outputs: validation workbook/CSV and report, revised BKM deck (WW43 rev), field map, ROS matching list,
row-owner summary, LVS name check (GNAC excluded; 63 flagged), Atlantis rename table
(TEMPLATENAME, DATABASE, TESTROWNAME, TESTSTRUCTNAME, TESTSTRUCTID, LIBRARY, OLD_TESTSTRUCTNAME).

## v2 - (in progress)

- Tooling: `rebuild_all.py` regenerates every output in order and lists any file in `OUTPUT/` or `VALIDATE/` that
  was not refreshed (no naming change).
- Matching pairs: the `/NP` and `/PP` environment qualifiers are removed (NP/PP originally meant NMOS pair / PMOS
  pair). Head stays `M{N|P}S`, environment is the normal one: `NP110-Z1-E-1L-CMOS-...` was
  `MNS110-Z1-1L-E-CMOS/NP-...` in v1, now `MNS110-Z1-1L-E-CMOS-0X1-IXI-SH-A:NOM`. Affects 189 N-pair + 189 P-pair
  names (CMOS and UNI); no duplicates created, status counts unchanged.
- ADT and HV height: all ADT and HV structures are double height (`DH`); the exception is the four ADT structures
  with `1GANG` in the name (`NS/PS132-Z5-1L-L-ADTUNI-{10X10|3X3}-IXI-SH-A:1GANG`), which stay `SH`. 156 ADT/HV names
  show `DH` (highlighted, code `ADT_HEIGHT`).
- HV pair rule: `X81ZS_PCL95` POR structures follow their `X81ZS_NCL95` twins and become HV (4 structures x 2 seats):
  `PS165-Z4-L-1L-UNI-10X10-IXI-SH-NOM` -> `PS165-Z4-1L-L-UNI-10X10-IXI-DH-A:HV` (highlighted, `HV_FROM_PAIR`). This
  accepts the six LVS-flagged gang names (SH -> DH); the LVS gang check now has 0 mismatches.
- Bitcell: the `NH` placeholder is replaced by the SRAM bitcell type in the height position: `SRN192-30-1L-PD-HDC1-
  SNXSN-SNXSN-SR-A:NOM`, `RFN315-42-1L-PG-TPRF1-SNXSN-SNXSN-RF-A:NOM` (9 fields again).
- Within die: no `W` head prefix; WID is written in the TSDR skew position, `A:WID` replaces `A:NOM`
  (`WMNS132-Z2-1L-L-CMOS-0X1-IXI-SH-A:NOM` -> `MNS132-Z2-1L-L-CMOS-0X1-IXI-SH-A:WID`; ROSPFT `...-A:WID-M1-WAC-1X`;
  bitcell `...-SR-A:WID`). BE (8 names, `DR` skew): `WID` is the first additional chunk (`...-DR-WID:WL`) - to confirm.
- PLY/TCN skews: skew kind `CDPP` when PLY CD and poly pitch both change: `A:PLY:OGD:CDPP:N1` (PFT `PLY:..:43PP`,
  MLR `PLYCDPP`, ISO `PLYFTCN`); the NOM of such a series (`PLY:..:NOM:44PP`) is `A:PLY:OGD:CDPP:NOM`. CD only at
  fixed 44PP stays `CD`: `A:PLY:OGD:CD:N0D4`, `A:TCN:OGD:CD:P0D4`; `PLYITCN` unchanged. Pitch value never written.
- FK (full KTCN): FK becomes a head prefix and the skew layer is `TCN`:
  `FKNS44-132-Z5-L-TCN:OGD:CD:N1` -> `FKNS132-Z5-1L-L-CMOS-1X1-IXI-SH-A:TCN:OGD:CD:N1` (was `NS132-...-A:FKTCN:...`);
  the POR pitch 44PP is implied, no trailing `:44PP`.
- Lego type on variants: every first additional token starts with the type letter, including variants that had none
  (`A:HV`, `A:STK`, `A:SHRD`, `A:1GANG`, `A:5T`, `A:NODBI:NOIMPLANT`, `A:YDBI:NOIMPLANT`, ...): 197 names changed.
- LLE tag removed: no `:LLE` in names. 12 corrected names are now shared by structures in different rows
  (`NCL02` / `NLLE23`); the collision check only fires when two different names share a row (0 such cases).
- LVS check: PLY identifiers; gangs also checked for WID / HV / 1GANG names and DH in any environment;
  `VALIDATE/LVS_Flagged_Structures.csv` is prefilled `N` (name OK) for the 57 PLY CD / UDEG mismatches, whose LVS data
  is wrong and will be corrected separately.
- New output `OUTPUT/Atlantis_Libraries_To_Edit.csv`: libraries / rows / structures to edit in Atlantis.
- BE and QnR: no changes to back-end (BE) and QnR names - the corrected name is the original name (owner). The
  checks still run; what the rules would change is listed as INFO ("not applied"). Removes the v1 BE hyphen scheme,
  the BE `WID` handling and the QnR delimiter / VT fixes and proposed QnR names; the BE and QnR proposal slides are
  dropped from the deck; `DR_to_NOM_Decisions.csv` now covers KLV only (24 groups). 268 QnR names stay over 64
  characters (INFO). The Atlantis rename table keeps the exact Atlantis name for BE / QnR structures (no case-only renames); the library list drops to 9 libraries (no back-end or QnR library left).
- BE freeze scope: BE-pattern names in device-tracker rows are front-end via chains and are changed (Domingo's
  `x81zs_tcn_ply_chn_01/02`: `VT_VCHN_TCN/M0B_12/32_17/9_12X17_NOM_110H` -> `VTVCHN110-TCN/M0B-12/32-17/9-12X17-A:NOM`);
  BE names in all other rows and all QnR names stay unchanged.
- Device rows not in the superflow: the Atlantis pull now includes every device-tracker row whatever its status, and
  their structures are validated from the Atlantis name (`Source` = `AtlantisOnly`, 223 names: MLR 110, ISO 95,
  ROSPFT 18). 59 tracker rows do not exist in Atlantis. New `OUTPUT/Atlantis_Row_Summary.csv`: result per row
  (renamed, kept BE / QnR, not mapped, device row not found in Atlantis).
- PLY CD + pitch skews carry `PLYPP` as the layer so they cannot be confused with PLY CD only:
  `A:PLY:OGD:CDPP:N2` -> `A:PLYPP:OGD:CDPP:N2`, series NOM `A:PLYPP:OGD:CDPP:NOM`; CD only stays `A:PLY:OGD:CD:..`.
- Row scope: only rows `Configured for Tape-in` that are in the superflow are changed. Rows in another status
  (Generated / Edits in progress / Definition, e.g. `x81zs_ctg03`, `x81zs_nm_lle01`, `x81zs_nm_ply01/02`,
  `x81zs_xm_lle01`, `x81zs_rospft03`) and tape-in rows outside the superflow (`conx81z` `x81zs_rb_mol_chn_01`,
  `x81zs_rb_tcn_klv_01/02`) keep their names; `AtlantisOnly` names are limited to that scope. Row summary gains
  `IN_SUPERFLOW` and lists superflow rows that are not tape-in in Atlantis (none).
- Rename table: a seat name shared by a device row and a BE row (19 structures in `x81zs_wid01/02`) is renamed in the
  device row and kept in the BE row (was unmapped as ambiguous); unmapped now = GNAC only.
- Miller open-cal reference: Miller (MLR) buildsheets reference their open-comp structure in the parameter
  `OPENCAL_STRUCTURE`. The rename table gains `OPENCAL_STRUCTURE` (new name of the referenced structure) and
  `OLD_OPENCAL_STRUCTURE`; pulled read-only into `OUTPUT/Atlantis_81Z_MLR_OpenCal_raw.csv`. 610 references to update
  (xtrcapx81z 602, widx81z 8), all resolved; `Atlantis_Libraries_To_Edit.csv` gains `OpenCal_To_Update`.
- KLV environment after VT: N / P = `UNI`, NP = `CMOS`, ND (no diffusion) = `/D` with polarity N
  (`NDGATEKLV110-Z1-S-DR` -> `GATEKLV110-NZ1-NL-S-/D-A:NOM`, `NPGATEKLV132-Z3-L-SDR` -> `GATEKLV132-NPZ3-NL-L-CMOS-A:NOM`).
- Via chains: poly layer `POL` -> `PLY` (`VGVCHN110-PLY/M0B-12/32-12/9-12X12-A:NOM`).
- DR = design rule = POR: written `A:NOM` (KLV and BE-pattern names in device rows; frozen BE unchanged); the
  per-structure DR approval is dropped, `DR_to_NOM_Decisions.csv` only lists the converted names.
- Layer-removal variants as TSDR `A:{N<layer>/..}:Z:REM:ALL[:extra]` (skew kind `REM`; IMPLANT -> IMP to fit 64):
  `A:NOM:NPXLNGLK` -> `A:NPXL/NGLK:Z:REM:ALL`, `A:NODBI` -> `A:NDBI:Z:REM:ALL`, `A:NODBI:NOIMPLANT` ->
  `A:NDBI/NIMP:Z:REM:ALL`, `A:15750G:NOIMPLANT` -> `A:NIMP:Z:REM:ALL:15750G` (106 names: MLR 50, KLV 32, PFT 24).
  Layout descriptors (VTMX, NEST, STKFTI, UDEG, JOG, TCNVCR ...) unchanged.
- Structure comments: rename table gains `TESTSTRUCTCOMMENT` / `OLD_TESTSTRUCTCOMMENT`; 1,802 empty comments in the
  rename scope get a suggestion built from the corrected name; `Atlantis_Libraries_To_Edit.csv` gains `Comments_To_Add`.
- ISO POR references whose series uses several directions / skew types: `OGD/PGD` and `CD/REG` -> `NOM` placeholder
  (`DIFFOGD132-NZ5-NL-L-A:FTIML:OGD/PGD:CD/REG:NOM` -> `DIFFOGD132-NZ5-NL-L-A:FTIML:NOM:NOM:NOM`).
- Buildsheet issues: new `buildsheet_issues.py` -> `OUTPUT/Buildsheet_Issues.xlsx/.csv` (read-only ISO buildsheet pull
  cached in `OUTPUT/Atlantis_81Z_ISO_Buildsheet_raw.csv`): FTICdMisregSkew with 5 numbers instead of 4 (72, 6 ISO
  rows), FTI skew value / position not matching the name (8, `x81zs_diffchn01/02/03`), LVS data to correct (57),
  structures without a name (36, `x81zs_rospft11/12`).
- Buildsheet update workbooks per library: new `buildsheet_update_files.py` -> `OUTPUT/Buildsheet_Updates/<LIBRARY>.xlsx`
  (Summary, Updates with new name / suggested comment / changed open-cal reference, Issues) + `_Index.csv`; 9 files.
- ROS tree: `ros_matching_list.py` maps every ROS / ROSPFT to its ROSPFT, standard PFT, MLR and FK (full KTCN) POR
  partners and the supporting structures (PFT / MLR / FK skews and variants, ISO and KLV of the same CH / Z / VT),
  with rows, libraries and test configs; new sheet `Tree` and `OUTPUT/ROS_Structure_Tree.csv`. FK POR = TCN CD NOM.
- Field map: new `Vocabulary` sheet (VT, ENV, Z / LNR, TSDR chunk codes incl. `REM`, `PLYPP`, removal layers).
- Test Package Builder GUI: `testpackage_gui.py` (PySide6) on `testpackage_model.py`, launcher
  `TestPackageBuilder.bat`: filter structures (family, CH, LNR, Z, polarity, VT, legs, env, library, owner, row, config),
  ROS branch explorer, add / drop structures, configs and branches with undo, change summary, validation (validator
  status, row status, config vs family, partial configs, ROS POR partners, new configs / rows), test config viewer
  (superflow tests + P1280 test config document, P1278 selectable), export in superflow format (Excel / CSV).
- LLE reference: in LLE rows `REF` is nominal for its own sub-family and is written `A:NOM` /
  `A:{descriptor}:NOM` without `_LLE` (68 Atlantis structures); POR / SDR also stay `A:NOM`. LLE descriptor
  `NPOGD` -> `NPT` to stay within 64 characters.
- Names follow the buildsheet build: DIFFCHN `FTI:OGD:REG:N4` built -6 nm -> `FTI:OGD:REG:N6` + `_2` (an N6 exists in the
  row), `FTI:OGD:REG:P6` built in the PGD REG column -> `FTI:PGD:REG:P6` (8 structures, highlighted `BUILDSHEET_NAME`),
  comment `Needs structure correction ... intended name ...`. DIFFISO / GATEGATE with 5 numbers in `FTICdMisregSkew` keep
  their name, comment `Buildsheet needs correction ...` (72). Buildsheet issues: PLY CD LVS mismatches (known LVS bug)
  and blank names of rows not taped in are no longer listed (89 issues: FTI format 72, FTI name 8, LVS diffusion 9);
  `buildsheet_issues.py` now runs before `atlantis_rename_table.py`.
- XTRCAP-only variants: `NOVCR` / `NOVCRNUB` are VCR-present variants, not layer-removal TSDR skews: `A:NVCR` /
  `A:NVCRNUB` (8 structures in `x81zs_nm04`, `x81zs_pm04`, `x81zs_npm_lle01`). Bare MLR gate counts in
  `x81zs_npm01` mean implant kept: `A:YIMP:Z:KEP:ALL:{gate-count}` (10 structures); `NoImplant` siblings remain
  `A:NIMP:Z:REM:ALL:{gate-count}`. `KEP` means kept, MAG `ALL`. No matching NOVCR/NOVCRNUB or MLR gate-count forms
  were found outside XTRCAP; ADT `1GANG` and YTEST free-text gate counts are unrelated.
- Analog PFT environment: `CMOS_A` -> `ANALOG` in ENV (72 structures in `x81zs_ncl08/09`, `x81zs_pcl08/09`).
- WID rows: Atlantis names with final `A:NOM` in `x81zs_wid01/02` now use `A:WID` (19 structures). This is applied
  per Atlantis row because 11 of these old names are shared with BE rows; the corresponding BE-row names are kept.
- XTRPFX TCNVCR LLE: preserve the `TCNVCR` label in names (36 structure instances in `x81zs_nlle37` /
  `x81zs_plle37`). `NONUB:6` -> `A:TCNVCR:PGDNUB:NONUB` (stable across cell heights);
  `NONUB:SRC_ViaT` -> `A:TCNVCR:NONUB:SRC:VIAT`. Absolute Nub sizes become relative magnitudes while retaining
  PGDNUB/OGDNUB: `PGDNUB:17` -> `A:TCNVCR:PGDNUB:P3D6`, `PGDNUB:15` -> `...:P1D6`,
  `OGDNUB:20` -> `A:TCNVCR:OGDNUB:N24` (44 nm OGD nominal).
- Diffusion jog: all 62 `JOG:nAm[k]` instances use path notation `A:JOG:n/Zm[/k]`: `2A52` -> `2/Z5/2`,
  `2A5` -> `2/Z5`. No collisions in a row; `/` is explicitly permitted inside this JOG path.
- Gate KLV duplicate resolution: ND and NP Gate KLV `DR` / `SDR` twins in rows `x81zs_tcn_gate_klv_02/01`
  normalize to the same NOM target. The DR copies in row 02 receive `A:NOM_2` before `_TCxx_y` (4 seat records,
  `KLV_DUP_INSTANCE`); the row 01 SDR copies keep `A:NOM`.
- Atlantis DFT checker 55-character rule: applied its recommendations to 94 unique XTRPFX names (96 structure IDs,
  184 export entries), all at or below 55 characters excluding TC suffix. Shortenings: `GLKRST`/`PFERST` ->
  `GLKRT`/`PFERT`, FTIRST path removes `L`/`R` markers, FDPR `OFF:Rn` -> `OFFn`, and LLE reference suffixes are
  removed consistently. No proposed-name collisions; no other library appeared in the supplied export.
- Validator WID rows (`WID_ROW`): superflow names in `X81ZS_WIDnn` rows now get the same row-specific `A:WID` as the
  Atlantis rename table / R3 superflow. 460 BE names only in WID rows: `Corrected_Name` final `A:NOM` -> `A:WID`.
  19 names shared with a BE / KLV row keep `Corrected_Name` and list the WID-row name in the new
  `Row_Specific_Names` column (`row=name`). All 479 R3 differences now match the validator; rename table unchanged.
- Cartesian `<TARGET>`: filled with the OPT_LVT leakage target, UL0 (uplift-0) values only, from
  `p1281_idoff_VCC_0p75V_25C_tttt.csv` via `p1281_OPT_LVT_model_mapping.csv` (model -> OPT_LVT model,
  `<MODEL>_<diff nm>_UL0`, A -> nA). No multi-leg scaling (done in official Ieff@opt targets). New columns
  `Target_Source`, `Model`, `OPT_LVT_Model`, `Diff_Width_nm`, `OPT_LVT_UL0_nA`. M1IL_01 R3: 1,350 / 1,350 filled.
- `rebuild_all.py` also runs `carry_packages.py` and the M1IL_01 cartesian (R3 and R2).
