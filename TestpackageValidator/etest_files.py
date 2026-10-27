"""Read-only access to Etest XML recipes and TestSpec files on network shares, using the user's Windows account.

Only directory listing and 'rb' file reads are used; this module has no write, rename or delete paths.
"""
from __future__ import annotations

import io
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePath

import pandas as pd
from lxml import etree

DEFAULT_ROOTS = {
    'EOL XML (Verigy AGX)': r'\\rf2p-sram-nas2.rf2prod.mfg.intel.com\esram_vss_shadow\Etest\VERIGY\AGX',
    'Inline XML (recipes)': r'\\rf3p-nas-patest.rf3prod.mfg.intel.com\patest\recipes',
    'TestSpec (P1277)': r'\\ra2pznas01.rf2prod.mfg.intel.com\Etest\P1277\TestSpec',
}
MAX_BYTES = 64 * 1024 * 1024
EXCEL_EXT = {'.xlsx', '.xlsm', '.xls'}
TOKEN_RX = re.compile(r'[A-Za-z0-9][A-Za-z0-9_:\-./+]*')


@dataclass(frozen=True)
class Entry:
    path: str
    name: str
    is_dir: bool
    size: int
    modified: float


def list_dir(path: str) -> list[Entry]:
    out = []
    with os.scandir(path) as it:
        for e in it:
            try:
                is_dir = e.is_dir()
                st = e.stat()
            except OSError:
                continue
            out.append(Entry(e.path, e.name, is_dir, 0 if is_dir else st.st_size, st.st_mtime))
    return sorted(out, key=lambda x: (not x.is_dir, x.name.casefold()))


def read_bytes(path: str) -> bytes:
    size = os.stat(path).st_size
    if size > MAX_BYTES:
        raise ValueError(f'{PurePath(path).name} is {size / 1e6:.0f} MB; the viewer limit is {MAX_BYTES / 1e6:.0f} MB')
    with open(path, 'rb') as f:
        return f.read()


def decode(data: bytes) -> str:
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def file_kind(path: str, data: bytes) -> str:
    if PurePath(path).suffix.lower() in EXCEL_EXT:
        return 'excel'
    head = decode(data[:512]).lstrip()
    return 'xml' if head.startswith('<') else 'text'


def parse_xml(data: bytes):
    # entity expansion, DTD loading and network access are disabled (XXE / entity-bomb safe)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)
    return etree.fromstring(data, parser)


def element_row(el) -> tuple[str, str, str]:
    text = (el.text or '').strip()[:300]
    if not isinstance(el.tag, str):
        kind = '#comment' if isinstance(el, etree._Comment) else '#entity' if isinstance(el, etree._Entity) else '#node'
        return kind, '', text
    attrs = '; '.join(f'{etree.QName(k).localname}={v}' for k, v in el.attrib.items())
    return etree.QName(el).localname, attrs, text


def read_excel(data: bytes) -> dict[str, pd.DataFrame]:
    return pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)


def read_table(text: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine='python', dtype=str, keep_default_na=False)
    except Exception:
        return None
    return df if len(df.columns) > 1 else None


def reference_counts(kind: str, data: bytes, root=None, sheets: dict[str, pd.DataFrame] | None = None) -> Counter:
    counts = Counter(TOKEN_RX.findall(decode(data))) if kind != 'excel' else Counter()
    values = []
    if root is not None:
        for el in root.iter():
            values += list(el.attrib.values()) + [el.text or '']
    for df in (sheets or {}).values():
        values += [v for v in df.fillna('').to_numpy().ravel() if isinstance(v, str)]
    for v in values:
        v = v.strip()
        if not v:
            continue
        if kind == 'excel':
            counts.update(TOKEN_RX.findall(v))
        # whole values that are a single token are already counted by the tokenizer
        if not TOKEN_RX.fullmatch(v):
            counts[v] += 1
    return counts
