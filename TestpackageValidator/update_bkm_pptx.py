#!/usr/bin/env python
"""Apply the WW43 structure-name decisions to the BKM deck; every changed token is highlighted.

Writes StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx next to the original (original is not modified).
"""
from __future__ import annotations

import copy
import difflib
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn
from pptx.text.text import _Run
from pptx.util import Emu, Pt

HERE = Path(__file__).resolve().parent
OUT = HERE / 'OUTPUT' / 'StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx'
RED = RGBColor(0xC0, 0x00, 0x00)
_PFT_TAIL = '-LayoutHeight-(Routing)-Additional_TCxx_Count'
_PFT_TAIL_NEW = '-LayoutHeight-NOM|Skew-(Routing)-Additional_TCxx_Count'
_PFT_TAIL_NOROUTE = '-LayoutHeight-NOM|Skew-Additional_TCxx_Count'

# (slide, old text, new text) - paragraph / table-cell substring replacements
EDITS = [
    (9, 'VT-Style-FTI-ISO-LayoutHeight-(Routing)-Style-Additional_TCxx_Count',
        'VT-Style-Stages-FanOut-LayoutHeight-NOM|Skew-(Routing)-Additional_TCxx_Count'),
    (9, 'X81ZROS132-Z5-2L-ULVT-NAN-STG11-FO3-SH-M1_TCxx_y', 'X81ZROS132-Z5-2L-U-NAN-STG11-FO3-SH-NOM-M1_TCxx_y'),
    (9, 'X81ZROS132-Z5-2L-LVT-INV-STG11-FO3-SH-M2-WAC_TCxx_y', 'X81ZROS132-Z5-2L-L-INV-STG11-FO3-SH-NOM-M2-WAC_TCxx_y'),
    (10, _PFT_TAIL, _PFT_TAIL_NEW),
    (10, 'ROSN132-Z3-2L-ELVT-NAN-1X1-IXI-SH-M1-WAC-6X_TCxx_y', 'ROSN132-Z3-2L-E-NAN-1X1-IXI-SH-NOM-M1-WAC-6X_TCxx_y'),
    (10, 'ROSP132-Z3-2L-SVT-AOI-10X10-IXI-SH-M2-STK-16X_TCxx_y', 'ROSP132-Z3-2L-S-AOI-10X10-IXI-SH-M2-STK-16X_TCxx_y'),
    (10, 'Precedes with M – Matching Pair PFT , Structure instance to be added in additional section',
         'Precedes with M – Matching Pair PFT (MNS/MPS); pair given in environment as CMOS/NP or CMOS/PP'),
    (10, 'Precedes with W – Within Die PFTs, Structure instance to be added in additional section',
         "Precedes with W – Within Die PFTs (W prefix replaces the '-WID' suffix; W before M)"),
    (11, _PFT_TAIL, _PFT_TAIL_NOROUTE),
    (11, 'NS132-Z5-1L-ULVT-CMOS-1X1-IXI-SH-M1-NOM_TCxx_y', 'NS132-Z5-1L-U-CMOS-1X1-IXI-SH-NOM_TCxx_y'),
    (11, 'NS110-Z8-1L-ELVT-BCMOS-1X1-IXI-DH_TCxx_y', 'NS110-Z8-1L-E-BCMOS-1X1-IXI-DH-NOM_TCxx_y'),
    (11, 'Precedes with M – Matching Pair PFT, Structure instance to be added in additional section',
         'Precedes with M – Matching Pair PFT (MNS/MPS); pair given in environment as CMOS/NP or CMOS/PP'),
    (11, 'Precedes with W – Within Die PFTs, Structure instance to be added in additional section',
         "Precedes with W – Within Die PFTs (W prefix replaces the '-WID' suffix; W before M)"),
    (11, 'FTI:PGD:CD:P5', 'A:FTI:PGD:CD:P5'),
    (11, 'PLY:OGD:CD:12', 'A:PLY:OGD:CD:12'),
    (11, 'PLY:OGD:PP:45', 'A:PLY:OGD:PP:45'),
    (12, 'DIFF-LEGS-VT-Environment-FTI-ISO-Bitcell-(Routing)-Additional_TCxx_Count',
         'DIFF-LEGS-DUT-Bitcell-FTI-ISO-NH-NOM|Skew-Additional_TCxx_Count'),
    (12, 'SRN192-30-1L-PD-HCC1-SNXSN-SNXSN-M1_TCxx_y', 'SRN192-30-1L-PD-HDC1-SNXSN-SNXSN-NH-NOM_TCxx_y'),
    (12, 'SRP192-30-1L-PU-HCC1-SNXSN-SNXSN-M1_TCxx_y', 'SRP192-26-1L-PU-HDC1-SNXSN-SNXSN-NH-NOM_TCxx_y'),
    (12, 'RFN315-49-1L-RP-TPRF1-SNXSN-SNXSN-M2_TCxx_y', 'RFN315-49-1L-RP-TPRF1-SNXSN-SNXSN-NH-NOM_TCxx_y'),
    (13, 'DIFF-#ADF-VT-Environment-(Routing)-Additonal_TCxx_Count',
         'DIFF-#ADF-VT-Environment-FTI-ISO-LayoutHeight-NOM|Skew-Additional_TCxx_Count'),
    (13, 'NM132-Z5-1ADF-LVT-CMOS-M1-NOM_TCxx_y', 'NM132-Z5-1ADF-L-CMOS-0X0-IXI-SH-NOM_TCxx_y'),
    (13, 'PM110-Z8-2ADF-ELVT-UNI-NOM_TCxx_y', 'PM110-Z8-2ADF-E-UNI-0X0-IXI-SH-NOM_TCxx_y'),
    (14, 'FailmodeCH-PolarityDIFF-VT-(SDR)TSDRSkew_Additional_Detail_Magnitude_TCxx_y',
         'FailmodeCH-PolarityDIFF-NL-VT-Type:Layer|NOM:Direction:SkewType:Magnitude[:Additional]_TCxx_y'),
    (14, 'CTGOGD132-NZ5-ULVTLL-A:TCP:OGD:REG:N7_TCxx_y', 'CTGOGD132-NZ5-NL-UL-A:TCP:OGD:REG:N7_TCxx_y'),
    (14, 'TCNETE165-NZ6-LVT-SDR_TCxx_y', 'TCNETE165-NZ6-NL-L-NOM_TCxx_y'),
    (14, 'DIFFOGD110-PZ3-SVT-B:FTIML:OGD:CD:P2_TCxx_y', 'DIFFOGD110-PZ3-NL-S-B:FTIML:OGD:CD:P2_TCxx_y'),
    (14, 'DIFFOGD110-PZ3-SVT-A:DPR:PGD:CD:N2_TCxx_y', 'DIFFOGD110-PZ3-NL-S-A:DPR:PGD:CD:N2_TCxx_y'),
    (15, _PFT_TAIL, _PFT_TAIL_NOROUTE),
    (15, 'PS165-Z6-1L-LVT-ADT-3X3-IXI-DH-M1-48X-STK_TCxx_y', 'PS165-Z6-1L-L-ADTUNI-3X3-IXI-DH-48X-STK_TCxx_y'),
    (15, 'NS110-Z8-1L-LVT-ADT-3X3-IXI-DH-4X-STK_TCxx_y', 'NS110-Z8-1L-L-ADTUNI-3X3-IXI-DH-4X-STK_TCxx_y'),
    (20, 'ROSN132-Z3-E-2L-INV-1X1-IXI-SH-M1_TCxx_y', 'ROSN132-Z3-2L-E-INV-1X1-IXI-SH-NOM-M1_TCxx_y'),
    (20, 'ROSN132-Z3-E-2L-INV-1X1-IXI-SH-M1-WAC-6X', 'ROSN132-Z3-2L-E-INV-1X1-IXI-SH-NOM-M1-WAC-6X'),
]

