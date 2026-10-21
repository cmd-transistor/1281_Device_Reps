import unittest
from dataclasses import replace
from unittest.mock import patch

from tsdr_rules import DEFAULT_OUTSHEET, Rule, load_rules, rule_advisories, source_status, review_rules


class RuleMetadataTests(unittest.TestCase):
    def test_nonnumeric_and_classification(self):
        rule = Rule("TGVCR_NOTE_01", "Conditional text", "", "out_sheet", 10, "VCR", "CD", "X81Z")
        self.assertIsNone(rule.numeric_um)
        self.assertEqual(rule.gating, "Reference note")
        self.assertEqual(replace(rule, label="TGVCR_01").gating, "Tape-in gating")
        self.assertEqual(replace(rule, label="TCVCR_01").gating, "Approval required to waive")
        for value in ("nan", "inf", "0.008 / 0.012", ">= 0.008"):
            self.assertIsNone(replace(rule, drawn=value).numeric_um)

    def test_missing_source_is_reported(self):
        with patch("tsdr_rules.load_rules", side_effect=FileNotFoundError("missing")):
            self.assertIn("no workbook-backed checks", source_status())
            self.assertEqual(rule_advisories({}), [])
            report = review_rules({})
            self.assertFalse(report.checks)
            self.assertIn("unavailable", report.skipped[0])
            self.assertIn("INCOMPLETE", report.summary)


