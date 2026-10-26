#!/usr/bin/env python
"""Validate X81Z test-structure names against the WW19'26 Device Structure Name BKM.

Reads every Test Structure Alias from the superflow, cross-checks it against the
Etest structures report and the row-aliasing file, parses each name into its BKM
fields, flags deviations and emits a BKM-ordered corrected name where possible.

Usage:
    python structure_name_validator.py [--superflow F] [--etest F] [--alias F] [--out F.xlsx]
"""
from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / 'OUTPUT'        # everything generated
VALIDATE_DIR = HERE / 'VALIDATE'    # files the owner reviews / approves (decision files, flagged lists)
DEFAULT_SUPERFLOW = HERE / '1X281Z_SUPER_FLOW_R2 - FE_26WW43p1_v2.csv'
DEFAULT_ETEST = HERE / 'X81Z_EtestStructuresReport.csv'
DEFAULT_ALIAS = HERE / '1X281Z_X81Z_CurrentAliasing.csv'
DEFAULT_OUT = OUTPUT_DIR / 'StructureName_Validation.xlsx'
DR_FILE = VALIDATE_DIR / 'DR_to_NOM_Decisions.csv'
ISO_DIR_FILE = VALIDATE_DIR / 'ISO_Skew_Direction_Decisions.csv'
REPORT_FILE = OUTPUT_DIR / 'StructureName_Validation_Report.md'

# --------------------------------------------------------------------------------------
# BKM constants (StructureNameNomenclature_WW19'26_DeviceBKMs.pptx)
# --------------------------------------------------------------------------------------
MAX_NAME_LEN = 64            # slide 5: structure name + TC information
MAX_ROW_LEN = 23             # slide 2: row name (without _TCxx_y)
ALLOWED_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:/.+')
TC_SUFFIX = re.compile(r'^(?P<base>.+?)(?P<tc>_TC\d+[A-Z]?_\d+)$')

VT_FULL_TO_CODE = {'HVT': 'H', 'SVT': 'S', 'LVT': 'L', 'ULVT': 'U', 'ULVTLL': 'UL', 'ELVT': 'E'}
VT_CODES = {'H', 'S', 'L', 'U', 'UL', 'E'}
VT_ANY = VT_CODES | set(VT_FULL_TO_CODE) | {'SRAMVT'}

# slide 7, x81Z logic library: CH -> {Z: diffusion width}
LOGIC_LIB = {
    110: {'Z1': 27, 'Z2': 31, 'Z3': 35, 'Z4': 40, 'Z8': 72},
    132: {'Z2': 31, 'Z3': 35, 'Z4': 40, 'Z5': 46, 'Z6': 49, 'Z8': 72, 'Z9': 86},
    165: {'Z2': 31, 'Z4': 40, 'Z6': 52, 'Z7': 62, 'Z9': 86},
}
LOGIC_WIDTH_TO_Z = {ch: {w: z for z, w in d.items()} for ch, d in LOGIC_LIB.items()}

# slide 8, x81Z SRAM / TPRF library: lib -> (CH, {DUT: diffusion width})
_HDC15 = (192, {'PU': 26, 'PG': 28, 'PD': 30})
_HDC46 = (192, {'PU': 27, 'PG': 28, 'PD': 29})
_HCC = (228, {'PU': 27, 'PG': 39, 'PD': 47})
_TPRF = (315, {'RP': 49, 'PU': 27, 'PG': 46, 'PD': 47})
BITCELL_LIB = {
    'HDC1': _HDC15, 'HDC5': _HDC15, 'HDC3': (192, {'PU': 26, 'PG': 28, 'PD': 30}),
    'HDC4': _HDC46, 'HDC6': _HDC46, 'HDC2': (198, {'PU': 27, 'PG': 29, 'PD': 32}),
    'HCC1': _HCC, 'HCC2': _HCC, 'TPRF1': _TPRF, 'TPRF2': _TPRF,
}

# Canonical field order after the CATEGORY+CH head (slides 9-13, 15)
SLOT_ORDER = {
    'ROS':     ['DIFF', 'LEGS', 'VT', 'STYLE', 'STG', 'FO', 'HEIGHT', 'ROUTING'],
    'ROSPFT':  ['DIFF', 'LEGS', 'VT', 'STYLE', 'NEST', 'ISO', 'HEIGHT', 'ROUTING'],
    'PFT':     ['DIFF', 'LEGS', 'VT', 'ENV', 'NEST', 'ISO', 'HEIGHT', 'ROUTING'],
    'BITCELL': ['DIFF', 'LEGS', 'DUT', 'LIB', 'NEST', 'ISO', 'ROUTING'],   # DUT sits in the VT position
    'MLR':     ['DIFF', 'ADF', 'VT', 'ENV', 'NEST', 'ISO', 'HEIGHT', 'ROUTING'],   # padded to PFT grid
}
OPTIONAL_SLOTS = {'ROUTING'}
ROUTING_REMOVED = {'PFT', 'MLR', 'BITCELL'}
ROUTING_AFTER_SKEW = {'ROS', 'ROSPFT'}      # keeps NOM/skew at field 9 like PFT/MLR/FK
# Values filled in when a field is absent (user decisions WW43)
SLOT_DEFAULTS = {
    'PFT':    {'NEST': '0X0', 'ISO': 'IXI'},
    'ROSPFT': {'NEST': '0X0', 'ISO': 'IXI'},
    'MLR':    {'NEST': '0X0', 'ISO': 'IXI', 'HEIGHT': 'SH'},
}
NOM_FAMILIES = {'ROS', 'ROSPFT', 'PFT', 'BITCELL', 'MLR'}
DEFAULT_VT = {'VTKLV': 'L'}                  # owner: VTKLV structures are LVT
HIGHLIGHT_CODES = {'ROUTING_REMOVED', 'LLE_TAG', 'DR_PENDING', 'VT_DEFAULT', 'ISO_DIR_PENDING', 'SKEW_DIR_SET'}
DR_DECISIONS: dict[str, str] = {}             # original base -> 'Y' / 'N' (DR_to_NOM_Decisions.csv)
ISO_DIR_DECISIONS: dict[str, dict] = {}       # original base -> {DIR, SKEW, Approve} (ISO_Skew_Direction_Decisions.csv)
NEIGHBOUR_DIR: dict[str, tuple] = {}          # original base -> (DIR, SKEW, neighbour name) from row layout
# Additional-section tokens that make a structure a non-POR variant (no NOM added)
VARIANT_RX = re.compile(r'^(?:NODBI|YDBI|NOIMPLANT|YIMPLANT|TAPNODBI|NOVCR|NOVCRNUB|VCRNUB|HV|5T|\d+G|GNAC\w*'
                        r'|STK|SHRD|SHRS|SHD|\d+GANG|DEPOP)$')
POR_ALIASES = {'POR', 'REF', 'SDR'}          # additional-section tokens meaning POR -> NOM
ISO_QUALIFIERS = {'NDBI', 'YDBI'}            # moved to a trailing ':' chunk in ISO skews
# TSDR skew grammar shared by all families: TYPE:LAYER:DIR:SKEW:MAG[:extra]
SKEW_KINDS = {'CD', 'REG', 'REF', 'DEN', 'ESD', 'ENC', 'EC', 'NES'}
TSDR_DIRS = {'OGD', 'PGD', 'Z'}
LAYER_DIR = {'PXL': 'PGD', 'PLY': 'OGD', 'TCN': 'OGD', 'FTI': 'OGD', 'FKTCN': 'OGD'}   # owner WW44
MERGED_LAYER_SKEW = re.compile(r'^(PLY|TCN|PXL|FTI)(CD|REG)(PP)?$')
MAG_RX = re.compile(r'^(?:[NP]\d+(?:D\d+)?[A-Z]?|NOM|\d+(?:D\d+)?)$')
# nm; absolute CD magnitudes are expressed relative to these (owner values; TCN KLV = series centre)
SKEW_NOMINAL = {('VCRNUB', 'OGD'): 44, ('VCR', 'PGD'): 12, ('TCN', 'OGD'): 12}
VCRNUB_PGD_NOMINAL = {110: 8.4, 132: 13.4, 165: 13.4}   # single-sided, by cell height (double-sided = 2x)
# leakage direction per ISO structure type (owner WW44); checked for POR-layer (SDR/NOM) structures
ISO_DIR_RULE = {'CTG': 'OGD', 'GATEGATE': 'PGD', 'TCNTCN': 'PGD', 'EPIEPI': 'PGD', 'DIFFOGD': 'OGD'}
DEFAULT_SKEW_TYPE = 'A'                      # lego type
KLV_DETAIL_MAP = [(re.compile(r'^CD(\d+)$'), r'CD:\1'),
                  (re.compile(r'^SDRENC([NP]\d+)$'), r'NOM:ENC:\1'),
                  (re.compile(r'^SDRENC$'), 'NOM:ENC'),
                  (re.compile(r'^REG(PGD|OGD)((?::.*)?)$'), r'\1:REG\2'),
                  (re.compile(r'^NES(OGD|PGD)$'), r'NES:\1')]

TEMPLATES = {
    'ROS':     '[W]X81ZROS{CH}-{Z}-{#L}-{VT}-{STYLE}-STG{n}-FO{n}-{SH|DH}-{NOM|skew}-(M#)-{ADD}',
    'ROSPFT':  '[W]ROS{N|P}{CH}-{Z}-{#L}-{VT}-{STYLE}-{NEST}-{ISO}-{SH|DH}-{NOM|skew}-(M#)-{ADD}',
    'PFT':     '[W][M]{N|P}S{CH}-{Z}-{#L}-{VT}-{ENV[/NP|/PP]}-{NEST}-{ISO}-{SH|DH}-{NOM|skew}-{ADD}',
    'BITCELL': '[W]{SR|RF}{N|P}{CH}-{DIFF}-{#L}-{DUT}-{LIB}-{NEST}-{ISO}-{NOM|skew}-{ADD}',
    'BCLA':    '{SR|RF}{CH}-{LIB}BCLA-{ADD}',
    'MLR':     '[W]{N|P|X}M{CH}-{Z}-{#ADF}-{VT}-{ENV|/D}-{NEST}-{ISO}-{SH|DH}-{NOM|skew}-{ADD}',
    'ISO':     '{FAILMODE}{CH}-{POL}{Z}-NL-{VT}-{TYPE}:{LAYER|NOM}:{DIR}:{SKEW}:{MAG}[:extra][_inst]',
    'KLV':     '{FAILMODE}KLV{CH}-{POL}{Z}-NL-{VT}-{DETAIL|NOM}[_inst]   (ISO layout)',
    'FK':      '{N|P}S{CH}-{Z}-1L-{VT}-CMOS-1X1-IXI-SH-A:FKTCN:{DIR}:{SKEW}:{MAG}:44PP   (PFT style)',
    'QREL':    'QnR - minimal fixes; Proposed: {QFAM}{CH}-{Z}-{VT}-{ENV}-{NEST}-{ISO}-R{rev}:{MOS}:{VAR}:{GEOM}:..-{LOC}',
    'BE':      '{VIA}{TEST}{CH}-{LAYERS}-{W/S}-{W/S}-{DIM}-{SKEW}:{N|P|NOM}{MAG}',
}

_STYLE = r'INV|NAN\d*|NAND\d*|NOR\d*|AOI\w*|OAI\w*|XOR\d*|XNOR\d*|BUF\w*|MUX\w*'
_VT_ALT = '|'.join(sorted(VT_ANY, key=len, reverse=True))
_NEST_ISO = re.compile(r'^[A-Z0-9]{1,4}X[A-Z0-9]{1,4}$')   # 0X1, 10X10, SXS, IXI, I2XI2, SNXSN
BKM_ENVS = {'CMOS', 'BCMOS', 'LCMOS', 'OCMOS', 'ADTUNI', '/D'}
RX = {
    'DIFF': re.compile(r'^Z\d+$'),
    'DIFF_NUM': re.compile(r'^\d{2}(?::\d{2})?$'),
    'LEGS': re.compile(r'^\d+L$'),
    'ADF': re.compile(r'^\d+ADF$'),
    'VT': re.compile(rf'^(?:{_VT_ALT})(?::(?:{_VT_ALT}))*$'),   # HVT:LVT = mixed-VT ROS
    # /D = no diffusion; /NP,/PP = matching-pair qualifier
    'ENV': re.compile(r'^(?:(?:[BLO]?CMOS|UNI|ADT|ADTUNI|ADTCMOS|ANALOG)(?:/(?:NP|PP))?|/D)$'),
    'STYLE': re.compile(rf'^(?:{_STYLE})$'),
    'NEST': _NEST_ISO,
    'ISO': _NEST_ISO,
    'HEIGHT': re.compile(r'^(?:SH|DH)$'),
    'ROUTING': re.compile(r'^M\d+$'),
    'STG': re.compile(r'^STG\d+$'),
    'FO': re.compile(r'^FO\d+$'),
    'DUT': re.compile(r'^(?:PU|PG|PD|RP|IR)$'),
    'LIB': re.compile(r'^(?:HDC\d|HCC\d|TPRF\d)$'),
}
def _slot_rx(fam: str, slot: str):
    if fam == 'BITCELL' and slot == 'DIFF':
        return RX['DIFF_NUM']
    return RX[slot]