# slide 5 VT table, column 1 (symbol used in the name): exact cell text -> new text
TABLE_EDITS = {5: {'Symbol': 'Code in name', 'HVT': 'H', 'SVT': 'S', 'LVT': 'L', 'ULVT': 'U', 'ULVTLL': 'UL',
                   'ELVT': 'E'}}

# (slide, anchor text identifying the text box, new paragraphs appended - fully highlighted)
ADDS = [
    (3, 'Make a uniform delimiter', [
        "'/' only inside the environment field (/D = no diffusion; CMOS/NP, CMOS/PP = matching pair), in "
        'back-end layer pairs / W-S values, and between alternative DIR / SKEW values of a POR reference '
        '(OGD/PGD, CD/REG)',
        'POR structures without a TSDR skew carry A:NOM (lego type first, as in skews) as the first additional '
        'token; POR / REF / SDR are written NOM',
        "Signed magnitudes are written N#/P#/NOM - never '+' / '-' (collides with the '-' delimiter)",
        "Inline '_' is not allowed; '_' only before seat name for a trailing instance index (e.g. _2)",
        "LLE (local layout effects) rows: a structure carries an LLE layout descriptor (NEST, VTMX, UDEG, FTIRST, "
        "...) or, if it has none (A:NOM, A:PXL), the tag ':LLE' (A:NOM:LLE)",
        "Every TSDR skew (ISO, PFT, MLR, KLV, BE) uses TYPE:LAYER:DIR:SKEW:MAG[:extra]; TYPE = lego type ('A' when not "
        'specified); DIR: PXL=PGD, PLY/TCN/FTI=OGD; decimals as D (N1D2); CD relative to nominal (VCR 12 nm, '
        'VCRNUB 44 nm)',
        'Layout-variant descriptors that are not TSDR skews (VTMX, NEST, UDEG, JOG, FTIRST, ...) carry the lego '
        "type as prefix (A:VTMX:CRL:H); a trailing REF / POR inside them is written NOM; signed values '+5' -> P5"]),
    (5, 'Character limit is 64', ['64-character limit is checked on the full name including _TCxx_y',
                                  'Metal-gate VT is written with the single-letter code (table) and follows LEGS/#ADF']),
    (11, 'BCMOS – Balanced NPN', ['/D – no diffusion (replaces CMOS/UNI)', 'CMOS/NP, CMOS/PP – matching pair',
                                  'ADTUNI – two active diffusion (ADT)', 'If nesting not specified: 0X0-IXI']),
    (11, 'Inline Structure Example', ['Matching: MNS132-Z5-1L-L-CMOS/NP-0X1-IXI-SH-NOM_TCxx_y',
                                      'WID: WNS132-Z5-1L-L-CMOS-0X1-IXI-SH-NOM_TCxx_y',
                                      'PLY/TCN CD skews - layer code states TCN and poly-pitch behaviour (POR PP 44): '
                                      'PLYFTCN = PLY skew, TCN fixed, PP = 44+MAG (A:PLYFTCN:OGD:CD:N1 = 43PP); '
                                      'PLYFPP = PLY only, fixed 44PP, TCN nominal; PLYITCN = PLY skew, TCN inverse, '
                                      'fixed 44PP (A:PLYITCN:OGD:CD:N0D4 = PLY -0.4 / TCN +0.4); TCNFPP = TCN only, '
                                      'fixed 44PP. ISO/KLV keep A:TCN:OGD:CD:{MAG}']),
    (13, 'Inline Structure Example', ['Metal routing (M#) is not part of PFT / MLR / BITCELL names (kept only '
                                      'for ROS / ROSPFT where needed); FTI-ISO-LayoutHeight padded 0X0-IXI-SH',
                                      '/D environment = no diffusion: NM132-Z5-1ADF-E-/D-0X0-IXI-SH-NOM_TCxx_y',
                                      'VCRNUB PGD CD relative to single-sided nominal 8.4 nm (110H) / 13.4 nm '
                                      '(132H, 165H); double-sided 16.8 / 26.8 nm']),
    (14, 'Examples:', ["POR layer 'SDR' is written NOM; diffusion always Z code (NZ1, not N27)",
                       'POR reference without its own direction lists all DIR / SKEW of its series: '
                       'DIFFOGD132-NZ5-NL-L-A:FTIML:OGD/PGD:CD/REG:NOM',
                       "Qualifiers (e.g. NDBI) go to a trailing ':' chunk: A:NOM:PGD:ESD:2TRK1PP:NDBI",
                       "No legs in ISO / KLV names: 'NL' (no legs) keeps VT in field 4; SRAM bitcells already carry "
                       "the device type (PD/PG/PU/RP/IR) in field 4 and 'NH' (no height) in field 8 so Nest, Iso and the "
                       "skew line up with the logic families; two diffusions are joined with ':' (SRN192-29:28-1L-PD-...)",
                       'KLV structures use this layout: GATEKLV132-NDZ5-NL-L-NPXLNGLK, VTKLV132-NZ5-NL-L-SDRENC']),
]

