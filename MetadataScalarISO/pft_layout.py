"""XTRPFX (PFT) layout preview built with the supplied PFT GDS generator.

``Supporting Files for Visualization/PFT_LLM.bat`` embeds ``claude_gds_generator.py``, its trained
templates (learned from the x81zs PFT test-row GDS) and the PFT buildsheet/decoder tables. They are
unpacked once to ``extracted/pft``. A decoder row is taken from the bundled table (exact name) or decoded
from the structure name, the generator writes a GDS2 text file, and the preview draws that real geometry
with the colours of ``klayout_layer_map.lyp``. Coordinates use the ISO preview units (x: 44 nm poly
pitch, y: CH/2 diffusion track) so rulers and measurements work unchanged.
"""

from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import math
import pickle
import re
import shutil
import sys
import threading
import zipfile
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SUPPORT_DIR = BASE_DIR / "Supporting Files for Visualization"
PFT_BAT = SUPPORT_DIR / "PFT_LLM.bat"
LAYER_MAP_LYP = SUPPORT_DIR / "klayout_layer_map.lyp"
PFT_DIR = BASE_DIR / "extracted" / "pft"
_PAYLOADS = ("claude_gds_generator.py", "svrf_layer_mapping.csv", "trained_model.zip", "PFT_Mini.csv",
             "Decoder_Mini.csv")
_PICKLE_CLASSES = frozenset({"DesignTemplate", "LayerTemplate", "DeltaTransform", "Polygon"})

_NAME_RE = re.compile(r"^(?P<mos>[NP])S(?P<ch>110|132|165)-Z\d+-(?P<flavor>UL|U|L|S|H|E)-\d+L-"
                      r"(?P<pft>CMOS_A|CMOS|UNI)-(?P<nest>\d+X\d+)-", re.IGNORECASE)
_FLAVOR_MG = {"L": "LVT", "S": "SVT", "H": "HVT", "U": "ULVT", "UL": "ULVTLL", "E": "ELVT"}
_DEFAULT_DIFFWIDTH = {110: 27, 132: 46, 165: 62}

DB_PER_POLY = 440      # 44 nm poly pitch in 0.1 nm database units
POLY_X_ORIGIN_DB = 160

# (preview layer, base layers from svrf_layer_mapping.csv); drawing purpose only.
PFT_LAYER_GROUPS = (
    ("NDIFF", {"ndiff"}), ("PDIFF", {"pdiff"}), ("FIN", {"fin"}), ("PLY", {"poly"}), ("TCN", {"tcn"}),
    ("CTCN", {"ctcn"}), ("FTI", {"fti"}), ("VG", {"vg"}), ("VT", {"vt"}), ("VCR", {"vcr"}),
    ("VCRNUB", {"vcrnub"}), ("TRM", {"trm"}), ("M0", {"m0", "m0_color1", "m0_color2"}), ("V0", {"v0"}),
    ("M1", {"m1"}), ("V1", {"v1"}), ("M2", {"m2", "m2_color1", "m2_color2"}),
)
EXTRA_LAYERS = ("VT flavor IDs", "Dummy fill", "IDs / other")
PFT_LAYERS = (*(name for name, _bases in PFT_LAYER_GROUPS), *EXTRA_LAYERS, "Full cell", "Grid")
PFT_LAYERS_OFF = frozenset({"FIN", "CTCN", "TRM", "M0", "V0", "M1", "V1", "M2", "VT flavor IDs", "Dummy fill",
                            "IDs / other", "Full cell", "Grid"})
# Pin vias from the MOS_PFT buildsheet; coordinates calibrated on the generator's NS110/132/165 layouts.
PIN_FIELDS = (("GATE", "DrawGATE", "VG"), ("SS", "DrawSS", "VT"), ("SF", "DrawSF", "VT"), ("DS", "DrawDS", "VT"),
              ("DF", "DrawDF", "VT"), ("TOPSOURCE", "DrawTOPSOURCE", "VT"), ("TAP", "DrawTAP", "VT"))
PIN_COLORS = {"GATE": "#7f00ff", "SS": "#17becf", "SF": "#1f77b4", "DS": "#e377c2", "DF": "#d6006f",
              "TOPSOURCE": "#8c564b", "TAP": "#2e7d32"}
