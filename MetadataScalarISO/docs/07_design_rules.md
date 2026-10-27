# 7. Design rules (TSDR)

The design-rule features turn the TSDR outsheet into a searchable rule book, run the subset of checks the
preview geometry supports, highlight findings on the layout and explain every rule in plain language.
They are **advisory**: the outsheet is the X81A opt97 reference (not certified X81Z signoff), and only
geometry visible in the unit-cell preview is checked.

## 7.1 Sources

| Source | Status |
|---|---|
| `p1281_1_opt97_X81A_26WW41.3.xlsx` (git-ignored) | Loaded by [tsdr_rules.py](../tsdr_rules.py): sheets `out_sheet` (5,916 records) and `TC_SDR` (17); 5,933 labelled rules/notes, 167 sections |
| *1281 Design Rule Overview* deck (earlier) | Used for the first pp44 / 12 nm / M0-width / VCRNUB advisory checks in `scalar_snapshot.design_rule_issues` |
| DRONE (`ltd-cit.intel.com/DRONE`) | Behind Azure AD OIDC SSO; not reachable without an API token or saved docs. A pluggable rule source like `RuleBook` is planned once the API shape is known |

## 7.2 Rule book (`tsdr_rules.py`)

* `load_rules()` reads the workbook (cached by mtime/size). Each `Rule` has label, description, drawn value,
  sheet/row reference, section/subsection/origin, SLV/SLL, operator.
* `Rule.numeric_um`, `gating` (Tape-in gating for `TG*`, approval-required for `TC*`, Reference note for
  `_NOTE`), `reference` (`out_sheet!D<row>`), and plain properties `plain`, `plain_gating`, `plain_section`.
* `RuleBook.search(query)` matches all tokens across label, description, section, subsection, origin and the
  plain text; `by_label`, `scope`.
* `source_status()` reports the source and count shown in the Summary.

## 7.3 Checks

`review_rules(row, fields)` returns a `RuleReview(findings, checks, skipped, plain_findings)`:

| Group | Rules |
|---|---|
| VCR rails | `TGVCR_1010/1011` (CD-skew PGD width), `TGVCR_1041` (misreg width), `TGVCR_1045` (PGD displacement) |
| VCRNUB | `TGVCR_6017/6018` (1PP nub OGD width), `TGVCR_6011/6012` (single-sided PGD length), `TGVCR_7045` (OGD displacement) |
| Geometry ([dr_geometry.py](../dr_geometry.py)) | FTI `TGFT_01/03/04/05/06/07`; VG/VT size `TGVG/TGVT_1001…4002`; via landing (`MODEL_LANDING`); distinct-shape overlap (`MODEL_OVERLAP`); poly-to-TCN side space `TGGCPL_99` within `TGGCPL_98`; M0 side space `TGM0_0118/0218` only inside explicit SALELE/LELE + FUT IDs between facing neighbours |

`dr_geometry.unit_shapes(row)` draws `draw_base` into an off-screen figure and classifies rectangles by colour
or gid (FTI/PLY/TCN/M0/VG/VT), converting with `x_pitch_nm(row)` and the cell pitch, and adds `m0:TSDRActive*`
ID regions. `review_geometry` is LRU-cached. Everything not evaluated is listed under **Not checked** so the
report never claims “clean” for unchecked rules (the original bug: “DR checker says everything is clean even
when it is not”).

## 7.4 Plain-language layer (`dr_plain.py`)

All 5,933 rules get a generated one-sentence explanation (≈0.5 s for the whole book):

* GDS numbers `(601;564)` removed; `layer;purpose` → readable names (`PLY_mask;TSDRActiveCDID46p` → “poly
  width-skew zone (46 nm pitch)”, `TSDRptap;id` → “p-tap marker”, `FUT3ID` → “FUT3 marker”).
* Jargon rewritten (`misreg` → shift, `ETE`/`end-to-end` → tip-to-tip, `in OGD` → “in X (across poly)”).
* Templates for width, length, pitch, size, space (incl. tip-to-tip / corner-to-corner), enclosure,
  extension, overlap, “must not interact”, “must be inside”, “only allowed inside”, rectangular, “must have”;
  values converted µm → nm with min/max/exact qualifiers and the measured direction; trailing conditions kept
  as “Condition: …”.
* Long multi-clause rules and notes fall back to a cleaned sentence.
* `section_summary` gives a one-line meaning for every section family (metal, via, poly-pitch skews, TCG,
  VCR/VCRNUB, taps, DPI/GLK/PXL masks, blocking masks, pads, C4, …); `glossary_for` lists the terms used
  (OGD, PGD, CD, misreg, ETE, SDR, TSDR, skew, ID, compliance line, FUT, depop, transition, enclosure, pitch).
* Findings: `plain_check` → “TGFT_01: FTI width in X (across poly) is 4 nm but must be at least 5 nm.”

## 7.5 GUI

* **Design rules** window: table *Rule / Limit / Status / What it means*; detail pane with plain sentence,
  group summary, terms and related rules; **Show details** switches to the raw outsheet text, cell reference,
  SLV/SLL and full related-rule text. **Review structure** lists plain findings and numbered highlights, or the
  full technical report with details on.
* **DR highlights** (Unit view): numbered red (rule) / amber (geometry advisory) boxes, max 200; hover shows the
  finding.
* **DR details** toggle next to it controls plain vs technical text everywhere (Summary pane, edit feedback,
  DR window, highlight hover).

## 7.6 Tests

[test_tsdr_rules.py](../test_tsdr_rules.py) (23 tests): rule metadata/classification, outsheet loading and
search, VCR/VCRNUB checks, FTI/VG/VT/landing/overlap/PLY-TCN/M0 geometry, M0 neighbour logic, poly pitch/ID
skews, `misc_id_rects` repetition and edge moves, plain text for every rule (no GDS numbers, every section
summarised), common sentence forms and plain findings/summaries.

```powershell
python -m unittest test_tsdr_rules -v
```