NOTES = {5: 'WW43 decision: VT stays after LEGS/#ADF as in the slide templates (supersedes "Move VT after diff '
            'width"); names use the single-letter metal-gate code.'}

NEW_SLIDES = [
    ('Structure Name – Full KTCN (FK) PFT   [NEW WW43]', [
        'TESTNAME_{N|P}S{CH}-{Z}-1L-{VT}-CMOS-1X1-IXI-SH-A:FKTCN:{Dir}:{SkewType}:{Mag}:44PP_TCxx_y',
        '',
        'Follows the CMOS PFT template; full-KTCN identity carried by the skew layer FKTCN; 44 = 44 PP',
        'Old: FKNS44-132-Z5-L-TCN:OGD:CD:N1_TCxx_y',
        'New: NS132-Z5-1L-L-CMOS-1X1-IXI-SH-A:FKTCN:OGD:CD:N1:44PP_TCxx_y',
        'Old: FKPS44-165-Z7-L-TCN:OGD:CD:NOM_TCxx_y',
        'New: PS165-Z7-1L-L-CMOS-1X1-IXI-SH-A:FKTCN:OGD:CD:NOM:44PP_TCxx_y']),
    ('Structure Name – Back-end (Via / Metal)   [NEW WW43]', [
        '{VIA}{TEST}{CH}-{Layers}-{W/S}-{W/S}-{Dim}-A:{VIA}:{Dir}:{Skew}:{N#|P#|NOM}_TCxx_y',
        '',
        'TEST = back-end structure type, joined to the category like the front-end KLV family '
        '(VT_KLV_…_132H -> VTKLV132-…, cf. VTKLV132-NZ5-L-…): KLV kelvin, MV via zipper, VCHN via chain, '
        'ECD electrical CD, SRP serpentine',
        "'-' replaces '_' as field delimiter; CH follows the category; '/' kept for layer pairs and W/S; "
        'skew in TYPE:LAYER:DIR:SKEW:MAG with layer = via/metal and DIR Z (back-end, as DIFFCHN); '
        'structures without skew carry A:NOM',
        'Old: V0_KLV_M0B/M1_12/9_24/20_21X9_CD_-3_TCxx_y  ->  V0KLV-M0B/M1-12/9-24/20-21X9-A:V0:Z:CD:N3_TCxx_y',
        'Old: V1_VCHN_M1/M2B_…_REGOGD_-13_TCxx_y  ->  V1VCHN-M1/M2B-…-A:V1:OGD:REG:N13_TCxx_y',
        'Old: V0_MV_M0B/M1_12/9_24/20_24X12_V0M0_-10_TCxx_y  ->  V0MV-…-A:V0:Z:V0M0:N10_TCxx_y',
        'Old: VG_KLV_Z1N/M0B_12X10_DR_E_110H_TCxx_y  ->  VGKLV110-Z1N/M0B-12X10-DR-E_TCxx_y',
        "DR -> NOM only where confirmed per structure (DR_to_NOM_Decisions.csv)",
        'Uniform field count per {VIA}{TEST}: core fields - skew field (A:... / A:NOM / DR) - one optional '
        "':'-joined additional field (NUB:0:1VCR, WL:DEN, TOP:L); '-WID' -> 'W' head prefix"]),
    ('Structure Name – QnR (draft, decoder pending)   [NEW WW43]', [
        '{QFAM}{CH}-{Z}-{VT}-{ENV}-{NEST}-{ISO}-R{rev}:{MOS code}:{CH variant}:{Geometry}:{Style}:{Topology}-{Location}',
        '',
        'Old: QMN3-NM44-132A-Z521616I-0X0-IXI-SVT-L-2T-CMOS-BP35_TCxx_y',
        'Draft: QMN132-Z5-S-CMOS-0X0-IXI-R3:NM44:A:21616I:L:2T-BP35_TCxx_y',
        "Interim rule: '-' delimiter (QCNP used '_'), single-letter VT, 'ULVT-LL' -> UL",
        'MOS code NM/PM/PC + 44 (PP); A = lego/orientation variant; BP/VP = pad/site index']),
]