M0_OFFSETS_DB = {110: (0, 235, 445, 655, 865), 132: (0, 300, 540, 780, 1020), 165: (0, 350, 590, 825, 1060, 1300)}
VIA_SIZE_DB = {"VG": (120, 130), "VT": (120, 150)}
# Conductor links traced from the placed vias (VG/VT/VCRNUB), as in the ISO current-flow trace.
_LINKS = {"VG": ("PLY", "M0"), "VT": ("TCN", "M0"), "VCRNUB": ("TCN", "VCR"), "PLY": ("VG", "PLY"),
          "TCN": ("VT", "VCRNUB", "TCN"), "VCR": ("VCRNUB",), "M0": ("VG", "VT")}
_ACTIVE_LAYERS = frozenset({"PLY", "TCN", "VCR", "M0"})

_lock = threading.Lock()


def _field(row: dict[str, str], name: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", name.casefold())
    return next((str(value) for field, value in row.items()
                 if value and re.sub(r"[^a-z0-9]", "", str(field).casefold()) == key), "")


def is_pft_row(row: dict[str, str]) -> bool:
    """MOS_PFT structure (NS/PS PFTs, FKNS/FKPS KTCN, ...): PFT name grammar or PFT buildsheet fields."""
    return bool(_NAME_RE.match(row.get("StructureName", ""))) or bool(_field(row, "DiffLegoPattern")) \
        or bool(_field(row, "structureSize") and _field(row, "DrawGATE"))


def _ensure_assets() -> Path:
    model = PFT_DIR / "trained_model" / "gds_templates.pkl"
    if model.exists() and all((PFT_DIR / name).exists() for name in _PAYLOADS if not name.endswith(".zip")):
        return PFT_DIR
    if not PFT_BAT.exists():
        raise FileNotFoundError(f"PFT generator payload not found: {PFT_BAT}")
    staging = PFT_DIR.with_name("pft.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with PFT_BAT.open("rb") as handle:
        for line in handle:
            if not line.startswith(b"::DATA::"):
                continue
            name, _sep, data = line[8:].rstrip(b"\r\n").partition(b"|")
            if name.decode() in _PAYLOADS:
                (staging / name.decode()).write_bytes(base64.b64decode(data))
    missing = [name for name in _PAYLOADS if not (staging / name).exists()]
    if missing:
        raise FileNotFoundError(f"{PFT_BAT.name} is missing payload(s): {', '.join(missing)}")
    target = (staging / "trained_model").resolve()
    with zipfile.ZipFile(staging / "trained_model.zip") as archive:
        for member in archive.infolist():
            if not (target / member.filename).resolve().is_relative_to(target):
                raise ValueError(f"Unsafe path in trained_model.zip: {member.filename}")
        archive.extractall(target)
    (staging / "trained_model.zip").unlink()
    shutil.rmtree(PFT_DIR, ignore_errors=True)
    staging.rename(PFT_DIR)
    return PFT_DIR


@lru_cache(maxsize=1)
def _generator():
    """(generator module, loaded GDSGenerator, decoder rows by name, PFT buildsheet rows by name)."""
    folder = _ensure_assets()
    spec = importlib.util.spec_from_file_location("claude_gds_generator_pft", folder / "claude_gds_generator.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class _ModelUnpickler(pickle.Unpickler):
        def find_class(self, module_name: str, name: str):
            if module_name in ("__main__", "claude_gds_generator") and name in _PICKLE_CLASSES:
                return getattr(module, name)
            raise pickle.UnpicklingError(f"Unexpected class in PFT model: {module_name}.{name}")

    with contextlib.redirect_stdout(io.StringIO()):
        generator = module.GDSGenerator(module.LayerResolver(folder / "svrf_layer_mapping.csv"))
        with (folder / "trained_model" / "gds_templates.pkl").open("rb") as handle:
            data = _ModelUnpickler(handle).load()
    generator.templates, generator.deltas = data["templates"], data["deltas"]
    generator.design_stats = data.get("design_stats", {})

    def table(name: str) -> dict[str, dict[str, str]]:
        with (folder / name).open(newline="", encoding="utf-8-sig") as handle:
            return {row["StructureName"].strip(): row for row in csv.DictReader(handle) if row.get("StructureName")}

    return module, generator, table("Decoder_Mini.csv"), table("PFT_Mini.csv")


def pft_rows(row: dict[str, str]) -> tuple[dict[str, str], dict[str, str] | None]:
    """(decoder row, PFT buildsheet row) for the generator."""
    _module, _generator_obj, decoders, buildsheets = _generator()
    name = row.get("StructureName", "").strip()
    reference = buildsheets.get(name)
    columns = list(next(iter(buildsheets.values())).keys()) if buildsheets else []
    own = {column: _field(row, column) for column in columns}
    own["StructureName"] = name
    buildsheet = own if _field(row, "DiffLegoPattern") or _field(row, "FTITracks") else reference
    if name in decoders:
        return dict(decoders[name]), buildsheet
    match = _NAME_RE.match(name)
    loose = re.match(r"^(?:FK)?(?P<mos>[NP])S\d*-?(?P<ch>110|132|165)?-?Z\d+-(?P<flavor>UL|U|L|S|H|E)-", name, re.I)
    if not match and not (loose and _field(row, "DiffLegoPattern")):
        raise ValueError(f"'{name}' does not follow the PFT naming (e.g. NS132-Z5-L-1L-CMOS-0X1-IXI-SH-...)")
    if match:
        mos, flavor, pft, nest = match["mos"], match["flavor"], match["pft"], match["nest"]
        height = int(match["ch"])
    else:
        cell = re.search(r"110|132|165", _field(row, "CellHeight") or loose["ch"] or "")
        if not cell:
            raise ValueError(f"'{name}': no CellHeight for the PFT generator")
        height = int(cell[0])
        mos = (_field(row, "TYPE") or loose["mos"])[:1]
        mg = _field(row, "MOSVT Type").upper()
        flavor = next((key for key, value in _FLAVOR_MG.items() if value == mg), loose["flavor"])
        pft = next((token for token in ("CMOS_A", "UNI", "CMOS") if token in name.upper()), "CMOS")
        nest_match = re.search(r"-(\d+X\d+)-", name, re.I)
        nest = nest_match[1] if nest_match else "0X1"
    width = re.search(r"/([0-9.]+)\s*$", (buildsheet or {}).get("DiffLegoPattern", ""))
    stacked, skip = re.search(r"StkFTI(\d)", name, re.I), re.search(r"Skp(\d)", name, re.I)
    decoder = {
        "StructureName": name, "LayoutName": "", "MOS": mos.upper(), "CELLHEIGHT": str(height),
        "DIFFWIDTH": str(round(float(width[1]) * 1000)) if width else str(_DEFAULT_DIFFWIDTH[height]),
        "MG": _FLAVOR_MG[flavor.upper()], "PFTTYPE": pft.upper(), "MULTILEG": "1", "STACK": "1",
        "INSTANCE": "1", "NESTING": nest.upper(), "NESTTYPE": "IXI", "NEST_FTILENGTH": "FXF",
        "STACKEDFTI": stacked[1] if stacked else "0", "SKIPFTITRACK": skip[1] if skip else "0", "CELLTYPE": "SH",
        "FRONTSIDE_BACKSIDE": "FS",
    }
    for column in ("JOGL", "JOGR"):
        decoder[column] = decoder["DIFFWIDTH"]
    for column in ("DISTJOGL", "DISTJOGR", "FTIROOST_DKA", "FTIROOST_CRL", "DIFFROOST_DKA", "DIFFROOST_CRL",
                   "MG_DKA", "MG_CRL"):
        decoder[column] = "0"
    return decoder, buildsheet


def pft_gds(row: dict[str, str], out_dir: Path) -> Path:
    """GDS2 text of the XTRPFX structure (cached by its generator inputs) plus a KLayout ``.lyp``."""
    decoder, buildsheet = pft_rows(row)
    digest = hashlib.sha1(json.dumps([decoder, buildsheet], sort_keys=True).encode()).hexdigest()[:16]
    path = out_dir / f"pft_{digest}.txt"
    if not path.exists():
        _module, generator, _decoders, _buildsheets = _generator()
        out_dir.mkdir(parents=True, exist_ok=True)
        with _lock, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            generator.pft_by_name = {decoder["StructureName"]: buildsheet} if buildsheet else {}
            produced = generator.generate({**decoder, "LayoutName": path.name}, out_dir)
        if produced is None:
            raise RuntimeError(f"PFT generator has no template for CH{decoder['CELLHEIGHT']} {decoder['MOS']} "
                               f"{decoder['PFTTYPE']}")
    lyp = path.with_suffix(".lyp")
    if LAYER_MAP_LYP.exists() and not lyp.exists():
        shutil.copyfile(LAYER_MAP_LYP, lyp)
    return path


def _styles() -> dict[str, dict]:
    """Preview styles in the ISO palette; ``*_active`` entries are conductors on a traced net."""
    from scalar_snapshot import (C_FTI, C_M0, C_NDIFF, C_PATH, C_PDIFF, C_POLY, C_POLY_ACTIVE, C_TCN,
                                 C_TCN_ACTIVE, C_TRM, C_V0_DUMMY, C_VCR, C_VCR_EDGE, C_VCRNUB)

    def style(face: str, alpha: float, edge: str | None = None, width: float = 0.0, hatch: str | None = None):
        return {"facecolor": face, "alpha": alpha, "edgecolor": edge or "none", "linewidth": width, "hatch": hatch}

    return {
        "NDIFF": style(C_NDIFF, 0.9), "PDIFF": style(C_PDIFF, 0.9), "FIN": style("#bdbdbd", 0.3),
        "PLY": style(C_POLY, 0.28), "PLY_active": style(C_POLY_ACTIVE, 0.62),
        "TCN": style(C_TCN, 0.28), "TCN_active": style(C_TCN_ACTIVE, 0.62), "CTCN": style("#72a57a", 0.3),
        "FTI": style(C_FTI, 0.9), "VG": style("#4d4d4d", 1.0, "k", 0.6), "VT": style("#7f7f7f", 1.0, "k", 0.6),
        "VCR": style(C_VCR, 0.3, C_VCR_EDGE, 0.4), "VCR_active": style(C_VCR, 0.8, C_VCR_EDGE, 0.7),
        "VCRNUB": style(C_VCRNUB, 1.0, C_VCR_EDGE, 0.8), "TRM": style(C_TRM, 0.38),
        "M0": style(C_M0, 0.3), "M0_active": style(C_PATH, 0.6), "V0": style(C_V0_DUMMY, 0.9, "k", 0.3),
        "M1": style("#0088ff", 0.25), "V1": style("#00aaff", 0.9), "M2": style("#10d7d6", 0.2),
        "VT flavor IDs": style("#cc79a7", 0.12), "IDs / other": style("#999999", 0.1),
        "Dummy fill": style("#d9d9d9", 0.45, "#9e9e9e", 0.3, "////"),
    }


def _layer_group(resolver, layer: int, datatype: int) -> str:
    info = resolver.resolve(layer, datatype)
    base, purpose = info["base_layer"].lower(), info["purpose"].lower()
    if purpose == "fill":
        return "Dummy fill"
    if layer == 273:
        return "VT flavor IDs"
    if purpose == "drawing":
        for name, bases in PFT_LAYER_GROUPS:
            if base in bases:
                return name
    return "IDs / other"


def _bbox(coords: list[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs, ys = [x for x, _y in coords], [y for _x, y in coords]
    return min(xs), min(ys), max(xs), max(ys)


def _active_shapes(groups: dict[tuple[str, int, int], list]) -> set[tuple[str, int, int, int]]:
    """Conductors connected to a placed VG / VT / VCRNUB (touching boxes join), keyed (group, layer, dt, index)."""
    boxes: dict[str, list[tuple[tuple[str, int, int, int], tuple[int, int, int, int]]]] = {}
    for (group, layer, datatype), polys in groups.items():
        if group in _LINKS:
            boxes.setdefault(group, []).extend(((group, layer, datatype, index), _bbox(coords))
                                               for index, coords in enumerate(polys))
    seen = {key for group in ("VG", "VT", "VCRNUB") for key, _box in boxes.get(group, [])}
    stack = [(key, box) for group in ("VG", "VT", "VCRNUB") for key, box in boxes.get(group, [])]
    while stack:
        key, (x0, y0, x1, y1) = stack.pop()
        for other_group in _LINKS[key[0]]:
            for other, (a0, b0, a1, b1) in boxes.get(other_group, []):
                if other not in seen and a0 <= x1 and x0 <= a1 and b0 <= y1 and y0 <= b1:
                    seen.add(other)
                    stack.append((other, (a0, b0, a1, b1)))
    return seen


@lru_cache(maxsize=4)
def _parsed(path: str, mtime: float):
    module, generator, _decoders, _buildsheets = _generator()
    groups: dict[tuple[str, int, int], list[list[tuple[int, int]]]] = {}
    for polygon in module.parse_gds_text(Path(path)).polygons:
        key = (_layer_group(generator.resolver, polygon.layer, polygon.datatype), polygon.layer, polygon.datatype)
        groups.setdefault(key, []).append(polygon.coords)
    return groups


def _m0_y(track: int, height: int) -> int:
    offsets = M0_OFFSETS_DB[height]
    return (track // len(offsets)) * height * 10 + offsets[track % len(offsets)]


def _box(xc: float, yc: float, w: float, h: float) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = round(xc - w / 2), round(yc - h / 2), round(xc + w / 2), round(yc + h / 2)
    return [(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]


def buildsheet_pins(row: dict[str, str], height: int) -> list[tuple[str, str, list[tuple[int, int]]]]:
    """(net, via group, polygon) for DrawGATE/SS/SF/DS/DF/TOPSOURCE/TAP; ``~vcr`` pins connect through VCRNUB."""
    from ScalarCalculation import expand_axis

    pins = []
    for base, field, kind in PIN_FIELDS:
        for device, block in enumerate(_field(row, field).split("/"), 1):
            # MOS_PFT has terminals 1 and 2 only; further '/' blocks are more vias of terminal 2 (catalog GATE2).
            device = min(device, 2)
            for entry in block.split(";"):
                core = entry.split("|")[0].strip()
                if not core:
                    continue
                parts = [part.strip() for part in core.split("~")[0].split(",")]
                if len(parts) < 2:
                    continue
                width, size_y = VIA_SIZE_DB[kind]
                nudge = (10 if height < 165 else 5) if kind == "VG" else 0
                for x in expand_axis(parts[0], 200):
                    xc = POLY_X_ORIGIN_DB + 60 + DB_PER_POLY * x if kind == "VG" else DB_PER_POLY * (x + 1)
                    for track in expand_axis(parts[1], 400):
                        pins.append((f"{base}{device}", kind, _box(xc, _m0_y(track, height) + nudge, width, size_y)))
    return pins


def buildsheet_nubs(row: dict[str, str], height: int) -> list[list[tuple[int, int]]]:
    from ScalarCalculation import expand_axis

    nubs = []
    length = 84 if height == 110 else 134
    for entry in _field(row, "VcrnubPattern").split(";"):
        parts = [part.strip() for part in entry.split("|")[0].split(",")]
        if len(parts) < 2:
            continue
        side = -1 if len(parts) > 2 and parts[2].lower() == "bottom" else 1
        for x in expand_axis(parts[0], 200):
            for track in expand_axis(parts[1], 100):
                y = track * height * 10
                nubs.append(_box(DB_PER_POLY * (x + 1), y + side * length / 2, DB_PER_POLY, length))
    return nubs


def _fti_walls(row: dict[str, str]) -> list[int]:
    from ScalarCalculation import expand_axis

    first = _field(row, "FTITracks").split(";")[0].split(",")[0]
    return sorted(set(expand_axis(first, 400)))


def _device_row(row: dict[str, str]) -> int | None:
    """Reference diffusion track of the PolyMatrix / TcnMatrix (``46,12;3,3;...`` -> 12): the device row."""
    for name in ("PolyMatrix", "TcnMatrix"):
        match = re.match(r"\s*\d+\s*,\s*(\d+)\s*;", _field(row, name))
        if match:
            return int(match[1])
    return None


def merge_pin_cuts(groups: dict[tuple[str, int, int], list], pins: list, row: dict[str, str], height: int) -> int:
    """Remove PLY / TCN cuts between each buildsheet pin and the device row (PolyMatrix / TcnMatrix cut removal).

    The generator reuses a generic PFT template, so FKTCN-style Kelvin TCNs (SF1..SS1, DS1..DF1) and gates landed
    away from the device row come out cut; the catalog layouts show them continuous. Returns the bridges added.
    """
    device = _device_row(row)
    if device is None:
        return 0
    track = height * 5
    centre = (device + 0.5) * track
    added = 0
    for group, kind in (("TCN", "VT"), ("PLY", "VG")):
        segments = [(key, _bbox(coords)) for key, polys in groups.items() if key[0] == group for coords in polys]
        for net, pin_kind, poly in pins:
            if pin_kind != kind or net.startswith("TAP"):
                continue
            px0, py0, px1, py1 = _bbox(poly)
            xc, yc = (px0 + px1) / 2, (py0 + py1) / 2
            low, high = min(yc, centre), max(yc, centre)
            column = sorted((box[1], box[3], box, key) for key, box in segments
                            if box[0] <= xc <= box[2] and box[3] >= low and box[1] <= high)
            if not column:
                continue
            # A via sitting in a cut gap (e.g. on the CRL) needs the conductor extended under it.
            column = sorted(column + [(py0, py1, None, None)], key=lambda item: item[:2])
            _y0, top, top_box, top_key = column[0]
            for b_bottom, b_top, b_box, b_key in column[1:]:
                if b_bottom > top:
                    conductor, key = (top_box, top_key) if top_box else (b_box, b_key)
                    x0, x1 = conductor[0], conductor[2]
                    if top_box and b_box:
                        x0, x1 = max(top_box[0], b_box[0]), min(top_box[2], b_box[2])
                    bridge = [(x0, top), (x0, b_bottom), (x1, b_bottom), (x1, top), (x0, top)]
                    groups[key].append(bridge)
                    segments.append((key, (x0, top, x1, b_bottom)))
                    added += 1
                if b_top > top:
                    top = b_top
                    if b_box:
                        top_box, top_key = b_box, b_key
    return added


def apply_pft_skews(groups: dict[tuple[str, int, int], list], pins: list, row: dict[str, str]) -> list[str]:
    """Resize / shift PLY, TCN and FTI inside the FTI walls around the device by the buildsheet skews.

    The generator always draws POR widths (12 nm) at the 44 nm pitch, so PW-skew PFTs (PolyCD + PP, PolyCD /
    TCN CD, ``poly:TSDRn12A`` IDs, FTI CD / REG) would look identical to POR without this. Returns notes.
    """
    from scalar_snapshot import (POR_POLY_PITCH_NM, _skew_geometry, poly_pitch_skew, poly_skew_geometry,
                                 tcn_skew_geometry)

    device_pins = [_bbox(poly) for net, _kind, poly in pins if not net.startswith("TAP")]
    skews = {"PLY": poly_skew_geometry(row), "TCN": tcn_skew_geometry(row),
             "FTI": _skew_geometry(row, "FTICdMisregSkew")}
    pitch = poly_pitch_skew(row)
    if not device_pins or not (pitch or any(skews.values())):
        return []
    low = min(box[0] for box in device_pins)
    high = max(box[2] for box in device_pins)
    wall_boxes = [box for key, polys in groups.items() if key[0] == "FTI" for box in map(_bbox, polys)]
    walls = sorted({(box[0] + box[2]) / 2 for box in wall_boxes})
    left = max((x for x in walls if x < low), default=low - 4 * DB_PER_POLY)
    right = min((x for x in walls if x > high), default=high + 4 * DB_PER_POLY)
    # Vertical extent: the wall FTIs bounding the device plus the pins (the TSDR region, not the whole column).
    spans = [(box[1], box[3]) for box in wall_boxes if abs((box[0] + box[2]) / 2 - left) < 1
             or abs((box[0] + box[2]) / 2 - right) < 1] + [(box[1], box[3]) for box in device_pins]
    bottom, top_y = min(span[0] for span in spans), max(span[1] for span in spans)
    centre = (left + right) / 2
    scale = pitch[0] / POR_POLY_PITCH_NM if pitch else 1.0
    moved = 0
    for key, polys in groups.items():
        group = key[0]
        if group not in ("PLY", "TCN", "FTI", "VG", "VT", "CTCN"):
            continue
        for index, coords in enumerate(polys):
            x0, y0, x1, y1 = _bbox(coords)
            xc = (x0 + x1) / 2
            if not left - DB_PER_POLY / 2 <= xc <= right + DB_PER_POLY / 2 or x1 - x0 > 2 * DB_PER_POLY \
                    or y1 < bottom or y0 > top_y:
                continue
            half = (x1 - x0) / 2
            xc = centre + (xc - centre) * scale
            fixed_cd = pitch and group in ("PLY", "TCN")
            if fixed_cd:
                half = (pitch[1] if group == "PLY" else pitch[2]) * 5
            for direction, variant, delta_nm in skews.get(group, ()):
                if fixed_cd and variant == "CD" and direction == "OGD":
                    continue
                delta = delta_nm * 10
                if direction == "OGD" and variant == "CD":
                    half += delta / 2
                elif direction == "OGD" and variant == "REG":
                    xc += delta
                elif direction == "PGD" and variant == "CD":
                    y0, y1 = y0 - delta / 2, y1 + delta / 2
                elif direction == "PGD" and variant == "REG":
                    y0, y1 = y0 + delta, y1 + delta
            half = max(half, 5)
            polys[index] = [(xc - half, y0), (xc - half, y1), (xc + half, y1), (xc + half, y0), (xc - half, y0)]
            moved += 1
    notes = []
    if pitch:
        notes.append(f"poly pitch {pitch[0]:g} nm, poly CD {pitch[1]:g} nm, TCN CD {pitch[2]:g} nm")
    for group, items in skews.items():
        for direction, variant, delta_nm in items:
            if not (pitch and group in ("PLY", "TCN") and variant == "CD" and direction == "OGD"):
                notes.append(f"{group} {direction} {variant} {delta_nm:+g} nm")
    return [f"skews applied between the FTI walls around the device ({moved} shapes): " + "; ".join(notes)]


def active_window(groups: dict[tuple[str, int, int], list], pins: list, height: int
                  ) -> tuple[float, float, float, float] | None:
    """(x0, x1, y0, y1) in db around the buildsheet pins, the PLY / TCN they land on and VCRNUBs on those TCNs.

    Long M0 / VCR conductors and other nub groups are left out so the view stays on the device under test.
    """
    # The body tap sits far from the device; frame the transistor terminals only.
    pins = [pin for pin in pins if not pin[0].startswith("TAP")] or pins
    if not pins:
        return None

    def overlaps(a, b) -> bool:
        return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]

    pin_boxes = [_bbox(poly) for _net, _kind, poly in pins]
    landed = [box for (group, _l, _d), polys in groups.items() if group in ("PLY", "TCN")
              for box in map(_bbox, polys) if any(overlaps(box, pin) for pin in pin_boxes)]
    nubs = [box for (group, _l, _d), polys in groups.items() if group == "VCRNUB"
            for box in map(_bbox, polys) if any(overlaps(box, conductor) for conductor in landed)]
    boxes = pin_boxes + landed + nubs
    xs = [v for box in boxes for v in (box[0], box[2])]
    ys = [v for box in boxes for v in (box[1], box[3])]
    track = height * 5
    x0, x1 = min(xs) - 2 * DB_PER_POLY, max(xs) + 2 * DB_PER_POLY
    y0, y1 = min(ys) - track, max(ys) + track
    # At least 16 poly pitches x 4 diffusion tracks so a single device is still readable.
    grow_x, grow_y = max(0, 16 * DB_PER_POLY - (x1 - x0)) / 2, max(0, 4 * track - (y1 - y0)) / 2
    return x0 - grow_x, x1 + grow_x, y0 - grow_y, y1 + grow_y


def render_pft(row: dict[str, str], gds: Path, out_path: Path, visible_layers: set[str] | None = None,
               dpi: int = 120, scale: int = 1, panel: bool = False) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Patch

    from ScalarCalculation import m0_tracks_per_height
    from scalar_snapshot import _save_figure

    visible = set(PFT_LAYERS) - PFT_LAYERS_OFF if visible_layers is None else set(visible_layers)
    groups = dict(_parsed(str(gds), gds.stat().st_mtime))
    height_match = re.search(r"\d+", row.get("CellHeight", "") or str(pft_rows(row)[0]["CELLHEIGHT"]))
    height = int(height_match[0]) if height_match else 132
    pins = buildsheet_pins(row, height) if height in M0_OFFSETS_DB else []
    pin_nets: dict[tuple, str] = {}
    if pins:
        # The generator copies template vias; the buildsheet pins are authoritative for this structure.
        groups = {key: polys for key, polys in groups.items() if key[0] not in ("VG", "VT", "VCRNUB")}
        for net, kind, poly in pins:
            key = (kind, 32 if kind == "VG" else 31, 0)
            groups.setdefault(key, []).append(poly)
            pin_nets[(*key, len(groups[key]) - 1)] = net
        nubs = buildsheet_nubs(row, height)
        if nubs:
            groups[("VCRNUB", 510, 0)] = nubs
        groups = {key: list(polys) for key, polys in groups.items()}
        merge_pin_cuts(groups, pins, row, height)
        skew_notes = apply_pft_skews(groups, pins, row)
    else:
        skew_notes = []
    active = _active_shapes(groups)
    db_per_track = height * 5
    window = None if "Full cell" in visible else active_window(groups, pins, height)
    shown = [coords for (group, _l, _d), polys in groups.items() if group in visible for coords in polys] or \
        [coords for polys in groups.values() for coords in polys]
    xs = [x for coords in shown for x, _y in coords]
    ys = [y for coords in shown for _x, y in coords]
    x0_db = POLY_X_ORIGIN_DB - DB_PER_POLY * math.ceil((POLY_X_ORIGIN_DB - min(xs)) / DB_PER_POLY)
    y0_db = db_per_track * math.floor(min(ys) / db_per_track)
    width = (max(xs) - x0_db) / DB_PER_POLY
    cells = (max(ys) - y0_db) / db_per_track
    view = ((window[0] - x0_db) / DB_PER_POLY, (window[1] - x0_db) / DB_PER_POLY,
            (window[2] - y0_db) / db_per_track, (window[3] - y0_db) / db_per_track) if window else \
        (0, width, 0, cells)
    if window:
        view = (max(0.0, view[0]), min(width, view[1]), max(0.0, view[2]), min(cells, view[3]))
    view_w, view_h = view[1] - view[0], view[3] - view[2]

    fig_w = min(30.0, max(11.0, 0.11 * view_w + 4.0))
    fig = plt.figure(figsize=(fig_w, max(4.0, min(14.0, (fig_w - 3.5) * view_h / max(view_w, 1.0) + 1.0))))
    ax = fig.add_axes((0.04, 0.06, 0.8, 0.9))
    legend_ax = fig.add_axes((0.86, 0.06, 0.13, 0.9))
    legend_ax.axis("off")
    styles = _styles()
    _module, generator, _decoders, _buildsheets = _generator()
    order = {name: index for index, name in enumerate(PFT_LAYERS)}
    drawn: dict[str, list[str]] = {}
    for (group, layer, datatype), polys in sorted(groups.items(), key=lambda item: (order[item[0][0]], item[0][1:])):
        if group not in visible:
            continue
        split: dict[str, list] = {}
        for index, coords in enumerate(polys):
            net = pin_nets.get((group, layer, datatype, index))
            state = f"{group}:{net}" if net else \
                f"{group}_active" if group in _ACTIVE_LAYERS and (group, layer, datatype, index) in active else group
            split.setdefault(state, []).append(
                [((x - x0_db) / DB_PER_POLY, (y - y0_db) / db_per_track) for x, y in coords])
        for state, verts in split.items():
            net = state.partition(":")[2]
            style = dict(styles[group], facecolor=PIN_COLORS[net.rstrip("0123456789")]) if net else styles[state]
            collection = PolyCollection(verts, facecolors=style["facecolor"], edgecolors=style["edgecolor"],
                                        linewidths=style["linewidth"], alpha=style["alpha"],
                                        zorder=1 + order[group] * 0.1 + (0.05 if state != group else 0),
                                        gid="GDS" if group in EXTRA_LAYERS else "GDS:snap")
            if style["hatch"]:
                collection.set_hatch(style["hatch"])
            ax.add_collection(collection)
            drawn.setdefault(state, []).append(f"{generator.resolver.name(layer, datatype)} {layer}/{datatype}")
    legend = []
    for state, names in drawn.items():
        group, _sep, net = state.partition(":")
        if net:
            legend.append(Patch(facecolor=PIN_COLORS[net.rstrip("0123456789")], edgecolor="k", linewidth=0.6,
                                label=f"{net} pin {group} (buildsheet)"))
            continue
        group, _sep, suffix = state.partition("_")
        label = f"{group} active (traced net)" if suffix else \
            f"{group} passive / dummy" if group in _ACTIVE_LAYERS else group
        style = styles[state]
        legend.append(Patch(facecolor=style["facecolor"], edgecolor=style["edgecolor"], linewidth=style["linewidth"],
                            alpha=style["alpha"], hatch=style["hatch"],
                            label=f"{label}\n  {', '.join(dict.fromkeys(names))}"))
    ax.set_xlim(view[0], view[1])
    ax.set_ylim(view[2], view[3])
    footer = []
    if window:
        footer.append("Active area: buildsheet pins, the PLY / TCN they land on and their VCRNUBs; "
                      "tick 'Full cell' for the whole structure")
    footer.extend(skew_notes)
    if footer:
        fig.text(0.04, 0.005, "\n".join(footer), fontsize=8, color="#7b3f00", va="bottom")
    ax.set_aspect("equal", adjustable="box")
    if "Grid" in visible:
        ax.grid(True, color="#b8bec4", linewidth=0.4, alpha=0.7)
    else:
        ax.grid(False)
    if panel:
        ax.tick_params(labelbottom=False, labelleft=False)
    per_height = m0_tracks_per_height({"CellHeight": f"ch{height}"})
    fig._iso_ruler = {"x_unit0": 0, "x_glob0": 0, "y_unit0": 0, "y_glob0": 0, "m0_unit0": 0, "m0_glob0": 0,
                      "per_height": per_height, "coords": "unit", "x_max": width, "cells": cells,
                      "m0_max": int(per_height * cells / 2)}
    legend_ax.legend(handles=legend, loc="upper left", fontsize=7, frameon=False, borderaxespad=0)
    if panel:
        fig._iso_legend = (legend_ax, legend)
    _save_figure(fig, ax, out_path, dpi, True, {**row, "CellHeight": f"ch{height}"}, "pft", scale)
    plt.close(fig)
