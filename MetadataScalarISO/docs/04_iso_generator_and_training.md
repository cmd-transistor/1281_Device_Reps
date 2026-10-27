# 4. ISO generator and training

This page explains every generator involved in the ISO content builder, what data each one “learns” from,
how the pieces were studied and calibrated, and how this repository uses them. In short:

* The **buildsheet generators** (ISO, PFT, MLR) are *rule-based engines tuned on small training tables*; they
  learn constants, keyed indexes and formulas from decoder ↔ buildsheet pairs at load time.
* The **GDS generators** learn geometry from real layouts: ISO uses a *chassis + delta* model over 12 CTG132
  GDS files; PFT uses a *pickled template + delta* model trained on 40 x81zs PFT GDS files.
* This repository’s **scalar engine and previews** were *calibrated by hand* against those GDS files and the
  catalog decks; no neural network was trained, and the assistant that wrote the code was not trained on
  these files.

## 4.1 The `ISO_LLM.bat` bundle

[ISO_LLM.bat](../ISO_LLM.bat) (and the copy in `Supporting Files for Visualization/`) is a self-contained
launcher: it finds a Python with tkinter, base64-decodes the `::DATA::name|payload` lines into a temp folder,
unzips the chassis set, runs `iso_gui.py`, then deletes the folder. The payloads were de-embedded into
[extracted/](../extracted) at the start of the project:

| Payload | Role |
|---|---|
| `iso_gui.py` | Original generator GUI (decoder rows → buildsheet → GDS) |
| `claude_iso_generator.py` | Decoder-DSL → buildsheet generator (“tuned” engine) |
| `ISO_Decoder.csv` | 33 training decoder rows (19 columns) |
| `ISO_Mini.csv` | The 33 matching training buildsheet rows (68 columns) |
| `ISO_PadConfig.csv` | 90 pad/FRT configurations appended to generated rows |
| `claude_iso_gds_generator.py` | Chassis + delta GDS-ASCII generator |
| `ISO_Mini_GDS.csv` | 12 GDS training rows with `LayoutFiles` |
| `svrf_layer_mapping.csv` | layer/datatype ↔ layer-name map |
| `ISO-GDSNamed.zip` | 12 chassis files `x81as_ctg_test_inst1..12.txt` (CTG132 N/NP/P × types A–D, 13–25 MB each) |

The second copy of the launcher carries a newer `claude_iso_gds_generator.py` (253 k vs 160 k base64 chars).

## 4.2 Decoder-DSL buildsheet generator (`claude_iso_generator.py`)

Input: a decoder row — `StructureName, MOS, MG, CELLHEIGHT, DEVWIDTH, UnitCellSizeOgd/Pgd,
ArraySizeOgd/Pgd, FTIPattern, PolyPlugs, TCNPlugs, VcxPattern, M0CutPattern, [LO1/HI1ActiveTracks],
VcrPattern, VcrNubPattern, ResizeShift`. Output: a full ISO buildsheet row.

### What it learns (“training”)

`load_training(ISO_Decoder.csv, ISO_Mini.csv)` pairs the 33 decoder rows with their buildsheet rows and
builds three things:

1. **Constant map** (`_build_constant_map`): every buildsheet column whose value is identical in all training
   rows (the ~40 empty/constant tail columns) is copied verbatim.
2. **Keyed training index** (`_build_indexes`): for `DiffPatternUnit, DiffPatternGlobal, M0CutPatternUnit,
   DummyViaPattern, VcxPatternUnit, LO1ActiveTracks, HI1ActiveTracks` it stores the observed value keyed by
   `(MOS, MG, CELLHEIGHT, TYPE)`; diffusion patterns are *width-templated* (`{W}`) so a new `DEVWIDTH`
   re-widens the trained shape.