def _lego(s: str) -> str:
    """POR NOM carries the lego type: -NOM -> -A:NOM; template NOM|Skew -> Type:NOM|Type:Skew."""
    return re.sub(r'(?<=-)NOM(?=-|_TC|$)', 'A:NOM', s).replace('NOM|Skew', 'Type:NOM|Type:Skew')


EDITS = [(s, o, _lego(n)) for s, o, n in EDITS]
ADDS = [(s, a, [_lego(t) for t in ts]) for s, a, ts in ADDS]


def _change_list() -> list[tuple[int | str, str]]:
    out = [(s, f'{o}  ->  {n}') for s, o, n in EDITS]
    out += [(s, f'VT table symbol {o}  ->  {n}') for s, m in TABLE_EDITS.items() for o, n in m.items()]
    out += [(s, f'Added: {p}') for s, _, ps in ADDS for p in ps]
    out += [(s, f'Speaker note: {t}') for s, t in NOTES.items()]
    out += [('new', t.split('   [')[0]) for t, _ in NEW_SLIDES]
    return out


CHANGES = _change_list()


def _highlight(run: _Run):
    run.font.bold = True
    run.font.color.rgb = RED
    rPr = run._r.get_or_add_rPr()
    for old in rPr.findall(qn('a:highlight')):
        rPr.remove(old)
    hl = rPr.makeelement(qn('a:highlight'), {})
    clr = hl.makeelement(qn('a:srgbClr'), {'val': 'FFFF00'})
    hl.append(clr)
    after = [qn(t) for t in ('a:uLnTx', 'a:uLn', 'a:uFillTx', 'a:uFill', 'a:latin', 'a:ea', 'a:cs', 'a:sym',
                             'a:hlinkClick', 'a:hlinkMouseOver', 'a:rtl', 'a:extLst')]
    nxt = next((c for c in rPr if c.tag in after), None)
    if nxt is not None:
        nxt.addprevious(hl)
    else:
        rPr.append(hl)


