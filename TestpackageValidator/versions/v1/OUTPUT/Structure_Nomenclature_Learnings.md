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
- **Sub-delimiter** is `:` (ISO-style chunks). `/` is only allowed in the environment field (`/D`, `CMOS/NP`, `CMOS/PP`), in back-end layer pairs and W/S values, and between alternative DIR/SKEW values of a POR reference (`OGD/PGD`, `CD/REG`).
- **Allowed characters**: `A-Z 0-9 - _ : / . +` (no lower case, spaces, commas).
- **Length**: at most 64 characters including `_TCxx_y` (7-8 chars). Keep base names about 56 or less.
- **Row names**: at most 23 characters (BKM slide 2).
- **Name = `[HEAD]-[fields...]_TCxx_y`**, head = category + 3-digit cell height (CH 110 / 132 / 165 for logic, 192 / 198 / 228 / 315 for bitcells).
- **Signed magnitudes** are never written with `+`/`-` (they collide with the delimiter): `N#` / `P#` / `NOM`, decimals with `D` (`N1D2` = -1.2).
- **Idempotency**: every correction rule must be stable when applied to its own output (the validator self-checks and reports `NOT_IDEMPOTENT`).
- **Collisions**: two different original names must not correct to the same name (reported as `COLLISION`). Exception that is not a collision: the same original name used in two rows (27 names, e.g. BE `DR` names in `BE_ECD_01` and `WID02` rows).

## 4. Field vocabulary