SLOT_RX = {fam: [(s, _slot_rx(fam, s)) for s in order] for fam, order in SLOT_ORDER.items()}

HEAD_RX = {
    'ROS':     re.compile(r'^(?P<WID>W?)(?P<CAT>X81ZROS)(?P<CH>\d{3})$'),
    'ROSPFT':  re.compile(r'^(?P<WID>W?)(?P<CAT>ROS)(?P<POL>[NP])(?P<CH>\d{3})$'),
    'PFT':     re.compile(r'^(?P<WID>W?)(?P<MAT>M?)(?P<POL>[NP])(?P<CAT>[SP])(?P<CH>\d{3})$'),
    'BITCELL': re.compile(r'^(?P<WID>W?)(?P<CAT>SR|RF)(?P<POL>[NP])(?P<CH>\d{3})$'),
    'MLR':     re.compile(r'^(?P<WID>W?)(?P<POL>N|P|X|XN|XP)(?P<CAT>M)(?P<CH>\d{3})$'),
}

# Expected test-configuration templates per family (superflow column C)
_PFT_CFG = r'^(KEY_LOGIC|MAT10P|AUTOBIAS|BTI|HCI)'
FAMILY_CFG = {
    'ROS': r'^ROS_', 'ROSPFT': _PFT_CFG, 'PFT': _PFT_CFG, 'BITCELL': _PFT_CFG, 'BCLA': r'^BCLA',
    'MLR': r'^(IG_|LD_|COV|CGT|VBD)', 'ISO': r'^(I\d|C_)', 'KLV': r'^R\d',
    'QREL': r'^(BTI|HCI|IG_|VBD|COV|LD_|CGT)', 'BE': r'^(R\d|I\d|C_)', 'FK': r'^(KEY_LOGIC|FKTCN)',
}
ISO_DIRS = {'OGD', 'PGD', 'Z'}
SEV_RANK = {'ERROR': 3, 'WARN': 2, 'INFO': 1, '': 0}

GRID_COLS = ['CAT', 'PFX', 'POL', 'CH', 'DIFF', 'DIFF_W', 'LEGS', 'ADF', 'VT', 'ENV', 'STYLE', 'DUT', 'LIB',
             'NEST', 'STG', 'ISO', 'FO', 'HEIGHT', 'ROUTING', 'TEST', 'SKEW', 'SKEW_TYPE', 'SKEW_LAYER',
             'SKEW_DIR', 'SKEW_KIND', 'SKEW_MAG', 'INST', 'ADD']


@dataclass
class Result:
    family: str
    fields: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)    # (severity, code, message, fixed_by_correction)
    corrected: str | None = None                  # base name (no TC suffix); None => manual fix needed
    proposed: str | None = None                   # alternative scheme for families without a BKM slide
    slots: set = field(default_factory=set)

    def add(self, sev: str, code: str, msg: str, fixed: bool = False):
        self.issues.append((sev, code, msg, fixed))


# --------------------------------------------------------------------------------------
# Family detection and parsers
# --------------------------------------------------------------------------------------
def detect_family(base: str) -> str:
    first = base.split('-')[0].split('_')[0]
    if re.match(r'^W?X81ZROS\d{3}$', first):
        return 'ROS'
    if re.match(r'^W?ROS[NP]\d{3}$', first):
        return 'ROSPFT'
    if re.match(r'^W?(SR|RF)[NP]\d{3}$', first):
        return 'BITCELL'
    if re.match(r'^(SR|RF)\d{3}$', first):
        return 'BCLA'
    if re.match(r'^W?M?[NP][SP]\d{3}$', first):
        return 'PFT'
    if re.match(r'^W?(N|P|X|XN|XP)M(\d{3})?$', first):
        return 'MLR'
    if re.match(r'^FK[NP]S\d+$', first):
        return 'FK'
    toks = base.split('-')
    if (re.match(r'^(N|P|NP|ND|PD)?(GATE|TCN|VG|VT|VCR)KLV\d{3}$', first) and len(toks) > 1
            and re.match(r'^(N|P|NP|ND|PD)?(Z\d+|\d{2})$', toks[1])):
        return 'KLV'
    if re.match(r'^Q[A-Z]+\d', first):
        return 'QREL'
    if '_' in base and re.match(r'^(V\d|VG|VT|VCR|M\d)$', base.split('_')[0]):
        return 'BE'
    if re.match(r'^W?(V\d|VG|VT|VCR|M\d)([A-Z]+)?(\d{3})?$', first):
        return 'BE'      # already in the hyphen BE scheme
    if re.match(r'^[A-Z]+\d{3}$', first) and base.count('-') >= 2:
        return 'ISO'
    return 'UNKNOWN'


def check_logic_lib(res: Result):
    ch, z = res.fields.get('CH'), res.fields.get('DIFF')
    if not ch:
        return
    ch = int(ch)
    if ch not in LOGIC_LIB:
        res.add('WARN', 'LIB_CH', f'Cell height {ch} not in x81Z logic library (110/132/165, slide 7)')
    elif z and z not in LOGIC_LIB[ch]:
        res.add('WARN', 'LIB_CH_Z', f'{ch}-{z} is not a library combination (slide 7: {ch} offers '
                                    f'{",".join(LOGIC_LIB[ch])})')
    elif z:
        res.fields['DIFF_W'] = LOGIC_LIB[ch][z]


def parse_slotted(base: str, fam: str, res: Result):
    order = SLOT_ORDER[fam]
    if re.search(r':[+-]\d', base):
        new = re.sub(r':([+-])(\d)', lambda m: ':' + ('N' if m.group(1) == '-' else 'P') + m.group(2), base)
        res.add('WARN', 'SKEW_CLEANUP', "Signed value after ':' ('-n' splits the field) -> N/P", True)
        base = new
    toks = base.split('-')
    head = toks.pop(0)
    if re.fullmatch(r'[A-Z]+', head) and toks and re.fullmatch(r'\d{3}', toks[0]):
        res.add('WARN', 'CAT_CH_SPLIT', f"Extra '-' between category and cell height ('{head}-{toks[0]}')", True)
        head += toks.pop(0)
    m = HEAD_RX[fam].match(head)
    if not m:
        res.add('ERROR', 'HEAD', f"Category/cell-height head '{head}' does not match {TEMPLATES[fam]}")
        return
    g = {k: v for k, v in m.groupdict().items() if v}
    res.fields.update({k: v for k, v in g.items() if k in ('CAT', 'POL', 'CH')})
    wid, mat = bool(g.get('WID')), bool(g.get('MAT'))
    pair = fam == 'PFT' and g.get('CAT') == 'P'

    # --- token normalisation before slot assignment ---
    if 'WID' in toks:
        toks = [t for t in toks if t != 'WID']
        if not wid:
            res.add('WARN', 'WID_PREFIX', "'-WID' suffix removed; WID structures are prefixed with 'W' (slide 10/11)",
                    True)
        wid = True
    if '/D' in toks:
        envs = [t for t in toks if t != '/D' and RX['ENV'].match(t)]
        if envs:
            toks = [t for t in toks if t not in envs]
            res.add('WARN', 'NODIFF_ENV', f"'/D' (no diffusion) is the environment; '{'-'.join(envs)}' dropped", True)
    adt = any(t == 'ADT' or t.startswith('ADT:') for t in toks)
    toks = [t[4:] if t.startswith('ADT:') else t for t in toks]

    filled: dict[str, str] = {}
    seq, core_pos, adds, trailing = [], [], [], []
    for i, tok in enumerate(toks):
        if '_' in tok:
            tok, rest = tok.split('_', 1)
            if (i == len(toks) - 1 and ':' in tok and not rest.isdigit()
                    and not re.match(r'[A-Z]:', tok)):
                new = f"{tok}:{rest.replace('_', ':')}"
                res.add('WARN', 'UNDERSCORE_IN_BODY', f"'_' inside '{tok}_{rest}' -> '{new}'", True)
                tok, rest = new, ''
            if rest:
                trailing.append(rest)
            if i != len(toks) - 1:
                res.add('WARN', 'UNDERSCORE_IN_BODY',
                        f"'_' inside field '{tok}_{rest}' ('_' is reserved for seat/row instance); "
                        f"'_{rest}' moved to end of name (slide 3)", True)
        if not tok:
            continue
        slot = None
        cands = [s for s, rx in SLOT_RX[fam] if rx.match(tok)]
        free = [s for s in cands if s not in filled]
        if free:
            slot = free[0]
        elif cands:
            if tok == filled[cands[0]]:
                res.add('WARN', 'DUP_FIELD', f"Repeated {cands[0]} token '{tok}' dropped", True)
                continue
            res.add('ERROR', 'DUP_FIELD', f"Second {cands[0]} token '{tok}' conflicts with "
                                          f"'{filled[cands[0]]}' - fix manually")
        if slot:
            filled[slot] = tok
            seq.append(slot)
            core_pos.append(i)
        else:
            adds.append((i, tok))

    expected = [s for s in order if s in filled]
    if seq != expected:
        res.add('WARN', 'ORDER', f"Field order {'-'.join(seq)} differs from BKM {'-'.join(expected)}", True)
    last_core = max(core_pos) if core_pos else -1
    for i, tok in adds:
        if i < last_core:
            res.add('WARN', 'NONBKM_TOKEN_POS',
                    f"Non-BKM token '{tok}' sits between core fields - moved to additional section", True)
    for s, v in SLOT_DEFAULTS.get(fam, {}).items():
        if s not in filled:
            filled[s] = v
            res.add('WARN', 'DEFAULT_FILL', f'{s} not specified - default {v} inserted for grid alignment', True)
    missing = [s for s in order if s not in filled and s not in OPTIONAL_SLOTS]
    for s in missing:
        res.add('ERROR', 'MISSING_FIELD', f'Mandatory BKM field {s} not found ({TEMPLATES[fam]})')
    if fam in ROUTING_REMOVED and 'ROUTING' in filled:
        res.add('WARN', 'ROUTING_REMOVED', f"Metal routing '{filled.pop('ROUTING')}' removed (kept only for ROS/ROSPFT)",
                True)

    vt = filled.get('VT')
    if vt and any(v in VT_FULL_TO_CODE for v in vt.split(':')):
        new = ':'.join(VT_FULL_TO_CODE.get(v, v) for v in vt.split(':'))
        res.add('WARN', 'VT_FORMAT', f"VT '{vt}' should be single-letter metal-gate code '{new}'", True)
        filled['VT'] = new
    env = filled.get('ENV')
    if env and adt and not env.startswith('ADT'):
        filled['ENV'] = 'ADT' + env
        res.add('WARN', 'ADT_ENV', f"ADT structure: environment '{env}' -> '{filled['ENV']}', 'ADT:' prefix "
                                   'removed from additional detail', True)
    if env and pair and '/' not in env:
        filled['ENV'] = f"{filled['ENV']}/{g['POL']}P"
        mat = True
        res.add('WARN', 'MATCHING_PREFIX', f"Matching pair: head '{g['POL']}P' -> 'M{g['POL']}S', pair moved to "
                                           f"environment '{filled['ENV']}'", True)
    env = filled.get('ENV')
    if env and env.split('/')[0] not in {e.split('/')[0] for e in BKM_ENVS} and env != '/D':
        res.add('INFO', 'ENV_VALUE', f"Environment '{env}' is not in the BKM list {sorted(BKM_ENVS)}")

    raw = [t for _, t in adds]
    joined, i = [], 0
    while i < len(raw):
        if (raw[i] in LAYER_DIR and i + 2 < len(raw) and raw[i + 1] in SKEW_KINDS | TSDR_DIRS
                and ':' in raw[i + 2]):
            joined.append(':'.join(raw[i:i + 3]))
            res.add('WARN', 'SKEW_SPLIT', f"Skew split by '-' ('{'-'.join(raw[i:i + 3])}') joined with ':'", True)
            i += 3
        else:
            joined.append(raw[i])
            i += 1
    addtoks = []
    lego = DEFAULT_SKEW_TYPE
    for t in joined:
        if re.fullmatch(r'[A-Z]:NOM', t):
            lego, t = t[0], 'NOM'
        if ':' in t:
            t = clean_add_token(t, res)
        if ':' in t or MERGED_LAYER_SKEW.match(t.split(':')[0]):
            t = norm_tsdr(t, res)
        if fam in ('PFT', 'MLR'):
            t = ply_tcn_vocab(t, res)
        if t in POR_ALIASES:
            res.add('WARN', 'POR_TO_NOM', f"POR token '{t}' -> 'NOM'", True)
            t = 'NOM'
        if t not in addtoks or t != 'NOM':
            addtoks.append(t)
    if fam in NOM_FAMILIES:
        for k, t in enumerate(addtoks):
            if (':' in t and not re.match(r'[A-Z]:', t)
                    and not all(VARIANT_RX.match(c) for c in t.split(':'))):
                addtoks[k] = f'{lego}:{t}'
                res.add('WARN', 'LEGO_PREFIX', f"Lego type added to '{t}' -> '{addtoks[k]}'", True)
        skewed = any(':' in t or VARIANT_RX.match(t) for t in addtoks)
        if 'NOM' in addtoks and addtoks[0] != 'NOM':
            addtoks.remove('NOM')
            addtoks.insert(0, 'NOM')
            res.add('WARN', 'NOM_POSITION', 'NOM moved to the first additional position', True)
        elif 'NOM' not in addtoks and not skewed:
            addtoks.insert(0, 'NOM')
            res.add('WARN', 'NOM_MISSING', 'POR structure without TSDR skew must carry NOM', True)
    if 'NOM' in addtoks:
        addtoks[addtoks.index('NOM')] = f'{lego}:NOM'
        if f'{lego}:NOM' not in base:
            res.add('WARN', 'NOM_LEGO', f"Lego type added to NOM -> '{lego}:NOM' (TYPE first, as in skews)", True)

    if fam == 'MLR' and len(addtoks) > 1 and not any(':' in t for t in addtoks):
        res.add('WARN', 'ADD_FOLD', f"Variant tokens {addtoks} joined into one ':' field (uniform field count)", True)
        addtoks = [':'.join(addtoks)]
    if fam == 'PFT':
        new_head = f"{'W' if wid else ''}{'M' if mat else ''}{g['POL']}{'S' if pair else g['CAT']}{g['CH']}"
    else:
        new_head = ('W' if wid else '') + (head[1:] if g.get('WID') else head)
    res.fields['PFX'] = ('W' if wid else '') + ('M' if mat else '')
    res.fields.update(filled)
    res.fields['ADD'] = '-'.join(addtoks)
    if trailing:
        res.fields['INST'] = '_'.join(trailing)
    res.slots = set(filled)
    if not missing:
        core = [filled[s] for s in order if s in filled and not (s == 'ROUTING' and fam in ROUTING_AFTER_SKEW)]
        tail = list(addtoks)
        if fam in ROUTING_AFTER_SKEW and 'ROUTING' in filled:
            tail.insert(1 if tail and re.match(r'[A-Z]:', tail[0]) else 0, filled['ROUTING'])
        res.corrected = '-'.join([new_head] + core + tail) + ''.join('_' + t for t in trailing)

    if fam in ('ROS', 'ROSPFT', 'PFT', 'MLR'):
        check_logic_lib(res)
    if fam == 'BITCELL':
        check_bitcell_lib(res)


