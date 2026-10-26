# X81Z / 1281 Structure Name Nomenclature - Learnings and Decisions

Hand-off document for other agents. It captures the rules, owner decisions, data findings, tools and open questions from the structure-name standardization work (WW43/WW44 2026). Source of truth for the rules is the code constants in `structure_name_validator.py`; this file explains them.

## 1. Goal

Validate and standardize the names of the test structures of the X81Z (1281) test package against the BKM deck `StructureNameNomenclature_WW19'26_DeviceBKMs.pptx`, using:

- the superflow CSV `1X281Z_SUPER_FLOW_R2 - FE_26WW43p1_v2.csv` (about 287k rows, 14,235 unique structure names, 7,004 unique bases without the `_TCxx_y` suffix),
- the Etest structures report `X81Z_EtestStructuresReport.csv` (`$$` delimited, must be loaded manually),
- the aliasing file `1X281Z_X81Z_CurrentAliasing.csv`.

Requirements from the owner: validate names, group them by family, correct them to the BKM, keep delimiter positions/field counts uniform, respect the 64-character limit, and **no manual fixes** (every name gets an automatic corrected name; open questions go to decision files).

## 2. Files and how to run

Folder: `S:\Github Reps\TestpackageValidator` (Windows, pwsh, Python 3.12, pandas 3.x, openpyxl, python-pptx). Scripts and raw inputs stay in the root; **`OUTPUT/`** holds everything generated; **`VALIDATE/`** holds the files the owner reviews or approves (`DR_to_NOM_Decisions.csv`, `ISO_Skew_Direction_Decisions.csv`, `LVS_Flagged_Structures.csv`) - the scripts read the approvals back from `VALIDATE/` on every run.

| File | Purpose |
|---|---|
| `lvs_name_check.py` | Compares structure names with LVS strings (`lvsDeviceData`), see section 15. `python lvs_name_check.py --lvs <file>`. |
| `structure_name_validator.py` | Main validator. `python structure_name_validator.py` writes the outputs below. |
| `update_bkm_pptx.py` | Writes `StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx` (changes in red bold on yellow, additions on separate slides, change log). Original deck untouched. Run after the validator. |
| `StructureName_Validation.xlsx` / `.csv` | All names: `Original_Name`, `Corrected_Name`, `Status`, `Issue_Codes`, `Issues`, `Highlight`, parsed fields (`CAT POL CH DIFF LEGS ADF VT ENV STYLE DUT LIB NEST STG ISO FO HEIGHT ROUTING TEST SKEW SKEW_TYPE SKEW_LAYER SKEW_DIR SKEW_KIND SKEW_MAG INST ADD TC`). Final names = `Corrected_Name`. |
| `StructureName_Validation_Report.md` | Short auto-generated summary (result, themes with counts, open items). |
| `DR_to_NOM_Decisions.csv` | Per-structure DR -> NOM approval (`Approve_Y_N`), shows name collisions. |
| `ISO_Skew_Direction_Decisions.csv` | ISO POR skews that got a direction/skew type from their device series; override with `Approve_Y_N`. |
| `split_structure_names.jsl` | JMP script: splits `Corrected_Name` into one column per field (family specific names). Untested in JMP. |
| `ros_matching_list.py` | Lists ROS/ROSPFT structures with matching PFT/MLR/FK partners (`ROS_Matching_Structures.xlsx`, `_Wide.csv`). |

Pitfalls: the BKM filename contains an apostrophe (use glob); close the CSV and PPTX before rerunning (PermissionError otherwise); after large multi-part edits of the big script re-read it (overlapping replace edits garbled it once); do not reuse the variable name `ch` in `parse_iso` (cell height); `_TCxx_y` suffix is part of the 64-character limit.

Current result: 13,460 names auto-fixed, 775 already compliant, 0 manual, 0 duplicates, longest name 64, all corrected names idempotent (re-validating a corrected name returns itself).

## 3. Universal rules

