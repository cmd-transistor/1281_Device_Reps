"""Plain-language explanations of TSDR outsheet rules and DR findings."""

from __future__ import annotations

from functools import lru_cache
import re


DIRECTIONS = {
    "OGD": "across the poly lines (X)",
    "PGD": "along the poly lines (Y)",
}

GLOSSARY = {
    "OGD": "Orthogonal-to-gate direction: across the poly lines, X in the preview.",
    "PGD": "Parallel-to-gate direction: along the poly lines, Y in the preview.",
    "CD": "Critical dimension: the drawn width of a line or via.",
    "Misreg": "Misregistration: a deliberate shift of a shape from its normal position.",
    "ETE": "End-to-end: the gap between two line tips that face each other.",
    "SDR": "Standard design rules: the normal on-grid widths, spaces and pitches.",
    "TSDR": "Test-structure skew rules: the exceptions allowed inside skew marker regions.",
    "Skew": "A deliberate change of width, pitch or position used to test process margin.",
    "ID": "A marker layer that labels a region; it is not printed on silicon.",
    "Compliance line": "An SDR line drawn on the edge of a skew zone so the skewed lines do not face a bare boundary.",
    "FUT": "Feature under test: the skewed shape the structure is built to measure.",
    "Depop": "Depopulated: a track left empty (the line or plug is removed).",
    "Transition": "A buffer region between skewed and normal layout.",
    "Enclosure": "How far an outer shape extends past an inner shape.",
    "Pitch": "Centre-to-centre distance between neighbouring lines.",
    "Fixed value": "The dimension must equal the value exactly.",
}

_PURPOSES = (
    (r"TSDRActiveCDID", "width-skew zone"),
    (r"TSDRActiveMisregID", "shift-skew zone"),
    (r"TSDRActiveCPID", "compliance-line marker"),
    (r"TSDRActiveFUT(\d*)ID", r"FUT\1 marker"),
    (r"TSDRActive(SALELE|LELE)", r"\1 skew zone"),
    (r"TSDRActiveImplant", "implant-skew zone"),
    (r"TSDRActivePitchID", "pitch-skew zone"),
    (r"TSDRNoDepop", "keep-all-lines marker"),
    (r"TSDRTransition", "transition zone "),
    (r"TSDRWingID", "wing marker"),
    (r"TSDRActive", "skew zone "),
    (r"toSynDrawing", "synthesized shape"),
    (r"^drawing$", ""),
    (r"^id$", "marker"),
)

_LAYERS = {
    "poly": "poly", "ply": "poly", "ndiff": "N diffusion", "pdiff": "P diffusion", "diff": "diffusion",
    "tcn": "TCN", "ctcn": "TCN plug", "tcp": "TCN plug", "fti": "FTI", "glk": "GLK", "vcrnub": "VCRNUB",
    "vcr": "VCR", "tsdrptap": "p-tap", "tsdrntap": "n-tap", "gcn": "GCN", "tcg": "TCG",
}

_QUALIFIERS = (
    (r"minimum of (?:the )?allowed range|\bminimum\b|\bmin\b", "at least"),
    (r"maximum of (?:the )?allowed range|\bmaximum\b|\bmax\b", "at most"),
    (r"fixed value|\bfixed\b|\bexact(?:ly)?\b|must be equal", "exactly"),
)

_NOT_LENGTH = re.compile(r"\b(number|count|ratio|density|percent|%|angle|degree|deg|times|multiple of)\b", re.IGNORECASE)


def _pitch_tag(suffix: str) -> str:
    match = re.fullmatch(r"(\d\d)(\d)Ap", suffix)
    if match:
        return f" ({match[1]}.{match[2]} nm pitch)"
    match = re.fullmatch(r"(\d+)p", suffix)
    return f" ({match[1]} nm pitch)" if match else (f" {suffix}" if suffix else "")


def _purpose(purpose: str) -> str:
    match = re.fullmatch(r"(TSDR\w*?ID|TSDRActive\w*?|TSDRTransition\w*?|TSDRNoDepop)(\d+A?p)?", purpose)
    base, suffix = (match[1], match[2] or "") if match else (purpose, "")
    for pattern, plain in _PURPOSES:
        if re.search(pattern, base):
            text = re.sub(pattern, plain, base).strip()
            text = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", text) if text != plain.strip() else text
            return (text + _pitch_tag(suffix)).strip()
    return re.sub(r"^TSDR", "", base) + _pitch_tag(suffix)