def check_bitcell_lib(res: Result):
    lib, dut, ch, diff = (res.fields.get(k) for k in ('LIB', 'DUT', 'CH', 'DIFF'))
    if lib not in BITCELL_LIB:
        return
    lch, widths = BITCELL_LIB[lib]
    if ch and int(ch) != lch:
        res.add('WARN', 'LIB_BITCELL_CH', f'{lib} cell height is {lch} per slide 8, name has {ch}')
    if dut in widths and diff and ':' not in diff and int(diff) != widths[dut]:
        res.add('WARN', 'LIB_BITCELL_DIFF', f'{lib} {dut} diffusion width is {widths[dut]} per slide 8, '
                                            f'name has {diff} - verify against PDK')


def parse_iso(base: str, fam: str, res: Result):
    toks = base.split('-')
    if fam == 'KLV':
        m = re.match(r'^(?P<PPOL>N|P|NP|ND|PD)?(?P<CAT>(?:GATE|TCN|VG|VT|VCR)KLV)(?P<CH>\d{3})$', toks[0])
    else:
        m = re.match(r'^(?P<CAT>[A-Z]+?)(?P<CH>\d{3})$', toks[0])
    if not m or len(toks) < 2:
        res.add('ERROR', 'HEAD', f"Head '{toks[0]}' does not match {TEMPLATES[fam]}")
        return
    cat, ch, ppol = m.group('CAT'), m.group('CH'), m.groupdict().get('PPOL')
    res.fields.update(CAT=cat, CH=ch)
    fixable = True

    m2 = re.match(r'^(?P<POL>N|P|NP|ND|PD)?(?P<D>Z\d+|\d{2})$', toks[1])
    if not m2:
        res.add('ERROR', 'POL_DIFF', f"Polarity+diffusion field '{toks[1]}' not recognised (expect e.g. NZ5)")
        return
    pol, diff = m2.group('POL'), m2.group('D')
    if ppol and not pol:
        res.add('WARN', 'POL_POSITION', f"Polarity '{ppol}' is in the category; ISO layout puts it with the "
                                        f"diffusion field ({ppol}{diff})", True)
        pol = ppol
    elif ppol and pol:
        res.add('ERROR', 'POL_DUP', f"Polarity given twice ('{ppol}' and '{pol}')")
        fixable = False
    if not pol:
        res.add('ERROR', 'POL_MISSING', 'Polarity missing from diffusion field')
        fixable = False
    if diff.isdigit():
        z = LOGIC_WIDTH_TO_Z.get(int(ch), {}).get(int(diff))
        if z:
            res.add('WARN', 'DIFF_NUMERIC', f"Diffusion given as width '{diff}'; BKM uses Z code ({ch}: {diff}->{z})",
                    True)
            diff = z
        else:
            res.add('ERROR', 'DIFF_NUMERIC', f"Diffusion width '{diff}' has no Z code at CH {ch} (slide 7)")
            fixable = False
    res.fields.update(POL=pol or '', DIFF=diff)
    check_logic_lib(res)

    rest = toks[2:]
    if rest and rest[0] == 'NL':
        rest = rest[1:]
    else:
        res.add('WARN', 'LEGS_NL', "No legs field in ISO/KLV - 'NL' (no legs) inserted so VT sits in field 4 like "
                                   'the logic families', True)
    res.fields['LEGS'] = 'NL'
    vt = rest[0] if rest and RX['VT'].match(rest[0]) else None
    if not vt and cat in DEFAULT_VT:
        vt = DEFAULT_VT[cat]
        res.add('WARN', 'VT_DEFAULT', f"VT missing - '{vt}' inserted ({cat} structures are "
                                      f"{[k for k, v in VT_FULL_TO_CODE.items() if v == vt][0]} per owner)", True)
        rest = [vt] + rest
    if vt:
        rest = rest[1:]
        new = ':'.join(VT_FULL_TO_CODE.get(v, v) for v in vt.split(':'))
        if new != vt:
            res.add('WARN', 'VT_FORMAT', f"VT '{vt}' should be '{new}'", True)
            vt = new
        res.fields['VT'] = vt
    else:
        res.add('ERROR', 'MISSING_FIELD', f'VT (metal gate) field missing after diffusion ({TEMPLATES[fam]})')
        fixable = False
    if not rest:
        res.add('ERROR', 'MISSING_FIELD', 'Skew/detail field missing')
        return

    # trailing "_n" instance is allowed (slide 3: extra detail at end with '_')
    tail = '-'.join(rest)
    inst = ''
    mi = re.match(r'^(.*?)_(\d+)$', tail)
    if mi:
        tail, inst = mi.groups()
    if len(rest) > 1:
        parts = tail.split('-')
        main = next((p for p in parts if ':' in p), None)
        letter = [p for p in parts if re.fullmatch(r'[A-Z]', p)]
        extra = [p for p in parts if p is not main and p not in letter]
        if fam == 'KLV' or main is None:
            new = ':'.join(parts)
        else:
            new = (letter[0] + ':' if letter and not re.match(r'^[A-Z]:', main) else '') + main
            new += ''.join(':' + e for e in extra)
        res.add('WARN', 'EXTRA_FIELDS', f"{len(rest)} hyphen fields after VT (BKM has exactly one); "
                                        f"'{tail}' -> '{new}' (extra detail as ':' chunks, slide 3)", True)
        tail = new
    if '_' in tail and fam == 'KLV':
        new = tail.replace('_', ':')
        res.add('WARN', 'UNDERSCORE_IN_BODY', f"'_' inside KLV detail '{tail}' -> '{new}'", True)
        tail = new
    if '_' in tail:
        out, extras = [], []
        for c in tail.split(':'):
            if '_' in c:
                parts = c.split('_')
                extras += [p for p in parts if p in ISO_QUALIFIERS]
                c = _fix_skew_chunk('_'.join(p for p in parts if p not in ISO_QUALIFIERS))
            out.append(c)
        new = ':'.join(out + extras)
        res.add('WARN', 'UNDERSCORE_IN_BODY', f"'_' inside skew '{tail}' -> '{new}' (qualifiers such as NDBI "
                                              'moved to a trailing chunk)', True)
        tail = new
    por = {'SDR'}
    if fam == 'KLV' and 'DR' in tail.split(':'):
        if dr_decision(base, res):
            por.add('DR')
    if any(c in por for c in tail.split(':')):
        new = ':'.join('NOM' if c in por else c for c in tail.split(':'))
        res.add('WARN', 'SDR_TO_NOM', f"POR '{tail}' -> '{new}' (POR structures carry NOM)", True)
        tail = new
    if fam == 'KLV':
        new = klv_tsdr(tail, cat)
        if new != tail:
            res.add('WARN', 'SKEW_SCHEME', f"KLV detail '{tail}' -> '{new}' (TYPE:LAYER:DIR:SKEW:MAG, best effort)",
                    True)
            tail = new
    else:
        sk = tail.split(':')
        if not re.fullmatch(r'[A-Z]', sk[0]) and len(sk) >= 4 and sk[1] in TSDR_DIRS:
            new = f'{DEFAULT_SKEW_TYPE}:{tail}'
            res.add('WARN', 'SKEW_SCHEME', f"Skew TYPE missing '{tail}' -> '{new}'", True)
            tail = new
        tail = norm_tsdr(tail, res, relative=True)
        sk0 = tail.split(':')
        if len(sk0) >= 5 and sk0[4] == '0':
            new = ':'.join(sk0[:4] + ['NOM'] + sk0[5:])
            res.add('WARN', 'SKEW_SCHEME', f"Zero skew '{tail}' -> '{new}'", True)
            tail = new
    if tail == 'NOM':
        tail = f'{DEFAULT_SKEW_TYPE}:NOM'
        res.add('WARN', 'NOM_LEGO', f"Lego type added to NOM -> '{tail}'", True)
    sk = tail.split(':')
    if fam == 'ISO' and len(sk) == 3 and re.fullmatch(r'[A-Z]', sk[0]) and sk[2] == 'NOM':
        res.fields['_isodir'] = True
        dec = ISO_DIR_DECISIONS.get(base, {})
        nb = NEIGHBOUR_DIR.get(base)
        src = None
        if dec.get('Approve') == 'Y' and dec.get('DIR') and dec.get('SKEW'):
            d, s, src = dec['DIR'], dec['SKEW'], 'ISO_Skew_Direction_Decisions.csv'
        elif dec.get('Approve') != 'N' and nb:
            d, s, src = nb[0], nb[1], nb[2]
        if src:
            new = f'{sk[0]}:{sk[1]}:{d}:{s}:NOM'
            res.add('WARN', 'SKEW_DIR_SET', f"Direction/skew type from {src}: '{tail}' -> '{new}'", True)
            tail = new
        else:
            res.add('WARN', 'ISO_DIR_PENDING', f"'{tail}' has no direction/skew type - no same-device neighbour; "
                                               'set it in ISO_Skew_Direction_Decisions.csv')
    res.fields['SKEW'] = tail
    res.fields['INST'] = inst
    chunks = tail.split(':')
    if fam == 'ISO':
        if len(chunks) >= 5:
            res.fields.update(SKEW_TYPE=chunks[0], SKEW_LAYER=chunks[1], SKEW_DIR=chunks[2],
                              SKEW_KIND=chunks[3], SKEW_MAG=':'.join(chunks[4:]))
            rule = ISO_DIR_RULE.get(cat)
            if chunks[1] == 'NOM' and rule and chunks[2] != rule:
                res.add('WARN', 'ISO_DIR_RULE', f"Leakage direction for {cat} is {rule}, name has '{chunks[2]}'")
        if re.fullmatch(r'[A-Z]:NOM', tail):
            pass
        elif len(chunks) < 5:
            res.add('WARN', 'SKEW_FORMAT', f"Skew '{tail}' has {len(chunks)} ':' chunks; BKM is "
                                           "TYPE:LAYER:DIR:SKEW:MAG (5, extra detail as further chunks)")
        else:
            if not re.fullmatch(r'[A-Z]', chunks[0]):
                res.add('WARN', 'SKEW_FORMAT', f"Skew type '{chunks[0]}' should be a single letter")
            if not _is_dir(chunks[2]):
                res.add('WARN', 'SKEW_FORMAT', f"Skew direction '{chunks[2]}' not in {sorted(ISO_DIRS)}")
    elif fam == 'KLV' and ':' not in tail:
        res.add('INFO', 'KLV_DETAIL', "KLV detail is a free token (no ':' chunks); consider "
                                      "LAYER:DIR:SKEW:MAG chunks to align with ISO")
    if fixable:
        res.corrected = f'{cat}{ch}-{pol}{diff}-NL-{vt}-{tail}' + (f'_{inst}' if inst else '')


def _is_dir(c: str) -> bool:
    return all(p in TSDR_DIRS for p in c.split('/'))


def _is_kind(c: str) -> bool:
    return all(p in SKEW_KINDS for p in c.split('/'))