@unittest.skipUnless(DEFAULT_OUTSHEET.is_file(), "Supplied internal outsheet is not present")
class OutsheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load_rules()

    def row(self, segment="0,0,top|-0.008,0,0,0"):
        return {"CellHeight": "ch132", "PolyPitch": "pp44", "DFActiveTracks": "1",
                "VcrnubPatternUnit": segment}

    def test_source_cells_and_cache(self):
        self.assertEqual(self.book.process_tag, "1281.ta-97")
        self.assertEqual(self.book.option, "opt97")
        self.assertGreater(len(self.book.rules), 5900)
        self.assertEqual(self.book.by_label("TGVCR_6017").reference, "out_sheet!D4231")
        self.assertAlmostEqual(self.book.by_label("TGVCR_6017").numeric_um, 0.036)
        self.assertEqual(self.book.by_label("GVA_W_01").operator, "=")
        self.assertEqual(self.book.by_label("GVA_W_01").reference, "TC_SDR!B4")
        self.assertIs(load_rules(), self.book)

    def test_search_retains_conditions(self):
        rule = self.book.by_label("TGVCR_6005")
        self.assertIn("1pp", rule.description)
        self.assertIn("TGVCR_6017", rule.description)
        self.assertEqual(self.book.search("TGVCR_6017")[0].label, "TGVCR_6017")
        self.assertTrue(self.book.search("VCRNUB minimum OGD"))

    def test_ogd_boundary_and_field_scope(self):
        self.assertEqual(rule_advisories(self.row()), [])
        row = self.row("0,0,top|-0.009,0,0,0")
        self.assertIn("TGVCR_6017", rule_advisories(row)[0])
        self.assertIn("X81A/opt97", rule_advisories(row)[0])
        self.assertEqual(rule_advisories(row, ("StructureComments",)), [])
        self.assertEqual(rule_advisories(dict(row, PolyPitch="pp42")), [])

    def test_pgd_and_vcr_limits(self):
        self.assertEqual(rule_advisories(self.row("0,0,top|0,0.010,0,0")), [])
        self.assertIn("TGVCR_6012", rule_advisories(self.row("0,0,top|0,0.011,0,0"))[0])
        row = dict(self.row(), VcrPatternUnit="0:2,0|0,0.021,0,0")
        self.assertTrue(any("TGVCR_1011" in issue for issue in rule_advisories(row)))

    def test_validator_integration(self):
        from scalar_snapshot import design_rule_issues

        issues = design_rule_issues(self.row("0,0,top|-0.009,0,0,0"), ("VcrnubPatternUnit",))
        self.assertTrue(any("TGVCR_6017" in issue for issue in issues))

    def test_pft_nominal_dimensions(self):
        row = {"CellHeight": "ch110", "PolyPitch": "pp44", "VcrnubPattern": "45,6,top|0,0.015,0,0"}
        self.assertEqual(rule_advisories(row), [])
        row["VcrnubPattern"] = "45,6,top|0,0.016,0,0"
        self.assertTrue(any("TGVCR_6012" in issue for issue in rule_advisories(row)))

    def test_oversize_vcr_without_skew_suffix(self):
        row = dict(self.row(), VcrPatternUnit="0:2,0", PreviewVcrWidthNm="40")
        self.assertTrue(any("TGVCR_1011" in issue for issue in rule_advisories(row)))

    def test_report_distinguishes_unevaluated_from_no_findings(self):
        report = review_rules(self.row())
        self.assertTrue(report.checks)
        self.assertFalse(report.findings)
        self.assertIn("INCOMPLETE", report.summary)
        report = review_rules(dict(self.row(), PolyPitch="pp42"))
        self.assertFalse(report.checks)
        self.assertIn("pp42", report.skipped[0])
        report = review_rules(self.row(), ("FTICdMisregSkew",))
        self.assertFalse(report.checks)
        self.assertIn("no modeled ISO unit-cell geometry", report.skipped[0])

    def test_invalid_values_are_not_nominal_passes(self):
        for value in ("nan", "inf", "bad", "-1"):
            row = dict(self.row(), VcrPatternUnit="0:2,0", PreviewVcrWidthNm=value)
            self.assertTrue(any("Invalid dimension" in issue for issue in review_rules(row).findings))
        report = review_rules(self.row("0,0,top|nonsense,0,0,0"))
        self.assertTrue(any("Invalid skew" in issue for issue in report.findings))
        report = review_rules(dict(self.row(), CellHeight=""))
        self.assertFalse(report.checks)
        self.assertIn("CellHeight", report.skipped[0])

    def test_preview_nub_width_without_suffix_is_checked(self):
        row = dict(self.row("0,0,top"), PreviewVcrnubWidthNm="60")
        self.assertTrue(any("TGVCR_6018" in issue for issue in rule_advisories(row)))

    def test_layer_geometry_and_spacing_findings(self):
        from dr_geometry import Shape, check_shapes

        shapes = (Shape("FTI", (0, 0, 20, 80)), Shape("PLY", (44, 0, 64, 80)),
                  Shape("TCN", (70, 0, 82, 80)), Shape("VG", (45, 10, 69, 23)),
                  Shape("M0", (0, 10, 100, 25)), Shape("M0", (0, 20, 100, 35)))
        report = check_shapes(shapes, self.book, {})
        labels = {finding.label for finding in report.findings}
        self.assertTrue({"TGFT_03", "TGGCPL_99", "TGVG_2001", "MODEL_OVERLAP"} <= labels)
        self.assertTrue(all(finding.bounds for finding in report.findings))

    def test_fti_ete_and_via_landing(self):
        from dr_geometry import Shape, check_shapes

        shapes = (Shape("FTI", (0, 0, 12, 50)), Shape("FTI", (0, 70, 12, 120)),
                  Shape("VT", (100, 0, 112, 15)))
        report = check_shapes(shapes, self.book, {})
        self.assertTrue(any(finding.label == "TGFT_05" for finding in report.findings))
        self.assertTrue(any(finding.label == "MODEL_LANDING" for finding in report.findings))

    def test_m0_spacing_requires_explicit_ids(self):
        from dr_geometry import Shape, check_shapes

        lines = (Shape("M0", (0, 0, 100, 12)), Shape("M0", (0, 16, 100, 28)))
        report = check_shapes(lines, self.book, {})
        self.assertFalse(any(finding.label == "TGM0_0118" for finding in report.findings))
        regions = (Shape("m0:tsdractivesalele", (-10, -10, 110, 40)),
                   Shape("m0:tsdractivefutid", (-1, -1, 101, 13)),
                   Shape("m0:tsdractivefut3id", (-1, 15, 101, 29)))
        report = check_shapes(lines + regions, self.book, {})
        self.assertTrue(any(finding.label == "TGM0_0118" for finding in report.findings))

    def test_renderer_geometry_and_report_integration(self):
        from dr_geometry import unit_shapes

        row = {"CellHeight": "ch132", "PolyPitch": "pp44", "UnitCellSizeOgd": "4", "UnitCellSizePgd": "1",
               "FTITracksUnit": "0,0:1", "PreviewPlyWidthNm": "20", "VcxPatternUnit": "vg,1,1"}
        shapes = unit_shapes(row)
        self.assertTrue(any(shape.layer == "PLY" and abs(shape.bounds[2] - shape.bounds[0] - 20) < 1e-5 for shape in shapes))
        report = review_rules(row, ("PreviewPlyWidthNm",))
        self.assertTrue(any("TGGCPL_99" in finding for finding in report.findings))

    def test_m0_fixed_space_ignores_non_neighbors(self):
        from dr_geometry import Shape, check_shapes

        shapes = tuple(Shape("M0", (0, y, 100, y + 12)) for y in (0, 21, 42))
        ids = (Shape("m0:tsdractivesalele", (-10, -10, 110, 70)),
               Shape("m0:tsdractivefutid", (-1, -1, 101, 55)),
               Shape("m0:tsdractivefut3id", (-1, -1, 101, 55)))
        result = check_shapes(shapes + ids, self.book, {})
        self.assertFalse(any(finding.label == "TGM0_0118" for finding in result.findings))


