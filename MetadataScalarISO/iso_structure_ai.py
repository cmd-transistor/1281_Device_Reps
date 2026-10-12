"""Prompt-driven ISO structure designer.

A prompt such as ``"CTG 132 NP HVT type A SDR reference, array 5x60"`` is turned
into a buildsheet row:

1. **Interpret** -- a rule-based parser (optionally merged with an
   OpenAI-compatible LLM, see ``ISO_AI_*`` below) extracts family, cell
   height, MOS, VT flavor, type letter, array/unit-cell size, device width,
   design keywords and explicit ``Field=value`` lines.
2. **Retrieve** -- every scorable structure in ``ISO_Metadata_Input.csv`` plus
   every design approved earlier is scored against that spec; the best one is
   the template. Weights learned from accept/reject feedback shift the ranking.
3. **Adapt** -- VT flavor, device width, array size and explicit field values
   are applied to a copy of the template; the StructureName is updated to
   match and made unique when the geometry differs from an existing name.
4. **Validate** -- :func:`ScalarCalculation.compute` derives fail modes and
   the scalar; the ``Test Type`` / ``Force`` / ``Scalar`` vectors and name
   checks are filled in exactly as for the production buildsheet.

Feedback (``revise`` / ``accept`` / ``reject``) is persisted in
``iso_ai_memory.json``: approved rows become retrievable references and
prompt-keyword -> design-pattern weights are reinforced or penalised.

Optional LLM interpretation (OpenAI-compatible ``/chat/completions``):
``ISO_AI_ENDPOINT`` (https, or http on localhost), ``ISO_AI_MODEL`` and
``ISO_AI_API_KEY``. LLM output only fills validated spec fields; it can never
write buildsheet cells directly.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
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

FAMILY_PATTERNS = (
    ("DIFFCHN", r"diff\s*-?\s*chn|diff(?:usion)?[\s-]*chain"),
    ("DIFFOGD", r"diff\s*-?\s*ogd|diff(?:usion)?[\s-]*(?:to[\s-]*diff(?:usion)?|isolation)|fti[\s-]*(?:isolation|cut)"),
    ("CTG", r"\bctg(?![a-z])|contact[\s-]*to[\s-]*gate|gate[\s-]*to[\s-]*contact"),
    ("GATEGATE", r"gate\s*-?\s*gate|gate[\s-]*to[\s-]*gate|gate[\s-]*(?:ete|end[\s-]*to[\s-]*end)|poly[\s-]*ete"),
    ("TCNTCN", r"tcn\s*-?\s*tcn|tcn[\s-]*to[\s-]*tcn|tcn[\s-]*(?:ete|end[\s-]*to[\s-]*end)|trench[\s-]*contact"),
    ("EPIEPI", r"epi\s*-?\s*epi|epi[\s-]*to[\s-]*epi|\bepi(?![a-z])"),
)
VIA_CHAIN_RE = re.compile(r"via[\s-]*chain|\b(?:vt|vg|vcr)_mv")
ALTERNATIVE_RE = re.compile(r"\b(another|different|alternative|other (?:template|option)|something else|next option)\b")
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


# ---------------------------------------------------------------------------
# Prompt specification
# ---------------------------------------------------------------------------
@dataclass
class Spec:
    family: str | None = None
    cell_height: int | None = None
    mos: str | None = None
    flavor: str | None = None
    type_letter: str | None = None
    array_ogd: int | None = None
    array_pgd: int | None = None
    unit_ogd: int | None = None
    unit_pgd: int | None = None
    devwidth_nm: int | None = None
    names: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    negative_tokens: list[str] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)

    def merge(self, other: "Spec") -> "Spec":
        """``other`` wins for every value it sets; keyword sets are combined."""
        merged = Spec(**{k: getattr(other, k) if getattr(other, k) is not None else getattr(self, k)
                         for k in SCALAR_SPEC_FIELDS})
        negative = set(self.negative_tokens) - set(other.tokens) | set(other.negative_tokens)
        merged.tokens = sorted((set(self.tokens) | set(other.tokens)) - negative)
        merged.negative_tokens = sorted(negative)
        merged.names = sorted(set(self.names) | set(other.names))
        merged.assignments = {**self.assignments, **other.assignments}
        return merged


SCALAR_SPEC_FIELDS = ("family", "cell_height", "mos", "flavor", "type_letter", "array_ogd", "array_pgd",
                      "unit_ogd", "unit_pgd", "devwidth_nm")


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
        "optional keys: family (CTG|GATEGATE|EPIEPI|TCNTCN|DIFFOGD|DIFFCHN), cell_height (110|132|165), "
        "mos (N|P|NP), flavor (hvt|svt|lvt|ulvt|ulvtll|elvt|uvt|evt), type_letter (one letter), "
        "array_ogd (int), array_pgd (int), devwidth_nm (int), tokens (lowercase design keywords such as "
        "sdr, ref, pxl, ftiml, ftisl, cd, reg, den, vcr, vcrnub, 1trk, 2trk, len10), negative_tokens "
        "(keywords to avoid). Omit anything the request does not state or clearly imply."
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

    def interpret(self, text: str) -> dict:
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "system", "content": self.SYSTEM}, {"role": "user", "content": text}]}
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


# ---------------------------------------------------------------------------
# Reference library and designs
# ---------------------------------------------------------------------------
def _row_tokens(row: dict[str, str]) -> frozenset[str]:
    text = f"{row.get('StructureName', '')} {row.get('StructureComments', '')}".lower()
    words = {w for w in re.split(r"[^a-z0-9]+", text) if len(w) > 1}
    # compound name parts such as 1FCLEN10 / STKx2FLEN8 also index their LEN and TRK tokens
    words |= set(re.findall(r"len\d+|\d+trk|stkx\d+", text))
    return frozenset(words)


@dataclass
class Reference:
    name: str
    row: dict[str, str]
    family: str
    source: str
    tokens: frozenset[str]
    pattern: str
    height: int | None
    mos: str
    flavor: str
    stype: str
    width_nm: int | None
    prompt_tokens: frozenset[str] = frozenset()

    @classmethod
    def from_row(cls, row: dict[str, str], source: str, prompt_tokens: frozenset[str] = frozenset()) -> "Reference":
        name = row.get("StructureName", "").strip()
        m = NAME_RE.match(name)
        width = DIFF_WIDTH_RE.search(row.get("DiffPatternUnit", ""))
        height_digits = re.sub(r"\D", "", row.get("CellHeight", ""))
        return cls(
            name=name, row=row, family=family_of(name), source=source,
            tokens=_row_tokens(row) | prompt_tokens, pattern=generic_name(AI_SUFFIX_RE.sub("", name)),
            height=int(m["height"]) if m else (int(height_digits) if height_digits else None),
            mos=m["mos"] if m else "", flavor=m["flavor"] if m else "", stype=type_of(name),
            width_nm=round(float(width[2]) * 1000) if width else None, prompt_tokens=prompt_tokens,
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
                    "learned_keywords": len(self.memory["token_weights"]), "llm": bool(self.llm)}

    # -- interpretation and retrieval ---------------------------------------
    def _interpret(self, text: str) -> tuple[Spec, list[str]]:
        spec, notes = parse_prompt(text, self.fields)
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
        if not candidates:
            raise ValueError("No remaining reference structures match this request"
                             + (f" for family {spec.family}" if spec.family else ""))
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
        ref, score, why, alternatives = self._select(design.spec, design.excluded)
        row, build_notes = self._build_row(ref, design.spec)
        design.template, design.source, design.score, design.row = ref.name, ref.source, score, row
        design.alternatives = alternatives
        design.reasoning = [n for n in notes if not n.startswith("WARNING")]
        design.reasoning += [f"Template {ref.name} ({ref.source}, score {score:.1f})"] + [f"  {w}" for w in why]
        design.reasoning += [n for n in build_notes if not n.startswith("WARNING")]
        design.warnings = [n.removeprefix("WARNING: ") for n in notes + build_notes if n.startswith("WARNING")]
        self._evaluate(design)

    # -- public API ----------------------------------------------------------
    def get(self, design_id: str) -> Design:
        with self._lock:
            try:
                return self.designs[design_id]
            except KeyError:
                raise UnknownDesign(design_id) from None

    def create(self, prompt: str) -> Design:
        prompt = prompt.strip()[:MAX_PROMPT_CHARS]
        if not prompt:
            raise ValueError("Prompt is empty")
        spec, notes = self._interpret(prompt)
        design = Design(id=uuid.uuid4().hex[:12], prompt=prompt, spec=spec)
        with self._lock:
            self._realize(design, notes)
            self.designs[design.id] = design
            self._log_event(design, "create", prompt)
        return design

    def revise(self, design_id: str, feedback: str) -> Design:
        feedback = feedback.strip()[:MAX_PROMPT_CHARS]
        if not feedback:
            raise ValueError("Feedback is empty")
        design = self.get(design_id)
        delta, notes = self._interpret(feedback)
        with self._lock:
            if ALTERNATIVE_RE.search(feedback.lower()):
                design.excluded.add(design.template)
                notes.append(f"Excluded previous template {design.template} on request")
            design.spec = design.spec.merge(delta)
            design.history.append({"action": "revise", "text": feedback, "template": design.template})
            design.version += 1
            design.status = "draft"
            self._realize(design, notes)
            self._log_event(design, "revise", feedback)
        return design

    def accept(self, design_id: str, note: str = "") -> Design:
        design = self.get(design_id)
        with self._lock:
            pattern = generic_name(AI_SUFFIX_RE.sub("", design.template))
            for token in design.spec.tokens:
                weights = self.memory["token_weights"].setdefault(token, {})
                weights[pattern] = weights.get(pattern, 0.0) + 1.0
            name = design.row.get("StructureName", "")
            self.memory["approved"] = [a for a in self.memory["approved"] if a["row"].get("StructureName") != name]
            self.memory["approved"].append({"prompt": design.prompt, "tokens": design.spec.tokens,
                                            "template": design.template, "row": dict(design.row),
                                            "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
            design.status = "accepted"
            design.history.append({"action": "accept", "text": note[:500], "template": design.template})
            self._log_event(design, "accept", note)
        return design

    def reject(self, design_id: str, reason: str = "") -> Design:
        design = self.get(design_id)
        reason = reason.strip()[:MAX_PROMPT_CHARS]
        delta, notes = self._interpret(reason) if reason else (Spec(), [])
        with self._lock:
            pattern = generic_name(AI_SUFFIX_RE.sub("", design.template))
            for token in design.spec.tokens:
                weights = self.memory["token_weights"].setdefault(token, {})
                weights[pattern] = weights.get(pattern, 0.0) - 1.0
            design.excluded.add(design.template)
            design.history.append({"action": "reject", "text": reason, "template": design.template})
            design.spec = design.spec.merge(delta)
            design.version += 1
            design.status = "draft"
            self._realize(design, notes + [f"Rejected template {design.template}"])
            self._log_event(design, "reject", reason)
        return design

    def layout_png(self, design_id: str, dpi: int = 100) -> Path | None:
        design = self.get(design_id)
        from scalar_snapshot import OVERLAYS, render_snapshot
        if design.result is None or design.result.family not in OVERLAYS:
            return None
        LAYOUT_DIR.mkdir(exist_ok=True)
        path = LAYOUT_DIR / f"{design.id}_v{design.version}_{dpi}.png"
        with self._render_lock:
            if not path.exists():
                try:
                    render_snapshot(design.row, design.result, path,
                                    f"AI design v{design.version} from {design.template}", dpi=dpi)
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