def _is_skew_token(t: str) -> bool:
    """Typed TSDR skew (TYPE:LAYER:DIR:SKEW:..) or a colon token carrying an N/P magnitude."""
    c = t.split(':')
    if len(c) >= 4 and re.fullmatch(r'[A-Z]', c[0]) and _is_dir(c[2]):
        return True
    return ':' in t and any(re.match(r'[NP]\d', x) for x in c[1:])


def clean_add_token(t: str, res: Result) -> str:
    """Best-effort clean-up of PFT/MLR additional colon tokens before TSDR normalisation."""
    old = t
    c = [(('N' if x[0] == '-' else 'P') + x[1:]) if re.fullmatch(r'[+-]\d+(?:\.\d+)?', x) else x
         for x in t.split(':')]
    c = [re.sub(r'^([NP]?\d+)\.(\d+)$', r'\1D\2', x) for x in c]
    if len(c) > 1 and c[-1] in ('REF', 'POR') and not re.fullmatch(r'[A-Z]', c[0]):
        c[-1] = 'NOM'
    t = ':'.join(c)
    if m := re.fullmatch(r'PCD:([^:]+):TCD:([^:]+)((?::.*)?)', t):
        p, q, rest = m.groups()
        mirror = {'N': 'P', 'P': 'N'}
        if (q == p == 'NOM' or q == mirror.get(p[0], '') + p[1:]) and rest in ('', ':44PP'):
            # TCN mirrors PLY and 44PP is the POR pitch -> both derivable; keeps the name within 64 chars
            t = f'{DEFAULT_SKEW_TYPE}:PLY/TCN:OGD:CD:{p}'
        else:
            t = f'{DEFAULT_SKEW_TYPE}:PLY/TCN:OGD:CD:{p}/{q}{rest}'
    elif m := re.fullmatch(r'PLYEC(FTI)?:((?:(?:CRL|DKA)[0-9]*:?)+?):?([NP]\d+(?:D\d+)?|NOM|0)', t):
        mag = 'NOM' if m.group(3) == '0' else m.group(3)
        t = f"{DEFAULT_SKEW_TYPE}:PLY:OGD:EC:{mag}{':FTI' if m.group(1) else ''}:{m.group(2).rstrip(':')}"
    elif m := re.fullmatch(r'PLYEC(FTI)?:(?:NOM:)?0', t):
        t = f"{DEFAULT_SKEW_TYPE}:PLY:OGD:EC:NOM{':FTI' if m.group(1) else ''}"
    elif m := re.fullmatch(r'PLYITCN:([NP]\d+(?:D\d+)?|NOM)((?::.*)?)', t):
        t = f'{DEFAULT_SKEW_TYPE}:PLYITCN:OGD:CD:{m.group(1)}{m.group(2)}'
    if t != old:
        res.add('WARN', 'SKEW_CLEANUP', f"Additional token '{old}' -> '{t}' (sign/decimal/REF, best-effort skew)",
                True)
    return t


def ply_tcn_vocab(t: str, res: Result) -> str:
    """PLY/TCN CD skews: the layer code states what happens to TCN and the poly pitch (POR PP = 44).
    PLYFTCN = PLY skewed, TCN fixed, PP = 44+MAG; PLYFPP = PLY only at fixed 44PP (TCN nominal);
    PLYITCN = PLY skewed, TCN inverse, fixed 44PP; TCNFPP = TCN only at fixed 44PP."""
    m = re.fullmatch(r'([A-Z]):(PLY|TCN|PLY/TCN|PLYITCN):OGD:CD:([NP]\d+(?:D\d+)?|NOM)(?::(\d*)PP)?', t)
    if not m:
        return t
    typ, lay, mag, pp = m.groups()
    if lay in ('PLY/TCN', 'PLYITCN'):
        lay = 'PLYITCN'
    elif lay == 'TCN':
        lay = 'TCNFPP' if pp in (None, '44') else lay
    elif pp is None or pp == '44':
        lay = 'PLYFPP'
    else:
        if pp:
            v = 0 if mag == 'NOM' else float(mag[1:].replace('D', '.')) * (-1 if mag[0] == 'N' else 1)
            if abs(int(pp) - 44 - v) > 1e-6:
                res.add('WARN', 'PLY_PITCH', f"Pitch {pp}PP does not equal 44+{mag} for PLY with TCN fixed")
                return t
        lay = 'PLYFTCN'
    new = f'{typ}:{lay}:OGD:CD:{mag}'
    if new != t:
        res.add('WARN', 'PLY_TCN_VOCAB', f"'{t}' -> '{new}' (PLYFTCN/PLYFPP/PLYITCN/TCNFPP vocabulary)", True)
    return new


def klv_tsdr(tail: str, cat: str) -> str:
    """Best-effort KLV detail -> TYPE:LAYER:DIR:SKEW:MAG; KLV resistance is vertical (Z) unless OGD/PGD given."""
    if re.fullmatch(r'[A-Z]:.*', tail) or tail == 'DR':
        return tail
    lay, a = cat[:-3], DEFAULT_SKEW_TYPE
    if m := re.fullmatch(r'CD:?(\d+)', tail):
        d = LAYER_DIR.get(lay, 'Z')
        v = int(m.group(1)) - SKEW_NOMINAL.get((lay, d), int(m.group(1)))
        return f"{a}:{lay}:{d}:CD:{'NOM' if v == 0 else ('N' if v < 0 else 'P') + str(abs(v))}"
    if m := re.fullmatch(r'(?:SDR|NOM:?)ENC:?([NP]\d+)?', tail):
        return f'{a}:{lay}:Z:ENC:{m.group(1) or "NOM"}'
    if m := re.fullmatch(r'(?:REG:?)?(PGD|OGD):?(?:REG)?(?::(.*))?', tail):
        rest = m.group(2) or ''
        if re.fullmatch(r'[NP]\d+[A-Z]?', rest.split(':')[0] if rest else ''):
            return f'{a}:{lay}:{m.group(1)}:REG:{rest}'
        return f'{a}:{lay}:{m.group(1)}:REG:NOM' + (f':{rest}' if rest else '')
    if m := re.fullmatch(r'NES:?(OGD|PGD)?', tail):
        return f'{a}:{lay}:{m.group(1) or "Z"}:NES:NOM'
    if tail == 'NOM':
        return tail
    return f'{a}:NOM:{tail}'


def norm_tsdr(tok: str, res: Result, relative: bool = False) -> str:
    """Rewrite a TSDR skew to TYPE:LAYER:DIR:SKEW:MAG[:extra]; non-skew colon tokens are returned unchanged."""
    t = ':'.join(re.sub(r'^([NP]?\d+)\.(\d+)$', r'\1D\2', c) for c in tok.split(':'))
    chunks = t.split(':')
    typ = chunks.pop(0) if len(chunks) > 1 and re.fullmatch(r'[A-Z]', chunks[0]) else ''
    if not chunks or sum(bool(MERGED_LAYER_SKEW.match(c)) for c in chunks) > 1:
        return tok
    layer, skew, pp = chunks.pop(0), None, False
    m = MERGED_LAYER_SKEW.match(layer)
    if m:
        layer, skew, pp = m.group(1), m.group(2), bool(m.group(3))
    dir_, mag, por, extras = None, None, False, []
    for c in chunks:
        if _is_dir(c) and not dir_:
            dir_ = c
        elif _is_kind(c) and not skew:
            skew = c
        elif c == 'POR':
            por = True
        elif MAG_RX.match(c) and not mag:
            mag = c
        else:
            extras.append(c)
    if por and mag in (None, 'P0', 'N0'):
        mag = 'NOM'
    if not (skew and mag):
        if t != tok:
            res.add('WARN', 'SKEW_SCHEME', f"Decimal magnitude '{tok}' -> '{t}'", True)
        return t
    if mag[0].isdigit() and not relative:
        key = (layer, dir_ or LAYER_DIR.get(layer))
        nom = SKEW_NOMINAL.get(key) if skew == 'CD' else None
        if key == ('VCRNUB', 'PGD') and skew == 'CD':
            nom = VCRNUB_PGD_NOMINAL.get(int(res.fields.get('CH') or 0))
        if nom is not None and re.fullmatch(r'\d+(?:D\d+)?', mag):
            d = round(float(mag.replace('D', '.')) - nom, 1)
            v = f'{abs(d):g}'.replace('.', 'D')
            mag = 'NOM' if d == 0 else f"{'N' if d < 0 else 'P'}{v}"
        else:
            res.add('WARN', 'SKEW_ABS', f"Unsigned magnitude '{mag}' in '{tok}' - nominal needed to express as N/P")
    if not dir_:
        dir_ = LAYER_DIR.get(layer)
        if not dir_:
            res.add('WARN', 'SKEW_DIR_MISSING', f"No direction (OGD/PGD) for layer '{layer}' in '{tok}'")
    new = ':'.join([typ or DEFAULT_SKEW_TYPE, layer] + ([dir_] if dir_ else []) + [skew, mag]
                   + (['PP'] if pp else []) + extras)
    if new != tok:
        res.add('WARN', 'SKEW_SCHEME', f"TSDR skew '{tok}' -> '{new}' (TYPE:LAYER:DIR:SKEW:MAG)", True)
    return new


def _fix_skew_chunk(chunk: str) -> str:
    """'GLK_OGD_DEN' -> 'GLK:OGD:DEN' (underscore used in place of ':'); 'NDBI_SDR' -> 'NDBISDR'."""
    parts = chunk.split('_')
    k = next((i for i, p in enumerate(parts) if p in ('OGD', 'PGD')), None)
    if k is None or k == 0:
        return ''.join(parts)
    return ':'.join([''.join(parts[:k]), parts[k]] + ([''.join(parts[k + 1:])] if parts[k + 1:] else []))


def parse_fk(base: str, res: Result):
    toks = base.split('-')
    m = re.match(r'^FK(?P<POL>[NP])S(?P<PP>\d+)$', toks[0])
    if not m or len(toks) < 5:
        res.add('ERROR', 'HEAD', f"FK name does not match FK{{N|P}}S{{PP}}-{{CH}}-{{Z}}-{{VT}}-{{skew}}")
        return
    ch, z, vt = toks[1:4]
    chunks = '-'.join(toks[4:]).split(':')
    chunks[0] = 'FK' + chunks[0] if chunks[0] == 'TCN' else chunks[0]
    if f"{m.group('PP')}PP" not in chunks:
        chunks.append(f"{m.group('PP')}PP")
    pft = f"{m.group('POL')}S{ch}-{z}-1L-{vt}-CMOS-1X1-IXI-SH-{':'.join(chunks)}"
    res.add('WARN', 'FK_TO_PFT', "Full-KTCN structure re-expressed in PFT style (1L, CMOS, 1X1, IXI, SH; skew "
                                 f"layer FKTCN; '{m.group('PP')}' -> {m.group('PP')}PP)", True)
    parse_slotted(pft, 'PFT', res)


def parse_bcla(base: str, res: Result):
    toks = base.split('-')
    m = re.match(r'^(?P<CAT>SR|RF)(?P<CH>\d{3})$', toks[0])
    m2 = re.match(r'^(?P<LIB>HDC\d|HCC\d|TPRF\d)BCLA$', toks[1]) if len(toks) > 1 else None
    if not (m and m2):
        res.add('ERROR', 'HEAD', f"BCLA name does not match {TEMPLATES['BCLA']}")
        return
    res.fields.update(CAT=m.group('CAT'), CH=m.group('CH'), LIB=m2.group('LIB'), ADD='-'.join(toks[2:]))
    lch = BITCELL_LIB.get(m2.group('LIB'), (None,))[0]
    if lch and int(m.group('CH')) != lch:
        res.add('WARN', 'LIB_BITCELL_CH', f"{m2.group('LIB')} cell height is {lch} per slide 8")
    res.corrected = base


def parse_qrel(base: str, res: Result):
    body = base
    if '_' in body:
        res.add('WARN', 'UNDERSCORE_DELIM', "'_' used as field delimiter; BKM uses '-' inside structure names "
                                            "('_' reserved for seat/row instance)", True)
        body = body.replace('_', '-')
    toks, out, i = body.split('-'), [], 0
    while i < len(toks):
        t = toks[i]
        if t == 'ULVT' and i + 1 < len(toks) and toks[i + 1] == 'LL':
            res.add('WARN', 'VT_SPLIT', "VT 'ULVT-LL' is split by a delimiter (shifts every later field); "
                                        "must be one field -> 'UL'", True)
            out.append('UL')
            res.fields['VT'] = 'UL'
            i += 2
            continue
        if t in VT_FULL_TO_CODE:
            res.add('WARN', 'VT_FORMAT', f"VT '{t}' should be '{VT_FULL_TO_CODE[t]}' (note: following "
                                         "L/S/SM/ML token is position-dependent)", True)
            res.fields['VT'] = VT_FULL_TO_CODE[t]
            out.append(VT_FULL_TO_CODE[t])
        else:
            out.append(t)
        i += 1
    res.fields['CAT'] = re.match(r'^[A-Z]+', toks[0]).group()
    res.fields['_nf'] = len(out)
    res.corrected = '-'.join(out)
    qnr_proposal(out, res)