- **Delimiter** between fields is `-`. `_` is reserved for the trailing seat/instance (`_TC22_1`, optional instance index such as `_2` before the TC part). Inline underscores inside fields are converted (`:` chunks or concatenation).
- **Sub-delimiter** is `:` (ISO-style chunks). `/` is allowed in the environment field (`/D`), in back-end layer pairs and W/S values, between removed layers of a REM skew (`A:NPXL/NGLK:Z:REM:ALL`), and inside a diffusion JOG path (`A:JOG:2/Z5/2`).
- **Allowed characters**: `A-Z 0-9 - _ : / . +` (no lower case, spaces, commas).
- **Length**: at most 64 characters including `_TCxx_y` (7-8 chars). Keep base names about 56 or less.
- **Row names**: at most 23 characters (BKM slide 2).
- **Name = `[HEAD]-[fields...]_TCxx_y`**, head = category + 3-digit cell height (CH 110 / 132 / 165 for logic, 192 / 198 / 228 / 315 for bitcells).
- **Signed magnitudes** are never written with `+`/`-` (they collide with the delimiter): `N#` / `P#` / `NOM`, decimals with `D` (`N1D2` = -1.2).
- **Idempotency**: every correction rule must be stable when applied to its own output (the validator self-checks and reports `NOT_IDEMPOTENT`).
- **Collisions**: different original names must not correct to the same name within a row (reported as `COLLISION`); the same original name used in two rows is allowed. Gate KLV `DR` and `SDR` twins in separate rows are distinct structures despite sharing a NOM target, so the DR copy in `x81zs_tcn_gate_klv_02` receives `_2` before the TC suffix, per the BKM instance-index convention.

## 4. Field vocabulary