class PolySkewAndMiscIdTests(unittest.TestCase):
    def test_pp_format_sets_pitch_and_cd(self):
        from scalar_snapshot import poly_pitch_skew, poly_skew_geometry, x_pitch_nm

        row = {"PolyCdSkew": "0.043;0.011;0.012"}
        self.assertEqual(poly_pitch_skew(row), (43.0, 11.0, 12.0))
        self.assertEqual(x_pitch_nm(row), 43.0)
        self.assertEqual(poly_skew_geometry(row), [("OGD", "CD", -1.0)])

    def test_id_layer_poly_skew(self):
        from scalar_snapshot import poly_skew_geometry, x_pitch_nm

        row = {"IdLayerBasedSkew": "poly:TSDRn12A"}
        self.assertEqual(poly_skew_geometry(row), [("OGD", "CD", -1.2)])
        self.assertEqual(x_pitch_nm(row), 44.0)

    def test_misc_id_repetition_and_edges(self):
        from scalar_snapshot import misc_id_rects

        rects = misc_id_rects("TSDRptap,id,10:12,3:4|1,3,0,0.132", 20, 30, 44.0, 66.0)
        self.assertEqual(len(rects), 3)
        self.assertEqual(rects[0][0], 10)
        self.assertEqual(rects[0][2], 12)
        self.assertEqual([round(r[1] - rects[0][1], 6) for r in rects], [0, 2, 4])
        moved = misc_id_rects("TSDRptap,id,10:12,3:4|-0.0044,0.0044,0,0", 20, 30, 44.0, 66.0)
        self.assertEqual(len(moved), 1)
        self.assertAlmostEqual(moved[0][0], 9.9)
        self.assertAlmostEqual(moved[0][2], 12.1)


class PlainLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = load_rules()

    def test_every_rule_has_plain_text_and_section_summary(self):
        for rule in self.book.rules:
            self.assertTrue(rule.plain)
            self.assertNotRegex(rule.plain, r"\(\d+;\d+\)")
            self.assertTrue(rule.plain_section)

    def test_common_forms(self):
        from dr_plain import simplify_text

        self.assertEqual(simplify_text("TGFT_01", "FTI width, minimum [OGD]", "0.005"),
                         "FTI must be at least 5 nm wide, measured across the poly lines (X).")
        self.assertEqual(simplify_text("TGFT_05", "FTI end-to-end space, minimum [PGD]", "0.04"),
                         "Tip-to-tip gap between FTI shapes must be at least 40 nm, measured along the poly lines (Y).")
        self.assertIn("must not touch or overlap FTI",
                      simplify_text("X", "SPF_mask;drawing (791;0) must not interact with {fti;drawing (184;0)}"))
        self.assertIn("width-skew zone (44.6 nm pitch)",
                      simplify_text("X", "PGD edges of PLY_mask;TSDRActiveCDID446Ap (601;712) must be centered on tcn tracks."))

    def test_findings_have_plain_wording(self):
        row = {"CellHeight": "132", "PolyPitch": "pp44", "VcrPatternUnit": "1:2,3|0,-20", "PreviewVcrWidthNm": "12"}
        report = review_rules(row, ("VcrPatternUnit",))
        self.assertEqual(len(report.findings), len(report.plain_findings))
        self.assertTrue(report.findings)
        self.assertTrue(all("must be" in text for text in report.plain_findings if text.startswith("TGVCR")))
        self.assertTrue(report.display_summary().startswith("Design-rule check (partial)"))
        self.assertTrue(report.display_summary(True).startswith("DR review INCOMPLETE"))


if __name__ == "__main__":
    unittest.main()