def _new_run(template, text: str):
    r = copy.deepcopy(template)
    r.find(qn('a:t')).text = text
    return r


def replace_in_paragraph(para, old: str, new: str) -> bool:
    runs = list(para.runs)
    full = ''.join(r.text for r in runs)
    if old not in full or not runs:
        return False
    new_full = full.replace(old, new, 1)
    owner = []                                    # char index -> run index
    for i, r in enumerate(runs):
        owner += [i] * len(r.text)
    split = lambda s: [t for t in re.split(r'([-_:|/ ()])', s) if t]
    a, b = split(full), split(new_full)
    pos_a = [sum(len(t) for t in a[:k]) for k in range(len(a))]
    new_rs, hl_flags = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            start, end = pos_a[i1], pos_a[i2 - 1] + len(a[i2 - 1])
            k = start
            while k < end:
                ri = owner[k]
                e = k
                while e < end and owner[e] == ri:
                    e += 1
                new_rs.append(_new_run(runs[ri]._r, full[k:e]))
                hl_flags.append(False)
                k = e
        elif j2 > j1:
            ri = owner[min(pos_a[i1] if i1 < len(a) else len(full) - 1, len(full) - 1)]
            new_rs.append(_new_run(runs[ri]._r, ''.join(b[j1:j2])))
            hl_flags.append(True)
    p = para._p
    for r in runs:
        p.remove(r._r)
    end = p.find(qn('a:endParaRPr'))
    for r, flag in zip(new_rs, hl_flags):
        if end is not None:
            end.addprevious(r)
        else:
            p.append(r)
        if flag:
            _highlight(_Run(r, para))
    return True


def _paragraphs(slide):
    for sh in slide.shapes:
        if sh.has_text_frame:
            yield from sh.text_frame.paragraphs
        if getattr(sh, 'has_table', False) and sh.has_table:
            for row in sh.table.rows:
                for cell in row.cells:
                    yield from cell.text_frame.paragraphs


