"""Geometry-backed, source-linked advisory checks on the drawn unit cell."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math


@dataclass(frozen=True)
class Shape:
    layer: str
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryFinding:
    layer: str
    label: str
    message: str
    bounds: tuple[float, float, float, float]
    reference: str = ""
    plain: str = ""


def edge_gaps(first: Shape, second: Shape) -> tuple[float, float, float]:
    ax0, ay0, ax1, ay1 = first.bounds
    bx0, by0, bx1, by1 = second.bounds
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    return dx, dy, math.hypot(dx, dy)


def pair_bounds(first: Shape, second: Shape) -> tuple[float, float, float, float]:
    return (min(first.bounds[0], second.bounds[0]), min(first.bounds[1], second.bounds[1]),
            max(first.bounds[2], second.bounds[2]), max(first.bounds[3], second.bounds[3]))


@dataclass(frozen=True)
class GeometryReview:
    findings: tuple[GeometryFinding, ...]
    checks: tuple[str, ...]
    skipped: tuple[str, ...]


def unit_shapes(row: dict[str, str]) -> tuple[Shape, ...]:
    from matplotlib.colors import to_hex
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
    from ScalarCalculation import geometry_from_row, expand_axis
    from scalar_snapshot import (C_FTI, C_POLY, C_TCN, C_M0, C_M0_ALT, _cell_pitch_nm, draw_base, x_pitch_nm)
    from scalar_snapshot import _misc_segments

    figure = Figure()
    axes = figure.add_subplot()
    layers = {C_FTI: "FTI", C_POLY: "PLY", C_TCN: "TCN", C_M0: "M0", C_M0_ALT: "M0"}
    draw_base(axes, row, geometry_from_row(row), {"FTI", "PLY", "TCN", "M0 lines", "VG", "VT"})
    shapes = set()
    for artist in axes.patches:
        if not isinstance(artist, Rectangle) or not artist.get_facecolor()[3]:
            continue
        layer = artist.get_gid() if artist.get_gid() in ("VG", "VT") else layers.get(to_hex(artist.get_facecolor()))
        if not layer:
            continue
        vertices = artist.get_patch_transform().transform(artist.get_path().vertices)
        xs = vertices[:, 0] * x_pitch_nm(row)
        ys = vertices[:, 1] * _cell_pitch_nm(row)
        bounds = tuple(round(float(value), 6) for value in (min(xs), min(ys), max(xs), max(ys)))
        shapes.add(Shape(layer, bounds))
    geometry = geometry_from_row(row)
    for name, parts in _misc_segments(row):
        if not name.lower().startswith("m0:tsdractive"):
            continue
        xs, ys = expand_axis(parts[2], geometry.ogd), expand_axis(parts[3], geometry.n_cells - 1)
        if xs and ys:
            shapes.add(Shape(name.lower(), ((min(xs) - 0.5) * 44, min(ys) * _cell_pitch_nm(row),
                                           (max(xs) + 0.5) * 44, (max(ys) + 1) * _cell_pitch_nm(row))))
    figure.clear()
    return tuple(sorted(shapes, key=lambda shape: (shape.layer, shape.bounds)))


def review_geometry(row: dict[str, str]) -> GeometryReview:
    from tsdr_rules import load_rules

    book = load_rules()
    stamp = book.source.stat()
    return _review_cached(tuple(sorted(row.items())), stamp.st_mtime_ns, stamp.st_size)


@lru_cache(maxsize=12)
def _review_cached(items: tuple, modified: int, size: int) -> GeometryReview:
    from ScalarCalculation import geometry_from_row
    from scalar_snapshot import _skew_geometry
    from tsdr_rules import load_rules

    row = dict(items)
    skipped = ["Unit-cell preview geometry only: global overrides, array boundaries and full TSDR ID enclosure are not checked.",
               "M0 width/color/pitch exceptions and TCG/GCN derived-mask rules are not evaluated."]
    if not row.get("UnitCellSizeOgd") or not row.get("UnitCellSizePgd"):
        return GeometryReview((), (), ("This template has no modeled ISO unit-cell geometry; geometric checks not performed.",))
    if row.get("PolyPitch", "pp44").lower() not in ("", "pp44"):
        return GeometryReview((), (), ("Geometric checks support pp44 only.",))
    geometry = geometry_from_row(row)
    if geometry.ogd * geometry.n_cells > 4000:
        return GeometryReview((), (), ("Unit cell exceeds the 4000-track geometric review budget; not evaluated.",))
    return check_shapes(unit_shapes(row), load_rules(), row, skipped, _skew_geometry(row, "FTICdMisregSkew"))


def check_shapes(shapes: tuple[Shape, ...], book, row: dict[str, str], skipped=None, fti_skews=()) -> GeometryReview:
    findings = []
    counts: dict[str, int] = {}
    skipped = list(skipped or ())
    rules = {rule.label: rule for rule in book.rules}
    groups = {layer: [shape for shape in shapes if shape.layer == layer] for layer in ("FTI", "PLY", "TCN", "M0", "VG", "VT")}

    def check(shape: Shape, value: float, label: str, operator: str, description: str) -> None:
        rule = rules.get(label)
        if not rule or rule.numeric_um is None:
            skipped.append(f"{label}: numeric source rule missing; not evaluated.")
            return
        counts[label] = counts.get(label, 0) + 1
        limit = 1000 * rule.numeric_um
        failed = value < limit - 1e-5 if operator == ">=" else value > limit + 1e-5 if operator == "<=" else abs(value - limit) > 1e-5
        if failed:
            from dr_plain import plain_check

            findings.append(GeometryFinding(shape.layer, label,
                f"{description}: {value:g} nm, expected {operator} {limit:g} nm (X81A/opt97 advisory; ID/region applicability unverified)",
                shape.bounds, rule.reference, plain_check(label, description, value, operator, limit)))

    def overlap(first: Shape, second: Shape) -> bool:
        return min(first.bounds[2], second.bounds[2]) - max(first.bounds[0], second.bounds[0]) > 1e-5 and \
            min(first.bounds[3], second.bounds[3]) - max(first.bounds[1], second.bounds[1]) > 1e-5

    def inside(shape: Shape, region: Shape) -> bool:
        return all((shape.bounds[0] >= region.bounds[0] - 1e-5, shape.bounds[1] >= region.bounds[1] - 1e-5,
                    shape.bounds[2] <= region.bounds[2] + 1e-5, shape.bounds[3] <= region.bounds[3] + 1e-5))

    def ids(name: str) -> list[Shape]:
        return [shape for shape in shapes if shape.layer == f"m0:{name}".lower()]

    for shape in groups["FTI"]:
        width = shape.bounds[2] - shape.bounds[0]
        check(shape, width, "TGFT_01", ">=", "FTI OGD width")
        check(shape, width, "TGFT_03", "<=", "FTI OGD width")
        check(shape, shape.bounds[3] - shape.bounds[1], "TGFT_04", ">=", "FTI PGD length")
        for direction, variant, value in fti_skews:
            if variant == "REG":
                check(shape, abs(value), "TGFT_06" if direction == "OGD" else "TGFT_07", "<=", f"FTI {direction} misregistration")
    for layer in ("VG", "VT"):
        for shape in groups[layer]:
            width, height = shape.bounds[2] - shape.bounds[0], shape.bounds[3] - shape.bounds[1]
            suffix = "2" if layer == "VT" and height >= 22 else "1"
            for value, axis, low, high in ((width, "OGD", "100", "200"), (height, "PGD", "300", "400")):
                check(shape, value, f"TG{layer}_{low}{suffix}", ">=", f"{layer} {axis} size")
                check(shape, value, f"TG{layer}_{high}{suffix}", "<=", f"{layer} {axis} size")
            for conductor in (("PLY" if layer == "VG" else "TCN"), "M0"):
                counts[f"MODEL_{layer}_{conductor}_LANDING"] = counts.get(f"MODEL_{layer}_{conductor}_LANDING", 0) + 1
                if not any(overlap(shape, other) for other in groups[conductor]):
                    findings.append(GeometryFinding(layer, "MODEL_LANDING",
                        f"{layer} has no overlapping {conductor} landing in the modeled unit cell (geometry advisory, not an enclosure-rule check)", shape.bounds,
                        plain=f"{layer} via has no {conductor} touching it in the preview, so it connects to nothing there."))

    for layer in groups:
        ordered = sorted(groups[layer], key=lambda shape: shape.bounds[0])
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                if second.bounds[0] > first.bounds[2] + 1e-5:
                    break
                dx, dy, distance = edge_gaps(first, second)
                marker = Shape(layer, pair_bounds(first, second))
                counts[f"MODEL_{layer}_PAIR_GEOMETRY"] = counts.get(f"MODEL_{layer}_PAIR_GEOMETRY", 0) + 1
                if overlap(first, second):
                    if layer in ("M0", "VG", "VT"):
                        findings.append(GeometryFinding(layer, "MODEL_OVERLAP", f"Distinct {layer} shapes overlap in the unit preview", marker.bounds,
                                                        plain=f"Two separate {layer} shapes overlap each other."))
                elif layer == "FTI" and dx == 0 and dy > 0:
                    check(marker, dy, "TGFT_05", ">=", "FTI PGD end-to-end space")
                elif layer == "M0" and dy > 0 and min(first.bounds[2], second.bounds[2]) > max(first.bounds[0], second.bounds[0]):
                    low, high = sorted((first, second), key=lambda shape: shape.bounds[1])
                    shared_x0, shared_x1 = max(first.bounds[0], second.bounds[0]), min(first.bounds[2], second.bounds[2])
                    if any(other not in (first, second) and low.bounds[3] <= other.bounds[1] and other.bounds[3] <= high.bounds[1]
                           and other.bounds[0] <= shared_x0 and other.bounds[2] >= shared_x1 for other in groups["M0"]):
                        continue
                    for mode, label in (("SALELE", "TGM0_0118"), ("LELE", "TGM0_0218")):
                        regions = ids(f"TSDRActive{mode}")
                        fut = ids("TSDRActiveFUTID")
                        other_fut = ids("TSDRActiveFUT3ID") if mode == "SALELE" else fut
                        if any(inside(first, region) and inside(second, region) for region in regions) and (
                                (any(overlap(first, region) for region in fut) and any(overlap(second, region) for region in other_fut)) or
                                (any(overlap(second, region) for region in fut) and any(overlap(first, region) for region in other_fut))):
                            check(marker, dy, label, "=", f"M0 PGD side space inside explicit {mode}/FUT IDs")

    rule = book.by_label("TGGCPL_98")
    search_distance = rule.numeric_um * 1000 if rule and rule.numeric_um is not None else 0
    if not search_distance:
        skipped.append("TGGCPL_98 missing: poly-to-TCN side-spacing search not performed.")
    for poly in groups["PLY"]:
        for tcn in groups["TCN"]:
            vertical_overlap = min(poly.bounds[3], tcn.bounds[3]) - max(poly.bounds[1], tcn.bounds[1])
            dx, dy, distance = edge_gaps(poly, tcn)
            if vertical_overlap > 1e-5 and dx < search_distance:
                check(Shape("PLY/TCN", pair_bounds(poly, tcn)), dx, "TGGCPL_99", "=", "Poly-to-TCN OGD side space")
    for layer in ("PLY", "TCN"):
        if not groups[layer]:
            skipped.append(f"{layer}: no shapes in the modeled unit cell.")
    if not ids("TSDRActiveSALELE") and not ids("TSDRActiveLELE"):
        skipped.append("M0 numeric side-spacing checks need explicit SALELE/LELE and FUT ID regions; only overlaps checked.")
    checks = tuple(f"{label}: {count} geometric comparisons" for label, count in counts.items())
    return GeometryReview(tuple(findings), checks, tuple(dict.fromkeys(skipped)))