def qnr_proposal(out: list[str], res: Result):
    """Best-effort BKM ordering; QnR-specific codes grouped in one ':' chunk until a decoder is available."""
    m = re.match(r'^(Q[A-Z]+?)(\d+)$', out[0])
    if not m:
        return
    qfam, rev = m.groups()
    q = dict(mos='', ch='', var='', z='', geom='', nest='', iso='', vt='', env='', loc='')
    other = []
    for t in out[1:]:
        if not q['mos'] and re.fullmatch(r'[NPX][MC]\d+', t):
            q['mos'] = t
        elif not q['ch'] and (mm := re.fullmatch(r'(\d{3})([A-Z]?)', t)):
            q['ch'], q['var'] = mm.groups()
        elif not q['z'] and (mm := re.fullmatch(r'(Z\d)(\w+)', t)):
            q['z'], q['geom'] = mm.groups()
        elif not q['nest'] and _NEST_ISO.match(t):
            q['nest'] = t
        elif not q['iso'] and _NEST_ISO.match(t):
            q['iso'] = t
        elif not q['vt'] and RX['VT'].match(t):
            q['vt'] = t
        elif not q['env'] and RX['ENV'].match(t):
            q['env'] = t
        elif not q['loc'] and re.fullmatch(r'(BP|VP)\d+', t):
            q['loc'] = t
        else:
            other.append(t)
    detail = ':'.join(x for x in [f'R{rev}', q['mos'], q['var'], q['geom'], *other] if x)
    res.proposed = '-'.join(x for x in [qfam + q['ch'], q['z'], q['vt'], q['env'], q['nest'], q['iso'],
                                        detail, q['loc']] if x)
    res.fields.update(CH=q['ch'], DIFF=q['z'], VT=q['vt'], ENV=q['env'], NEST=q['nest'], ISO=q['iso'],
                      POL=q['mos'][:1], ADD=detail)
    res.add('INFO', 'QNR_PROPOSED', 'Best-effort BKM-ordered Proposed_Name; QnR-specific codes (MOS code, '
                                    'variant, geometry, style, topology) grouped in one chunk pending decoder')


BE_LAYER = re.compile(r'^(?:M\d+[A-Z]?|TCN|POLY|Z\d+[NP]*|V\d)$')
BE_CH = re.compile(r'^(\d{3})(C?H)$')
SIGNED = re.compile(r'^[+-]\d+(?:\.\d+)?$')


def parse_be(base: str, res: Result):
    if '_' not in base:     # already in the hyphen scheme
        m = re.match(r'^W?(?P<CAT>V\d|VG|VT|VCR|M\d)(?P<TEST>[A-Z]+)?(?P<CH>\d{3})?$', base.split('-')[0])
        res.fields.update({k: v for k, v in m.groupdict().items() if v})
        res.corrected = base
        return
    toks = base.split('_')
    stype = toks[1] if len(toks) > 1 and re.fullmatch(r'[A-Z]+', toks[1]) else ''
    res.fields.update(CAT=toks[0], TEST=stype)
    k = 2 if stype else 1
    if len(toks) > k + 1 and '/' not in toks[k] and BE_LAYER.match(toks[k]) and BE_LAYER.match(toks[k + 1]):
        res.add('WARN', 'BE_LAYER_DELIM', f"Layer pair '{toks[k]}_{toks[k + 1]}' should be "
                                          f"'{toks[k]}/{toks[k + 1]}' like its peers", True)
        toks[k:k + 2] = [toks[k] + '/' + toks[k + 1]]
    for i, t in enumerate(toks):
        m = BE_CH.match(t)
        if m:
            res.fields['CH'] = m.group(1)
            if m.group(2) == 'CH':
                res.add('WARN', 'BE_CH_FORMAT', f"Cell height '{t}' -> '{m.group(1)}H' (peers use NNNH)", True)
                toks[i] = m.group(1) + 'H'
        elif t == 'DR' and dr_decision(base, res):
            res.add('WARN', 'SDR_TO_NOM', "Design-rule token 'DR' -> 'NOM' (approved in DR_to_NOM_Decisions.csv)",
                    True)
            toks[i] = 'NOM'
    res.fields['_toks'] = toks
    res.add('WARN', 'BE_SCHEME', "BE scheme aligned to BKM: '_' field delimiter -> '-', cell height moved after "
                                 "category, signed magnitudes -> N/P/NOM", True)


def dr_decision(base: str, res: Result) -> bool:
    """DR -> NOM only after per-structure approval; otherwise DR is kept and flagged."""
    res.fields['_dr'] = True
    d = DR_DECISIONS.get(base, '')
    if d == 'Y':
        return True
    if d == 'N':
        res.add('INFO', 'DR_KEPT', "'DR' kept (rejected in DR_to_NOM_Decisions.csv)")
    else:
        res.add('WARN', 'DR_PENDING', "'DR' -> 'NOM' awaiting confirmation in DR_to_NOM_Decisions.csv")
    return False


def compute_iso_neighbours(et: pd.DataFrame):
    """ISO POR names without direction get every DIR / SKEW used by the same device+layer series in the row."""
    e = et[['ROW_NAME', 'STRUCTURE_NAME_AS_TESTED', 'ATLANTIS_ORDINAL']].copy()
    e['ord'] = pd.to_numeric(e.ATLANTIS_ORDINAL.str.extract(r'(\d+)')[0], errors='coerce')
    e['dev'] = e.STRUCTURE_NAME_AS_TESTED.str.split('-').str[:2].str.join('-')
    e['layer'] = e.STRUCTURE_NAME_AS_TESTED.str.extract(r'-[A-Z]:([A-Z]+):')[0]
    skew_rx = re.compile(r'-[A-Z]:[A-Z]+:(OGD|PGD):([A-Z]+):')
    por_rx = r'^[A-Z]+\d{3}-[NP]{1,2}(?:Z)?\d+-[A-Z]+-[A-Z]:[A-Z]+:SDR_'
    for row, g in e.dropna(subset=['ord']).groupby('ROW_NAME'):
        for r in g[g.STRUCTURE_NAME_AS_TESTED.str.match(por_rx)].itertuples():
            base = TC_SUFFIX.match(r.STRUCTURE_NAME_AS_TESTED).group('base')
            if base in NEIGHBOUR_DIR:
                continue
            found = [skew_rx.search(n) for n in g[(g.dev == r.dev) & (g.layer == r.layer)].STRUCTURE_NAME_AS_TESTED]
            dirs = {m.group(1) for m in found if m}
            kinds = Counter(m.group(2) for m in found if m)
            if not dirs:
                continue
            order = {'CD': 0, 'REG': 1}
            d = '/'.join(x for x in ('OGD', 'PGD') if x in dirs)
            s = '/'.join(sorted(kinds, key=lambda k: (order.get(k, 9), k)))
            NEIGHBOUR_DIR[base] = (d, s, f'{r.dev} {r.layer} series in row: ' +
                                   ', '.join(f'{k} x{v}' for k, v in sorted(kinds.items())) + f'; DIR {d}')


def parse(base: str) -> Result:
    fam = detect_family(base)
    res = Result(fam)
    if fam in SLOT_ORDER:
        parse_slotted(base, fam, res)
    elif fam in ('ISO', 'KLV'):
        parse_iso(base, fam, res)
    elif fam == 'FK':
        parse_fk(base, res)
    elif fam == 'BCLA':
        parse_bcla(base, res)
    elif fam == 'QREL':
        parse_qrel(base, res)
    elif fam == 'BE':
        parse_be(base, res)
    else:
        res.add('ERROR', 'UNKNOWN_FAMILY', 'Name does not match any BKM structure family')
    return res


