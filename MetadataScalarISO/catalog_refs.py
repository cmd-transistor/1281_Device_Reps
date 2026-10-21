"""Structure catalogs (PowerPoint decks in ``81Z Catalog/``) as slide PNGs plus a text index, so a
structure can be shown next to the catalog slides that name it."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

CATALOG_DIR = Path(__file__).with_name("81Z Catalog")
EXTRACT_DIR = Path(__file__).with_name("extracted") / "catalog"


def _deck_dir(deck: Path) -> Path:
    return EXTRACT_DIR / re.sub(r"[^A-Za-z0-9]+", "_", deck.stem)[:60]


def pending_decks() -> list[Path]:
    """Decks whose slides have not been exported yet (or changed since)."""
    out = []
    for deck in sorted(CATALOG_DIR.rglob("*.pptx")):
        if deck.name.startswith("~$"):
            continue
        index = _deck_dir(deck) / "index.json"
        if not index.exists() or index.stat().st_mtime < deck.stat().st_mtime:
            out.append(deck)
    return out


def _shape_texts(shapes, out: list[str]) -> None:
    for shape in shapes:
        try:
            if shape.Type == 6:  # msoGroup
                _shape_texts(shape.GroupItems, out)
            elif shape.HasTable:
                table = shape.Table
                out.append(" | ".join(table.Cell(r, c).Shape.TextFrame.TextRange.Text.strip()
                                      for r in range(1, table.Rows.Count + 1)
                                      for c in range(1, table.Columns.Count + 1)))
            elif shape.HasTextFrame and shape.TextFrame.HasText:
                out.append(shape.TextFrame.TextRange.Text.strip().replace("\r", "\n"))
        except Exception:  # charts, OLE objects and the like carry no readable text
            continue


def extract(decks: list[Path] | None = None, progress=None) -> int:
    """Export slides (PNG) and slide text through PowerPoint, read-only; returns the number of decks done.

    PowerPoint is used (not python-pptx) because sensitivity-labelled decks are encrypted containers.
    """
    import pythoncom
    import win32com.client

    decks = pending_decks() if decks is None else decks
    if not decks:
        return 0
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    try:
        for number, deck in enumerate(decks, 1):
            target = _deck_dir(deck)
            target.mkdir(parents=True, exist_ok=True)
            if progress:
                progress(f"Exporting catalog {number}/{len(decks)}: {deck.name}")
            presentation = app.Presentations.Open(str(deck), True, False, False)  # read-only, no window
            try:
                index = []
                for i in range(1, presentation.Slides.Count + 1):
                    slide = presentation.Slides(i)
                    texts: list[str] = []
                    _shape_texts(slide.Shapes, texts)
                    index.append({"slide": i, "text": [text for text in texts if text]})
                    png = target / f"slide_{i:03d}.png"
                    if not png.exists():
                        slide.Export(str(png), "PNG", 1600, 900)
            finally:
                presentation.Close()
            (target / "index.json").write_text(json.dumps({"deck": deck.name, "slides": index}, indent=1),
                                               encoding="utf-8")
    finally:
        app.Quit()
        pythoncom.CoUninitialize()
    _index.cache_clear()
    return len(decks)


@lru_cache(maxsize=1)
def _index() -> list[dict]:
    slides = []
    for path in sorted(EXTRACT_DIR.glob("*/index.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        deck, entries = (data["deck"], data["slides"]) if isinstance(data, dict) else (path.parent.name, data)
        for entry in entries:
            text = "\n".join(entry["text"])
            slides.append({"deck": deck, "slide": entry["slide"], "png": path.parent / f"slide_{entry['slide']:03d}.png",
                           "text": text.casefold(), "title": next(iter(entry["text"]), "")[:120]})
    return slides


def find_slides(row: dict[str, str], limit: int = 12) -> list[dict]:
    """Catalog slides for a structure, best first: exact name, name without the skew value, test row,
    then the skew feature (e.g. ``PXL:REG``, ``VCRNUB:OGD:CD``) within the same structure family."""
    name = (row.get("StructureName") or "").strip().casefold()
    test_row = (row.get("TestRow") or row.get("TestRowName") or "").strip().casefold()
    stem = re.sub(r"[:_-]?[np]\d+(?:\.\d+)?$|:\d+(?:\.\d+)?$", "", name)
    family = re.match(r"[a-z]+", name)
    feature = re.search(r"((?:[a-z]+:)+(?:cd|reg)|[a-z]+-(?:cd|reg)-(?:ogd|pgd|por))", name)
    scored = []
    for slide in _index():
        text = slide["text"]
        score = 0
        if name and name in text:
            score += 100
        elif stem and len(stem) > 8 and stem in text:
            score += 60
        if len(test_row) >= 6 and test_row in text:
            score += 40
        if feature and feature[1] in text and family and re.search(rf"\b{family[0]}", text):
            score += 20
        if score and slide["png"].exists():
            scored.append({**slide, "score": score})
    scored.sort(key=lambda slide: (-slide["score"], slide["deck"], slide["slide"]))
    return scored[:limit]