### 4.1 Fixed slot grid (logic families)
`0 [W][M]CAT+CH | 1 DIFF (Z) | 2 LEGS (#L) or #ADF | 3 VT | 4 ENV/STYLE/DUT | 5 NEST/STG | 6 ISO/FO | 7 SH/DH | 8 NOM-or-skew | 9+ ADD`.
ROS/ROSPFT additionally have a routing field `M#`, written **after** the NOM/skew field (`...-SH-A:NOM-M2-WAC`) so that field 9 is the NOM/skew in ROS, ROSPFT, PFT, MLR and FK (alignment decision WW45; variants without a NOM/skew put `M#` first). **Order is CH - DIFF - LEGS - VT** (owner decision: VT after LEGS/#ADF, superseding the old "VT after diffusion width").

### 4.2 Individual fields
- **VT**: single-letter metal-gate codes `H` (HVT), `S` (SVT), `L` (LVT), `U` (ULVT), `UL` (ULVTLL), `E` (ELVT). Split `ULVT-LL` is joined to `UL`. Mixed-VT ROS use `H:L`. VTKLV structures with no VT get `L` (owner: they are LVT; highlighted).
- **DIFF**: `Z` code. Logic library (slide 7), CH -> Z -> diffusion width nm: 110: Z1 27, Z2 31, Z3 35, Z4 40, Z8 72. 132: Z2 31, Z3 35, Z4 40, Z5 46, Z6 49, Z8 72, Z9 86. 165: Z2 31, Z4 40, Z6 52, Z7 62, Z9 86. Numeric widths in names (ISO used 27/31/46...) are converted to Z. ISO/KLV prefix the polarity (`NZ5`, `PZ5`, `NDZ5`, `NPZ5`).
- **LEGS**: `#L` (1L, 2L...). MLR uses `#ADF` (active diffusion fins) in the same position - not comparable to legs.
- **ENV** (PFT/MLR): `CMOS`, `BCMOS`, `LCMOS`, `OCMOS`, `UNI`, `ADTUNI`, `/D` (no diffusion, replaces `CMOS/UNI`), `CMOS/NP` and `CMOS/PP` (matching pair; `NP` = N-P, `PP` = P-P).
- **NEST** `#X#` (0X0, 0X1, 9X9, SXS, SNXSN...) and **ISO** `IXI`, `I2XI2`. Defaults when missing: `0X0` / `IXI`. `0X0` and `0X1` are valid. MLR additionally defaults height `SH`.
- **HEIGHT**: `SH` / `DH` (layout height), defaulted to `SH` for MLR.
- **Prefixes on the head**: `M` = matching pair PFT (`MNS`/`MPS`, pair given in ENV), `W` = within die (W before M; replaces the old `-WID` suffix).
- **ROS-only**: STYLE (`INV`, `NAND2`, `NOR2`, `AOI..`), `STG##`, `FO#`.
- **Bitcell**: `SRN192-30-1L-PD-HDC1-SNXSN-SNXSN-A:NOM` = category+CH, diffusion width (numeric kept), legs, DUT (`PU/PG/PD/RP/IR`, in the VT position), library (`HDC1..6`, `HCC1/2`, `TPRF1/2`), nest, iso, NOM/skew (field 8; bitcells have no layout height). Bitcell diffusion widths that differ from slide 8 are flagged (20 names), **kept by owner decision**.

### 4.3 Metal routing (owner decision WW45)
Metal routing `M#` is **removed** from PFT, MLR and BITCELL names (3,732 names changed, highlighted `ROUTING_REMOVED`). It is kept only for ROS and ROSPFT "where necessary" (present in the original). Earlier rules ("all MLR routed at M2", "PFT default M1") were superseded.

## 5. NOM / POR / DR

- POR structures are written **`A:NOM`** (lego type `A`, then NOM) as the first additional token. `POR`, `REF`, `SDR` all mean POR and become `NOM`. No corrected name contains both POR and NOM or `NOM-NOM`.
- NOM is **not** added to variants: tokens such as `NODBI`, `YDBI`, `NOIMPLANT`, `YIMPLANT`, `HV`, `5T`, `nnnnG`, `GNAC*`, `NOVCR`, `STK`, `SHRD`, `SHRS`, `DEPOP`, or ADT variants; nor to structures that already have a colon skew/descriptor.
- Layout-variant descriptors that are not TSDR skews (`VTMX`, `NEST`, `UDEG`, `JOG`, `FTIRST`, `STKFTI`, `MGREG`, `FDPNST`...) carry the lego type as prefix (`A:VTMX:CRL:H`) instead of a separate `A:NOM`.
- Existing NOM in original names (1,396): PFT 1,022, ISO 312, BE 50 (`NOM0`), FK 12. Kept, lego prefix added.
- **DR** (851 superflow names, only BE 803 and KLV 48): meaning not confirmed. Converted to NOM **only** after approval per structure in `DR_to_NOM_Decisions.csv` (261 groups). Evidence against "DR = POR": for `NDGATEKLV132-Z5-L` and `NPGATEKLV132-Z5-L` an `SDR` (POR) variant also exists and would collide with the DR variant if converted. DR currently stays DR.
- `NOM0` in BE (`..._NOM0_110H_VCR_12`) is the nominal counterpart of the `M0VCR` siblings (`..._M0VCR_110H_VCR_12`); it is written `A:NOM`. The `M0VCR` siblings currently get `A:NOM-M0VCR:VCR:12`, which is probably wrong (open question, section 12).

## 6. TSDR skew grammar (all families)

`TYPE:LAYER:DIR:SKEW:MAG[:extra]`, e.g. `A:PLY:OGD:CD:N1`.

- **TYPE** = lego type, single letter, default `A` when absent.
- **LAYER**: PXL, PLY, TCN, FTI, FKTCN, VCR, VCRNUB, FTIML, DPR, TCP, EC...
- **DIR**: `OGD` (orthogonal to gate direction), `PGD` (parallel to gate direction), `Z` (vertical/back-end). Default when absent: PXL = PGD; PLY, TCN, FTI, FKTCN = OGD (owner WW44). Alternatives joined by `/` (`OGD/PGD`).
- **SKEW kinds**: `CD`, `REG`, `REF`, `DEN`, `ESD`, `ENC`, `EC`, `NES`.
- **MAG**: `N#`, `P#`, `NOM`; decimal as `D`; a plain `0` becomes `NOM`. Absolute CD values are made relative to a nominal (nm): VCRNUB OGD CD 44 (36 -> N8), VCR PGD CD 12, TCN OGD CD 12. VCRNUB PGD nominal by cell height, single sided: 110 -> 8.4, 132 and 165 -> 13.4 (double sided = 2x).
- Merged forms are split: `PLYCDPP`, `PLYCD`, `TCNCD`. A skew split by `-` (`FTI-CD-OGD:N1`) is re-joined with `:`. A signed value after a colon (`TCNVCR:DRN:-10`) becomes `N10`/`P10` before tokenizing.
- `PLYEC:...` forms (`PLYEC:DKA_CRL:N4`, `PLYECFTI:CRL:0`) -> `A:PLY:OGD:EC:{mag}[:FTI]:{chunks}`.

### 6.1 PLY/TCN CD skew vocabulary (owner answers WW44)
Layer code states what happens to TCN and the poly pitch (POR PP = 44 nm); pitch is implied to save characters.

| Code | Meaning | Source forms |
|---|---|---|
| `PLYFTCN` | PLY skewed, TCN fixed, pitch = 44 + MAG (N1 = 43PP) | ISO `PLYFTCN`, PFT `PLY:..:42PP-50PP`, MLR `PLYCDPP` |
| `PLYFPP` | PLY only skewed, pitch fixed 44, TCN nominal | PFT `PLY:..:44PP`, MLR `PLYCD` |
| `PLYITCN` | PLY skewed and TCN skewed inversely, pitch fixed 44 (N0D4: PLY -0.4, TCN +0.4) | PFT `PCD:..:TCD:..`, MLR `PLYiTCN` |
| `TCNFPP` | only TCN skewed, pitch fixed 44 | PFT `TCN:..:44PP`, MLR `TCNCD` |
| `TCN` | ISO/KLV TCN skew, unchanged | `A:TCN:OGD:CD:N1` |

The owner proposed longer forms (`PLYCD:N0D4:FIXTCN:44PP`) which exceed 64 characters on the longest PFT/MLR names; the compact vocabulary was chosen. FK keeps `A:FKTCN:OGD:CD:{MAG}:44PP`. `PLYFSPCR` (ISO) is kept as is.

## 7. Family templates

Family detection is by head pattern. Templates (from `TEMPLATES`):

| Family | Template | Fields |
|---|---|---|
| PFT | `[W][M]{N\|P}S{CH}-{Z}-{#L}-{VT}-{ENV}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-{ADD}` | 9 |
| MLR | `[W]{N\|P\|X}M{CH}-{Z}-{#ADF}-{VT}-{ENV\|/D}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-{ADD}` (padded to the PFT grid, ADD variants joined with `:`) | 9 |
| FK | `{N\|P}S{CH}-{Z}-1L-{VT}-CMOS-1X1-IXI-SH-A:FKTCN:{DIR}:{SKEW}:{MAG}:44PP` | 9 |
| ROS | `[W]X81ZROS{CH}-{Z}-{#L}-{VT}-{STYLE}-STG{n}-FO{n}-{SH\|DH}-{NOM\|skew}-(M#)-{ADD}` | 11 |
| ROSPFT | `[W]ROS{N\|P}{CH}-{Z}-{#L}-{VT}-{STYLE}-{NEST}-{ISO}-{SH\|DH}-{NOM\|skew}-(M#)-{ADD}` | 12 |
| BITCELL | `[W]{SR\|RF}{N\|P}{CH}-{DIFF}-{#L}-{DUT}-{LIB}-{NEST}-{ISO}-{NOM\|skew}-{ADD}` (DUT PD/PG/PU/RP/IR sits in the VT position; no layout height, so the skew is field 8; two diffusions joined with `:`, e.g. `29:28`) | 8 |
| ISO | `{FAILMODE}{CH}-{POL}{Z}-NL-{VT}-{TYPE}:{LAYER\|NOM}:{DIR}:{SKEW}:{MAG}[:extra]` (`NL` = no legs, WW45) | 5 |
| KLV | `{FAILMODE}KLV{CH}-{POL}{Z}-NL-{VT}-{DETAIL\|NOM}` (ISO layout) | 5 |
| BE | `{VIA}{TEST}{CH}-{LAYERS}-{W/S}-{W/S}-{DIM}-{SKEW}[-extra]` | 5-7 |
| QREL | draft, see 7.4 | 11-12 |
| BCLA | `{SR\|RF}{CH}-{LIB}BCLA[-OPEN]` | 2-3 |

### 7.1 ISO
- Failmode categories: `DIFFOGD`, `CTG`, `GATEGATE`, `TCNTCN`, `EPIEPI`, ... with CH. Polarity goes with the diffusion (`NZ5`).
- Direction rule for POR-layer (SDR/NOM) structures: CTG OGD; GATEGATE, TCNTCN, EPIEPI PGD; DIFFOGD OGD (`ISO_DIR_RULE`).
- ISO POR written as 3-chunk `TYPE:LAYER:SDR` had no direction: they become the POR reference of their device series and list every DIR/SKEW used by that series in the row (e.g. `A:FTIML:OGD/PGD:CD/REG:NOM`), derived from neighbours in the same row (ordinal order). 18 such structures are in `ISO_Skew_Direction_Decisions.csv`; `Approve_Y_N` Y overrides DIR/SKEW, N blocks the automatic value.
- Skew split over several `-` fields (`DIFFCHN LEN.., A-..`) is folded into one `:` skew; qualifiers (`NDBI`, `YDBI`) go to a trailing chunk.

### 7.2 KLV
Layout like ISO: `GATEKLV132-NDZ5-L-A:NOM`. Polarity was sometimes in the category (`NDGATEKLV132-Z5`) and is moved to the diffusion field. Details are mapped best effort to TSDR (`CD10` -> `CD:10`, `SDRENCN2` -> `NOM:ENC:N2`, `REGPGD` -> `PGD:REG`), KLV resistance direction `Z` unless OGD/PGD given. Categories: GATEKLV, TCNKLV, VGKLV, VTKLV, VCRKLV. **ND meaning is unknown** (treated as a polarity prefix next to N, P, NP, PD) - open question.

### 7.3 Back-end (BE)
- Original names used `_` as delimiter, e.g. `V0_KLV_M0B/M1_12/9_24/20_21X9_CD_-3_110H`. Scheme: `{VIA}{TEST}{CH}` head (TEST such as KLV, MV, VCHN, ECD, SRP joined like the front-end KLV family; CH moved to the head, `NNNCH` -> `NNN`), layers kept with `/` (`M0B/M1`), W/S pairs kept with `/`, then the skew field `A:{VIA}:{DIR}:{KIND}:{MAG}` (DIR `Z` unless REG/CD/ENCL + OGD/PGD is given), `DR`, or `A:NOM` when no skew.
- **Uniform layout per `{VIA}{TEST}` group**: core fields (mode count of the group) - skew field (`A:..` / `A:NOM` / `DR`) - one `:`-joined additional field. Extras (CUT, PWR, DEN, WL, TOP, NUB:0, 1VCR, LCS, RET-XP2, CAT2, C2, TSDR...) move after the skew; `-WID` becomes a `W` head prefix (`WM2SRP165-...`). Examples: `V0KLV-M0C/M1-37/9-24/20-24X30-DR-WL:DEN`, `M0ECD110-B-12-9-30UM-A:NOM`.
- Field count still varies between BE types (5/6/7) and where the optional extra field is present or not.

### 7.4 QnR (QREL) - draft
`Corrected_Name` has only minimal fixes (`_` -> `-`, VT letter). `Proposed_Name` is a best-effort BKM ordering `{QFAM}{CH}-{Z}-{VT}-{ENV}-{NEST}-{ISO}-R{rev}:{MOS}:{VAR}:{GEOM}...-{LOC}` pending a decoder for geometry/style/topology codes. QMN/QMP have an extra token (`COG`, `SAF`): 11 vs 12 fields; flagged INFO only.

## 8. Matching pairs, WID, ADT, FK
- Matching pair PFT: head `M{N|P}S` + ENV `CMOS/NP` or `CMOS/PP` (old `NP`/`PP` heads rewritten).
- WID: `W` prefix on the head (W before M); old `-WID` suffix removed.
- ADT: ENV `ADTUNI` (old `UNI` + `ADT:` prefix removed).
- FK (full-KTCN): re-expressed in PFT style `NS110-Z1-1L-L-CMOS-1X1-IXI-SH-A:FKTCN:OGD:CD:N1:44PP`; no point-count token.

## 9. LLE rows (owner WW45)
LLE = local layout effects. Rows with `LLE` in the row name (e.g. `X81ZS_NLLE23`) contain 2,122 PFT/MLR names. Standard rule: an LLE-row structure either carries an LLE-only layout descriptor (`NEST`, `VTMX`, `UDEG`, `FTIRST`, `STKFTI`, `MGREG`, `FDPNST`, `JOG`... appear only in LLE rows) or, if its descriptor type also occurs outside LLE rows (`A:NOM`: 72 names, `A:PXL`: 344 names), the tag `:LLE` is appended (`...-A:NOM:LLE`, `...-A:PXL:PGD:CD:N1:LLE`). The tag is computed from the data (shared descriptor types), applied to 416 names, fits within 64 characters. This also resolved 12 name collisions (`...M1-NOM` in a CL row versus `...-POR` in an LLE row).

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
- Field counts after correction: PFT 9, MLR 9, FK 9, BITCELL 8, ROS 11, ROSPFT 12, ISO 5, KLV 5; BE 5/6/7; QREL 11/12; BCLA 2/3.
- ROS/ROSPFT partners (POR, regular `NS`/`PS` PFT): PFT 202 of 383 ROS and 76 of 170 ROSPFT; MLR all; FKTCN 13 and 4.
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
- Gangs: SH 1; DH in CMOS 2; BCMOS DH-balanced 1 (PFT, FK).
- Poly CD 12 nm for most skews; PLY CD skews (PLYFTCN/PLYFPP/PLYITCN) 12 + signed skew; PP skews are not defined in the document and are not checked.
- Polarity of the active device = name polarity (PFT, ROSPFT, MLR, FK).
- Not checked (rule not in the document): name legs (`#L`) versus stacked / shared / instances; the output has a `Legs_Crosstab` sheet to derive it from the data.
A mismatch flags the name, but the LVS string may be the wrong one. Outputs: `OUTPUT/LVS_Name_Check.xlsx/.csv`, `VALIDATE/LVS_Flagged_Structures.csv`. The LVS data file itself was not yet available when this was written; the checker was tested on the examples of the deck.
