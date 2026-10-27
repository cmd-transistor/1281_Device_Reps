"""Data model behind testpackage_gui.py: multi-node superflow selection, comparison, validation and export.

Every superflow line is one test (Test Row Alias, Test Structure Alias, Test Configuration Name, Test Names) with an
'X' in each test package (flow) column it belongs to. A package selection is a boolean mask over these lines; add /
drop works on structures, test configs or rows and is compared with the original column or a selected package/superflow.
"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import sharepoint_excel

HERE = Path(__file__).resolve().parent
SUPERFLOW = HERE / '1X281Z_SUPER_FLOW_R2 - FE_26WW43p1_v2.csv'
VALIDATION = HERE / 'OUTPUT' / 'StructureName_Validation.csv'
ATLANTIS = HERE / 'OUTPUT' / 'Atlantis_81Z_TapeIn_Structures_raw.csv'
ROW_SUMMARY = HERE / 'OUTPUT' / 'Atlantis_Row_Summary.csv'
ROS_TREE = HERE / 'OUTPUT' / 'ROS_Structure_Tree.csv'
TRACKER = HERE / 'X81Z_Device_Testrow_Tracker.xlsx'
SHAREPOINT_URL = ('https://intel.sharepoint.com/sites/ltdtestdevelopment/7nm/Parametric%20Test%20Documents/'
                  'Forms/AllItems.aspx?viewid=b40b5a38%2D90c6%2D4568%2Db88f%2D6f47a2921ee7')
BASE_COLS = ['Test Row Alias', 'Test Structure Alias', 'Test Configuration Name', 'Test Names']
ROW_TC = re.compile(r'_TC\d+[A-Z]?_\d+$')
TAPE_IN = 'Configured for Tape-in'
FAMILY_CFG = {   # same as structure_name_validator.FAMILY_CFG
    'ROS': r'^ROS_', 'ROSPFT': r'^(KEY_LOGIC|MAT10P|AUTOBIAS|BTI|HCI)', 'PFT': r'^(KEY_LOGIC|MAT10P|AUTOBIAS|BTI|HCI)',
    'BITCELL': r'^(KEY_LOGIC|MAT10P|AUTOBIAS|BTI|HCI)', 'BCLA': r'^BCLA', 'MLR': r'^(IG_|LD_|COV|CGT|VBD)',
    'ISO': r'^(I\d|C_)', 'KLV': r'^R\d', 'QREL': r'^(BTI|HCI|IG_|VBD|COV|LD_|CGT)', 'BE': r'^(R\d|I\d|C_)',
    'FK': r'^(KEY_LOGIC|FKTCN)',
}
STRUCT_COLS = ['State', 'Row', 'Library', 'Owner', 'Structure', 'Corrected_Name', 'Family', 'CH', 'Z', 'LNR_nm',
               'POL', 'VT', 'Legs', 'Env', 'Configs', 'Tests', 'In_Package', 'Status']


@dataclass(frozen=True)
class SuperflowSource:
    path: Path
    node: str
    revision: int

    @property
    def label(self) -> str:
        rev = f'R{self.revision}' if self.revision >= 0 else 'revision unknown'
        return f'{self.node} | {rev} | {self.path.name}'


def discover_onedrive_roots() -> list[Path]:
    candidates = [Path(os.environ[key]) for key in ('OneDriveCommercial', 'OneDrive') if os.environ.get(key)]
    candidates.append(Path.home() / 'OneDrive - Intel Corporation')
    roots = []
    for path in candidates:
        path = path.expanduser()
        if path.is_dir():
            resolved = path.resolve()
            if resolved not in roots:
                roots.append(resolved)
    return roots


def discover_superflows(root: Path, extra_paths=(), extra_roots=()) -> list[SuperflowSource]:
    roots = {Path(root).expanduser().resolve(), *(Path(p).expanduser().resolve() for p in extra_roots)}
    files = set()
    for base in roots:
        for path in base.glob('**/*.csv'):
            normalized = re.sub(r'[^A-Z0-9]', '', path.stem.upper())
            is_superflow = 'SUPERFLOW' in normalized or (
                'TESTPACKAGE' in normalized and ('DEFINITION' in normalized or 'PACKAGEDEF' in normalized))
            if is_superflow and not path.name.startswith('~$'):
                files.add(path.resolve())
    files.update(Path(p).expanduser().resolve() for p in extra_paths
                 if Path(p).expanduser().is_file() and Path(p).suffix.lower() == '.csv')
    if root.resolve() == HERE.resolve() and SUPERFLOW.exists():
        files.add(SUPERFLOW.resolve())
    sources = []
    for path in files:
        node_match = re.search(r'(?:P\d{4}|1X\d+Z)', path.stem, re.IGNORECASE)
        node = node_match.group(0).upper() if node_match else path.stem.split('SUPER', 1)[0].strip(' _-.') or 'Other'
        rev_match = re.search(r'(?:^|[^A-Z0-9])R(\d+)(?:[^A-Z0-9]|$)', path.stem, re.IGNORECASE)
        revision = int(rev_match.group(1)) if rev_match else -1
        sources.append(SuperflowSource(path, node, revision))
    return sorted(sources, key=lambda source: (source.node, -source.revision, -source.path.stat().st_mtime,
                                                source.path.name.casefold()))


def discover_config_docs(root: Path, extra_roots=()) -> dict[str, Path]:
    roots = {HERE, Path(root), *(Path(p) for p in extra_roots), *discover_onedrive_roots()}
    paths = {p.resolve() for base in roots if base.exists()
             for p in base.glob('**/P12*_Test_Configs*.xlsx') if not p.name.startswith('~$')}
    ordered = sorted(paths, key=lambda p: (('1280' not in p.name), p.name.casefold(), str(p).casefold()))
    counts = pd.Series([p.name for p in ordered]).value_counts().to_dict()
    return {(p.name if counts[p.name] == 1 else f'{p.parent.name}/{p.name}'): p for p in ordered}


@dataclass
class Change:
    label: str
    mask: np.ndarray
    value: bool
    before: np.ndarray = field(repr=False, default=None)


class ConfigDocument:
    """Test config workbook (P1280 / P1278): finds the block of a test config in any sheet."""

    SUFFIXES = ['_DRN', '_FKTCN_D', '_FKTCN_S', '_D', '_S', '_LONG', '_NOTOP', '_5T', '_5PT', '_4PT', '_3PT', '_2PT',
                '_X801A_LEGO_D_DRN', '_SHORT']

    def __init__(self, path: Path | str, sheets: dict[str, pd.DataFrame] | None = None):
        self.path = path
        if sheets is None:
            sheets = {s: pd.read_excel(path, sheet_name=s, header=None, dtype=str)
                      for s in pd.ExcelFile(path).sheet_names}
        self.sheets = {s: g.fillna('').astype(str).map(str.strip) for s, g in sheets.items()}
        self.values = {}
        for s, g in self.sheets.items():
            for (r, c), v in np.ndenumerate(g.values):
                if v and len(v) < 60:
                    self.values.setdefault(v.upper(), []).append((s, r, c))

    def candidates(self, cfg: str) -> list[str]:
        out, name = [cfg.upper()], cfg.upper()
        for _ in range(3):
            for suf in self.SUFFIXES:
                if name.endswith(suf):
                    name = name[:-len(suf)]
                    out.append(name)
        parts = cfg.upper().split('_')
        return out + ['_'.join(parts[:k]) for k in range(len(parts) - 1, 1, -1)]

    def lookup(self, cfg: str) -> tuple[str, list[tuple[str, pd.DataFrame]]]:
        for cand in self.candidates(cfg):
            hits = self.values.get(cand, [])
            if hits:
                blocks = {}
                for s, r, c in hits:
                    b = self.block(s, r, c)
                    if len(b) > len(blocks.get(s, ())):
                        blocks[s] = b
                return cand, sorted(blocks.items(), key=lambda kv: -len(kv[1]))[:6]
        return '', []

    def block(self, sheet: str, r: int, c: int) -> pd.DataFrame:
        g = self.sheets[sheet]
        end = r + 1
        while end < len(g) and g.iat[end, c] == '' and any(g.iloc[end].astype(bool)):
            end += 1
        head = next((i for i in range(r, -1, -1) if any('Test Name' in v for v in g.iloc[i])), None)
        rows = g.iloc[r:end]
        cols = [k for k in g.columns if rows[k].astype(bool).any() or (head is not None and g.iat[head, k])]
        out = rows[cols].copy()
        if head is not None:
            names = [g.iat[head, k] or (g.iat[head + 1, k] if head + 1 < len(g) else '') or f'C{k}' for k in cols]
            out.columns = [n if names.count(n) == 1 else f'{n} {i}' for i, n in enumerate(names)]
        return out


class TestPackageModel:
    def __init__(self, progress=lambda msg: None, data_root: Path | None = None,
                 superflow_path: Path | None = None, node: str | None = None,
                 sharepoint_docs: list[str] | None = None, superflow_paths: list[Path] | None = None):
        self.data_root = Path(data_root or HERE).expanduser()
        self.superflow_paths = [Path(p).expanduser().resolve() for p in superflow_paths or []]
        self.onedrive_roots = discover_onedrive_roots()
        self.sources = discover_superflows(self.data_root, self.superflow_paths, self.onedrive_roots)
        if not self.sources:
            raise FileNotFoundError(SUPERFLOW)
        source_by_path = {source.path: source for source in self.sources}
        if superflow_path is not None:
            selected_path = Path(superflow_path).expanduser().resolve()
            if selected_path not in source_by_path:
                raise FileNotFoundError(selected_path)
        else:
            latest = {}
            for source in self.sources:
                if source.node not in latest:
                    latest[source.node] = source
            preferred_node = node if node in latest else ('1X281Z' if '1X281Z' in latest else sorted(latest)[0])
            selected_path = latest[preferred_node].path
        self.superflow_source = source_by_path[selected_path]
        self.superflow_path = selected_path
        self.docs: dict[str, Path | str] = discover_config_docs(
            self.data_root, [path.parent for path in self.superflow_paths] + self.onedrive_roots)
        self.sharepoint_docs: list[str] = []
        for url in sharepoint_docs or []:
            self.add_sharepoint_doc(url)
        progress('Loading superflow ...')
        sf = pd.read_csv(self.superflow_path, dtype=str, keep_default_na=False)
        self.columns = list(sf.columns)
        self.flows = [c for c in sf.columns if c not in BASE_COLS]
        self.sf = sf
        self.row_key = sf['Test Row Alias'].str.replace(ROW_TC, '', regex=True).str.lower()
        self.test = sf['Test Names'].str.split('|').str[0]
        self.struct_codes, self.struct_index = pd.factorize(sf['Test Structure Alias'])
        self.config_codes, self.config_index = pd.factorize(sf['Test Configuration Name'])

        progress('Loading validator results ...')
        self.validator_available = self.superflow_source.node == '1X281Z' and VALIDATION.exists()
        if self.validator_available:
            val = pd.read_csv(VALIDATION, dtype=str, keep_default_na=False)
            val = val[val.Source == 'Superflow'].drop_duplicates('Original_Name').set_index('Original_Name')
        else:
            val = pd.DataFrame(columns=['Corrected_Name', 'Family', 'Status', 'Max_Severity', 'Issue_Codes', 'CH',
                                        'DIFF', 'DIFF_W', 'POL', 'VT', 'ENV', 'LEGS', 'ADF'],
                               index=pd.Index([], name='Original_Name'))

        progress('Loading Atlantis rows and owners ...')
        lib, status = {}, {}
        if self.validator_available and ATLANTIS.exists():
            a = pd.read_csv(ATLANTIS, dtype=str, keep_default_na=False)
            lib = dict(zip(a.TestRowName.str.lower(), a.Library))
            status = dict(zip(a.TestRowName.str.lower(), a.RowStatus))
        owner = {}
        if self.validator_available and ROW_SUMMARY.exists():
            rs = pd.read_csv(ROW_SUMMARY, dtype=str, keep_default_na=False)
            owner = {k: v for k, v in zip(rs.TESTROWNAME.str.lower(), rs.OWNER) if v}
        self.row_status = status

        g = pd.DataFrame({'s': self.struct_codes, 'row': sf['Test Row Alias'], 'rk': self.row_key,
                          'cfg': sf['Test Configuration Name']}).groupby('s')
        st = pd.DataFrame({'Structure': self.struct_index})
        st['Row'] = g.row.first().values
        st['RowKey'] = g.rk.first().values
        st['Configs'] = g.cfg.nunique().values
        st['Tests'] = g.size().values
        st['Library'] = st.RowKey.map(lib).fillna('')
        st['Owner'] = st.RowKey.map(owner).fillna('')
        st['Row_Status'] = st.RowKey.map(status).fillna('not loaded' if not self.validator_available else 'not in Atlantis')
        v = val.reindex(st.Structure)
        st['Corrected_Name'] = v.Corrected_Name.fillna('').where(v.Corrected_Name.fillna('').ne(''), st.Structure).values
        st['Family'] = v.Family.fillna('').values
        st['Status'] = v.Status.fillna('').values
        st['Max_Severity'] = v.Max_Severity.fillna('').values
        st['Issue_Codes'] = v.Issue_Codes.fillna('').values
        for src, dst in [('CH', 'CH'), ('DIFF', 'Z'), ('DIFF_W', 'LNR_nm'), ('POL', 'POL'), ('VT', 'VT'),
                         ('ENV', 'Env')]:
            st[dst] = v[src].fillna('').values
        st['Legs'] = np.where(v.LEGS.fillna('').values != '', v.LEGS.fillna('').values, v.ADF.fillna('').values)
        st['Base'] = st.Corrected_Name.str.replace(ROW_TC, '', regex=True)
        self.structs = st

        progress('Loading ROS tree and test config documents ...')
        self.tree = (pd.read_csv(ROS_TREE, dtype=str, keep_default_na=False)
                 if self.validator_available and ROS_TREE.exists() else pd.DataFrame())
        self._doc_cache: dict[str, ConfigDocument] = {}
        self.package = None
        self.orig = np.zeros(len(sf), bool)
        self.sel = self.orig.copy()
        self.history: list[Change] = []
        self.set_package(self.flows[0])

    # ----- package state -------------------------------------------------------------------------------------------
    def set_package(self, name: str, new: bool = False):
        if new and name not in self.sf.columns:
            self.sf[name] = ''
            self.flows.append(name)
            self.columns.append(name)
        self.package = name
        self.orig = (self.sf[name].str.strip() != '').to_numpy()
        self.sel = self.orig.copy()
        self.history.clear()

    def apply(self, label: str, mask: np.ndarray, value: bool) -> int:
        changed = mask & (self.sel != value)
        if not changed.any():
            return 0
        self.history.append(Change(label, changed, value, self.sel[changed].copy()))
        self.sel[changed] = value
        return int(changed.sum())

    def undo(self) -> str:
        if not self.history:
            return ''
        ch = self.history.pop()
        self.sel[ch.mask] = ch.before
        return ch.label

    def reset(self):
        self.sel = self.orig.copy()
        self.history.clear()

    # ----- masks ----------------------------------------------------------------------------------------------------
    def lines(self, structures=None, configs=None, rows=None) -> np.ndarray:
        m = np.ones(len(self.sf), bool)
        if structures is not None:
            idx = self.struct_index.get_indexer(list(structures))
            m &= np.isin(self.struct_codes, idx[idx >= 0])
        if configs is not None:
            idx = self.config_index.get_indexer(list(configs))
            m &= np.isin(self.config_codes, idx[idx >= 0])
        if rows is not None:
            m &= self.sf['Test Row Alias'].isin(list(rows)).to_numpy()
        return m

    def struct_state(self) -> pd.DataFrame:
        n = len(self.struct_index)
        sel = np.bincount(self.struct_codes[self.sel], minlength=n)
        orig = np.bincount(self.struct_codes[self.orig], minlength=n)
        st = self.structs.copy()
        st['In_Package'] = sel
        st['State'] = np.where(sel == 0, '', np.where(sel == st.Tests.values, 'full', 'partial'))
        st['Changed'] = sel != orig
        return st

    def config_state(self, structures=None) -> pd.DataFrame:
        m = self.lines(structures=structures) if structures is not None else np.ones(len(self.sf), bool)
        d = pd.DataFrame({'Config': self.sf['Test Configuration Name'][m], 'sel': self.sel[m], 'orig': self.orig[m],
                          's': self.struct_codes[m]})
        g = d.groupby('Config')
        out = pd.DataFrame({'Structures': g.s.nunique(), 'Tests': g.size(), 'In_Package': g.sel.sum(),
                            'Originally': g.orig.sum()}).reset_index()
        out['State'] = np.where(out.In_Package == 0, '', np.where(out.In_Package == out.Tests, 'full', 'partial'))
        return out

    def config_tests(self, cfg: str) -> pd.DataFrame:
        m = (self.sf['Test Configuration Name'] == cfg).to_numpy()
        d = pd.DataFrame({'Test': self.test[m], 'sel': self.sel[m]})
        g = d.groupby('Test')
        return pd.DataFrame({'Structures': g.size(), 'In_Package': g.sel.sum()}).reset_index()

    def is_sharepoint_doc(self, name: str) -> bool:
        return isinstance(self.docs.get(name), str)

    def load_doc(self, name: str) -> ConfigDocument:
        source = self.docs[name]
        if isinstance(source, str):
            return ConfigDocument(source, sharepoint_excel.read_workbook(source))
        return ConfigDocument(source)

    def doc(self, name: str) -> ConfigDocument | None:
        if name not in self.docs:
            return None
        if name not in self._doc_cache:
            self._doc_cache[name] = self.load_doc(name)
        return self._doc_cache[name]

    def add_sharepoint_doc(self, url: str) -> str:
        url = sharepoint_excel.normalize_url(url)
        if url in self.sharepoint_docs:
            return next(n for n, s in self.docs.items() if s == url)
        name, n = f'SharePoint: {sharepoint_excel.display_name(url)}', 2
        while name in self.docs:
            name, n = f'SharePoint: {sharepoint_excel.display_name(url)} ({n})', n + 1
        self.docs[name] = url
        self.sharepoint_docs.append(url)
        return name

    def remove_sharepoint_doc(self, name: str):
        url = self.docs.pop(name)
        self.sharepoint_docs.remove(url)
        self._doc_cache.pop(name, None)

    def reload_sharepoint_docs(self):
        for name in [n for n in self._doc_cache if self.is_sharepoint_doc(n)]:
            del self._doc_cache[name]

    def reference_matches(self, counts) -> pd.DataFrame:
        """Superflow structures / configs / rows / tests named in an Etest file, and whether the package uses them."""
        lower = {}
        for k, v in counts.items():
            lower[k.lower()] = lower.get(k.lower(), 0) + v
        out = []
        for kind, col, case in [('Structure', self.sf['Test Structure Alias'], True),
                                ('Test config', self.sf['Test Configuration Name'], True),
                                ('Test name', self.test, True), ('Test row', self.row_key, False)]:
            used = set(col[self.sel])
            for name in set(col):
                n = counts.get(name, 0) if case else lower.get(name, 0)
                if n:
                    out.append((kind, name, n, 'yes' if name in used else ''))
        st = self.struct_state()
        for corrected, struct, inc in zip(st.Corrected_Name, st.Structure, st.In_Package):
            if corrected and corrected != struct and counts.get(corrected):
                out.append(('New structure name', corrected, counts[corrected], 'yes' if inc else ''))
        return pd.DataFrame(out, columns=['Kind', 'Name', 'Occurrences', 'In_Package']) \
            .sort_values(['Kind', 'Name']).reset_index(drop=True)

    # ----- change summary -------------------------------------------------------------------------------------------
    def changes(self) -> pd.DataFrame:
        add, drop = self.sel & ~self.orig, self.orig & ~self.sel
        m = add | drop
        d = pd.DataFrame({'Change': np.where(add[m], 'added', 'dropped'),
                          'Test Row Alias': self.sf['Test Row Alias'][m].values,
                          'Test Structure Alias': self.sf['Test Structure Alias'][m].values,
                          'Test Configuration Name': self.sf['Test Configuration Name'][m].values,
                          'Test Names': self.sf['Test Names'][m].values})
        return d

    @staticmethod
    def _compare_frames(current: pd.DataFrame, other: pd.DataFrame, current_label: str,
                        other_label: str) -> tuple[str, pd.DataFrame]:
        current_counts = current.groupby(BASE_COLS, dropna=False).size().rename('Current')
        other_counts = other.groupby(BASE_COLS, dropna=False).size().rename('Compared')
        delta = pd.concat([current_counts, other_counts], axis=1).fillna(0).astype(int).reset_index()
        delta['Delta'] = delta.Current - delta.Compared
        changed = delta[delta.Delta.ne(0)].copy()
        changed['Change'] = np.select(
            [changed.Current.eq(0), changed.Compared.eq(0), changed.Delta.gt(0)],
            ['Only in compared', 'Only in current', 'More in current'], default='More in compared')
        changed['Tests'] = changed.Delta.abs()
        changed = changed[['Change', *BASE_COLS, 'Tests']].sort_values(
            ['Change', 'Test Row Alias', 'Test Structure Alias', 'Test Configuration Name', 'Test Names'])
        current_only = delta[delta.Compared.eq(0)]
        compared_only = delta[delta.Current.eq(0)]
        shared = delta[delta.Current.gt(0) & delta.Compared.gt(0)]
        current_structures = current['Test Structure Alias'].nunique()
        other_structures = other['Test Structure Alias'].nunique()
        current_rows = current['Test Row Alias'].nunique()
        other_rows = other['Test Row Alias'].nunique()
        current_configs = current['Test Configuration Name'].nunique()
        other_configs = other['Test Configuration Name'].nunique()
        shared_structures = len(set(current['Test Structure Alias']) & set(other['Test Structure Alias']))
        shared_rows = len(set(current['Test Row Alias']) & set(other['Test Row Alias']))
        shared_configs = len(set(current['Test Configuration Name']) & set(other['Test Configuration Name']))
        current_only_signatures = int(len(current_only))
        compared_only_signatures = int(len(compared_only))
        shared_different_counts = int(shared.Delta.ne(0).sum())
        summary = '\n'.join([
            f'Current: {current_label}',
            f'Compared: {other_label}',
            f'Test rows: {len(current):,} current vs {len(other):,} compared '
            f'({len(current) - len(other):+,} net)',
            f'Shared signatures: {len(shared):,} | Only current: {current_only_signatures:,} signatures / '
            f'{int(current_only.Current.sum()):,} rows | Only compared: {compared_only_signatures:,} signatures / '
            f'{int(compared_only.Compared.sum()):,} rows',
            f'Shared signatures with different occurrence counts: {shared_different_counts:,}',
            f'Structures: {current_structures:,} current, {other_structures:,} compared, {shared_structures:,} shared',
            f'Rows: {current_rows:,} current, {other_rows:,} compared, {shared_rows:,} shared',
            f'Configs: {current_configs:,} current, {other_configs:,} compared, {shared_configs:,} shared',
            f'Difference table groups: {len(changed):,} signatures',
        ])
        return summary, changed

    def compare_package(self, package: str) -> tuple[str, pd.DataFrame]:
        if package not in self.flows:
            raise ValueError(f'Unknown package column: {package}')
        other_mask = self.sf[package].str.strip().ne('').to_numpy()
        other = self.sf.loc[other_mask, BASE_COLS]
        current = self.sf.loc[self.sel, BASE_COLS]
        return self._compare_frames(current, other, f'{self.superflow_source.label} / {self.package}', package)

    @staticmethod
    def source_packages(path: Path) -> list[str]:
        cols = list(pd.read_csv(path, dtype=str, keep_default_na=False, nrows=0).columns)
        missing = [col for col in BASE_COLS if col not in cols]
        if missing:
            raise ValueError(f'{path.name} is missing superflow columns: {", ".join(missing)}')
        return [col for col in cols if col not in BASE_COLS]

    def compare_superflow(self, path: Path, package: str) -> tuple[str, pd.DataFrame]:
        path = Path(path).expanduser().resolve()
        if path == self.superflow_path:
            return self.compare_package(package)
        if package not in self.source_packages(path):
            raise ValueError(f'{package} is not a package column in {path.name}')
        other_sf = pd.read_csv(path, dtype=str, keep_default_na=False)
        other_mask = other_sf[package].str.strip().ne('').to_numpy()
        other = other_sf.loc[other_mask, BASE_COLS]
        current = self.sf.loc[self.sel, BASE_COLS]
        return self._compare_frames(current, other, f'{self.superflow_source.label} / {self.package}',
                                    f'{path.name} / {package}')

    def summary(self) -> dict:
        add, drop = self.sel & ~self.orig, self.orig & ~self.sel
        uniq = lambda m, col: int(self.sf[col][m].nunique()) if m.any() else 0
        st = self.struct_state()
        n0 = np.bincount(self.struct_codes[self.orig], minlength=len(st))
        n1 = st.In_Package.values
        return {
            'package': self.package,
            'tests': int(self.sel.sum()), 'tests_orig': int(self.orig.sum()),
            'structures': int((n1 > 0).sum()), 'structures_orig': int((n0 > 0).sum()),
            'rows': uniq(self.sel, 'Test Row Alias'), 'rows_orig': uniq(self.orig, 'Test Row Alias'),
            'configs': uniq(self.sel, 'Test Configuration Name'), 'configs_orig': uniq(self.orig, 'Test Configuration Name'),
            'tests_added': int(add.sum()), 'tests_dropped': int(drop.sum()),
            'structures_added': int(((n1 > 0) & (n0 == 0)).sum()), 'structures_dropped': int(((n1 == 0) & (n0 > 0)).sum()),
            'structures_changed': int(((n1 != n0) & (n1 > 0) & (n0 > 0)).sum()),
            'configs_added': uniq(add, 'Test Configuration Name'), 'configs_dropped': uniq(drop, 'Test Configuration Name'),
            'history': [h.label for h in self.history],
        }

    def summary_text(self) -> str:
        s = self.summary()
        delta = lambda a, b: f'{s[a]:,} (was {s[b]:,}, {s[a] - s[b]:+,})'
        lines = [f"Package {s['package']}",
                 f"  Tests       {delta('tests', 'tests_orig')}",
                 f"  Structures  {delta('structures', 'structures_orig')}",
                 f"  Rows        {delta('rows', 'rows_orig')}",
                 f"  Configs     {delta('configs', 'configs_orig')}",
                 '',
                 f"Added:   {s['tests_added']:,} tests, {s['structures_added']:,} new structures, "
                 f"{s['configs_added']:,} configs touched",
                 f"Dropped: {s['tests_dropped']:,} tests, {s['structures_dropped']:,} structures removed",
                 f"Partially changed structures: {s['structures_changed']:,}"]
        if s['history']:
            lines += ['', 'Actions:'] + [f'  {i}. {h}' for i, h in enumerate(s['history'], 1)]
        return '\n'.join(lines)

    # ----- validation -----------------------------------------------------------------------------------------------
    def validate(self) -> pd.DataFrame:
        out = []
        add = lambda sev, check, row, struct, detail: out.append(
            {'Severity': sev, 'Check': check, 'Row': row, 'Structure': struct, 'Detail': detail})
        if not self.sel.any():
            add('ERROR', 'EMPTY', '', '', 'The package has no tests')
            return pd.DataFrame(out)
        st = self.struct_state()
        inc = st[st.In_Package > 0]
        if self.validator_available:
            for r in inc[inc.Status == 'MANUAL'].itertuples():
                add('ERROR', 'NAME_MANUAL', r.Row, r.Structure, 'Name has no automatic correction (validator MANUAL)')
            for r in inc[(inc.Max_Severity == 'ERROR') & (inc.Status != 'MANUAL')].itertuples():
                add('WARN', 'NAME_ERROR', r.Row, r.Structure, f'Validator errors in the original name; corrected: '
                                                              f'{r.Corrected_Name}')
            for r in inc[inc.Family == ''].itertuples():
                add('WARN', 'NOT_VALIDATED', r.Row, r.Structure, 'Structure not in the validator output')
            bad_row = inc[inc.Row_Status != TAPE_IN].drop_duplicates('RowKey')
            for r in bad_row.itertuples():
                add('WARN', 'ROW_STATUS', r.Row, '', f'Row status in Atlantis: {r.Row_Status}')
        else:
            add('INFO', 'NODE_VALIDATION', '', '',
                f'No node-specific validator or Atlantis metadata is loaded for {self.superflow_source.node}; '
                'package structure/config comparisons are available, but name and row checks are not.')
        # config vs family
        d = pd.DataFrame({'s': self.struct_codes[self.sel], 'cfg': self.sf['Test Configuration Name'][self.sel].values})
        d = d.drop_duplicates()
        d['Family'] = st.Family.values[d.s]
        for fam, pat in FAMILY_CFG.items():
            bad = d[(d.Family == fam) & ~d.cfg.str.match(pat)]
            for (cfg,), g in bad.groupby(['cfg']):
                add('WARN', 'CONFIG_FAMILY', '', f'{len(g)} {fam} structures',
                    f"Config '{cfg}' is unusual for {fam} (expected {pat})")
        # structure / config only partly selected
        m = self.lines(structures=inc.Structure)
        p = pd.DataFrame({'s': self.struct_codes[m], 'cfg': self.sf['Test Configuration Name'][m].values,
                          'sel': self.sel[m]}).groupby(['s', 'cfg']).sel.agg(['sum', 'size'])
        part = p[(p['sum'] > 0) & (p['sum'] < p['size'])].reset_index()
        for r in part.itertuples():
            add('WARN', 'PARTIAL_CONFIG', st.Row.values[r.s], st.Structure.values[r.s],
                f"Config '{r.cfg}': {r.sum} of {r.size} tests selected")
        # configs / rows that are new to this package (review before release)
        cfg = self.sf['Test Configuration Name']
        new_cfg = set(cfg[self.sel]) - set(cfg[self.orig])
        for c in sorted(new_cfg):
            add('INFO', 'NEW_CONFIG', '', '', f"Config '{c}' was not in {self.package} before")
        rows = self.sf['Test Row Alias']
        for r in sorted(set(rows[self.sel]) - set(rows[self.orig])):
            add('INFO', 'NEW_ROW', r, '', f'Row was not in {self.package} before')
        # ROS branch completeness
        if len(self.tree):
            bases = set(inc.Base)
            t = self.tree
            roots = t[(t.Level == '0') & t.Structure.isin(bases)]
            por = t[(t.Level == '1') & t.Group.isin(roots.Group)]
            for (grp, root), g in por.groupby(['Group', 'Root']):
                missing = sorted({f for f, s in zip(g.Role, g.Structure) if s not in bases}
                                 - {f for f, s in zip(g.Role, g.Structure) if s in bases})
                if missing:
                    add('INFO', 'ROS_BRANCH', '', root, f"No {', '.join(missing)} POR partner in the package")
        res = pd.DataFrame(out, columns=['Severity', 'Check', 'Row', 'Structure', 'Detail'])
        order = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
        return res.sort_values(['Severity', 'Check'], key=lambda s: s.map(order) if s.name == 'Severity' else s)

    # ----- export ---------------------------------------------------------------------------------------------------
    def export(self, path: Path, only_package: bool = True, progress=lambda msg: None):
        df = self.sf.copy()
        df[self.package] = np.where(self.sel, 'X', '')
        if only_package:
            df = df[self.sel]
        st = self.struct_state()
        inc = st[st.In_Package > 0][['Row', 'Library', 'Owner', 'Structure', 'Corrected_Name', 'Family', 'CH', 'Z',
                                     'LNR_nm', 'POL', 'VT', 'Legs', 'Env', 'Tests', 'In_Package', 'Status']]
        summ = pd.DataFrame({'Item': list(self.summary_text().splitlines())})
        progress('Validating ...')
        val = self.validate()
        progress(f'Writing {len(df):,} tests ...')
        with pd.ExcelWriter(path, engine='xlsxwriter') as xw:
            head = xw.book.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': 'white'})
            sheets = [('Superflow', df), ('Summary', summ), ('Changes', self.changes()), ('Validation', val),
                      ('Structures', inc)]
            for name, d in sheets:
                d.to_excel(xw, sheet_name=name, index=False)
                ws = xw.sheets[name]
                for i, col in enumerate(d.columns):
                    ws.write(0, i, col, head)
                    ws.set_column(i, i, min(max([len(str(col))] + [len(str(x)) for x in d[col].head(200)]) + 2, 60))
                ws.freeze_panes(1, 0)
                if len(d):
                    ws.autofilter(0, 0, len(d), len(d.columns) - 1)
        return len(df)

    def export_csv(self, path: Path):
        df = self.sf.copy()
        df[self.package] = np.where(self.sel, 'X', '')
        df.to_csv(path, index=False)
        return len(df)
