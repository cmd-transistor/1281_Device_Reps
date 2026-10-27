# 10. Development history

A chronological record of how the ISO content builder grew from a scalar script into a multi-template
library viewer, reconstructed from the working sessions (2026-09-29 → 2026-10-26). Each phase lists the
requests, what was built, and what was learned.

## Phase 1 — Understanding the ISO generator and scalars (2026-09-29)

* De-embedded `ISO_LLM.bat` into `extracted/`: `iso_gui.py`, `claude_iso_generator.py`,
  `claude_iso_gds_generator.py`, decoder/buildsheet/pad tables, layer map and 12 chassis GDS files; studied how
  decoder rows become buildsheets ([ISO generator](04_iso_generator_and_training.md)).
* Wrote/extended [ScalarCalculation.py](../ScalarCalculation.py) for all 2,581 library rows, added the
  `ScalarFormula` column, annotated snapshots, the variants catalog and the PowerPoint summary/catalog
  ([scalar_catalog.py](../scalar_catalog.py)).
* Corrected CTG counting (active TCN from VT vias; one path per gate per track; no SDR override → 72/unit for
  E/F), separate FTI segments, six-element test vectors, structure-name validation (0 anomalies).
* Repository README; first commits to GitHub.

## Phase 2 — Prompt-based designer (2026-10-02 → 10-05)

* Built [iso_structure_ai.py](../iso_structure_ai.py) (`StructureDesigner`) and [iso_ai_api.py](../iso_ai_api.py):
  prompt parsing, reference retrieval with IDF scoring, feedback learning in `iso_ai_memory.json`, and an
  OpenAI-compatible LLM path that emits decoder DSL expanded by `claude_iso_generator`.
* Feedback: retrieval recommended open-cal / wrong-type references and could not create new geometry →
  explicit filters, “reference-only mode” messaging, provider set-up via `ISO_AI_*` env vars; advice to use an
  organization-approved endpoint rather than a shared Ollama.

## Phase 3 — Accurate ISO layouts and an interactive editor (2026-10-06 → 10-09)

* Real-time layer toggles; M0 tracks/cuts, LO/HI terminals and V0 landings verified against the chassis.
* Repeated corrections converged on the **single M0 track index** model: equal spacing, 5/6 tracks per height,
  rails on every multiple (CRL), VCR on NP boundaries numbered on every CRL, LO/HI numbering = M0 numbering,
  CH165 signal-track mapping.
* TRM = PXL; PLY TSDR drawing; FTI CD skews visible with nominal outlines; active PLY/TCN highlighted darker;
  gate/FTI overlap and via-landing fixes; TCP between TCNs.
* **2026-10-07: the tool was repositioned as an 81Z structure library** (viewer + editor) instead of a
  generator; natural-language search.
* Buildsheet tab tabulated (filled vs empty, decoded layers), live editing with recalculation, layout-first
  layout, global view with buffers, V0 in the global region, measurement grid.

## Phase 4 — Productivity features (2026-10-12 → 10-13)

* Test-row builder (add, paste, update, export), measurement keep/erase, distinct LO/HI/PLY colours.
* Clarified the role of the bundled generators (only `parse_chassis` and the PXL/TRM layer are used at run
  time) — see [ISO generator §4.4](04_iso_generator_and_training.md#44-how-this-repository-was-calibrated-against-the-generators).
* KLayout integration (GDS + `.lyp`), BKM structure-name suggestions, VT-flavour display aligned with DevFlav,
  CH/LNR-dependent diffusion width, VCRNUB from the buildsheet, Measure toggle on **K**, design-rule-deck
  advisory checks, buildsheet syntax awareness, rulers/guide tracks outside the plot, FTI ML/SL choice, VT
  placement independent of unit size, exact FTI skew measurement.

## Phase 5 — Atlantis as the data source (2026-10-14 → 10-15)

* SQL route (`atlantis_db.py`), SAP BI attempt, then the **WCF bridge** to the installed Atlantis client
  ([Atlantis integration](06_atlantis_integration.md)); separate Atlantis lookup with rows/structures tabs,
  natural-language search, full-screen start, legend copy.
* XTRPFX (PFT) and XTRCAP (MLR) recognised; templates that do not follow the unit-cell pattern show their
  active area.

## Phase 6 — PFT, MLR and remaining templates (2026-10-15 → 10-16)

* XTRCAP rebuilt as Miller structures from the `MLR_LLM.bat` logic (`?DF/?SF/?GF/?TAP` terminals,
  DiffPatternGlobal projection, legend colours).
* PFT previews from the embedded PFT GDS generator ([pft_layout.py](../pft_layout.py)), KTCN family, active
  window; Atlantis template help text wired into renderers; VIA_CHN, BE_ECD, VIA_KLV and FE_ECD/GNAC mapping
  ([template_layouts.py](../template_layouts.py)).
* M0 lines/cuts emphasised; right-click removal as real buildsheet edits; robust search; Atlantis access
  documented in [ATLANTIS_ACCESS.md](../ATLANTIS_ACCESS.md).
* PTECH module attempted but blocked (no WSL/bash on the machine, install token returned 401).

## Phase 7 — Full structures, design rules and skews (2026-10-19 → 10-20)

* **Unit cell** / **Full structure** navigation; Full structure performance 281 s → 40.7 s (batched patches) →
  8.9 s (raster tiling); grid layers off in Full structure.
* TSDR outsheet rule book, honest “INCOMPLETE” coverage reporting, geometric checks and numbered DR
  highlights ([Design rules](07_design_rules.md)); DRONE investigated (SSO-protected).
* PLY CD/PP (`PP;CD;TCN`) and `poly:TSDRn12A` skews; TSDR tap IDs repeated and aligned on poly/FTI centres.
* Plain-language explanations for all 5,933 rules with a details toggle.
* New catalogs (ROSPFT, PW MLR, PFT PW skew, MOL contact/KLV) → ROSPFT renderer, TCN KLV mapping, VCR via
  chains, FKTCN Kelvin cut merging and GATE2 naming, PFT skew post-processing; offline corpus regression.

## Phase 8 — Structure-name harmonisation (2026-10-21 → 10-23, separate session)

Cross-family naming alignment (colon skew scheme, NOM vs POR, OGD/PGD direction, LLE tags, SRAM bitcell
types, CDPP, KLV ND/NP, LVS device-data checks, change log and summary e-mail). This work produced its own
scripts and reports outside this folder; it informs the BKM name grammar used by **Suggest BKM names**.

## Phase 9 — Documentation (2026-10-26)

This `docs/` set.

## Lessons learned

1. **Calibrate one coordinate model on real GDS first.** Most early layout bugs were different parts of the
   code using different Y scales.
2. **Generators are templates, not truth.** Both GDS generators replay a learned chassis/template; they ignore
   skews and some matrices, so previews post-process them and say so.
3. **Report coverage honestly.** A DR checker that prints “clean” for rules it never evaluated is worse than
   none; list *Not checked* explicitly.
4. **Cache keys must include the render style.** Every drawing change bumps `_r7_` / `_w4_`.
5. **Measure performance before optimising.** The Full-structure cost was matplotlib per-patch overhead, not
   the grid.
6. **Use catalogs as acceptance tests** and keep an offline corpus so regressions are caught without Atlantis.
7. **Keep data read-only and credentials out of code.** Atlantis via Windows auth, DB/LLM settings via
   environment variables, internal row caches git-ignored.