3. **Formula fallbacks** (`_generate_*`): rules mined from the training rows for unseen keys, e.g.
   DiffPatternUnit lattice per MOS/TYPE, stride `[3]` for NP-A vs `[7]` otherwise in DiffPatternGlobal,
   M0 cut pattern per (CH, TYPE), VG/VT tracks per (CH, TYPE), LO1/HI1 tracks per (CH, MOS, TYPE).

Pattern columns are never learned: the **DSL expander** (`_expand_pattern`) converts each decoder pattern
axis-aware (X uses `UnitCellSizeOgd`, Y uses `UnitCellSizePgd`), see
[Buildsheet DSL §1.8](01_buildsheet_dsl.md#18-decoder-dsl-generator-input). `ResizeShift` appends
`|d1,d2,d3,d4` to matching vg/vt/vcr/vcrnub segments; blank decoder Vcr cells stay blank on purpose (not
indexed). `validate_decoder_row` blocks bad MOS / MG / CELLHEIGHT.

### Limits

Only one engine (`tuned`), CTG-centric training (33 rows, CTG types A–D), and per-key formulas that encode
the training families. New families or unusual geometries need explicit decoder patterns.

## 4.3 Chassis + delta GDS generator (`claude_iso_gds_generator.py`)

Learns from `ISO_Mini_GDS.csv` (12 rows) and the 12 chassis GDS-ASCII files:

1. **Parse** each chassis into a `LayerInventory` (BOUNDARY polygons per layer/datatype).
2. **Pick** the chassis whose `(CELLHEIGHT, MOS, TYPE)` best matches the target.
3. **Transform** parametric layers through a registry `LAYER_TRANSFORMS`:

| Layer | Transform |
|---|---|
| NDIFF 1/0, PDIFF 8/0 | `diff_pattern_transform` (DEVWIDTH rescale, N/P pattern) |
| FTI 184/0 | `fti_pattern_transform` |
| POLY 2/0 | `poly_plugs_transform` |
| TCN 5/0 | `tcn_plugs_transform` |
| VT 31/0, VG 32/0 | `vcx_pattern_transform` |
| GLK 65/0 | `glk_transform` |
| CTCN 66/0 | `ctcn_transform` |
| LVS dummy gate 81/115 | `lvsdum_transform` |
| TRM 509/0 | `trm_transform` |
| M0 55/0, 120/0, 241/0 | `m0cut_pattern_transform` |
| M1 4/0, V0 56/0 | `v0m1_transform` |
| VT flavour IDs | `vtflavor_transform` |

4. **Emit** the chassis with transformed layers, rewritten STRNAME/header and `# LAYERNAME` annotations.

Because only CTG132 chassis exist, other heights/families are not reliable; streaks at the top in KLayout
are chassis remnants. This repository uses the generator for (a) `parse_chassis`-based checks of the M0 /
V0 / VG / VT conventions, (b) the PXL/TRM layer, and (c) the “Generator” GDS source for CTG132.

## 4.4 How this repository was calibrated against the generators

| Convention | Evidence |
|---|---|
| One M0 track index for vias, V0, cuts and LO/HI | CTG132 chassis: V0 56/0, VG 32/0, VT 31/0, M0 241/0 + 120/0 |
| 5 M0 tracks per height (CH110/132), 6 (CH165); rails every multiple | chassis + lego table supplied by the user |
| `M0CutPatternUnit` X in half poly pitches, `rK` from top track | chassis M0 cuts |
| `LO1Data`: 5 V0 per track, alternate rows shifted by one jumper | chassis V0 grid |
| Plug model (plugs 3/4 → cells 2..4, TCN plugs 4/5 → cells 3..5) | chassis poly/TCN |
| VCRNUB 1 PP × 13.4 nm | PFT generator GDS |
| `DummyViaPattern` X = global M1 track (x = k − 0.5) | chassis V0/M1 |
| M0 colours CLR1 120/0 red, CLR2 241/0 green; rails 27 nm | chassis + `klayout_layer_map.lyp` |

Everything else was inferred from the 2,581-row library (`ISO_Metadata_Input.csv`) and confirmed visually
against the catalog decks ([Catalogs](08_catalogs_and_verification.md)). Study scripts are in
[study/](../study): `list_variants.py`, `variant_table.py`, `measure_unit_y.py`, `column_positions.py`,
`render_crop.py`, `render_all.py`, `extract_pptx.py`, `pptx_to_png.py`, `dump_generators.py`.

## 4.5 Prompt-driven designer (`iso_structure_ai.py`)

`StructureDesigner` has two modes.

### Reference-only mode (no LLM configured)

1. `parse_prompt` extracts a `Spec` (family via regex synonyms, direction, CH, MOS, flavour, type, unit /
   array sizes, device width, open-cal, negative keywords, `Field = value` assignments).
2. `_select` filters library references by family/direction/type/CH/MOS/unit size/open-cal and ranks them with
   `_score`: exact-name +20, CH ±5, MOS ±5, type +4/−2, flavour +1.5, unit size +2, width +1, IDF-weighted
   keyword hits, −3×IDF for avoided keywords, +0.6 for SDR references, **learned token weights** (±6 cap,
   ×0.8) and similarity to previously approved designs.
3. `_build_row` adapts the best reference (assignments, name with `:AI` suffix), then `_evaluate` recomputes
   the scalar and name checks.

### Decoder-generation mode (OpenAI-compatible endpoint)

Set `ISO_AI_ENDPOINT` (HTTPS, or http for localhost), `ISO_AI_MODEL`, optional `ISO_AI_API_KEY`. The LLM
returns a JSON proposal (family, direction, type, CH, MOS, MG, flavour, width, unit/array sizes,
`decoder_patterns`, assumptions, clarifying questions). The designer:

* rejects proposals that conflict with explicit request values or omit required inputs/patterns;
* validates MG against `claude_iso_generator.VALID_MG` and the decoder row with `validate_decoder_row`;
* expands the patterns with `_generate_iso_row_tuned` (constants from the training tables);
* recomputes the scalar and refuses designs with no calculable fail modes.

Accepted LLM designs (last 3 of the same family) are passed back as *preference examples*.

### Feedback learning (`iso_ai_memory.json`)

| Action | Effect |
|---|---|
| `accept` | Stores the row/prompt/tokens in `approved`; for reference designs adds +1 to `token_weights[token][pattern]` |
| `reject` | Excludes the template, re-selects; −1 to the token→pattern weights |
| `revise` | Merges the feedback spec; “another/different” excludes the current template |

Every action is logged in `events` (capped at 2,000). Current memory: 57 learned keywords, 1 approved design,
145 events. The library GUI is a viewer/editor and does not generate new structures; generation is exposed
through [iso_ai_api.py](../iso_ai_api.py):

| Endpoint | Purpose |
|---|---|
| `GET /health` | engine statistics |
| `POST /structures {"prompt"}` | new design |
| `GET /structures/<id>` | current design |
| `POST /structures/<id>/feedback {"action": revise\|accept\|reject, "text"}` | iterate / learn |
| `GET /structures/<id>/buildsheet.csv`, `/layout.png` | outputs |

`ISO_AI_API_TOKEN` enables bearer auth (required for non-loopback binds).

## 4.6 Lessons learned

* Retrieval alone recommended open-cal or wrong-type references; filters for open-cal/standard and type were
  added, and new geometry requires the LLM decoder path — the tool says so instead of silently substituting.
* On a shared multi-user machine, prefer an organization-approved HTTPS inference endpoint with
  authentication over a per-user local model server (e.g. Ollama has no built-in per-user isolation).
* The tool was then repositioned as a **library viewer/editor for 81Z structures** (turn of 2026-10-07);
  generation stays available through the API.
* The decisive modelling choice was a single M0 track index for all vertical references; most layout bugs
  disappeared once that was calibrated on the chassis.
