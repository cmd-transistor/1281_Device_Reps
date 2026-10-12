"""Prompt-driven ISO structure generation and reference suggestions.

With an OpenAI-compatible endpoint configured, the model produces a new
decoder-DSL design which is expanded by ``claude_iso_generator`` and checked by
``ScalarCalculation``. Without an endpoint, the tool is explicitly in
reference-only mode and suggests/adapts rows from ``ISO_Metadata_Input.csv``;
that mode cannot create new geometry. Accepted AI-generated layouts can serve
as preference examples for later requests. Learning is stored in
``iso_ai_memory.json``.

Configure ``ISO_AI_ENDPOINT``, ``ISO_AI_MODEL`` and optionally
``ISO_AI_API_KEY``. The endpoint must use HTTPS except for localhost.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import importlib
import io
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ScalarCalculation import (
    BIAS_CONDITIONS, DEFAULT_INPUT, FAMILY_PREFIXES, FORMULA_COL, TEST_TYPES, Result,
    compute, family_of, generic_name, structure_name_issues, type_of,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MEMORY = BASE_DIR / "iso_ai_memory.json"
LAYOUT_DIR = BASE_DIR / "ai_layouts"
MAX_PROMPT_CHARS = 4000
MAX_FIELD_CHARS = 2000
MAX_EVENTS = 2000

FLAVOR_LETTER = {"hvt": "H", "svt": "S", "lvt": "L", "ulvt": "U", "uvt": "U",
                 "ulvtll": "UL", "elvt": "E", "evt": "E"}
VALID_HEIGHTS = (110, 132, 165)
VALID_MOS = ("N", "P", "NP")
COMPUTED_FIELDS = frozenset({"Scalar", "Test Type", "Force", FORMULA_COL})
DECODER_FIELDS = frozenset({
    "FTIPattern", "PolyPlugs", "TCNPlugs", "VcxPattern", "M0CutPattern",
    "LO1ActiveTracks", "HI1ActiveTracks", "VcrPattern", "VcrNubPattern", "ResizeShift",
})

FAMILY_PATTERNS = (
    ("DIFFCHN", r"diff\s*-?\s*chn|diff(?:usion)?[\s-]*chain"),
    ("DIFFOGD", r"diff\s*-?\s*ogd|diff(?:usion)?[\s-]*(?:to[\s-]*diff(?:usion)?|isolation)|fti[\s-]*(?:isolation|cut)"),
    ("CTG", r"\bctg(?![a-z])|contact[\s-]*to[\s-]*gate|gate[\s-]*to[\s-]*contact"),
    ("GATEGATE", r"gate\s*-?\s*gate|gate[\s-]*to[\s-]*gate|gate[\s-]*(?:ete|end[\s-]*to[\s-]*end)|poly[\s-]*ete"),
    ("TCNTCN", r"tcn\s*-?\s*tcn|tcn[\s-]*to[\s-]*tcn|tcn[\s-]*(?:ete|end[\s-]*to[\s-]*end)|trench[\s-]*contact"),
    ("EPIEPI", r"epi\s*-?\s*epi|epi[\s-]*to[\s-]*epi|\bepi(?![a-z])"),
)
VIA_CHAIN_RE = re.compile(r"via[\s-]*chain|\b(?:vt|vg|vcr)_mv")
VG_CLEARANCE_RE = re.compile(r"\b(?:vg|via[\s-]*g(?:ate)?)\b.*\b(?:farther|closer|distance|spacing|clearance)\b|\b(?:farther|closer|distance|spacing|clearance)\b.*\b(?:vg|via[\s-]*g(?:ate)?)\b")
CRL_RE = re.compile(r"\bcrl\b", re.IGNORECASE)
ALTERNATIVE_RE = re.compile(r"\b(another|different|alternative|other (?:template|option)|something else|next option|try next|next match)\b")
ASSIGN_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_ ]*?)\s*=\s*(.*?)\s*$")
UNIT_RE = re.compile(r"unit[\s-]*cell(?:\s*size)?\s*(?:of\s*)?(\d{1,4})\s*[x×*]\s*(\d{1,4})")
ARRAY_RE = re.compile(r"(?:array(?:\s*size)?\s*(?:of\s*)?)?\b(\d{1,4})\s*[x×*]\s*(\d{1,4})\b")
HEIGHT_RE = re.compile(r"(?<!\d)(110|132|165)(?!\d)")
FLAVOR_RE = re.compile(r"\b(ulvtll|ulvt|elvt|hvt|svt|lvt|uvt|evt)\b")
TYPE_RE = re.compile(r"\btype[\s_-]*([a-z])\b")
WIDTH_RE = re.compile(r"(?:dev(?:ice)?[\s_-]*)?width\s*(?:=|:|of|is)?\s*(\d+(?:\.\d+)?)\s*(nm|um|µm)?")
TRACK_RE = re.compile(r"\b([12])\s*-?\s*(?:trk|tracks?)\b")
LEN_RE = re.compile(r"\blen(?:gth)?\s*-?\s*(\d+)\b")
NEGATIVE_RE = re.compile(r"\b(?:not|no|without|instead of|remove|exclude|except)\s+([a-z0-9_]+)")
NAME_RE = re.compile(r"^(?P<fam>[A-Z]+)(?P<height>\d+)-(?P<mos>NP|N|P)(?P<z>Z?)(?P<num>\d+)-(?P<flavor>UL|U|L|S|H|E)-")
OPEN_CAL_RE = re.compile(r"\b(?:OC\s*[1-9]?|open[\s_-]*cal(?:ibration)?)\b", re.IGNORECASE)
STANDARD_RE = re.compile(r"\bstandard\b|\bnot\s+(?:an?\s+)?open[\s_-]*cal", re.IGNORECASE)
GATE_TYPE_RE = re.compile(r"^([A-Z])(?:[:_-])")
DIFF_FLAVOR_RE = re.compile(r"(,\s*)([np])(ulvtll|ulvt|elvt|hvt|svt|lvt|uvt|evt)\b", re.IGNORECASE)
DIFF_WIDTH_RE = re.compile(r"(,\s*)(\d*\.\d+)(\s*,\s*[np][a-z]*)", re.IGNORECASE)
AI_SUFFIX_RE = re.compile(r":AI\d*$")

STOPWORDS = frozenset("""
a an the and or with for of to in on at by from is it be as that this which one same but also more less
make create generate build design structure structures please need want new use using like similar
iso test etest cell unit array size type width device dev nm um ch should would can could i me my we our
not no without instead remove exclude except change set give get add
""".split())


class UnknownDesign(LookupError):
    pass


class NoMatchingTemplate(ValueError):
    pass


# ---------------------------------------------------------------------------
# Prompt specification
# ---------------------------------------------------------------------------
@dataclass
class Spec:
    family: str | None = None
    direction: str | None = None
    cell_height: int | None = None
    mos: str | None = None
    flavor: str | None = None
    type_letter: str | None = None
    array_ogd: int | None = None
    array_pgd: int | None = None
    unit_ogd: int | None = None
    unit_pgd: int | None = None
    devwidth_nm: int | None = None
    exclude_open_cal: bool | None = None
    include_open_cal: bool | None = None
    names: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    negative_tokens: list[str] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)
    unsupported_requirements: list[str] = field(default_factory=list)

    def merge(self, other: "Spec") -> "Spec":
        """``other`` wins for every value it sets; keyword sets are combined."""
        merged = Spec(**{k: getattr(other, k) if getattr(other, k) is not None else getattr(self, k)
                         for k in SCALAR_SPEC_FIELDS})
        negative = set(self.negative_tokens) - set(other.tokens) | set(other.negative_tokens)
        merged.tokens = sorted((set(self.tokens) | set(other.tokens)) - negative)
        merged.negative_tokens = sorted(negative)
        merged.names = sorted(set(self.names) | set(other.names))
        merged.assignments = {**self.assignments, **other.assignments}
        merged.unsupported_requirements = sorted(set(self.unsupported_requirements) | set(other.unsupported_requirements))
        return merged


SCALAR_SPEC_FIELDS = ("family", "direction", "cell_height", "mos", "flavor", "type_letter", "array_ogd", "array_pgd",
                      "unit_ogd", "unit_pgd", "devwidth_nm", "exclude_open_cal", "include_open_cal")


def _bounded_int(value: object, low: int, high: int) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high else None


def parse_prompt(text: str, fields: list[str]) -> tuple[Spec, list[str]]:
    """Rule-based prompt interpretation; ``Field=value`` lines become assignments."""
    spec, notes = Spec(), []
    lookup = {re.sub(r"\s+", "", f).lower(): f for f in fields}
    free: list[str] = []
    for line in text.splitlines():
        m = ASSIGN_RE.match(line)
        column = lookup.get(re.sub(r"\s+", "", m.group(1)).lower()) if m else None
        if not column:
            free.append(line)
            continue
        value = m.group(2)
        if column in COMPUTED_FIELDS:
            notes.append(f"{column} is computed from the geometry; explicit value ignored")
        elif value[:1] in ("=", "@", "\t", "\r") or len(value) > MAX_FIELD_CHARS:
            notes.append(f"Unsafe or oversized value for {column} ignored")
        else:
            spec.assignments[column] = value

    t = " ".join(free).lower()
    t = re.sub(r"ulvt[\s-]*ll", "ulvtll", t)
    if VIA_CHAIN_RE.search(t):
        raise ValueError("Via-chain structures are out of scope: the scalar calculator only covers "
                         + ", ".join(FAMILY_PREFIXES))
    for family, pattern in FAMILY_PATTERNS:
        if re.search(pattern, t):
            spec.family = family
            break
    direction_matches = re.findall(r"\b(pgd|ogd)\b", t)
    if direction_matches:
        spec.direction = direction_matches[-1].upper()
    if m := UNIT_RE.search(t):
        spec.unit_ogd, spec.unit_pgd = _bounded_int(m[1], 1, 1000), _bounded_int(m[2], 1, 1000)
        t = t[:m.start()] + " " + t[m.end():]
    if m := ARRAY_RE.search(t):
        spec.array_ogd, spec.array_pgd = _bounded_int(m[1], 1, 10000), _bounded_int(m[2], 1, 10000)
        t = t[:m.start()] + " " + t[m.end():]
    if m := HEIGHT_RE.search(t):
        spec.cell_height = int(m[1])
    if re.search(r"\b(np|cmos|n\s*[+/&]\s*p|n\s*and\s*p)\b", t):
        spec.mos = "NP"
    elif re.search(r"\b(n|nmos|nfet|n[- ]?type|n[- ]?diff(?:usion)?)\b", t):
        spec.mos = "N"
    elif re.search(r"\b(p|pmos|pfet|p[- ]?type|p[- ]?diff(?:usion)?)\b", t):
        spec.mos = "P"
    if m := FLAVOR_RE.search(t):
        spec.flavor = m[1]
    if m := TYPE_RE.search(t):
        spec.type_letter = m[1].upper()
    if m := WIDTH_RE.search(t):
        value = float(m[1])
        if m[2] in ("um", "µm") or value < 1:
            value *= 1000
        spec.devwidth_nm = _bounded_int(round(value), 5, 500)

    if STANDARD_RE.search(t):
        spec.exclude_open_cal = True
    elif OPEN_CAL_RE.search(t):
        spec.include_open_cal = True
    if VG_CLEARANCE_RE.search(t):
        spec.unsupported_requirements.append(
            "VG-to-fail-mode clearance is not optimized from prose; verify the displayed VcxPatternUnit or specify it explicitly."
        )
    if CRL_RE.search(t):
        spec.unsupported_requirements.append("CRL is not a modeled buildsheet field; verify it with the process owner.")

    negative = set(NEGATIVE_RE.findall(t))
    tokens = {f"{n}trk" for n in TRACK_RE.findall(t)} | {f"len{n}" for n in LEN_RE.findall(t)}
    tokens |= {w for w in re.findall(r"[a-z0-9_]+", t) if len(w) > 1 and not w.isdigit() and w not in STOPWORDS}
    spec.tokens = sorted(tokens - negative)
    spec.negative_tokens = sorted(negative)
    return spec, notes


def _spec_from_llm(data: dict, vocabulary: set[str]) -> Spec:
    """Validate untrusted LLM output field by field."""
    spec = Spec()
    family = str(data.get("family") or "").upper()
    spec.family = family if family in FAMILY_PREFIXES else None
    height = _bounded_int(data.get("cell_height"), 0, 1000)
    spec.cell_height = height if height in VALID_HEIGHTS else None
    mos = str(data.get("mos") or "").upper()
    spec.mos = mos if mos in VALID_MOS else None
    flavor = str(data.get("flavor") or "").lower()
    spec.flavor = flavor if flavor in FLAVOR_LETTER else None
    letter = str(data.get("type_letter") or "").upper()
    spec.type_letter = letter if re.fullmatch(r"[A-Z]", letter) else None
    direction = str(data.get("direction") or "").upper()
    spec.direction = direction if direction in ("PGD", "OGD") else None
    spec.array_ogd = _bounded_int(data.get("array_ogd"), 1, 10000)
    spec.array_pgd = _bounded_int(data.get("array_pgd"), 1, 10000)
    spec.devwidth_nm = _bounded_int(data.get("devwidth_nm"), 5, 500)

    def words(key: str) -> list[str]:
        values = data.get(key) or []
        if not isinstance(values, list):
            return []
        return sorted({str(v).lower() for v in values[:30] if str(v).lower() in vocabulary})

    spec.tokens, spec.negative_tokens = words("tokens"), words("negative_tokens")
    return spec


class LLMClient:
    """Minimal OpenAI-compatible chat-completions client (stdlib only)."""

    SYSTEM = (
        "You convert requests for ISO E-test structures into JSON. Reply with one JSON object only, using "
        "optional keys: family (CTG|GATEGATE|EPIEPI|TCNTCN|DIFFOGD|DIFFCHN), direction (PGD|OGD), cell_height (110|132|165), "
        "mos (N|P|NP), flavor (hvt|svt|lvt|ulvt|ulvtll|elvt|uvt|evt), type_letter (one letter), "
        "array_ogd (int), array_pgd (int), devwidth_nm (int), tokens (lowercase design keywords such as "
        "sdr, ref, pxl, ftiml, ftisl, cd, reg, den, vcr, vcrnub, 1trk, 2trk, len10), negative_tokens "
        "(keywords to avoid). Omit anything the request does not state or clearly imply."
    )
    GENERATE_SYSTEM = (
        "Design a new 1281-node ISO E-test structure from the user's physical request. Do not choose or copy a "
        "StructureName or complete row from a library. Return exactly one JSON object with family, direction, "
        "type_letter, cell_height, mos, mg, devwidth_nm, unit_ogd, unit_pgd, array_ogd, array_pgd, flavor, "
        "decoder_patterns, assumptions, and clarifying_questions. decoder_patterns must contain explicit values "
        "for FTIPattern, PolyPlugs, TCNPlugs, VcxPattern, and M0CutPattern (empty string only when intentionally "
        "absent); CTG, GATEGATE, EPIEPI, and TCNTCN must also explicitly set LO1ActiveTracks and HI1ActiveTracks. "
        "VcrPattern, VcrNubPattern, and ResizeShift are optional. "
        "Use the decoder DSL: axes use All, integer, range a:b, list a/b/c, reverse r0, or stride a:b:Skip:n; "
        "repeat form a:Skip:n:Repeat; pattern fields are comma-separated axes and semicolon-separated segments. "
        "VcxPattern segments are kind,x-axis,y-axis, e.g. vg,1:23:Skip:3,6/13; vt,0:r0:Skip:3,14. "
        "Accepted AI-generated examples are preference signals only: do not copy them or treat them as a default layout. "
        "Do not infer unspecified physical details from a reference. Put missing decisions in clarifying_questions "
        "and return no geometry when a required process/layout decision is unknown. Explain each assumption."
    )

    def __init__(self, endpoint: str, model: str, api_key: str = "", timeout: float = 30.0) -> None:
        self.endpoint, self.model, self.api_key, self.timeout = endpoint, model, api_key, timeout

    @classmethod
    def from_env(cls) -> "LLMClient | None":
        endpoint = os.environ.get("ISO_AI_ENDPOINT", "").strip()
        if not endpoint:
            return None
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("ISO_AI_ENDPOINT must use https (plain http is allowed only for localhost)")
        model = os.environ.get("ISO_AI_MODEL", "").strip()
        if not model:
            raise ValueError("ISO_AI_MODEL must be set when ISO_AI_ENDPOINT is configured")
        return cls(endpoint, model, os.environ.get("ISO_AI_API_KEY", "").strip())

    def _complete(self, system: str, text: str) -> dict:
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}]}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.endpoint, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        content = payload["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("LLM reply is not a JSON object")
        return data

    def interpret(self, text: str) -> dict:
        return self._complete(self.SYSTEM, text)

    def generate_structure(self, prompt: str, current_row: dict[str, str] | None = None) -> dict:
        request = prompt
        if current_row:
            columns = ("StructureName", "CellHeight", "PolyPitch", "UnitCellSizeOgd", "UnitCellSizePgd",
                       "ArraySizeOgd", "ArraySizePgd", "DiffPatternUnit", "FTITracksUnit", "PolyPlugsUnit",
                       "TcnPlugsUnit", "VcxPatternUnit", "LO1ActiveTracks", "HI1ActiveTracks")
            request += "\nCurrent generated buildsheet geometry (revise this design, do not select a library row):\n"
            request += json.dumps({c: current_row.get(c, "") for c in columns}, indent=1)
        return self._complete(self.GENERATE_SYSTEM, request)


# ---------------------------------------------------------------------------
# Reference library and designs
# ---------------------------------------------------------------------------
def _row_tokens(row: dict[str, str]) -> frozenset[str]:
    text = f"{row.get('StructureName', '')} {row.get('StructureComments', '')}".lower()
    words = {w for w in re.split(r"[^a-z0-9]+", text) if len(w) > 1}
    # compound name parts such as 1FCLEN10 / STKx2FLEN8 also index their LEN and TRK tokens
    words |= set(re.findall(r"len\d+|\d+trk|stkx\d+", text))
    if OPEN_CAL_RE.search(text):
        words.add("open_cal")
    return frozenset(words)


def _canonical_family(name: str) -> str:
    return family_of(name)


def _name_type(name: str) -> str:
    match = NAME_RE.match(name)
    if not match:
        return ""
    tail = name[match.end():]
    type_match = GATE_TYPE_RE.match(tail)
    return type_match[1] if type_match else ""


@dataclass
class Reference:
    name: str
    row: dict[str, str]
    family: str
    direction: str
    source: str
    tokens: frozenset[str]
    pattern: str
    height: int | None
    mos: str
    flavor: str
    stype: str
    width_nm: int | None
    open_cal: bool = False
    prompt_tokens: frozenset[str] = frozenset()

    @classmethod
    def from_row(cls, row: dict[str, str], source: str, prompt_tokens: frozenset[str] = frozenset()) -> "Reference":
        name = row.get("StructureName", "").strip()
        m = NAME_RE.match(name)
        width = DIFF_WIDTH_RE.search(row.get("DiffPatternUnit", ""))
        height_digits = re.sub(r"\D", "", row.get("CellHeight", ""))
        direction = re.search(r":(PGD|OGD):", name, re.IGNORECASE)
        return cls(
            name=name, row=row, family=_canonical_family(name),
            direction=direction[1].upper() if direction else "", source=source,
            tokens=_row_tokens(row) | prompt_tokens, pattern=generic_name(AI_SUFFIX_RE.sub("", name)),
            height=int(m["height"]) if m else (int(height_digits) if height_digits else None),
            mos=m["mos"] if m else "", flavor=m["flavor"] if m else "", stype=_name_type(name),
            width_nm=round(float(width[2]) * 1000) if width else None,
            open_cal=bool(OPEN_CAL_RE.search(f"{name} {row.get('StructureComments', '')}")),
            prompt_tokens=prompt_tokens,
        )


@dataclass
class Design:
    id: str
    prompt: str
    spec: Spec
    template: str = ""
    source: str = ""
    score: float = 0.0
    row: dict[str, str] = field(default_factory=dict)
    fail_modes: int | None = None
    scalar: str = ""
    formula: str = ""
    reasoning: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    history: list[dict[str, str]] = field(default_factory=list)
    excluded: set[str] = field(default_factory=set)
    status: str = "draft"
    version: int = 1
    result: Result | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "version": self.version, "status": self.status, "prompt": self.prompt,
            "template": self.template, "template_source": self.source, "score": round(self.score, 2),
            "structure_name": self.row.get("StructureName", ""), "fail_modes": self.fail_modes,
            "scalar": self.scalar, "formula": self.formula, "spec": asdict(self.spec),
            "reasoning": self.reasoning, "alternatives": self.alternatives, "warnings": self.warnings,
            "history": self.history, "buildsheet": self.row,
        }


# ---------------------------------------------------------------------------
# Designer engine
# ---------------------------------------------------------------------------
class StructureDesigner:
    def __init__(self, library_csv: Path = DEFAULT_INPUT, memory_path: Path = DEFAULT_MEMORY,
                 use_llm: bool = True) -> None:
        self._lock = threading.RLock()
        self._render_lock = threading.Lock()
        with open(library_csv, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            self.fields = [f for f in reader.fieldnames or [] if f]
            rows = list(reader)
        self.library_rows = [dict(row) for row in rows
                             if (row.get("TestRow") or "").strip().lower().startswith("x81z")]
        if not self.library_rows:
            self.library_rows = [dict(row) for row in rows]
        self.prompt_fields = self.fields + sorted(DECODER_FIELDS)
        self.output_fields = list(self.fields)
        for column in ("Test Type", "Scalar", "Force"):
            if column not in self.output_fields:
                self.output_fields.append(column)
        if FORMULA_COL not in self.output_fields:
            self.output_fields.insert(self.output_fields.index("Scalar") + 1, FORMULA_COL)

        self.references: list[Reference] = []
        self.names: set[str] = set()
        for row in rows:
            name = row.get("StructureName", "").strip()
            if family_of(name) in FAMILY_PREFIXES and name not in self.names:
                self.names.add(name)
                self.references.append(Reference.from_row(row, "buildsheet"))
        doc_freq = Counter(t for ref in self.references for t in ref.tokens)
        self._idf = {t: math.log(1 + len(self.references) / n) for t, n in doc_freq.items()}
        self.vocabulary = set(doc_freq)

        self.memory_path = memory_path
        self.memory = self._load_memory()
        self.designs: dict[str, Design] = {}
        self.llm = LLMClient.from_env() if use_llm else None

    # -- persistence --------------------------------------------------------
    def _load_memory(self) -> dict:
        memory = {"version": 1, "token_weights": {}, "approved": [], "events": []}
        if self.memory_path.exists():
            with open(self.memory_path, encoding="utf-8") as fh:
                memory.update(json.load(fh))
        return memory

    def _save_memory(self) -> None:
        tmp = self.memory_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.memory, fh, indent=1)
        os.replace(tmp, self.memory_path)

    def _log_event(self, design: Design, action: str, text: str) -> None:
        self.memory["events"].append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "design": design.id, "action": action,
            "text": text[:500], "template": design.template, "structure": design.row.get("StructureName", ""),
        })
        del self.memory["events"][:-MAX_EVENTS]
        self._save_memory()

    def stats(self) -> dict:
        with self._lock:
            return {"references": len(self.references), "approved": len(self.memory["approved"]),
                    "feedback_events": len(self.memory["events"]),
                    "learned_keywords": len(self.memory["token_weights"]), "llm": bool(self.llm),
                    "mode": "decoder-generation" if self.llm else "reference-only"}

    def _generate_from_model(self, prompt: str, requested: Spec,
                             current_row: dict[str, str] | None = None) -> tuple[dict[str, str], list[str], Spec]:
        if not self.llm:
            raise ValueError("No LLM endpoint is configured. Natural-language geometry generation is unavailable.")
        accepted_examples = [entry for entry in self.memory["approved"]
                             if entry.get("source") == "LLM-generated geometry"
                             and (not requested.family or family_of(entry.get("row", {}).get("StructureName", "")) == requested.family)]
        if accepted_examples:
            pattern_columns = ("FTITracksUnit", "PolyPlugsUnit", "TcnPlugsUnit", "VcxPatternUnit",
                               "LO1ActiveTracks", "HI1ActiveTracks")
            examples = [{"feedback_tokens": entry.get("tokens", []),
                         "accepted_patterns": {c: entry.get("row", {}).get(c, "") for c in pattern_columns}}
                        for entry in accepted_examples[-3:]]
            prompt += "\nUser-approved AI-generated layouts (preference examples only; do not copy):\n"
            prompt += json.dumps(examples, indent=1)
        proposal = self.llm.generate_structure(prompt, current_row)
        questions = proposal.get("clarifying_questions") or []
        if not isinstance(questions, list):
            raise ValueError("Model returned invalid clarifying_questions; no buildsheet was generated.")
        if questions:
            raise ValueError("Please clarify before generating: " + "; ".join(str(q) for q in questions[:6]))

        family = str(proposal.get("family") or "").upper()
        direction = str(proposal.get("direction") or "").upper()
        type_letter = str(proposal.get("type_letter") or "").upper()
        height = _bounded_int(proposal.get("cell_height"), 1, 300)
        mos = str(proposal.get("mos") or "").upper()
        mg = str(proposal.get("mg") or "").upper()
        flavor = str(proposal.get("flavor") or "").lower()
        width = _bounded_int(proposal.get("devwidth_nm"), 5, 500)
        unit_ogd = _bounded_int(proposal.get("unit_ogd"), 1, 1000)
        unit_pgd = _bounded_int(proposal.get("unit_pgd"), 1, 1000)
        array_ogd = _bounded_int(proposal.get("array_ogd"), 1, 10000)
        array_pgd = _bounded_int(proposal.get("array_pgd"), 1, 10000)
        for label, expected, actual in (
            ("family", requested.family, family), ("direction", requested.direction, direction),
            ("type", requested.type_letter, type_letter), ("cell height", requested.cell_height, height),
            ("MOS", requested.mos, mos), ("VT flavor", requested.flavor, flavor),
            ("unit Ogd", requested.unit_ogd, unit_ogd), ("unit Pgd", requested.unit_pgd, unit_pgd),
            ("device width", requested.devwidth_nm, width), ("Ogd array size", requested.array_ogd, array_ogd),
            ("Pgd array size", requested.array_pgd, array_pgd),
        ):
            if expected is not None and str(expected).lower() != str(actual).lower():
                raise ValueError(f"Model proposal conflicts with your {label} request ({expected} vs {actual}); no buildsheet generated.")
        missing = [name for name, value in (
            ("family", family), ("direction", direction), ("type", type_letter), ("cell height", height),
            ("MOS", mos), ("VT flavor", flavor), ("MG", mg), ("device width", width),
            ("unit Ogd", unit_ogd), ("unit Pgd", unit_pgd), ("array Ogd", array_ogd), ("array Pgd", array_pgd),
        ) if value in (None, "")]
        if missing:
            raise ValueError("Model did not determine required design inputs: " + ", ".join(missing)
                             + ". No buildsheet generated.")
        if family not in FAMILY_PREFIXES or direction not in ("OGD", "PGD") or mos not in VALID_MOS:
            raise ValueError("Model returned an unsupported family, direction, or MOS; no buildsheet generated.")
        if height not in VALID_HEIGHTS or flavor not in FLAVOR_LETTER:
            raise ValueError("Model returned an unsupported cell height or VT flavor; no buildsheet generated.")
        if not re.fullmatch(r"[A-Z]", type_letter):
            raise ValueError("Model returned an invalid structure type; no buildsheet generated.")

        patterns = proposal.get("decoder_patterns")
        if not isinstance(patterns, dict):
            raise ValueError("Model did not return decoder_patterns; no buildsheet generated.")
        decoder_columns = {
            "FTIPattern", "PolyPlugs", "TCNPlugs", "VcxPattern", "M0CutPattern",
            "LO1ActiveTracks", "HI1ActiveTracks", "VcrPattern", "VcrNubPattern", "ResizeShift",
        }
        decoder = {key: value for key, value in patterns.items() if key in decoder_columns}
        for key in decoder_columns:
            if key in requested.assignments:
                decoder[key] = requested.assignments[key]
        required_patterns = {"FTIPattern", "PolyPlugs", "TCNPlugs", "VcxPattern", "M0CutPattern"}
        if family in {"CTG", "GATEGATE", "EPIEPI", "TCNTCN"}:
            required_patterns |= {"LO1ActiveTracks", "HI1ActiveTracks"}
        absent = sorted(required_patterns - decoder.keys())
        if absent:
            raise ValueError("Model omitted explicit decoder pattern fields: " + ", ".join(absent)
                             + ". No library geometry was substituted.")
        for key, value in decoder.items():
            if not isinstance(value, str) or len(value) > MAX_FIELD_CHARS:
                raise ValueError(f"Decoder field {key} must be a string under {MAX_FIELD_CHARS} characters.")

        generator_dir = BASE_DIR / "extracted"
        if not generator_dir.is_dir():
            raise ValueError("The decoder DSL generator in extracted/ is missing.")
        if str(generator_dir) not in sys.path:
            sys.path.insert(0, str(generator_dir))
        generator = importlib.import_module("claude_iso_generator")
        allowed_mg = generator.VALID_MG
        if mg not in allowed_mg:
            raise ValueError(f"Model returned unsupported MG {mg!r}; expected one of {sorted(allowed_mg)}.")

        height_code = {110: "1", 132: "5", 165: "7"}[height]
        device_token = f"{mos}{width}" if family == "DIFFOGD" else f"{mos}Z{height_code}"
        base_name = f"{family}{height}-{device_token}-{FLAVOR_LETTER[flavor]}-{type_letter}:{direction}:GEN"
        structure_name = self._unique_name(base_name)
        decoder_row = {
            "StructureName": structure_name, "MOS": mos, "MG": mg, "CELLHEIGHT": str(height),
            "DEVWIDTH": str(width), "UnitCellSizeOgd": str(unit_ogd), "UnitCellSizePgd": str(unit_pgd),
            "ArraySizeOgd": str(array_ogd), "ArraySizePgd": str(array_pgd), **decoder,
        }
        validation = generator.validate_decoder_row(decoder_row)
        if validation.has_blocking_errors:
            raise ValueError("Generated decoder inputs failed validation: "
                             + "; ".join(validation.input_errors + validation.range_errors))
        fields, _, train_rows = generator.load_training(
            generator_dir / "ISO_Decoder.csv", generator_dir / "ISO_Mini.csv")
        constants = generator._build_constant_map(train_rows, fields)
        generated = generator._generate_iso_row_tuned(decoder_row, constants, fields, indexes={})
        row = {field_name: generated.get(field_name, "") for field_name in self.fields}
        row["StructureName"] = structure_name
        row["TestRow"] = "AI_GENERATED"
        row["StructureComments"] = "AI-generated from decoder DSL; review geometry before use."
        notes = ["Generated from explicit decoder DSL; no full structure row was selected."]
        assumptions = proposal.get("assumptions") or []
        if isinstance(assumptions, list):
            notes.extend(f"Assumption: {str(item)[:300]}" for item in assumptions[:12])
        row.update({key: value for key, value in decoder.items() if key in row})
        for key, value in requested.assignments.items():
            if key in row:
                row[key] = value
        result = compute(row)
        if not result.fail_modes:
            raise ValueError("Generated decoder geometry produced no calculable fail modes. "
                             "Revise the layout request; no buildsheet was returned.")
        generated_spec = _spec_from_llm(proposal, self.vocabulary)
        generated_spec.unit_ogd = unit_ogd
        generated_spec.unit_pgd = unit_pgd
        return row, notes, requested.merge(generated_spec)

    # -- interpretation and retrieval ---------------------------------------
    def _interpret(self, text: str) -> tuple[Spec, list[str]]:
        spec, notes = parse_prompt(text, self.prompt_fields)
        lowered = text.lower()
        spec.names = sorted(n for n in self.names | self._approved_names() if n.lower() in lowered)
        if self.llm:
            try:
                spec = _spec_from_llm(self.llm.interpret(text), self.vocabulary).merge(spec)
                notes.append(f"LLM interpretation merged ({self.llm.model}); explicit prompt values take priority")
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                notes.append(f"LLM unavailable, rule-based interpretation used: {exc}")
        return spec, notes

    def _approved_names(self) -> set[str]:
        return {a["row"].get("StructureName", "") for a in self.memory["approved"]}

    def _candidates(self) -> list[Reference]:
        learned = [Reference.from_row(dict(a["row"]), "learned", frozenset(a.get("tokens", [])))
                   for a in self.memory["approved"]]
        return self.references + learned

    def _score(self, ref: Reference, spec: Spec, prompt_tokens: set[str]) -> tuple[float, list[str]]:
        score, why = 0.0, []

        def add(points: float, reason: str) -> None:
            nonlocal score
            if points:
                score += points
                why.append(f"{points:+.1f} {reason}")

        if ref.name in spec.names:
            add(20, "exact structure name in prompt")
        if spec.cell_height:
            add(5 if ref.height == spec.cell_height else -5, f"cell height {ref.height}")
        if spec.mos:
            add(5 if ref.mos == spec.mos else -5, f"MOS {ref.mos}")
        if spec.flavor and ref.flavor == FLAVOR_LETTER[spec.flavor]:
            add(1.5, f"VT flavor {spec.flavor}")
        if spec.type_letter:
            add(4 if ref.stype == spec.type_letter else -2, f"type {ref.stype}")
        if spec.unit_ogd and ref.row.get("UnitCellSizeOgd") == str(spec.unit_ogd):
            add(2, f"unit cell Ogd {spec.unit_ogd}")
        if spec.unit_pgd and ref.row.get("UnitCellSizePgd") == str(spec.unit_pgd):
            add(2, f"unit cell Pgd {spec.unit_pgd}")
        if spec.devwidth_nm and ref.width_nm == spec.devwidth_nm:
            add(1, f"device width {spec.devwidth_nm} nm")
        if hits := prompt_tokens & ref.tokens:
            add(sum(self._idf.get(t, 1.0) for t in hits), "keywords " + ", ".join(sorted(hits)))
        if avoid := set(spec.negative_tokens) & ref.tokens:
            add(-3 * sum(self._idf.get(t, 1.0) for t in avoid), "avoided keywords " + ", ".join(sorted(avoid)))
        if {"sdr", "ref"} <= ref.tokens:
            add(0.6, "SDR reference baseline")
        weights = self.memory["token_weights"]
        learned = sum(weights.get(t, {}).get(ref.pattern, 0.0) for t in prompt_tokens)
        add(0.8 * max(-6.0, min(6.0, learned)), "learned from earlier feedback")
        if ref.source == "learned" and ref.prompt_tokens:
            overlap = len(prompt_tokens & ref.prompt_tokens) / len(prompt_tokens | ref.prompt_tokens or {""})
            if overlap:
                add(1 + 6 * overlap, f"previously approved design (prompt similarity {overlap:.0%})")
        return score, why

    def _select(self, spec: Spec, excluded: set[str]) -> tuple[Reference, float, list[str], list[str]]:
        candidates = [r for r in self._candidates() if r.name not in excluded]
        if spec.family:
            candidates = [r for r in candidates if r.family == spec.family]
        family_candidates = candidates
        if spec.direction:
            candidates = [r for r in candidates if r.direction == spec.direction]
        if spec.type_letter:
            candidates = [r for r in candidates if r.stype == spec.type_letter]
        if spec.cell_height:
            candidates = [r for r in candidates if r.height == spec.cell_height]
        if spec.mos:
            candidates = [r for r in candidates if r.mos == spec.mos]
        if spec.unit_ogd:
            candidates = [r for r in candidates if r.row.get("UnitCellSizeOgd") == str(spec.unit_ogd)]
        if spec.unit_pgd:
            candidates = [r for r in candidates if r.row.get("UnitCellSizePgd") == str(spec.unit_pgd)]
        if spec.exclude_open_cal:
            candidates = [r for r in candidates if not r.open_cal]
        if spec.include_open_cal:
            candidates = [r for r in candidates if r.open_cal]
        if not candidates:
            requested = [value for value in (
                spec.family,
                spec.direction,
                f"type {spec.type_letter}" if spec.type_letter else "",
                f"CH{spec.cell_height}" if spec.cell_height else "",
                f"MOS {spec.mos}" if spec.mos else "",
                "standard (not open-cal)" if spec.exclude_open_cal else "open-cal" if spec.include_open_cal else "",
            ) if value]
            available = [r for r in family_candidates if not spec.type_letter or r.stype == spec.type_letter]
            examples = sorted({f"{r.name} (CH{r.height}, MOS {r.mos or '?'}, {r.direction or 'direction unknown'})"
                               for r in available})[:4]
            detail = (" Available matching-type references: " + "; ".join(examples)) if examples else " No matching type reference is present in the library."
            if spec.family == "GATEGATE" and spec.type_letter == "B" and not examples:
                detail += (" The bundled GATEETE B sample is marked OGD and described as CTGOGD, so it is not a "
                           "validated GATEGATE/PGD template and was not substituted.")
            raise NoMatchingTemplate("No reference matches " + (" / ".join(requested) or "the requested filters")
                                     + ". No fallback design was selected." + detail)
        prompt_tokens = set(spec.tokens)
        scored = sorted(((*self._score(r, spec, prompt_tokens), i, r) for i, r in enumerate(candidates)),
                        key=lambda item: (-item[0], item[2]))
        best_score, why, _, best = scored[0]
        if not spec.family and best_score < 1.0:
            raise ValueError("Could not infer the structure. Mention a family (CTG, GATEGATE, EPIEPI, TCNTCN, "
                             "DIFFOGD, DIFFCHN) and attributes such as cell height, MOS and VT flavor.")
        alternatives = [f"{r.name} (score {s:.1f}, {r.source})" for s, _, _, r in scored[1:4]]
        return best, best_score, why, alternatives

    # -- adaptation and validation ------------------------------------------
    def _unique_name(self, name: str) -> str:
        taken = self.names | self._approved_names()
        base, n = AI_SUFFIX_RE.sub("", name), 1
        candidate = f"{base}:AI"
        while candidate in taken:
            n += 1
            candidate = f"{base}:AI{n}"
        return candidate

    def _build_row(self, ref: Reference, spec: Spec) -> tuple[dict[str, str], list[str]]:
        row, notes = dict(ref.row), []
        if spec.flavor:
            for column in ("DiffPatternUnit", "DiffPatternGlobal"):
                row[column] = DIFF_FLAVOR_RE.sub(lambda m: f"{m[1]}{m[2].lower()}{spec.flavor}", row.get(column, ""))
            notes.append(f"Diffusion VT flavor set to {spec.flavor}")
        if spec.devwidth_nm:
            width = f"{spec.devwidth_nm / 1000:.3f}"
            for column in ("DiffPatternUnit", "DiffPatternGlobal"):
                row[column] = DIFF_WIDTH_RE.sub(lambda m: f"{m[1]}{width}{m[3]}", row.get(column, ""))
            notes.append(f"Diffusion width set to {width} um")
        if spec.array_ogd:
            row["ArraySizeOgd"] = str(spec.array_ogd)
        if spec.array_pgd:
            row["ArraySizePgd"] = str(spec.array_pgd)
        if spec.array_ogd or spec.array_pgd:
            notes.append(f"Array set to {row.get('ArraySizeOgd')} x {row.get('ArraySizePgd')}")
        if spec.cell_height and ref.height != spec.cell_height:
            notes.append(f"WARNING: no matching template at cell height {spec.cell_height}; "
                         f"kept {ref.height} because track geometry is height-specific")
        if spec.mos and ref.mos != spec.mos:
            notes.append(f"WARNING: no matching {spec.mos} template; kept {ref.mos} diffusion layout")

        name = row.get("StructureName", "")
        if m := NAME_RE.match(name):
            letter = FLAVOR_LETTER[spec.flavor] if spec.flavor else m["flavor"]
            number = str(spec.devwidth_nm) if spec.devwidth_nm and not m["z"] else m["num"]
            name = f"{m['fam']}{m['height']}-{m['mos']}{m['z']}{number}-{letter}-{name[m.end():]}"
        for column, value in spec.assignments.items():
            row[column] = value
            notes.append(f"{column} set explicitly")
        if "StructureName" not in spec.assignments:
            geometry_changed = any(row.get(c, "") != ref.row.get(c, "") for c in self.fields
                                   if c not in ("StructureName", "StructureComments", "TestRow"))
            if geometry_changed and name in self.names | self._approved_names():
                name = self._unique_name(name)
            elif not geometry_changed and ref.source == "buildsheet":
                notes.append("Design is identical to the reference structure")
            row["StructureName"] = name
        if "TestRow" not in spec.assignments:
            row["TestRow"] = "AI_GEN"
        if "StructureComments" not in spec.assignments:
            row["StructureComments"] = f"AI design from {ref.name}"
        return row, notes

    def _evaluate(self, design: Design) -> None:
        row, design.result = design.row, None
        try:
            design.result = compute(row)
        except Exception as exc:  # malformed explicit field values must not crash the session
            design.warnings.append(f"Scalar calculation failed: {exc}")
        res = design.result
        scored = bool(res and res.fail_modes)
        design.fail_modes = res.fail_modes if scored else None
        design.scalar = res.scalar if scored else ""
        design.formula = res.formula if scored else ""
        row["Scalar"] = ",".join([design.scalar] * len(BIAS_CONDITIONS)) if scored else ""
        row["Test Type"] = ",".join(TEST_TYPES) if scored else ""
        row["Force"] = ",".join(BIAS_CONDITIONS) if scored else ""
        row[FORMULA_COL] = design.formula
        if res:
            design.warnings.extend(res.notes)
            if not scored:
                design.warnings.append("No fail modes found; scalar left blank")
            for issue in structure_name_issues([row], [res]):
                design.warnings.append(f"{issue['Check']}: expected {issue['Expected']}, observed {issue['Observed']}")

    def _realize(self, design: Design, notes: list[str]) -> None:
        notes = list(notes) + [f"WARNING: {item}" for item in design.spec.unsupported_requirements]
        ref, score, why, alternatives = self._select(design.spec, design.excluded)
        row, build_notes = self._build_row(ref, design.spec)
        design.template, design.source, design.score, design.row = ref.name, ref.source, score, row
        design.alternatives = alternatives
        design.reasoning = [n for n in notes if not n.startswith("WARNING")]
        design.reasoning += [f"Template {ref.name} ({ref.source}, score {score:.1f})"] + [f"  {w}" for w in why]
        design.reasoning += [n for n in build_notes if not n.startswith("WARNING")]
        design.warnings = [n.removeprefix("WARNING: ") for n in notes + build_notes if n.startswith("WARNING")]
        self._evaluate(design)

    def _realize_direct(self, design: Design, prompt: str, current_row: dict[str, str] | None = None,
                        require_alternative: bool = False) -> None:
        row, notes, design.spec = self._generate_from_model(prompt, design.spec, current_row)
        if require_alternative and current_row:
            geometry_columns = ("UnitCellSizeOgd", "UnitCellSizePgd", "ArraySizeOgd", "ArraySizePgd",
                                "DiffPatternUnit", "DiffPatternGlobal", "FTITracksUnit", "FTITracksGlobal",
                                "PolyPlugsUnit", "PolyPlugsGlobal", "TcnPlugsUnit", "TcnPlugsGlobal",
                                "VcxPatternUnit", "LO1ActiveTracks", "HI1ActiveTracks")
            if all(row.get(column, "") == current_row.get(column, "") for column in geometry_columns):
                raise ValueError("The model repeated the current geometry. Add a concrete layout change before generating an alternative.")
        design.template = "New decoder-DSL design"
        design.source = "LLM-generated geometry"
        design.score = 0.0
        design.row = row
        design.alternatives = []
        design.reasoning = ["Generated decoder geometry directly from the request; no complete library row was selected."] + notes
        design.warnings = list(design.spec.unsupported_requirements)
        self._evaluate(design)
        self.names.add(design.row["StructureName"])

    # -- public API ----------------------------------------------------------
    def get(self, design_id: str) -> Design:
        with self._lock:
            try:
                return self.designs[design_id]
            except KeyError:
                raise UnknownDesign(design_id) from None

    def open_library_row(self, source_row: dict[str, str]) -> Design:
        row = dict(source_row)
        reference = Reference.from_row(row, "81Z library")
        flavor = next((name for name, letter in FLAVOR_LETTER.items() if letter == reference.flavor), None)
        spec = Spec(
            family=reference.family,
            direction=reference.direction or None,
            cell_height=reference.height,
            mos=reference.mos or None,
            flavor=flavor,
            type_letter=reference.stype or None,
            devwidth_nm=reference.width_nm,
            array_ogd=_bounded_int(row.get("ArraySizeOgd"), 1, 10000),
            array_pgd=_bounded_int(row.get("ArraySizePgd"), 1, 10000),
            unit_ogd=_bounded_int(row.get("UnitCellSizeOgd"), 1, 1000),
            unit_pgd=_bounded_int(row.get("UnitCellSizePgd"), 1, 1000),
        )
        name = row.get("StructureName", "")
        design = Design(id=uuid.uuid4().hex[:12], prompt=name, spec=spec)
        design.template = name
        design.source = "81Z library"
        design.row = row
        design.status = "library"
        design.reasoning = ["Opened directly from the selected 81Z buildsheet row; no new structure was generated."]
        self._evaluate(design)
        with self._lock:
            self.designs[design.id] = design
        return design

    def open_library_row(self, source_row: dict[str, str]) -> Design:
        row = dict(source_row)
        reference = Reference.from_row(row, "81Z library")
        flavor = next((name for name, letter in FLAVOR_LETTER.items() if letter == reference.flavor), None)
        spec = Spec(
            family=reference.family,
            direction=reference.direction or None,
            cell_height=reference.height,
            mos=reference.mos or None,
            flavor=flavor,
            type_letter=reference.stype or None,
            devwidth_nm=reference.width_nm,
            array_ogd=_bounded_int(row.get("ArraySizeOgd"), 1, 10000),
            array_pgd=_bounded_int(row.get("ArraySizePgd"), 1, 10000),
            unit_ogd=_bounded_int(row.get("UnitCellSizeOgd"), 1, 1000),
            unit_pgd=_bounded_int(row.get("UnitCellSizePgd"), 1, 1000),
        )
        design = Design(id=uuid.uuid4().hex[:12], prompt=row.get("StructureName", ""), spec=spec)
        design.template = row.get("StructureName", "")
        design.source = "81Z library"
        design.row = row
        design.status = "library"
        self._evaluate(design)
        with self._lock:
            self.designs[design.id] = design
        return design

    def create(self, prompt: str) -> Design:
        prompt = prompt.strip()[:MAX_PROMPT_CHARS]
        if not prompt:
            raise ValueError("Prompt is empty")
        spec, notes = parse_prompt(prompt, self.prompt_fields)
        design = Design(id=uuid.uuid4().hex[:12], prompt=prompt, spec=spec)
        with self._lock:
            if self.llm:
                self._realize_direct(design, prompt)
            else:
                decoder_assignments = sorted(DECODER_FIELDS & spec.assignments.keys())
                if decoder_assignments:
                    raise ValueError("Decoder-level geometry requires a configured LLM generation endpoint. "
                                     "No pattern fields were applied: " + ", ".join(decoder_assignments))
                notes.append("LLM is not configured; this is a reference-template suggestion, not new geometry generation.")
                self._realize(design, notes)
            self.designs[design.id] = design
            self._log_event(design, "create", prompt)
        return design

    def revise(self, design_id: str, feedback: str) -> Design:
        feedback = feedback.strip()[:MAX_PROMPT_CHARS]
        if not feedback:
            raise ValueError("Feedback is empty")
        design = self.get(design_id)
        direct = design.source == "LLM-generated geometry"
        delta, notes = parse_prompt(feedback, self.prompt_fields) if direct else self._interpret(feedback)
        with self._lock:
            updated = copy.deepcopy(design)
            if ALTERNATIVE_RE.search(feedback.lower()):
                updated.excluded.add(updated.template)
                notes.append(f"Excluded previous template {updated.template} on request")
            updated.spec = updated.spec.merge(delta)
            updated.history.append({"action": "revise", "text": feedback, "template": updated.template})
            updated.version += 1
            updated.status = "draft"
            if direct:
                updated.prompt = f"{design.prompt}\nRevision: {feedback}"[:MAX_PROMPT_CHARS]
                self._realize_direct(updated, updated.prompt, design.row)
            else:
                self._realize(updated, notes)
            self.designs[design_id] = updated
            self._log_event(updated, "revise", feedback)
        return updated

    def accept(self, design_id: str, note: str = "") -> Design:
        design = self.get(design_id)
        with self._lock:
            if design.source != "LLM-generated geometry":
                pattern = generic_name(AI_SUFFIX_RE.sub("", design.template))
                for token in design.spec.tokens:
                    weights = self.memory["token_weights"].setdefault(token, {})
                    weights[pattern] = weights.get(pattern, 0.0) + 1.0
            name = design.row.get("StructureName", "")
            self.memory["approved"] = [a for a in self.memory["approved"] if a["row"].get("StructureName") != name]
            self.memory["approved"].append({"prompt": design.prompt, "tokens": design.spec.tokens,
                                            "template": design.template, "row": dict(design.row),
                                            "source": design.source,
                                            "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
            design.status = "accepted"
            design.history.append({"action": "accept", "text": note[:500], "template": design.template})
            self._log_event(design, "accept", note)
        return design

    def reject(self, design_id: str, reason: str = "") -> Design:
        design = self.get(design_id)
        reason = reason.strip()[:MAX_PROMPT_CHARS]
        direct = design.source == "LLM-generated geometry"
        delta, notes = (parse_prompt(reason, self.prompt_fields) if direct else self._interpret(reason)) if reason else (Spec(), [])
        with self._lock:
            updated = copy.deepcopy(design)
            rejected_pattern = generic_name(AI_SUFFIX_RE.sub("", updated.template))
            rejected_tokens = list(updated.spec.tokens)
            rejected_template = updated.template
            updated.excluded.add(rejected_template)
            updated.history.append({"action": "reject", "text": reason, "template": rejected_template})
            updated.spec = updated.spec.merge(delta)
            updated.version += 1
            updated.status = "draft"
            if direct:
                updated.prompt = (f"{design.prompt}\nGenerate a distinctly different layout; do not repeat the current decoder geometry. "
                                  f"Feedback: {reason or 'try another geometry'}")[:MAX_PROMPT_CHARS]
                self._realize_direct(updated, updated.prompt, design.row, require_alternative=True)
            else:
                self._realize(updated, notes + [f"Skipped {rejected_template}"])
                for token in rejected_tokens:
                    weights = self.memory["token_weights"].setdefault(token, {})
                    weights[rejected_pattern] = weights.get(rejected_pattern, 0.0) - 1.0
            self.designs[design_id] = updated
            self._log_event(updated, "reject", reason)
        return updated

    def layout_png(self, design_id: str, dpi: int = 120,
                   visible_layers: set[str] | None = None, global_view: bool = False,
                   global_units: tuple[int, int] = (1, 1), coords: str = "unit",
                   routing: bool = True) -> Path | None:
        design = self.get(design_id)
        from scalar_snapshot import OVERLAYS, layout_layers, render_global_snapshot, render_snapshot
        if design.result is None or design.result.family not in OVERLAYS:
            return None
        LAYOUT_DIR.mkdir(exist_ok=True)
        available = layout_layers(design.row)
        visible = set(available) if visible_layers is None else visible_layers
        layer_mask = "".join("1" if layer in visible else "0" for layer in available)
        suffix = (f"_global{global_units[0]}x{global_units[1]}{'r' if routing else ''}" if global_view
                  else f"_tile{global_units[0]}x{global_units[1]}" if global_units != (1, 1) else "") + f"_{coords}"
        extras = sorted(visible - set(available))
        if extras:
            suffix += "_x" + hashlib.sha1("|".join(extras).encode()).hexdigest()[:8]
        path = LAYOUT_DIR / f"{design.id}_v{design.version}_{dpi}_l{layer_mask}{suffix}.png"
        with self._render_lock:
            if not path.exists():
                try:
                    if global_view:
                        render_global_snapshot(design.row, path, dpi=dpi, visible_layers=visible,
                                               units_ogd=global_units[0], units_pgd=global_units[1],
                                               coords=coords, routing=routing)
                    elif global_units != (1, 1):
                        render_global_snapshot(design.row, path, dpi=dpi, visible_layers=visible,
                                               units_ogd=global_units[0], units_pgd=global_units[1],
                                               coords="unit", routing=False, buffer=False)
                    else:
                        render_snapshot(design.row, design.result, path,
                                        f"AI design v{design.version} from {design.template}", dpi=dpi,
                                        preview=True, visible_layers=visible, coords=coords)
                except Exception as exc:  # rendering is a preview; keep the design usable
                    design.warnings.append(f"Layout rendering failed: {exc}")
                    return None
        return path

    def buildsheet_csv(self, rows: list[dict[str, str]]) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=self.output_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    def export(self, rows: list[dict[str, str]], path: Path) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write(self.buildsheet_csv(rows))