def generic_checks(name: str, base: str, tc: str, res: Result):
    if not tc:
        res.add('ERROR', 'NO_TC_SUFFIX', 'Missing _TCxx_y seat/row-instance suffix')
    if len(name) > MAX_NAME_LEN:
        res.add('ERROR', 'LEN_LIMIT', f'{len(name)} chars > {MAX_NAME_LEN} (name incl. TC suffix, slide 5)')
    if name != name.upper():
        res.add('ERROR', 'LOWERCASE', 'Lower-case characters present')
    bad = sorted({c for c in name.upper() if c not in ALLOWED_CHARS})
    if bad:
        res.add('ERROR', 'CHARSET', f"Disallowed characters {bad} (allowed: A-Z 0-9 - _ : / . +; '|' is the "
                                    "test|structure separator)")
    if '--' in base or base.startswith('-') or base.endswith('-') or '::' in base:
        res.add('ERROR', 'EMPTY_FIELD', 'Empty field (doubled or dangling delimiter)')
    if res.family != 'BE' and any('/' in t and not RX['ENV'].match(t) for t in re.sub(
            r'(?<=:)(?:OGD|PGD|CD|REG|REF|DEN|ESD|ENC)(?:/(?:OGD|PGD|CD|REG|REF|DEN|ESD|ENC))+(?=:)', 'X',
            base).split('-')):
        res.add('ERROR', 'SLASH', "'/' is only allowed in the environment field (/D, CMOS/NP, CMOS/PP)")


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def load_superflow(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    cols = {c.strip().lower(): c for c in df.columns}
    df = df[[cols['test row alias'], cols['test structure alias'], cols['test configuration name']]]
    df.columns = ['row', 'struct', 'cfg']
    return df[df.struct.str.strip() != '']


def load_etest(path: Path) -> pd.DataFrame:
    with open(path, encoding='utf-8-sig', errors='replace') as fh:
        lines = fh.read().splitlines()
    hdr = [h.strip() for h in lines[0].split('$$')]
    rows = [ln.split('$$') for ln in lines[1:] if ln.strip()]
    return pd.DataFrame([r[:len(hdr)] for r in rows], columns=hdr)


# --------------------------------------------------------------------------------------
# Main validation
# --------------------------------------------------------------------------------------
def validate(sf: pd.DataFrame, et: pd.DataFrame, al: pd.DataFrame | None, include_etest_only: bool):
    sf_info = sf.groupby('struct').agg(row=('row', lambda s: '; '.join(sorted(set(s)))),
                                       cfgs=('cfg', lambda s: sorted(set(s))),
                                       n_tests=('cfg', 'size'))
    et_map = {r.STRUCTURE_NAME_AS_TESTED: r for r in et.itertuples(index=False)}
    compute_iso_neighbours(et)

    names = [(n, 'Superflow') for n in sf_info.index]
    if include_etest_only:
        sf_set = set(sf_info.index)
        names += [(n, 'EtestReportOnly') for n in sorted(set(et.STRUCTURE_NAME_AS_TESTED) - sf_set)
                  if n and not n.startswith('EMPTY_')]

    results = {}
    for name, src in names:
        m = TC_SUFFIX.match(name)
        base, tc = (m.group('base'), m.group('tc')) if m else (name, '')
        res = parse(base)
        generic_checks(name, base, tc, res)
        res.fields['TC'] = tc
        results[name] = (src, base, tc, res)

    # --- family-level alignment checks ---
    by_fam = defaultdict(list)
    for name, (src, base, tc, res) in results.items():
        by_fam[res.family].append(name)
    for fam in SLOT_ORDER:
        members = [results[n][3] for n in by_fam.get(fam, [])]
        if not members:
            continue
        share = sum('ROUTING' in r.slots for r in members) / len(members)
        if share >= 0.5:
            for r in members:
                if 'ROUTING' not in r.slots and r.slots:
                    r.add('INFO', 'SLOT_ALIGN', f'Optional routing (M#) omitted while {share:.0%} of {fam} '
                                                'names carry it - breaks text-to-column alignment')
    # QREL: field count per sub-category
    q = defaultdict(list)
    for n in by_fam.get('QREL', []):
        r = results[n][3]
        q[r.fields['CAT']].append(r)
    for cat, members in q.items():
        mode = Counter(r.fields['_nf'] for r in members).most_common(1)[0][0]
        for r in members:
            if r.fields['_nf'] != mode:
                r.add('INFO', 'DELIM_COUNT', f"{r.fields['_nf']} fields vs {mode} for most {cat} names "
                                             '(QnR decoder pending)')
    # BE: field count, missing CH, hyphen scheme
    g = defaultdict(list)
    for n in by_fam.get('BE', []):
        r = results[n][3]
        if '_toks' in r.fields:
            g[(r.fields['CAT'], r.fields['TEST'])].append(r)
    for key, members in g.items():
        ch_share = sum('CH' in r.fields for r in members) / len(members)
        signed_idx = {i for r in members for i, t in enumerate(r.fields['_toks']) if SIGNED.match(t)}
        for r in members:
            toks = r.fields['_toks']
            if 'CH' not in r.fields and ch_share >= 0.5:
                r.add('INFO', 'BE_MISSING_CH', f'Cell height missing while {ch_share:.0%} of '
                                               f'{key[0]}_{key[1]} names carry it')
            r.corrected = be_hyphen(toks, r.fields['TEST'], signed_idx)
            sk = next((t for t in r.corrected.split('-')[1:] if ':' in t and not t.startswith('A:')), None)
            if sk:
                kind, mag = sk.split(':', 1)
                m = re.fullmatch(r'(REG|CD|ENCL)(OGD|PGD)', kind)
                d = m.group(2) if m else 'Z'
                new = f"{DEFAULT_SKEW_TYPE}:{key[0]}:{d}:{m.group(1) if m else kind}:{mag}"
                r.corrected = r.corrected.replace(f'-{sk}', f'-{new}', 1)
                r.add('WARN', 'SKEW_SCHEME', f"BE skew '{sk}' -> '{new}' (TYPE:LAYER:DIR:SKEW:MAG; Z = "
                                             'vertical/back-end)', True)
            parts = r.corrected.split('-')
            if 'NOM' in parts[1:]:
                parts[parts.index('NOM', 1)] = f'{DEFAULT_SKEW_TYPE}:NOM'
                r.corrected = '-'.join(parts)
        # uniform layout: core fields - skew field (A:.. / DR) - one ':'-joined additional field
        skew_at = lambda p: next((i for i, t in enumerate(p) if i and (t.startswith('A:') or t == 'DR')), None)
        idx = [skew_at(r.corrected.split('-')) for r in members]
        core = Counter(i for i in idx if i is not None).most_common(1)
        core = core[0][0] if core else None
        for r, si in zip(members, idx):
            parts = r.corrected.split('-')
            if core is None:
                continue
            if si is None:
                if len(parts) < core:
                    r.add('WARN', 'DELIM_COUNT', f'{len(parts)} fields, {key[0]}{key[1]} core has {core}')
                    continue
                parts.insert(core, f'{DEFAULT_SKEW_TYPE}:NOM')
                si = core
                r.add('WARN', 'NOM_MISSING', 'BE POR structure without skew carries A:NOM in the skew field', True)
            if si < core:
                r.add('WARN', 'DELIM_COUNT', f'{si} core fields before the skew, {key[0]}{key[1]} core has {core}')
                continue
            extras = parts[core:si] + parts[si + 1:]
            head = parts[0]
            if 'WID' in extras:
                extras.remove('WID')
                head = 'W' + head
                r.add('WARN', 'WID_PREFIX', "'-WID' removed; 'W' prefix added to the head", True)
            new = parts[1:core]
            new = [head] + new + [parts[si]] + ([':'.join(extras)] if extras else [])
            if len(extras) > 1 or si != core:
                r.add('WARN', 'BE_UNIFORM', f"Additional details {extras} placed after the skew field as one "
                                            "':' field (uniform field count)", True)
            r.corrected = '-'.join(new)

    # --- LLE rows: names whose additional descriptor type also occurs outside LLE rows carry ':LLE' ---
    def add_key(res):
        tok = res.fields.get('ADD', '').split('-')[0]
        return ':'.join(tok.split(':')[:2])

    in_lle, out_lle = set(), set()
    for name, (src, base, tc, res) in results.items():
        if src == 'Superflow' and res.family in ('PFT', 'MLR') and res.corrected is not None:
            rows = sf_info.loc[name].row.split('; ')
            (in_lle if all('LLE' in r for r in rows) else out_lle).add(add_key(res))
    shared = in_lle & out_lle
    for name, (src, base, tc, res) in results.items():
        if (src == 'Superflow' and res.family in ('PFT', 'MLR') and res.corrected is not None
                and all('LLE' in r for r in sf_info.loc[name].row.split('; ')) and add_key(res) in shared):
            head, sep, tail = res.corrected.partition('_')
            res.corrected = f'{head}:LLE{sep}{tail}'
            res.add('WARN', 'LLE_TAG', f"LLE-row structure without an LLE-only layout descriptor ({add_key(res)}) - "
                                       "':LLE' appended", True)

    # --- cross-reference checks ---
    for name, (src, base, tc, res) in results.items():
        fam = res.family
        if src == 'Superflow':
            info = sf_info.loc[name]
            pat = FAMILY_CFG.get(fam)
            if pat:
                bad = [c for c in info.cfgs if not re.match(pat, c)]
                if bad:
                    res.add('WARN', 'CFG_MISMATCH', f'Config(s) {bad} unexpected for {fam} family - '
                                                    'structure may be misnamed/misclassified')
            for row in info.row.split('; '):
                rp = row_polarity(row)
                sp = res.fields.get('POL', '')
                if fam in ('PFT', 'MLR', 'BITCELL', 'ROSPFT') and rp and sp in ('N', 'P') and rp != sp:
                    res.add('WARN', 'ROW_POLARITY', f'Structure polarity {sp} sits in {rp}-polarity row {row}')
            e = et_map.get(name)
            if e is None:
                res.add('ERROR', 'ETEST_MISSING', 'Not found in Etest STRUCTURE_NAME_AS_TESTED')
            else:
                if e.ROW_NAME not in info.row.split('; '):
                    res.add('WARN', 'ETEST_ROW', f'Etest row {e.ROW_NAME} differs from superflow row {info.row}')
                if (e.ATLANTIS_STRUCTURE_NAME or '').upper() + tc != name:
                    res.add('WARN', 'ATLANTIS_MISMATCH', f"Atlantis name '{e.ATLANTIS_STRUCTURE_NAME}' + TC "
                                                         'differs from STRUCTURE_NAME_AS_TESTED')
        if res.corrected is not None:
            full = res.corrected + tc
            if len(full) > MAX_NAME_LEN:
                res.add('ERROR', 'FIX_LEN', f'Corrected name is {len(full)} chars > {MAX_NAME_LEN}')
        if res.proposed and len(res.proposed + tc) > MAX_NAME_LEN:
            res.add('INFO', 'PROPOSED_LEN', f'Proposed name is {len(res.proposed + tc)} chars > {MAX_NAME_LEN}')

    # --- self-check: corrected names must re-parse to themselves ---
    for name, (src, base, tc, res) in results.items():
        if res.corrected is None or res.family in ('QREL', 'UNKNOWN'):
            continue
        again = parse(res.corrected).corrected
        if again != res.corrected:
            res.add('ERROR', 'NOT_IDEMPOTENT', f"Corrected name re-validates to '{again}' - rule conflict")

    # --- collisions after correction ---
    final = defaultdict(list)
    for name, (src, base, tc, res) in results.items():
        final[(res.corrected if res.corrected is not None else base) + tc].append(name)
    for tgt, origs in final.items():
        if len(origs) > 1:
            for o in origs:
                others = [x for x in origs if x != o]
                results[o][3].add('ERROR', 'COLLISION', f'Corrected name {tgt} collides with {others}')

    return results, sf_info, by_fam


def be_hyphen(toks: list[str], stype: str, signed_idx: set[int]) -> str:
    ch = next((t for t in toks if BE_CH.match(t)), '')
    start = 2 if stype else 1
    rest = []
    for i, t in enumerate(toks[start:], start=start):
        if t == ch:
            continue
        if t == 'NOM0':
            t = 'NOM'
        if i in signed_idx and re.fullmatch(r'[+-]?\d+(?:\.\d+)?', t):
            v = t.lstrip('+-').replace('.', 'D')
            t = 'NOM' if float(t) == 0 else ('N' if t[0] == '-' else 'P') + v
            if rest and re.search(r'[A-Z]', rest[-1]):
                rest[-1] += ':' + t
                continue
        rest.append(t)
    return '-'.join([toks[0] + stype + ch[:3]] + rest)


def row_polarity(row: str) -> str:
    ident = re.sub(r'^X81ZS?_', '', row)
    m = re.match(r'^(MNP|NP|MN|MP|N|P)(?=[A-Z])', ident)
    if not m or m.group(1) in ('MNP', 'NP'):
        return ''
    return m.group(1)[-1]


def status_of(res: Result, original_base: str) -> tuple[str, str]:
    open_issues = [i for i in res.issues if not i[3]]
    max_sev = max((i[0] for i in res.issues), key=lambda s: SEV_RANK[s], default='')
    if res.corrected is None or any(i[0] == 'ERROR' for i in open_issues):
        return 'MANUAL', max_sev
    if res.corrected != original_base:
        return 'AUTO-FIX', max_sev
    if any(i[0] == 'WARN' for i in open_issues):
        return 'REVIEW', max_sev
    return 'OK', max_sev


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------
def build_frames(results, sf_info, al, by_fam):
    recs, issues, spread = [], [], Counter()
    for name, (src, base, tc, res) in results.items():
        status, max_sev = status_of(res, base)
        info = sf_info.loc[name] if name in sf_info.index else None
        cand = res.corrected + tc if res.corrected is not None else ''
        corr_full, draft = ('', cand) if status == 'MANUAL' else (cand, '')
        rec = {
            'Source': src, 'Row': info.row if info is not None else '',
            'Configs': '; '.join(info.cfgs) if info is not None else '',
            'N_Tests': int(info.n_tests) if info is not None else 0,
            'Family': res.family, 'Template': TEMPLATES.get(res.family, ''),
            'Original_Name': name, 'Len': len(name), 'Status': status, 'Max_Severity': max_sev,
            'Issue_Codes': ', '.join(sorted({i[1] for i in res.issues if i[0] != 'INFO'})),
            'Issues': ' | '.join(f'[{i[0]}] {i[2]}' for i in res.issues),
            'Corrected_Name': corr_full, 'Corrected_Len': len(corr_full) if corr_full else '',
            'Changed': bool(corr_full) and corr_full != name,
            'Draft_Name_Manual': draft,
            'Proposed_Name': (res.proposed + tc) if res.proposed else '',
            'Highlight': ', '.join(sorted({i[1] for i in res.issues} & HIGHLIGHT_CODES)),
        }
        for c in GRID_COLS:
            rec[c] = res.fields.get(c, '')
        rec['TC'] = tc
        recs.append(rec)
        for sev, code, msg, fixed in res.issues:
            issues.append({'Original_Name': name, 'Family': res.family, 'Severity': sev, 'Code': code,
                           'Fixed_By_Correction': fixed, 'Message': msg, 'Corrected_Name': corr_full})
        for c in GRID_COLS:
            v = res.fields.get(c, '')
            if v != '' and c not in ('ADD', 'SKEW', 'INST'):
                spread[(res.family, c, str(v))] += 1

    st = pd.DataFrame(recs)
    sev_order = {'MANUAL': 0, 'AUTO-FIX': 1, 'REVIEW': 2, 'OK': 3}
    st = st.sort_values(['Family', 'Status', 'Original_Name'],
                        key=lambda s: s.map(sev_order) if s.name == 'Status' else s).reset_index(drop=True)
    iss = pd.DataFrame(issues)
    fs = pd.DataFrame([{'Family': f, 'Field': c, 'Value': v, 'Count': n} for (f, c, v), n in spread.items()])
    fs = fs.sort_values(['Family', 'Field', 'Count'], ascending=[True, True, False])

    summ = st.pivot_table(index=['Source', 'Family'], columns='Status', values='Original_Name',
                          aggfunc='count', fill_value=0)
    summ['Total'] = summ.sum(axis=1)
    summ = summ.reset_index()
    codes = iss.pivot_table(index=['Severity', 'Code'], columns='Family', values='Original_Name',
                            aggfunc='count', fill_value=0)
    codes['Total'] = codes.sum(axis=1)
    codes = codes.reset_index().sort_values(['Severity', 'Total'], ascending=[True, False],
                                            key=lambda s: s.map(SEV_RANK).mul(-1) if s.name == 'Severity' else s)

    coll = iss[iss.Code == 'COLLISION'][['Original_Name', 'Family', 'Corrected_Name', 'Message']]

    rows = []
    sf_rows = sf_info.reset_index().assign(row=lambda d: d.row.str.split('; ')).explode('row')
    fam_of = {n: r[3].family for n, r in results.items()}
    for row, grp in sf_rows.groupby('row'):
        rbase = re.sub(r'_TC\d+[A-Z]?_\d+$', '', row)
        rec = {'Row': row, 'Row_Base': rbase, 'Row_Base_Len': len(rbase),
               'Len_OK': len(rbase) <= MAX_ROW_LEN, 'N_Structures': len(grp),
               'Families': ', '.join(sorted({fam_of[s] for s in grp.struct}))}
        if al is not None:
            a = al[al.NewTestRowAlias == row]
            rec['In_Aliasing'] = not a.empty
            rec['Alias_Changed'] = bool((a.CurrentTestRowAlias != a.NewTestRowAlias).any()) if not a.empty else ''
        rows.append(rec)
    rowdf = pd.DataFrame(rows)
    return st, iss, fs, summ, codes, coll, rowdf


RECOMMENDATIONS = [
    ('Field order', 'VT is placed after LEGS/#ADF per the slide templates (CH-DIFF-LEGS-VT). Slide 5 speaker note '
                    '"Move VT after diff width" is superseded (WW43 decision).'),
    ('VT code', 'Single-letter metal-gate codes (H/S/L/U/UL/E) everywhere, including ROS, ISO and QnR names that '
                'still use HVT/LVT/ULVTLL. Slide 5 symbol table updated.'),
    ('Fixed slot grid', 'Logic families share one slot index: 0=[W][M]CAT+CH 1=DIFF 2=LEGS/#ADF 3=VT 4=ENV/STYLE/DUT '
                        '5=NEST/STG 6=ISO/FO 7=SH/DH 8=NOM|skew 9+=ADD (ROS/ROSPFT carry M# right after the NOM/skew field where needed; '
                        'needed; PFT/MLR/BITCELL carry no metal routing). MLR is padded with 0X0-IXI-SH; PFT/ROSPFT '
                        'default 0X0/IXI.'),
    ('NOM', "POR structures (no ':' skew and no variant token such as NODBI/NOIMPLANT/HV/5T/nnnnG/GNAC/NOVCR or ADT "
            'STK/SHRD/SHRS) carry A:NOM (lego type A) as the first additional token. POR/REF/SDR -> NOM; ISO SDR '
            'layer -> NOM; DR -> NOM only after per-structure approval in DR_to_NOM_Decisions.csv.'),
    ('TSDR skew', "Every family uses the ISO grammar TYPE:LAYER:DIR:SKEW:MAG[:extra]. TYPE defaults to 'A'; DIR from "
                  'layer when absent (PXL=PGD, PLY/TCN/FTI=OGD); merged PLYCDPP/PLYCD/TCNCD split; decimals as D '
                  '(N1D2); VCRNUB OGD CD absolute -> relative to 44 nm (36 -> N8); KLV details and BE magnitudes use '
                  "':' (CD:10, NOM:ENC:N2, VCRM0B:P11, CD:P1)."),
    ('Polarity position', 'Logic families carry polarity in the category (NS, PM, ROSN); ISO carries it with the '
                          'diffusion (NZ5). KLV mixed both (NDGATEKLV132-Z5 vs VGKLV132-NZ5) - corrected to ISO '
                          'layout.'),
    ('Diffusion code', 'Use Z codes everywhere (ISO DIFFOGD/GATEGATE used numeric widths 27/31/46); slide 7 Z vs width '
                       'table still has "?" entries - close those before tape-in.'),
    ('Underscore', "'_' is reserved for seat/row instance. Inline underscores (CMOS_A, NDBI_SDR, 2TRK_1PP, QCNP "
                   "fields, BE names) break '_' splitting in CNAI/SLR. Use ':' for ISO detail chunks and '-' for fields."),
    ('Environment qualifiers', "'/' is allowed only inside the environment field: '/D' = no diffusion (replaces "
                               "CMOS/UNI), 'CMOS/NP' / 'CMOS/PP' = matching pair, 'ADTUNI' = ADT environment."),
    ('Matching / WID', "Matching pairs: head M{N|P}S + ENV '{env}/NP|PP'. WID: 'W' prefix on the head (W before M), "
                       "'-WID' suffix removed (WMNS132-..., WROSN132-..., WSRN192-..., WNM132-...)."),
    ('FK structures', 'Full-KTCN re-expressed in PFT style: {N|P}S{CH}-{Z}-1L-{VT}-CMOS-1X1-IXI-SH-'
                      'FKTCN:OGD:CD:{MAG}:44PP (no point-count token).'),
    ('PLY/TCN skews', 'One vocabulary across ISO/PFT/MLR: PLYFTCN = PLY CD skew, TCN fixed, PP = 44+MAG (ISO PLYFTCN, '
                      'PFT PLY:..:43PP, MLR PLYCDPP); PLYFPP = PLY only at fixed 44PP, TCN nominal (PFT PLY:..:44PP, '
                      'MLR PLYCD); PLYITCN = PLY skew with inverse TCN skew at fixed 44PP (PFT PCD:..:TCD:.., MLR '
                      'PLYiTCN); TCNFPP = TCN only at fixed 44PP (PFT TCN:..:44PP, MLR TCNCD). ISO/KLV TCN stays '
                      'A:TCN:OGD:CD. Pitch is implied by the layer code to stay within 64 chars.'),
    ('BE uniform layout', 'Per {VIA}{TEST} group: core fields (mode count) - skew field (A:.. / A:NOM / DR) - one '
                          "':'-joined additional field. Extra tokens (CUT, PWR, DEN, WL, TOP, NUB:0, 1VCR) moved "
                          "after the skew; '-WID' -> W head prefix."),
    ('BE families', "Scheme {VIA}{TEST}{CH}-{LAYERS}-{W/S}-{W/S}-{DIM}-{SKEW|NOM}-{N|P|NOM}{MAG}. TEST (KLV, MV, "
                    "VCHN, ECD, SRP, ...) is kept and joined to the category exactly like the front-end KLV family "
                    "(VT_KLV_..._132H -> VTKLV132-..., cf. VTKLV132-NZ5-L-...). '/' kept in layer pairs and W/S."),
    ('QnR', 'Corrected_Name only fixes delimiters/VT; Proposed_Name is a best-effort BKM ordering pending a decoder '
            'for geometry/style/topology codes.'),
    ('Length budget', f'Hard limit {MAX_NAME_LEN} chars incl. _TCxx_y (7-8 chars). Keep base names <= 56 to survive '
                      'TC11B-style seat names; see Corrected_Len.'),
    ('Library tables', 'Bitcell diffusion widths in names (e.g. RFN315-42-PG, RFN315-54-RP) differ from slide 8; '
                       'confirm against the PDK and update whichever is stale.'),
]

# What each issue code means and how the validator resolves it (used in the report)
CODE_DOC = {
    'ORDER': ('Fields out of BKM order (mostly VT before LEGS/#ADF)', 'Re-ordered to the BKM slot order'),
    'VT_FORMAT': ('Full VT symbol (LVT, ULVTLL, ...) instead of the metal-gate code', 'Mapped to H/S/L/U/UL/E'),
    'VT_SPLIT': ("VT split across a delimiter ('ULVT-LL'), shifting every later field", "Joined -> 'UL'"),
    'VT_DEFAULT': ('VT field missing (VTKLV)', "'L' inserted (VTKLV structures are LVT per owner) - highlighted"),
    'SKEW_CLEANUP': ('Non-standard PFT/MLR skew tokens (+5, 10.4, trailing REF/POR, PCD:..:TCD:.., PLYEC, PLYITCN)',
                     'Sign/decimal fixed, REF/POR/0 -> NOM, PCD/TCD inverse pair -> A:PLYITCN:OGD:CD:{mag}, '
                     'PLYEC -> A:PLY:OGD:EC, PLYITCN -> A:PLYITCN:OGD:CD'),
    'LEGO_PREFIX': ('Colon descriptor / skew without lego type (VTMX:CRL:H)', "Lego type prefixed (A:VTMX:CRL:H)"),
    'NOM_MISSING': ('POR structure without TSDR skew lacks NOM', 'A:NOM inserted as first additional token'),
    'NOM_LEGO': ('NOM without lego type', "Written TYPE:NOM (A:NOM) - lego type first, as in skews"),
    'ISO_DIR_PENDING': ('ISO POR skew has no direction / skew type and no same-device neighbour',
                        'Pending ISO_Skew_Direction_Decisions.csv - highlighted'),
    'SKEW_DIR_SET': ('ISO POR skew without direction (A:FTIML:SDR)',
                     'POR reference of its series: all DIR / SKEW used by the same device+layer in the row '
                     '(e.g. OGD/PGD:CD/REG) - highlighted'),
    'NOM_POSITION': ('NOM not in first additional position', 'Moved to first additional position'),
    'POR_TO_NOM': ("POR synonyms 'POR' / 'REF' / 'SDR' in the additional section", "Replaced with 'NOM'"),
    'SDR_TO_NOM': ("ISO/KLV 'SDR' (and approved 'DR') used for POR", "Replaced with 'NOM'"),
    'DR_PENDING': ("'DR' token whose NOM equivalence is not confirmed", 'Kept as DR; listed in DR_to_NOM_Decisions.csv'),
    'DR_KEPT': ("'DR' rejected for NOM conversion", 'Kept as DR'),
    'DEFAULT_FILL': ('Nesting / isolation / layout height not specified', '0X0 / IXI / SH inserted for grid alignment'),
    'ROUTING_REMOVED': ('Metal routing (M1/M2) in a PFT, MLR or BITCELL name', 'Removed (kept only for ROS/ROSPFT) '
                                                                               '- highlighted'),
    'LLE_TAG': ('LLE-row structure without an LLE-only layout descriptor (A:NOM, A:PXL are shared with non-LLE rows)',
                "':LLE' appended (local layout effects) - highlighted"),
    'ADD_FOLD': ('MLR variant tokens in two fields (NODBI-NOIMPLANT)', "Joined into one ':' field"),
    'MATCHING_PREFIX': ("Matching pair used NP/PP head", "Head M{N|P}S; pair moved to environment (CMOS/NP, CMOS/PP)"),
    'WID_PREFIX': ("WID structures used a '-WID' suffix", "Suffix removed; 'W' prefix added to the head"),
    'NODIFF_ENV': ("'/D' (no diffusion) coexisted with CMOS/UNI", "'/D' kept as the environment, CMOS/UNI dropped"),
    'ADT_ENV': ("ADT structures used UNI environment plus 'ADT:' additional prefix", "Environment 'ADTUNI'; 'ADT:' "
                                                                                     'prefix removed'),
    'DUP_FIELD': ('Same field given twice (ADT VT repeated)', 'Identical repeat dropped; conflicting repeat = manual'),
    'SKEW_SCHEME': ('TSDR skew not in TYPE:LAYER:DIR:SKEW:MAG form (no TYPE, merged PLYCDPP/TCNCD, decimal N1.2, '
                    'KLV CD10/SDRENCN2, BE CD-P1)', "Rewritten to TYPE:LAYER:DIR:SKEW:MAG (TYPE 'A' when absent, "
                                                    'DIR from layer, decimal as D, VCRNUB CD relative to 44 nm)'),
    'SKEW_SPLIT': ("Skew split by '-' (FTI-CD-OGD:N1)", "Joined with ':' and re-ordered (A:FTI:OGD:CD:N1)"),
    'SKEW_ABS': ('Unsigned absolute skew magnitude (VCR:PGD:CD:10)', 'Flagged - nominal VCR CD needed for N/P form'),
    'SKEW_DIR_MISSING': ('Skew without direction and no layer rule', 'Flagged'),
    'CAT_CH_SPLIT': ("Extra '-' between category and cell height (NM-132)", 'Joined (NM132)'),
    'NONBKM_TOKEN_POS': ('Non-BKM token between core fields', 'Moved to the additional section'),
    'UNDERSCORE_IN_BODY': ("'_' inside a field (CMOS_A, NDBI_SDR, 2TRK_1PP, GLK_OGD_DEN)",
                           "Moved to end / converted to ':' chunks / concatenated; qualifiers (NDBI) as trailing chunk"),
    'UNDERSCORE_DELIM': ("'_' used as field delimiter (QCNP)", "Replaced with '-'"),
    'EXTRA_FIELDS': ('ISO skew split over several hyphen fields (DIFFCHN LEN.., A-..)',
                     "Folded into one ':' skew; extra detail as trailing ':' chunks"),
    'DIFF_NUMERIC': ('ISO diffusion given as width (N27, P46)', 'Converted to Z code via slide 7 (NZ1, PZ5)'),
    'POL_POSITION': ('KLV polarity in the category (NDGATEKLV132-Z5)', 'Moved to the diffusion field (GATEKLV132-NDZ5)'),
    'FK_TO_PFT': ('Full-KTCN names followed no BKM template', 'Re-expressed in PFT style (1L, CMOS, 1X1, IXI, SH, '
                                                              'FKTCN skew layer, 44PP)'),
    'BE_SCHEME': ("BE names used '_' as field delimiter, CH at the end and signed magnitudes",
                  "'-' delimiter, {VIA}{TEST}{CH} head, +n/-n/0 -> Pn/Nn/NOM"),
    'BE_CH_FORMAT': ("BE cell height written NNNCH", "Normalised to NNN (head)"),
    'BE_LAYER_DELIM': ("BE layer pair written with '_' (M1_M2C)", "Written with '/' (M1/M2C)"),
    'SKEW_FORMAT': ('ISO skew has fewer than 5 chunks', 'Flagged for review (not auto-changed)'),
    'DELIM_COUNT': ('Field count differs from family majority', 'BE: uniform layout applied; QnR: info until '
                                                                'decoder'),
    'BE_UNIFORM': ('BE extra tokens between/after core fields gave a different field count',
                   "Moved after the skew field as one ':'-joined field"),
    'PLY_TCN_VOCAB': ('PLY/TCN CD skews written several ways (PLY:..:43PP, PLYCDPP, PCD:..:TCD, PLYCD, TCNCD)',
                      'Mapped to PLYFTCN / PLYFPP / PLYITCN / TCNFPP'),
    'PLY_PITCH': ('PLY pitch is not 44+MAG', 'Flagged'),
    'LIB_BITCELL_DIFF': ('Bitcell diffusion width differs from slide 8 table', 'Flagged - verify against PDK'),
    'LIB_CH_Z': ('CH/Z combination not in slide 7 library', 'Flagged'),
    'COLLISION': ('Two different names correct to the same name', 'Manual'),
    'MISSING_FIELD': ('Mandatory field missing', 'Manual'),
    'LEN_LIMIT': ('Name longer than 64 chars incl. _TCxx_y', 'Manual'),
    'FIX_LEN': ('Corrected name longer than 64 chars', 'Manual'),
    'CHARSET': ('Disallowed characters (space, comma, lower case, ...)', 'Manual'),
    'SLASH': ("'/' outside the environment field", 'Manual'),
    'UNKNOWN_FAMILY': ('Name matches no BKM family', 'Manual'),
}


def dr_review(results) -> pd.DataFrame:
    """One row per superflow structure (base name) containing a DR token."""
    finals = {(r.corrected if r.corrected is not None else b) + tc for _, (s, b, tc, r) in results.items()}
    rows = defaultdict(lambda: {'tcs': []})
    for name, (src, base, tc, res) in results.items():
        if src != 'Superflow' or not res.fields.get('_dr') or res.corrected is None:
            continue
        cand = re.sub(r'(?<=[-:])DR(?=$|[-:_])', 'NOM', res.corrected)
        if res.family == 'KLV':
            cand = re.sub(r'(?<=-)NOM(?=$|_)', f'{DEFAULT_SKEW_TYPE}:NOM', cand)
        rec = rows[base]
        rec.update(Family=res.family, Original_Base=base, Corrected_Keep_DR=res.corrected, Corrected_If_NOM=cand)
        rec['tcs'].append(tc)
        if cand != res.corrected and cand + tc in finals:
            rec['Conflict'] = f'{cand}{tc} already exists'
    out = []
    for base, rec in sorted(rows.items()):
        tcs = rec.pop('tcs')
        out.append({**rec, 'Instances': ' '.join(sorted(tcs)), 'Conflict': rec.get('Conflict', ''),
                    'Approve_Y_N': DR_DECISIONS.get(base, '')})
    cols = ['Family', 'Original_Base', 'Instances', 'Corrected_Keep_DR', 'Corrected_If_NOM', 'Conflict', 'Approve_Y_N']
    return pd.DataFrame(out, columns=cols)


def load_dr_decisions(path: Path):
    if path.exists():
        d = pd.read_csv(path, dtype=str, keep_default_na=False)
        DR_DECISIONS.update({b: v.strip().upper()[:1] for b, v in zip(d.Original_Base, d.Approve_Y_N) if v.strip()})


def load_iso_dir_decisions(path: Path):
    if path.exists():
        d = pd.read_csv(path, dtype=str, keep_default_na=False)
        for r in d.itertuples(index=False):
            ISO_DIR_DECISIONS[r.Original_Base] = {'DIR': r.DIR.strip().upper(), 'SKEW': r.SKEW.strip().upper(),
                                                  'Approve': r.Approve_Y_N.strip().upper()[:1]}


def iso_dir_review(results, sf_info) -> pd.DataFrame:
    """POR ISO structures whose skew lacks direction/skew type, with the skews used by their row neighbours."""
    by_row = defaultdict(Counter)
    for name, (src, base, tc, res) in results.items():
        if src == 'Superflow' and res.fields.get('SKEW_DIR'):
            k = f"{res.fields['SKEW_LAYER']}:{res.fields['SKEW_DIR']}:{res.fields['SKEW_KIND']}"
            for row in sf_info.loc[name].row.split('; '):
                by_row[row][k] += 1
    rows = {}
    for name, (src, base, tc, res) in results.items():
        if src != 'Superflow' or not res.fields.get('_isodir'):
            continue
        row = sf_info.loc[name].row
        rec = rows.setdefault(base, {'Row': set(), 'Instances': [], 'Original_Base': base, 'Current': res.corrected})
        rec['Row'].update(row.split('; '))
        rec['Instances'].append(tc)
    out = []
    for base, rec in sorted(rows.items()):
        dec = ISO_DIR_DECISIONS.get(base, {})
        sib = Counter()
        for r in rec['Row']:
            sib.update(by_row[r])
        nb = NEIGHBOUR_DIR.get(base)
        if dec.get('Approve') == 'Y' and dec.get('DIR') and dec.get('SKEW'):
            d, s, srcn = dec['DIR'], dec['SKEW'], 'override (Approve_Y_N = Y)'
        elif nb and dec.get('Approve') != 'N':
            d, s, srcn = nb[0], nb[1], 'device series in row (auto)'
        else:
            d, s, srcn = '', '', 'pending'
        out.append({'Row': ' '.join(sorted(rec['Row'])), 'Original_Base': base,
                    'Instances': ' '.join(sorted(rec['Instances'])), 'Current_Corrected': rec['Current'],
                    'Row_Sibling_Skews': ', '.join(f'{k} x{v}' for k, v in sorted(sib.items())),
                    'Basis': nb[2] if nb else '', 'Source': srcn, 'DIR': d, 'SKEW': s,
                    'Approve_Y_N': dec.get('Approve', '')})
    return pd.DataFrame(out, columns=['Row', 'Original_Base', 'Instances', 'Current_Corrected', 'Row_Sibling_Skews',
                                      'Basis', 'Source', 'DIR', 'SKEW', 'Approve_Y_N'])


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ['| ' + ' | '.join(map(str, cols)) + ' |', '|' + '---|' * len(cols)]
    for r in df.itertuples(index=False):
        lines.append('| ' + ' | '.join(str(v).replace('|', '\\|') for v in r) + ' |')
    return '\n'.join(lines)


SUMMARY_THEMES = [
    ('Field order, VT and diffusion', ['ORDER', 'VT_FORMAT', 'VT_SPLIT', 'VT_DEFAULT', 'DUP_FIELD', 'CAT_CH_SPLIT',
                                       'POL_POSITION', 'DIFF_NUMERIC']),
    ('Delimiters and default padding', ['UNDERSCORE_IN_BODY', 'UNDERSCORE_DELIM', 'DEFAULT_FILL', 'ROUTING_REMOVED', 'LLE_TAG',
                                        'EXTRA_FIELDS', 'NONBKM_TOKEN_POS']),
    ('NOM / POR / DR', ['NOM_LEGO', 'NOM_MISSING', 'NOM_POSITION', 'POR_TO_NOM', 'SDR_TO_NOM', 'LEGO_PREFIX',
                        'DR_PENDING', 'DR_KEPT']),
    ('TSDR skew grammar (incl. PLY/TCN)', ['SKEW_SCHEME', 'SKEW_CLEANUP', 'SKEW_SPLIT', 'SKEW_DIR_SET',
                                           'ISO_DIR_RULE', 'ISO_DIR_PENDING', 'PLY_TCN_VOCAB']),
    ('Environment, matching, WID, ADT, FK', ['MATCHING_PREFIX', 'WID_PREFIX', 'NODIFF_ENV', 'ADT_ENV', 'FK_TO_PFT']),
    ('Back-end and QnR layout', ['BE_SCHEME', 'BE_CH_FORMAT', 'BE_LAYER_DELIM', 'BE_UNIFORM', 'BE_MISSING_CH',
                                 'DELIM_COUNT']),
]


def write_report(path: Path, st: pd.DataFrame, iss: pd.DataFrame, drdf: pd.DataFrame, isodf: pd.DataFrame):
    """Short summary; per-name detail lives in the workbook and the decision CSVs."""
    sf = st[st.Source == 'Superflow']
    sfi = iss[iss.Original_Name.isin(set(sf.Original_Name))]
    final = sf.Corrected_Name.where(sf.Corrected_Name != '', sf.Draft_Name_Manual)
    status = sf.Status.value_counts()
    L = ['# X81Z Structure Name Validation - Summary', '',
         f'Superflow names: **{len(sf)}** ({sf.Original_Name.str.replace(r"_TC.*", "", regex=True).nunique()} '
         f'unique bases). Etest-report-only names (not corrected here): {int((st.Source != "Superflow").sum())}. '
         f'Detail: `{DEFAULT_OUT.name}`.', '',
         '## Result', '',
         f'- Auto-fixed **{status.get("AUTO-FIX", 0)}**, already compliant **{status.get("OK", 0)}**, '
         f'manual **{status.get("MANUAL", 0)}**',
         f'- Duplicates after correction: **{int(final.duplicated().sum())}**; longest name '
         f'**{int(final.str.len().max())}** of {MAX_NAME_LEN} characters', '',
         '## What changed', '', '| Theme | Names | Example |', '|---|---|---|']
    seen = set()
    for theme, codes in SUMMARY_THEMES:
        g = sfi[sfi.Code.isin(codes)]
        seen |= set(codes)
        if g.empty:
            continue
        ex = g.sort_values('Original_Name').iloc[0]
        L.append(f'| {theme} | {g.Original_Name.nunique()} | `{ex.Original_Name}` -> `{ex.Corrected_Name}` |')
    other = sfi[~sfi.Code.isin(seen) & (sfi.Severity != 'INFO')]
    if len(other):
        L.append(f'| Other ({", ".join(sorted(other.Code.unique()))}) | {other.Original_Name.nunique()} | |')
    L += ['', '## Per family', '']
    fam = sf.pivot_table(index='Family', columns='Status', values='Original_Name', aggfunc='count', fill_value=0)
    fam['Total'] = fam.sum(axis=1)
    L += [_md_table(fam.reset_index()), '', '## Open items', '',
          f'- DR -> NOM: **{len(drdf)}** structures (BE/KLV) keep DR until `Approve_Y_N` = Y in `{DR_FILE.name}`; '
          f'{int((drdf.Conflict != "").sum())} would collide with an existing NOM name',
          f'- ISO POR skews given a direction from their device series: **{len(isodf)}** '
          f'(`{ISO_DIR_FILE.name}`)',
          f'- Highlighted changes to confirm: **{int((sf.Highlight != "").sum())}** (yellow rows in the Structures '
          'sheet)',
          f'- Bitcell widths differing from BKM slide 8: **{int(sf.Issue_Codes.str.contains("LIB_BITCELL_DIFF").sum())}**'
          ' (kept)',
          '- QnR names are a draft until the code decoder is available', '',
          '## Files', '',
          f'- `{DEFAULT_OUT.name}` / `.csv`: every name with original, corrected, status and issue codes',
          f'- `{DR_FILE.name}`, `{ISO_DIR_FILE.name}`: decisions to approve',
          '- `StructureNameNomenclature_WW43_rev_DeviceBKMs.pptx`: revised BKM, changes highlighted', '']
    path.write_text('\n'.join(L), encoding='utf-8')

def write_excel(out: Path, frames: dict[str, pd.DataFrame]):
    from openpyxl.styles import PatternFill
    yellow = PatternFill('solid', fgColor='FFFF00')
    with pd.ExcelWriter(out, engine='openpyxl') as xw:
        for sheet, df in frames.items():
            df.to_excel(xw, sheet_name=sheet, index=False)
            ws = xw.sheets[sheet]
            ws.freeze_panes = 'A2'
            if len(df):
                ws.auto_filter.ref = ws.dimensions
            for i, col in enumerate(df.columns, start=1):
                sample = df[col].astype(str).head(500)
                width = min(max([len(str(col))] + [len(s) for s in sample]) + 2, 80)
                ws.column_dimensions[ws.cell(1, i).column_letter].width = width
            if 'Highlight' in df.columns:
                for r, flag in enumerate(df['Highlight'], start=2):
                    if flag:
                        for c in range(1, len(df.columns) + 1):
                            ws.cell(r, c).fill = yellow


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--superflow', type=Path, default=DEFAULT_SUPERFLOW)
    ap.add_argument('--etest', type=Path, default=DEFAULT_ETEST)
    ap.add_argument('--alias', type=Path, default=DEFAULT_ALIAS)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--superflow-only', action='store_true',
                    help='skip names that exist only in the Etest report')
    args = ap.parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)
    VALIDATE_DIR.mkdir(exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sf = load_superflow(args.superflow)
    et = load_etest(args.etest)
    al = None
    if args.alias.exists():
        al = pd.read_csv(args.alias, dtype=str, keep_default_na=False)
        al.columns = [c.strip() for c in al.columns]
    load_dr_decisions(DR_FILE)
    load_iso_dir_decisions(ISO_DIR_FILE)

    results, sf_info, by_fam = validate(sf, et, al, include_etest_only=not args.superflow_only)
    st, iss, fs, summ, codes, coll, rowdf = build_frames(results, sf_info, al, by_fam)
    drdf = dr_review(results)
    drdf.to_csv(DR_FILE, index=False)
    isodf = iso_dir_review(results, sf_info)
    isodf.to_csv(ISO_DIR_FILE, index=False)

    tmpl = pd.DataFrame([{'Family': f, 'Template': t, 'Slot_Order': '-'.join(SLOT_ORDER.get(f, []))}
                         for f, t in TEMPLATES.items()])
    rec = pd.DataFrame(RECOMMENDATIONS, columns=['Topic', 'Recommendation'])
    write_excel(args.out, {
        'Summary': summ, 'Issue_Codes': codes, 'Structures': st, 'Issues': iss, 'Field_Spread': fs,
        'Collisions': coll, 'DR_Review': drdf, 'ISO_Dir_Review': isodf, 'Rows': rowdf, 'Templates': tmpl,
        'Recommendations': rec,
    })
    st.to_csv(args.out.with_suffix('.csv'), index=False)
    write_report(REPORT_FILE, st, iss, drdf, isodf)

    print(f'Validated {len(st)} names ({(st.Source == "Superflow").sum()} superflow) -> {args.out}')
    print(summ.to_string(index=False))


if __name__ == '__main__':
    main()