def _layer(name: str) -> str:
    base = re.sub(r"_mask$", "", name, flags=re.IGNORECASE)
    plain = _LAYERS.get(base.lower(), base.upper() if re.fullmatch(r"[a-z]{1,4}\d*", base) else base)
    return plain + (" mask" if name.lower().endswith("_mask") and plain == base else "")


def plain_names(text: str) -> str:
    """Rewrite raw layer;purpose (gds) references as short readable names."""
    text = re.sub(r"\s*\(\d+\s*;\s*\d+\)", "", text)

    def name(match: re.Match) -> str:
        layer, purpose = match[1], match[2]
        purpose_text = _purpose(purpose)
        layer_text = _layer(layer)
        if purpose_text in ("", "marker") and layer.lower().startswith("tsdr"):
            return f"{layer_text} marker"
        return f"{layer_text} {purpose_text}".strip()

    text = re.sub(r"\b([A-Za-z][A-Za-z0-9_]*)\s*[;:]\s*([A-Za-z][A-Za-z0-9_]*)\b", name, text)
    text = re.sub(r"\b(tcn|ctcn|fti|glk|vcr|vcrnub|ply)\b", lambda m: _LAYERS[m[1].lower()], text)
    text = re.sub(r"\b(gva|vg|vt|gcn|tcg|tcp|trm|m\d{1,2}|v\d{1,2})\b", lambda m: m[1].upper(), text)
    text = re.sub(r"\bwidth_(\d+)_(\d+)(?:/(\d+))?",
                  lambda m: f"width-class {m[1]} lines ({m[2]}" + (f"-{m[3]}" if m[3] else "") + " nm)", text)
    text = re.sub(r"\b(?:in|along) OGD\b", "in X (across poly)", text)
    text = re.sub(r"\b(?:in|along) PGD\b", "in Y (along poly)", text)
    text = re.sub(r"\bmisreg(?:istration)?\b", "shift", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCD (skew|change)", r"width \1", text)
    text = re.sub(r"\bETE\b|\bend[- ]to[- ]end\b", "tip-to-tip", text)
    text = re.sub(r"\s+OR\s+", " or ", text)
    text = re.sub(r"\s+AND\s+", " and ", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,")


def _direction(text: str) -> str:
    found = re.findall(r"\[(OGD|PGD)\]|\b(?:in|along|for) (OGD|PGD)\b|\((OGD|PGD)\)|\((OGD|PGD) lines only\)", text)
    names = [next(group for group in groups if group) for groups in found]
    return f", measured {DIRECTIONS[names[-1]]}" if names else ""


def _clean(text: str) -> str:
    text = plain_names(text)
    text = re.sub(r"\s*\[(?:OGD|PGD)\]", "", text)
    text = re.sub(r",?\s*(?:minimum|maximum|min|max|fixed value)(?: of (?:the )?allowed range)?\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.")


def format_value(drawn: str, description: str = "") -> str:
    try:
        value = float(drawn)
    except (TypeError, ValueError):
        return drawn.strip()
    if _NOT_LENGTH.search(description):
        return f"{value:g}"
    return f"{value * 1000:g} nm" if abs(value) < 1 else f"{value:g} µm"


def _qualifier(text: str, operator: str = "") -> str:
    if operator:
        return {">=": "at least", "<=": "at most", "=": "exactly", ">": "more than", "<": "less than"}.get(operator, operator)
    tail = text.rsplit(",", 1)[-1] if "," in text else text
    for pattern, plain in _QUALIFIERS:
        if re.search(pattern, tail, re.IGNORECASE):
            return plain
    for pattern, plain in _QUALIFIERS:
        if re.search(pattern, text, re.IGNORECASE):
            return plain
    return ""


def _sentence(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut.rstrip(",;:") + " …"
    return text[:1].upper() + text[1:] if text else text


@lru_cache(maxsize=None)
def simplify_text(label: str, description: str, drawn: str = "", operator: str = "") -> str:
    """One plain-language sentence for a rule; falls back to a cleaned-up description."""
    raw = re.sub(r"\s+", " ", description).strip()
    if not raw:
        return "No description in the outsheet."
    note = "_NOTE" in label.upper() or raw.lower().startswith("note")
    clean = re.sub(r"^note\s+for\s+", "About ", _clean(raw), flags=re.IGNORECASE)
    clean = re.sub(r"^note\s*:?\s*", "", clean, flags=re.IGNORECASE)
    if note:
        return _sentence("Background: " + clean)
    value = format_value(drawn, raw) if drawn else ""
    quality = _qualifier(raw, operator)
    amount = f"{quality} {value}".strip() if value else ""
    direction = _direction(raw)
    named = plain_names(raw)
    cuts = list(re.finditer(r"(?:,\s*|\s+)(?:minimum|maximum|min|max|fixed value)(?: of (?:the )?allowed range)?"
                            r"(?=\s*(?:\[|in\s|\(|$|,))", named, re.IGNORECASE))
    cut = cuts[-1] if cuts else None
    body = (named[:cut.start()] if cut else named).strip(" ,")
    tail = re.sub(r"\[(?:OGD|PGD)\]|\bin [XY] \((?:across|along) poly\)", "", named[cut.end():] if cut else "").strip(" ,.")
    condition = f" Condition: {tail.strip('()')}." if tail else ""
    body = re.sub(r"\s*\[(?:OGD|PGD)\]|\s*\((?:OGD|PGD)\)|,?\s*in [XY] \((?:across|along) poly\)", "", body)
    body = re.sub(r"\s*\((?:OGD|PGD) lines only\)", "", body).replace("),", ")")
    if re.fullmatch(r"Parameter for (\w+)", body):
        return _sentence(f"Value used by rule {body.split()[-1]}: {value}.")

    templates = (
        (r"^Enclosure of (?P<a>.+?) by (?P<b>.+)$",
         lambda m: f"{m['b']} must extend past {m['a']} by {amount}{direction}."),
        (r"^(?P<a>.+?),?\s+(?P<kind>tip-to-tip|corner-to-corner)\s+space\w*(?:\s+to\s+(?P<b>.+))?$",
         lambda m: f"{m['kind'].capitalize()} gap between {m['a']}" + (f" and {m['b']}" if m["b"] else " shapes")
         + f" must be {amount}{direction}."),
        (r"^(?P<a>.+?)\s+(?:ID\s+)?must enclose\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must extend past {m['b']} by {amount}{direction}."),
        (r"^(?P<a>.+?),?\s+(?:side\s+)?space\w*\s+to\s+(?P<b>.+)$",
         lambda m: f"Keep {m['a']} {amount} away from {m['b']}{direction}."),
        (r"^(?P<a>.+?),?\s+(?:side\s+)?space\w*$",
         lambda m: f"Gap between neighbouring {m['a']} must be {amount}{direction}."),
        (r"^(?P<a>width-class [^,]+)$",
         lambda m: f"{m['a']} must be {amount} wide{direction}."),
        (r"^(?P<a>.+?)\s+enclosure\s+by\s+(?P<b>.+)$",
         lambda m: f"{m['b']} must extend past {m['a']} by {amount}{direction}."),
        (r"^(?P<a>.+?)\s+enclosure\s+of\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must extend past {m['b']} by {amount}{direction}."),
        (r"^(?P<a>.+?)\s+(?:extension|overhang)\s+(?:beyond|past|into|over|of)\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must stick out past {m['b']} by {amount}{direction}."),
        (r"^(?P<a>.+?)\s+overlap\s+(?:of|with|to|by)?\s*(?P<b>.+)$",
         lambda m: f"{m['a']} must overlap {m['b']} by {amount}{direction}."),
        (r"^Only allowed (?P<a>.+?) inside (?P<b>.+?)(?: except (?P<c>.+))?$",
         lambda m: f"Inside {m['b']}, {m['a']} must be {amount}{direction}" + (f" (except {m['c']})" if m["c"] else "") + "."),
        (r"^(?P<a>.+?)\s+(?:width|CD)(?![-\w])(?P<rest>.*)$",
         lambda m: f"{m['a']}{m['rest']} must be {amount} wide{direction}."),
        (r"^(?P<a>.+?)\s+length(?![-\w])(?P<rest>.*)$",
         lambda m: f"{m['a']}{m['rest']} must be {amount} long{direction}."),
        (r"^(?P<a>.+?)\s+(?<!nm )pitch(?![-\w])(?P<rest>.*)$",
         lambda m: f"Centre-to-centre pitch of {m['a']}{m['rest']} must be {amount}{direction}."),
        (r"^(?P<a>.+?)\s+(?:size|dimension)(?![-\w])(?P<rest>.*)$",
         lambda m: f"{m['a']}{m['rest']} size must be {amount}{direction}."),
    )
    if amount and not re.search(r"[.;]\s|\b(?:applies|follow|regardless|when|if)\b", body, re.IGNORECASE):
        for pattern, build in templates:
            match = re.match(pattern, body, re.IGNORECASE)
            if match:
                return _sentence(re.sub(r"\s+,", ",", build(match)) + condition, 320)
    if amount:
        return _sentence(f"{clean}: {amount}{direction}.")
    actions = (
        (r"^(?P<a>.+?)\s+must not (?:interact|overlap|touch)(?: with)?\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must not touch or overlap {m['b']}."),
        (r"^(?P<a>.+?)\s+must (?:interact|overlap)(?: with)?\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must touch or overlap {m['b']}."),
        (r"^(?P<a>.+?)\s+must be rectangular\b.*$",
         lambda m: f"{m['a']} must be a plain rectangle."),
        (r"^(?P<a>.+?)\s+must (?:be (?:drawn |fully |completely )*)?(?:inside|enclosed by|within)\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must lie completely inside {m['b']}."),
        (r"^(?P<a>.+?)\s+(?:is|are) (?:legal|allowed) only (?:in|inside)\s+(?P<b>.+)$",
         lambda m: f"{m['a']} is only allowed inside {m['b']}."),
        (r"^(?P<a>.+?)\s+(?:is|are) not allowed\s*(?P<b>.*)$",
         lambda m: f"{m['a']} is not allowed {m['b']}".strip() + "."),
        (r"^(?P<a>.+?)\s+must have\s+(?P<b>.+)$",
         lambda m: f"{m['a']} must contain {m['b']}."),
    )
    for pattern, build in actions:
        match = re.match(pattern, clean, re.IGNORECASE)
        if match:
            return _sentence(build(match))
    return _sentence(clean + ("" if clean.endswith((".", "…")) else "."))


def simplify(rule) -> str:
    return simplify_text(rule.label, rule.description, rule.drawn, rule.operator)


def plain_gating(rule) -> str:
    if "_NOTE" in rule.label.upper():
        return "Background note"
    if rule.label.startswith("TG"):
        return "Must pass before tape-in"
    if rule.label.startswith("TC"):
        return "Can be waived only with approval"
    return "Reference"


_SECTIONS = (
    (r"^M(\d+) Rules", lambda m: f"Metal {m[1]} wiring: line widths, spaces and how M{m[1]} lines may be widened, "
                                 "narrowed or shifted inside skew zones."),
    (r"^V(\d+) Rules", lambda m: f"Via {m[1]}: via sizes and how V{m[1]} vias may be resized or shifted inside skew zones."),
    (r"^Viabp Rules", lambda m: "Backside via (viabp) sizes and spacing."),
    (r"Poly Pitch / CD Skew Rules on non-SDR (\w+)", lambda m: f"Areas where the poly pitch is changed{_pitch_tag(m[1])} "
                                                             "instead of 44 nm and/or the poly width is changed; "
                                                             "neighbouring TCN, VCR and taps must follow."),
    (r"TCG Skew Rules for (non-)?SDR (\w+)", lambda m: f"TCG mask rules when TCN width or position is skewed"
                                                     f"{_pitch_tag(m[2])}."),
    (r"Poly Direct Print", lambda m: "Poly width skews drawn with sizing markers on the normal 44 nm pitch."),
    (r"^Poly Skew Rules", lambda m: "General poly skew rules."),
    (r"VCR and VCRNUB", lambda m: "VCR rails and VCRNUB stubs: allowed width, length and shift when skewed."),
    (r"TSDR([pn])tap", lambda m: f"{m[1]}-tap marker regions placed next to skewed areas: size and spacing to diffusion."),
    (r"^TCP .*Bitcell", lambda m: "TCN plug (TCP) skews inside SRAM bitcells."),
    (r"^TCP ", lambda m: "TCN plug (TCP) skews: where TCN cuts may be resized or moved."),
    (r"^TCN Skew", lambda m: "TCN width and position skews."),
    (r"^Patterned FTI", lambda m: "Patterned FTI (SPF/SNF) masks: size and spacing of skewed FTI."),
    (r"^FTI Rules", lambda m: "FTI width, length, end-to-end space and allowed shift."),
    (r"^GCN Rules", lambda m: "GCN (gate contact) width and position skews."),
    (r"^VG Rules", lambda m: "VG (via to gate) size and landing on poly/M0."),
    (r"^VT Rules", lambda m: "VT (via to TCN) size and landing on TCN/M0."),
    (r"^ALTVT", lambda m: "Alternate VT via sizes and placement."),
    (r"DPI Jog", lambda m: "Allowed jog angles for DPI (direct print) skews."),
    (r"^DPI_mask", lambda m: "DPI (direct print integrated ribbon) mask skews: widths and spaces of the printed ribbon."),
    (r"^GLK_mask", lambda m: "GLK mask skews: where GLK may be resized or moved."),
    (r"^PXL_mask", lambda m: "PXL mask skews: where PXL may be resized or moved."),
    (r"Tall Cell", lambda m: "Tall-cell device skews and their transition zones."),
    (r"Blocking Mask Skew Rules: (\w+)", lambda m: f"{m[1]} implant/blocking mask: where it may be grown, shrunk "
                                                   "or moved and which markers must be present."),
    (r"Skip Well", lambda m: "Skipping well implants in test structures."),
    (r"Epiblock", lambda m: "Epi-block marker skews."),
    (r"Diffusion / Fin ETE", lambda m: "Diffusion/fin end-to-end gap skews."),
    (r"Edge Aligned Diffusion", lambda m: "Diffusion skews where diffusion edges line up."),
    (r"Devflav", lambda m: "Device-flavour (VT type) skews."),
    (r"VT Mapping in SRAM", lambda m: "How VT flavours map onto SRAM bitcells."),
    (r"SRAM bitcell", lambda m: "Skews allowed inside SRAM bitcells."),
    (r"Turbo Cell|LNR in Turbo", lambda m: "Turbo-cell specific rules."),
    (r"Global Checks", lambda m: "Checks that apply to the whole test chip."),
    (r"(\w+)PAD Check", lambda m: f"{m[1]} pad: pad size, spacing and placement."),
    (r"^C4[TB]", lambda m: "C4 bump size, spacing and placement."),
    (r"_mask Rules", lambda m: "Mask-specific size and placement rules."),
    (r"GVA Rules", lambda m: "GVA rules."),
    (r"Density", lambda m: "Pattern-density limits."),
    (r"Integration", lambda m: "Integration rules that tie several layers together."),
    (r"Regular Design Ensure", lambda m: "Rules that keep front-end skews on a regular, printable layout."),
    (r"^INTRO", lambda m: "How to read this outsheet."),
)


@lru_cache(maxsize=None)
def section_summary(section: str) -> str:
    for pattern, build in _SECTIONS:
        match = re.search(pattern, section)
        if match:
            return build(match)
    return f"Rules in “{section}”." if section else ""


def glossary_for(*texts: str) -> list[str]:
    joined = " ".join(texts)
    return [f"{term}: {meaning}" for term, meaning in GLOSSARY.items()
            if re.search(rf"\b{re.escape(term)}", joined, re.IGNORECASE)]


def plain_location(text: str) -> str:
    """Measured-quantity wording for findings, e.g. 'FTI OGD width' -> 'FTI width in X (across poly)'."""
    text = re.sub(r"\bmisregistration\b|\bmisreg\b", "shift", text, flags=re.IGNORECASE)
    text = re.sub(r"\bend-to-end\b", "tip-to-tip", text)
    for axis, plain in (("OGD", "in X (across poly)"), ("PGD", "in Y (along poly)")):
        if re.search(rf"\b{axis}\b", text):
            text = re.sub(rf"\s*\b{axis}\b", "", text) + f" {plain}"
    return plain_names(text)


def plain_check(label: str, what: str, value: float, operator: str, limit: float, rule=None) -> str:
    """Short plain sentence for a measured value that broke a rule limit."""
    need = {">=": "at least", "<=": "at most", "=": "exactly"}.get(operator, operator)
    text = f"{label}: {plain_location(what)} is {value:g} nm but must be {need} {limit:g} nm."
    return text + (f" Rule: {simplify(rule)}" if rule is not None else "")