### 4.1 Fixed slot grid (logic families)
`0 [M]CAT+CH | 1 DIFF (Z) | 2 LEGS (#L) or #ADF | 3 VT | 4 ENV/STYLE/DUT | 5 NEST/STG | 6 ISO/FO | 7 SH/DH (bitcell: SR/RF) | 8 NOM-or-skew | 9+ ADD`.
ROS/ROSPFT additionally have a routing field `M#`, written **after** the NOM/skew field (`...-SH-A:NOM-M2-WAC`) so that field 9 is the NOM/skew in ROS, ROSPFT, PFT, MLR and FK (alignment decision WW45; variants without a NOM/skew put `M#` first). **Order is CH - DIFF - LEGS - VT** (owner decision: VT after LEGS/#ADF, superseding the old "VT after diffusion width").

### 4.2 Individual fields
- **VT**: single-letter metal-gate codes `H` (HVT), `S` (SVT), `L` (LVT), `U` (ULVT), `UL` (ULVTLL), `E` (ELVT). Split `ULVT-LL` is joined to `UL`. Mixed-VT ROS use `H:L`. VTKLV structures with no VT get `L` (owner: they are LVT; highlighted).
- **DIFF**: `Z` code. Logic library (slide 7), CH -> Z -> diffusion width nm: 110: Z1 27, Z2 31, Z3 35, Z4 40, Z8 72. 132: Z2 31, Z3 35, Z4 40, Z5 46, Z6 49, Z8 72, Z9 86. 165: Z2 31, Z4 40, Z6 52, Z7 62, Z9 86. Numeric widths in names (ISO used 27/31/46...) are converted to Z. ISO/KLV prefix the polarity (`NZ5`, `PZ5`, `NDZ5`, `NPZ5`).
- **LEGS**: `#L` (1L, 2L...). MLR uses `#ADF` (active diffusion fins) in the same position - not comparable to legs.
- **ENV** (PFT/MLR): `CMOS`, `BCMOS`, `LCMOS`, `OCMOS`, `ANALOG`, `UNI`, `ADTUNI`, `/D` (no diffusion, replaces `CMOS/UNI`). Legacy `CMOS_A` is normalized to ENV `ANALOG` (72 PFT structures). Matching pairs keep their normal environment (no `/NP` / `/PP` qualifier). KLV (owner WW45) carries the environment after VT: N / P = `UNI`, NP = `CMOS`, ND (no diffusion) = `/D` with polarity `N` (`GATEKLV132-NZ5-NL-L-/D-A:NOM`).
- **NEST** `#X#` (0X0, 0X1, 9X9, SXS, SNXSN...) and **ISO** `IXI`, `I2XI2`. Defaults when missing: `0X0` / `IXI`. `0X0` and `0X1` are valid. MLR additionally defaults height `SH`.
- **HEIGHT**: `SH` / `DH` (layout height), defaulted to `SH` for MLR.
- **Prefixes on the head**: `M` = matching pair PFT (`MNS` = NMOS pair, `MPS` = PMOS pair; environment unchanged), `FK` = full KTCN. Within die is no longer a head prefix (WID goes in the skew position).
- **WID rows**: if a corrected Atlantis structure in a `x81zs_widNN` row ends in `A:NOM`, the row-specific rename writes `A:WID` instead. Shared structures in their frozen BE row retain the original name; WID PFT structures already marked WID remain unchanged.
- **ROS-only**: STYLE (`INV`, `NAND2`, `NOR2`, `AOI..`), `STG##`, `FO#`.
- **Bitcell**: `SRN192-30-1L-PD-HDC1-SNXSN-SNXSN-SR-A:NOM` = category+CH, diffusion width (numeric kept), legs, DUT (`PU/PG/PD/RP/IR`, in the VT position), library (`HDC1..6`, `HCC1/2`, `TPRF1/2`), nest, iso, SRAM bitcell type (`SR` / `RF`, same as the head category; placeholder in the height position), NOM/skew (field 9). Bitcell diffusion widths that differ from slide 8 are flagged (20 names), **kept by owner decision**.

### 4.3 Metal routing (owner decision WW45)
Metal routing `M#` is **removed** from PFT, MLR and BITCELL names (3,732 names changed, highlighted `ROUTING_REMOVED`). It is kept only for ROS and ROSPFT "where necessary" (present in the original). Earlier rules ("all MLR routed at M2", "PFT default M1") were superseded.

## 5. NOM / POR / DR

- POR structures are written **`A:NOM`** (lego type `A`, then NOM) as the first additional token. `POR` and `SDR` mean POR and become `NOM`. In LLE rows `REF` is nominal for its own LLE sub-family (e.g. 9X9 for FTI roosting; POR is the 0X1 structure) and is written **`A:NOM`** (descriptors: `A:UDEG:NOM`, `A:FDPR:NOM`, `A:NPT:ND:OGD:2PP:NOM`); no `_LLE` suffix is used. `REF` outside LLE rows (ADT `ADT:REF`) stays `NOM`. LLE descriptor `NPOGD` is written `NPT` so the LLE references fit 64 characters. No corrected name contains both POR and NOM or `NOM-NOM`.
- NOM is **not** added to variants: tokens such as `NODBI`, `YDBI`, `NOIMPLANT`, `YIMPLANT`, `HV`, `5T`, `nnnnG`, `GNAC*`, `NOVCR`, `STK`, `SHRD`, `SHRS`, `DEPOP`, `1GANG`, or ADT variants; nor to structures that already have a colon skew/descriptor. They carry the lego type like every other first additional token (v2): `A:HV`, `A:STK`, `A:NODBI:NOIMPLANT`, so field 9 always starts with `TYPE:`.
- Layout-variant descriptors that are not TSDR skews (`VTMX`, `NEST`, `UDEG`, `JOG`, `FTIRST`, `STKFTI`, `MGREG`, `FDPNST`...) carry the lego type as prefix (`A:VTMX:CRL:H`) instead of a separate `A:NOM`.
- Existing NOM in original names (1,396): PFT 1,022, ISO 312, BE 50 (`NOM0`), FK 12. Kept, lego prefix added.
- **DR** = design rule = POR (owner WW45): written `A:NOM` in KLV names and in BE-pattern names of device rows (`GATEKLV110-NPZ1-NL-E-CMOS-A:NOM`, `VGKLV132-Z5N/M0B-12X10-A:NOM-TOP:L`); frozen BE names keep DR. `VALIDATE/DR_to_NOM_Decisions.csv` now only lists the converted names. The DR and SDR versions of `NDGATEKLV132-Z5-L` give the same name but sit in different rows (`x81zs_tcn_gate_klv_01/02`), so they do not collide.
- **Layer-removal variants** are TSDR skews, not NOM (owner WW45): `A:{layers}:Z:REM:ALL[:extra]`, removed layers written `N<layer>`, several joined with `/`, explicitly present layers kept as `Y<layer>`, IMPLANT abbreviated `IMP` (64-char limit): `A:NOM:NPXLNGLK` -> `A:NPXL/NGLK:Z:REM:ALL`, `NGLK` -> `A:NGLK:Z:REM:ALL`, `NODBI` -> `A:NDBI:Z:REM:ALL`, `TAPNODBI` -> `A:TAPNDBI:Z:REM:ALL`, `NODBI:NOIMPLANT` -> `A:NDBI/NIMP:Z:REM:ALL`, `NODBI:YIMPLANT` -> `A:NDBI/YIMP:Z:REM:ALL`, `15750G:NOIMPLANT` -> `A:NIMP:Z:REM:ALL:15750G`. **XTRCAP only:** `NOVCR` / `NOVCRNUB` are VCR-present variants `A:NVCR` / `A:NVCRNUB` (8 structures); bare gate counts with implant kept are `A:YIMP:Z:KEP:ALL:{gate-count}` (10 structures). No-implant gate-count variants retain `A:NIMP:Z:REM:ALL:{gate-count}`. Other layout descriptors such as `VTMX`, `NEST`, `STKFTI`, `UDEG`, `DRST`, `GNAC*`, `nnnnG`, `5T`, `STK`, `SHRD` stay as BKM descriptors.
- `NOM0` in BE (`..._NOM0_110H_VCR_12`) is the nominal counterpart of the `M0VCR` siblings (`..._M0VCR_110H_VCR_12`); it is written `A:NOM`. The `M0VCR` siblings currently get `A:NOM-M0VCR:VCR:12`, which is probably wrong (open question, section 12).

## 6. TSDR skew grammar (all families)

`TYPE:LAYER:DIR:SKEW:MAG[:extra]`, e.g. `A:PLY:OGD:CD:N1`.

- **TYPE** = lego type, single letter, default `A` when absent.
- **LAYER**: PXL, PLY, PLYITCN, TCN, FTI, VCR, VCRNUB, FTIML, DPR, TCP, EC...
- **DIR**: `OGD` (orthogonal to gate direction), `PGD` (parallel to gate direction), `Z` (vertical/back-end). Default when absent: PXL = PGD; PLY, TCN, FTI = OGD (owner WW44). A POR reference whose series uses both directions is written `NOM` (placeholder, owner WW45).
- **SKEW kinds**: `CD`, `CDPP`, `REG`, `REF`, `DEN`, `ESD`, `ENC`, `EC`, `NES`, `REM` (layers removed, MAG `ALL`), `KEP` (layer kept, MAG `ALL`; XTRCAP gate-count cases only).
- **MAG**: `N#`, `P#`, `NOM`; decimal as `D`; a plain `0` becomes `NOM`. Absolute CD values are made relative to a nominal (nm): VCRNUB OGD CD 44 (36 -> N8), VCR PGD CD 12, TCN OGD CD 12. VCRNUB PGD nominal by cell height, single sided: 110 -> 8.4, 132 and 165 -> 13.4 (double sided = 2x).
- Merged forms are split: `PLYCDPP`, `PLYCD`, `TCNCD`. A skew split by `-` (`FTI-CD-OGD:N1`) is re-joined with `:`. A signed value after a colon (`TCNVCR:DRN:-10`) becomes `N10`/`P10` before tokenizing.
- `PLYEC:...` forms (`PLYEC:DKA_CRL:N4`, `PLYECFTI:CRL:0`) -> `A:PLY:OGD:EC:{mag}[:FTI]:{chunks}`.

**TCNVCR / diffusion JOG (WW46):** Preserve the `TCNVCR` label in XTRPFX LLE names. `TCNVCR:NONUB:6` becomes the stable descriptor `A:TCNVCR:PGDNUB:NONUB`, avoiding a height-dependent magnitude and preserving cross-comparison across rows. `NONUB:SRC_ViaT` remains explicit as `A:TCNVCR:NONUB:SRC:VIAT`. Absolute Nub sizes are expressed as relative magnitudes while retaining their source labels: `PGDNUB:17` -> `A:TCNVCR:PGDNUB:P3D6`, `PGDNUB:15` -> `...:P1D6`, and `OGDNUB:20` -> `A:TCNVCR:OGDNUB:N24` from the 44 nm OGD nominal. `JOG:2A52` means Z2 -> active-device diffusion Z5 -> Z2, written `A:JOG:2/Z5/2`; one-sided `JOG:2A5` -> `A:JOG:2/Z5`.

**Atlantis DFT name length (WW43p5):** The DFT checker allows 55 characters excluding TC suffix information. Apply its recommendations only to the listed XTRPFX names: `GLKRST` -> `GLKRT`, `PFERST` -> `PFERT`, FTIRST `LxxCxRxx` paths drop the `L`/`R` markers, and FDPR `OFF:Rn` -> `OFFn`. All LLE nominal references use `NOM` without `_LLE`. The approved changes cover 94 unique row/name targets (96 Atlantis structure IDs); all proposed names meet the limit and have no same-row collisions.

### 6.1 PLY/TCN CD skews (owner WW45, v2)
The layer and skew kind state whether the poly pitch changes with the CD (POR PP = 44 nm). `PLYPP` + `CDPP` = PLY CD and poly pitch both change; `PLY` + `CD` = PLY CD only, so the two series cannot be confused.

| Name part | Meaning | Source forms |
|---|---|---|
| `A:PLYPP:OGD:CDPP:{MAG}` | PLY CD skewed, TCN fixed, so the pitch moves: pitch = 44 + MAG (N1 = 43PP). The NOM point of such a series (`PLY:..:NOM:44PP` next to 42PP..50PP siblings in the row) is `A:PLYPP:OGD:CDPP:NOM` | ISO `PLYFTCN`, PFT `PLY:..:42PP-50PP`, MLR `PLYCDPP` |
| `A:PLY:OGD:CD:{MAG}` | PLY CD only, pitch fixed 44, TCN nominal | PFT `PLY:..:44PP`, MLR `PLYCD` |
| `A:PLYITCN:OGD:CD:{MAG}` | PLY CD and TCN CD skewed inversely, pitch fixed 44 (N0D4: PLY -0.4, TCN +0.4) | PFT `PCD:..:TCD:..`, MLR `PLYiTCN` |
| `A:TCN:OGD:CD:{MAG}` | TCN CD only, pitch fixed 44 | PFT `TCN:..:44PP`, MLR `TCNCD`, ISO/KLV, FK |

The pitch value itself is never written (44 + MAG for CDPP, 44 otherwise); FK no longer carries `:44PP`. Other `..PP` chunks that remain are spacings counted in poly pitches, not the pitch: ISO `1TRK1PP` / `2TRK1PP`, PFT `FDPNST:FXF:1PP`, `FDPR:FXF:IR1PP`, `NPT:..:2PP` (was `NPOGD`), `TAPPROX:OGD:2PP`, `VTMX:IS3PP` / `JOG1PP` / `OGD0PP`. `PLYFSPCR` (ISO) is kept as is.

## 7. Family templates

Family detection is by head pattern. Templates (from `TEMPLATES`):

| Family | Template | Fields |
|---|---|---|
| PFT | `[M]{N\|P}S{CH}-{Z}-{#L}-{VT}-{ENV}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-{ADD}` | 9 |
| MLR | `{N\|P\|X}M{CH}-{Z}-{#ADF}-{VT}-{ENV\|/D}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-{ADD}` (padded to the PFT grid, ADD variants joined with `:`) | 9 |
| FK | `FK{N\|P}S{CH}-{Z}-1L-{VT}-CMOS-1X1-IXI-SH-A:TCN:{DIR}:{SKEW}:{MAG}` (44PP implied) | 9 |
| ROS | `X81ZROS{CH}-{Z}-{#L}-{VT}-{STYLE}-STG{n}-FO{n}-{SH\|DH}-{NOM\|skew}-(M#)-{ADD}` | 11 |
| ROSPFT | `ROS{N\|P}{CH}-{Z}-{#L}-{VT}-{STYLE}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-(M#)-{ADD}` | 12 |
| BITCELL | `{SR\|RF}{N\|P}{CH}-{DIFF}-{#L}-{DUT}-{LIB}-{NEST}-{ISO}-{SR\|RF}-{NOM\|skew}-{ADD}` (DUT PD/PG/PU/RP/IR sits in the VT position; field 8 is the SRAM bitcell type SR / RF, a placeholder for the height position; two diffusions joined with `:`, e.g. `29:28`) | 9 |
| ISO | `{FAILMODE}{CH}-{POL}{Z}-NL-{VT}-{TYPE}:{LAYER\|NOM}:{DIR}:{SKEW}:{MAG}[:extra]` (`NL` = no legs, WW45) | 5 |
| KLV | `{FAILMODE}KLV{CH}-{POL}{Z}-NL-{VT}-{DETAIL\|NOM}` (ISO layout) | 5 |
| BE | unchanged (original name) | - |
| QREL | unchanged (original name) | - |
| BCLA | `{SR\|RF}{CH}-{LIB}BCLA[-OPEN]` | 2-3 |

### 7.1 ISO
- Failmode categories: `DIFFOGD`, `CTG`, `GATEGATE`, `TCNTCN`, `EPIEPI`, ... with CH. Polarity goes with the diffusion (`NZ5`).
- Direction rule for POR-layer (SDR/NOM) structures: CTG OGD; GATEGATE, TCNTCN, EPIEPI PGD; DIFFOGD OGD (`ISO_DIR_RULE`).
- ISO POR written as 3-chunk `TYPE:LAYER:SDR` had no direction: they become the POR reference of their device series and take the DIR/SKEW used by that series in the row, derived from neighbours in the same row (ordinal order). When the series uses several directions or skew types (was `A:FTIML:OGD/PGD:CD/REG:NOM`), each is written `NOM` as a placeholder: `A:FTIML:NOM:NOM:NOM` (owner WW45, 27 names). 18 such structures are in `ISO_Skew_Direction_Decisions.csv`; `Approve_Y_N` Y overrides DIR/SKEW, N blocks the automatic value.
- Skew split over several `-` fields (`DIFFCHN LEN.., A-..`) is folded into one `:` skew; qualifiers (`NDBI`, `YDBI`) go to a trailing chunk.

### 7.2 KLV
Layout like ISO: `GATEKLV132-NDZ5-L-A:NOM`. Polarity was sometimes in the category (`NDGATEKLV132-Z5`) and is moved to the diffusion field. Details are mapped best effort to TSDR (`CD10` -> `CD:10`, `SDRENCN2` -> `NOM:ENC:N2`, `REGPGD` -> `PGD:REG`), KLV resistance direction `Z` unless OGD/PGD given. Categories: GATEKLV, TCNKLV, VGKLV, VTKLV, VCRKLV. **ND meaning is unknown** (treated as a polarity prefix next to N, P, NP, PD) - open question.

### 7.3 Back-end (BE) and QnR (QREL) - not changed (owner, v2)
BE and QnR names keep their original names (`Corrected_Name` = original) unless the row is a device-tracker row: BE-pattern names in device rows (front-end via chains such as `x81zs_tcn_ply_chn_01/02`, `VT_VCHN_TCN/M0B_...`) are changed with the BE hyphen scheme (`VTVCHN110-TCN/M0B-12/32-17/9-12X17-A:NOM`). The validator still parses frozen names and lists what the earlier rules would change as INFO ("not applied"): BE `_` delimiter, CH at the end, signed magnitudes, `-WID` suffix; QnR `_` delimiter (QCNP), full VT symbols, 268 QnR names over 64 characters. No proposed QnR scheme is written any more; DR -> NOM decisions now cover KLV only.

Row scope (owner WW45): only rows `Configured for Tape-in` in Atlantis that are also in the superflow are changed. `atlantis_rename_table.py` still pulls every device-tracker row whatever its status (for the report), but rows in another status (Generated, Edits in progress, Definition) and tape-in rows outside the superflow keep their names. Device-row structures missing from the superflow / Etest report are validated from the Atlantis pull (`Source` = `AtlantisOnly`) only inside that scope. `OUTPUT/Atlantis_Row_Summary.csv` lists every row with its result and an `IN_SUPERFLOW` column (renamed, kept BE / QnR, not mapped, not changed: row not in superflow / not Configured for Tape-in, device row not found in Atlantis).

Miller (MLR) buildsheets reference an open-comp structure in the parameter `OPENCAL_STRUCTURE` (e.g. `NM132-Z3-L-1ADF-CMOS-M2` -> `XM132-Z3-L-1ADF-CMOS-M2`). When the XM structure is renamed, that parameter must be updated too: the rename table lists it in `OPENCAL_STRUCTURE` (new) / `OLD_OPENCAL_STRUCTURE`.

Empty Atlantis structure comments in the rename scope (1,802: MOS_PFT 602, MLR 586, ISO 332, CTM_PFT 282) get a suggested comment built from the corrected name (`TESTSTRUCTCOMMENT`, old value in `OLD_TESTSTRUCTCOMMENT`), e.g. `NMOS PFT, ELVT, 165H, Z4 (40nm), 1 leg, CMOS, nest 0X1, iso IXI, single height; POR` or `PMOS DIFFCHN isolation, LVT, 132H, Z5 (46nm); PXL registration skew PGD -8 (LEN6)`. Existing comments and BE / QnR structures are not touched.

Via chains write the poly layer `PLY` (was `POL`): `VGVCHN132-PLY/M0B-12/32-15/9-12X15-A:NOM`.

Buildsheet checks (`buildsheet_issues.py`, runs before the rename table): ISO `FTICdMisregSkew` holds OGD CD, PGD CD, OGD REG, PGD REG in um (plus an optional flag); the SDR structure of a series is the all-zero point and its skews share every other device parameter (verified for all 27 SDR references). When the buildsheet builds another skew than the name says (DIFFCHN `FTI:OGD:REG:N4` built -6 nm, `FTI:OGD:REG:P6` built in the PGD REG column), the name follows the build (`_2` when that name already exists in the row) and the comment says `Needs structure correction ... intended name <Atlantis name>`. A value-count error (5 numbers, DIFFISO / GATEGATE) keeps the name; the comment says `Buildsheet needs correction`. PLY CD LVS mismatches (known LVS bug) and blank names in rows not taped in are not listed.

Test package builder (`TestPackageBuilder.bat` / `testpackage_gui.py`): works on the superflow lines (one test per line, `X` in the package column). Test configs are looked up in `P1280_Test_Configs Q1 2024 Rev1_DEV.xlsx` (exact name, then without `_DRN` / `_5PT` / `_NOTOP` ... suffixes); 87 of the 123 superflow configs are found there, 83 in P1278.

## 8. Matching pairs, WID, ADT, FK
- Matching pair PFT: head `M{N|P}S`, environment unchanged (old `NP`/`PP` heads = NMOS pair / PMOS pair, rewritten to `MNS`/`MPS`).
- Within die (WID): no `W` prefix; WID is written in the TSDR skew position, `NS132-Z5-1L-U-CMOS-1X1-IXI-SH-A:WID` (`A:WID` replaces `A:NOM`; ROSPFT `...-A:WID-M1-WAC-1X`; BE names are not changed).
- ADT and HV structures are double height (`DH`). ADT: ENV `ADTUNI` (old `UNI` + `ADT:` prefix removed). The exception is the four ADT structures with 1 GANG, which have `1GANG` in the name and stay `SH` (`NS/PS132-Z5-1L-L-ADTUNI-{10X10|3X3}-IXI-SH-A:1GANG`). Changes are highlighted (`ADT_HEIGHT`).
- HV pair rule: P-row POR structures whose N-row twin is HV become HV too (`X81ZS_PCL95` follows `X81ZS_NCL95`): `PS165-Z4-1L-L-UNI-10X10-IXI-DH-A:HV`; highlighted (`HV_FROM_PAIR`). LVS data confirms 2 gangs for these.
- FK (full-KTCN): FK is a head prefix, PFT layout, TCN skew, POR pitch 44PP implied: `FKNS132-Z5-1L-L-CMOS-1X1-IXI-SH-A:TCN:OGD:CD:N1` (old `FKNS44-132-Z5-L-TCN:OGD:CD:N1`; the old `44` was the pitch).

## 9. LLE rows
The `:LLE` tag (WW45) was removed in v2 at the owner's request. Without it, 12 names (`NCL02` / `NLLE23` rows, e.g. `...-M1-NOM` versus `...-POR`) correct to the same name; they sit in different rows, never twice in one row, so the validator only treats names that share a row as collisions.

## 10. Change summary (all rounds)
1. Field order CH-DIFF-LEGS-VT; VT single letter; full VT symbols mapped (slide 5 table updated).
2. Delimiters: `_` to `-`/`:`; `+`/`-` magnitudes to `N/P`; defaults `0X0-IXI-SH`.
3. NOM/POR/SDR/REF unification with lego prefix; DR kept pending approval.
4. Skew grammar `TYPE:LAYER:DIR:SKEW:MAG` everywhere; PLY/TCN vocabulary; ISO direction rule and neighbour-derived direction.
5. ENV rules (`/D`, matching, ADT), `W`/`M` head prefixes, FK -> PFT style.
6. ISO diffusion widths -> Z codes; KLV polarity moved; KLV detail mapped.
7. BE hyphen scheme and uniform layout; MLR variant tokens joined; routing removed from PFT/MLR/BITCELL; `:LLE` tag.
8. Revised BKM deck (`update_bkm_pptx.py`): edits highlighted red on yellow, per-slide additions moved to follow-up slides (in-place appends overflowed), new slides for FK, BE, QnR, change log (12 entries per slide); long template/example lines shrunk to avoid wrapping.

## 11. Discrepancies found in the source names (what was wrong)
- VT before LEGS/#ADF; full VT symbols; `ULVT-LL` split across a delimiter; VTKLV without VT.
- Inline underscores (`CMOS_A`, `NDBI_SDR`, `2TRK_1PP`, `GLK_OGD_DEN`), `_` used as delimiter (QCNP, BE).
- POR written as `POR`/`REF`/`SDR`/`0`; NOM missing on POR structures; NOM without lego type; NOM not first.
- Skews without TYPE or direction, merged forms (`PLYCDPP`), decimals `N1.2`, signed values `-10`, `TCNVCR:DRN:-10`, `PLYEC` variants, `0` magnitudes.
- ISO diffusion as widths (`N27`), ISO skew split over several hyphen fields, KLV polarity in the category.
- Matching pair as `NP`/`PP` head, `-WID` suffix, `CMOS/UNI` together with `/D`, `ADT:` prefix.
- BE: CH at the end, `NNNCH`, `M1_M2C` layer pairs, signed magnitudes, different extras per name.
- MLR routed at M1 or without routing; PFT/MLR/BITCELL routing in some names only.
- Bitcell widths differing from slide 8 (flagged, kept).
- Duplicate fields (ADT VT repeated; 4 names).
- 230 names exceeded 64 characters after adding `A:NOM`; fixed with lego-prefix descriptors and compact PCD form.

## 12. Open questions / not decided
1. **DR meaning** (BE/KLV): is DR the POR reference? If yes set `Approve_Y_N`=Y in `DR_to_NOM_Decisions.csv` (two KLV GATE groups collide with an existing SDR -> NOM name).
2. **ND** polarity prefix meaning (KLV `NDGATEKLV`, 54 names).
3. **`M0VCR` / `NOM0` (BE ECD)**: meaning of `M0VCR` and its skew value; currently `A:NOM-M0VCR:VCR:12`.
4. **QnR decoder** for geometry/style/topology codes (`COG`/`SAF` tokens).
5. **Optional fields still non-uniform**: BE extra field, QREL `COG`/`SAF`, BCLA `-OPEN`. No placeholder was chosen; absent fields are omitted.
6. **ISO direction approvals** (18) and highlighted changes (about 900) need owner confirmation.
7. Bitcell diffusion widths (20) differ from slide 8 - verify against the PDK.
8. Slide 7 Z-versus-width table still has `?` entries to close before tape-in.
9. ROS-PFT matching key (CH, Z, VT, legs, polarity) is an assumption; PFT partners are plain `NS`/`PS` only (no `M…`, `W…`), POR (`A:NOM`) only by default.

## 13. Useful numbers
- Superflow families (names): ISO 3,420; PFT 3,404; BE 3,031; MLR 2,064; ROS 766; QREL 614; ROSPFT 412; KLV 308; BITCELL 120; FK 60; BCLA 36.
- Field counts after correction: PFT 9, MLR 9, FK 9, BITCELL 9, ROS 11, ROSPFT 12, ISO 5, KLV 5; BCLA 2/3; BE and QREL unchanged.
- ROS/ROSPFT partners (POR, regular `NS`/`PS` PFT): PFT 202 of 383 ROS and 76 of 170 ROSPFT; MLR all; FK 13 and 4.
- Etest-report-only names (not corrected here): 4,732.

## 14. Working method that worked
- Parse each name into slots, keep a list of issues with codes (`CODE_DOC` explains each) and a `fixed` flag, build the corrected name from slots, then self-check idempotency, length and collisions.
- Decide with the owner on ambiguous items (ask with fixed options and concrete examples including length consequences), record each answer as a rule constant and in the deck.
- Prefer deriving rules from the data (e.g. which descriptor types occur only in LLE rows) over assumptions; mention assumptions explicitly.
- Always regenerate outputs and check: 0 duplicates, 0 manual, max length 64, 0 non-idempotent.

## 15. LVS string versus structure name (`lvs_name_check.py`)

Source: `LVS_String_generation.pptx`. LVS string = devices separated by `;`, each `Drain,Gate,Source,Bulk,Model+Vt,DiffWidth(nm),PolyCD(nm),Gangs,LegsStacked,Instances,LegsShared`. Active device = all terminals on pads (no `FLOAT` on drain/source); non-active = gate on the pad, a source/drain floating, usually opposite polarity (GNAC and unipolar: same polarity). Floating node = `float`; unused bulk = `FAKE_NMOS_BULK` / `FAKE_PMOS_BULK`. Input table (Excel `Find_LVS_string` tab): `StructureName`, `shortedPinPairs`, `lvsDeviceData`.

Name -> LVS rules used for flagging:
- Model = `n|p` + `hp` (`uhp` for BCMOS balanced) + cell letter (110H `c`, 132H `b`, 165H `a`) + vt (`hvt svt lvt ulvt ulvtll elvt` = H S L U UL E).
- Diffusion width (nm) = slide 7 library from Z and cell height (110: Z1 27, Z2 31, Z3 35, Z4 40, Z8 72; 132: Z2 31, Z3 35, Z4 40, Z5 46, Z6 49, Z8 72, Z9 86; 165: Z2 31, Z4 40, Z6 52, Z7 62, Z9 86).
- Gangs: SH 1; DH 2 (BCMOS DH-balanced 1); checked for POR / WID / HV / 1GANG names (PFT, FK).
- Poly CD 12 nm for most skews; PLY CD skews (`A:PLY:OGD:CD` / `A:PLYPP:OGD:CDPP`, `PLYITCN`) 12 + signed skew; PP skews are not defined in the document and are not checked. PLY CD (48) and UDEG (9) mismatches are LVS data errors, to be corrected separately (owner WW45); the flagged file is prefilled `N` for those.
- Polarity of the active device = name polarity (PFT, ROSPFT, MLR, FK).
- Not checked (rule not in the document): name legs (`#L`) versus stacked / shared / instances; the output has a `Legs_Crosstab` sheet to derive it from the data.
A mismatch flags the name, but the LVS string may be the wrong one. Outputs: `OUTPUT/LVS_Name_Check.xlsx/.csv`, `VALIDATE/LVS_Flagged_Structures.csv`. The LVS data file itself was not yet available when this was written; the checker was tested on the examples of the deck.