def add_text_slide(prs, title: str, lines: list[str], headings: frozenset = frozenset()):
    """White 'blank-white' slide with the deck's title box; lines in `headings` are plain bold section labels."""
    layout = prs.slides[2].slide_layout
    s = prs.slides.add_slide(layout)
    src_title = next(sh for sh in prs.slides[2].shapes if sh.name == 'TextBox 2')
    el = copy.deepcopy(src_title._element)
    s.shapes._spTree.append(el)
    tb = s.shapes[-1]
    paras = tb.text_frame.paragraphs
    for p in paras[1:]:
        p._p.getparent().remove(p._p)
    runs = paras[0].runs
    for r in runs[1:]:
        paras[0]._p.remove(r._r)
    runs[0].text = title
    runs[0].font.size = Pt(28 if len(title) < 50 else 22)
    body = s.shapes.add_textbox(Emu(376518), Emu(1100000), prs.slide_width - Emu(753036), Emu(5200000))
    body.text_frame.word_wrap = True
    chars = sum(len(l) for l in lines)
    size = 16 if chars < 700 and len(lines) < 10 else 14 if chars < 1200 else 12
    for i, line in enumerate(lines):
        para = body.text_frame.paragraphs[0] if i == 0 else body.text_frame.add_paragraph()
        para.space_after = Pt(6)
        run = para.add_run()
        run.text = line
        run.font.size = Pt(size)
        if line in headings:
            run.font.bold = True
            run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
        elif line:
            _highlight(run)
    return s


def main():
    src = next(HERE.glob("StructureNameNomenclature_WW19*DeviceBKMs.pptx"))
    prs = Presentation(src)
    slides = list(prs.slides)
    misses = []
    for sno, old, new in EDITS:
        if not any(replace_in_paragraph(p, old, new) for p in _paragraphs(slides[sno - 1])):
            misses.append((sno, old))
    for sl in slides:                              # longer template line must not wrap and push the examples down
        for p in _paragraphs(sl):
            if p.text.startswith('TESTNAME_') and len(p.text) > 95:
                for r in p.runs:
                    r.font.size = Pt(12)
            elif len(p.text) > 46 and p.runs and all(r.font.size == Pt(32) for r in p.runs):
                for r in p.runs:
                    r.font.size = Pt(28)
    for sno, mapping in TABLE_EDITS.items():
        table = next(sh.table for sh in slides[sno - 1].shapes if getattr(sh, 'has_table', False) and sh.has_table)
        for row in table.rows:
            para = row.cells[1].text_frame.paragraphs[0]
            txt = ''.join(r.text for r in para.runs).strip()
            if txt in mapping:
                replace_in_paragraph(para, txt, mapping[txt])
    for sno, text in NOTES.items():
        tf = slides[sno - 1].notes_slide.notes_text_frame
        run = tf.add_paragraph().add_run()
        run.text = text
        _highlight(run)
    id_list = prs.slides._sldIdLst
    ids = list(id_list)

    def place(after):
        el = list(id_list)[-1]
        id_list.remove(el)
        after.addnext(el)
        return el

    by_slide: dict[int, list[tuple[str, list[str]]]] = {}
    for sno, anchor, texts in ADDS:
        by_slide.setdefault(sno, []).append((anchor, texts))
    for sno, groups in sorted(by_slide.items()):
        lines = []
        for anchor, texts in groups:
            lines += ([''] if lines else []) + [anchor] + texts
        add_text_slide(prs, f'WW43 additions (to WW19 slide {sno})', lines,
                       frozenset(a for a, _ in groups))
        place(ids[sno - 1])
    tail = ids[15]
    for title, lines in NEW_SLIDES:
        add_text_slide(prs, title, lines)
        tail = place(tail)
    log = [f'Slide {s}: {c}' for s, c in CHANGES]
    for k in range(0, len(log), 12):
        add_text_slide(prs, 'WW43 change log' + (' (cont.)' if k else ''), log[k:k + 12])
        tail = place(tail)
    OUT.parent.mkdir(exist_ok=True)
    prs.save(OUT)
    print(f'{len(EDITS) + sum(map(len, TABLE_EDITS.values()))} replacements, {len(ADDS)} additions, '
          f'{len(NEW_SLIDES)} new slides -> {OUT.name}')
    for m in misses:
        print('NOT FOUND:', m)


if __name__ == '__main__':
    main()
