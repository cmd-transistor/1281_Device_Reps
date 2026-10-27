#!/usr/bin/env python
"""Test Package Builder - select structures / test configs for a superflow test package, compare package/source deltas,
check the selection against available validator results, and export a superflow-format Excel / CSV.

    python testpackage_gui.py        (needs PySide6; reads superflows and config workbooks from the selected data folder)

Choose a local OneDrive-synced SharePoint folder from Sources to discover updated superflow CSVs and P12xx config
workbooks. The newest R<number> file is selected by default per node; older revisions remain available for comparison.

Tabs: Structures (filter by family, CH, LNR, polarity, VT, legs, library, owner, row, config; add / drop structures
and configs), Branch explorer (ROS -> ROSPFT / PFT / MLR / FK -> supporting structures), Test configs (tests in the
superflow and config documents), Changes, Compare, Validation.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, QSettings, QUrl, Qt, QThread, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QKeySequence, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QScrollArea, QSplitter, QTableView, QTabWidget, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import etest_files
from testpackage_model import HERE, SHAREPOINT_URL, STRUCT_COLS, SuperflowSource, TestPackageModel

APP = 'X81Z Test Package Builder'
NAVY, BLUE, LIGHT = '#1F3864', '#2F5597', '#E8EDF5'
STATE_COLOR = {'full': '#E2F0D9', 'partial': '#FFF2CC'}
SEV_COLOR = {'ERROR': '#F8CBAD', 'WARN': '#FFF2CC', 'INFO': '#DDEBF7'}
STYLE = f"""
QMainWindow, QWidget#Page {{ background: #F4F6FA; }}
QFrame#Header {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {NAVY}, stop:1 {BLUE}); }}
QFrame#Header QLabel {{ color: white; }}
QLabel#Title {{ font-size: 18px; font-weight: 600; }}
QFrame#Card {{ background: rgba(255,255,255,0.12); border-radius: 6px; }}
QLabel#CardValue {{ font-size: 16px; font-weight: 600; }}
QLabel#CardLabel {{ font-size: 10px; color: #D9E2F3; }}
QPushButton, QToolButton#Primary {{ background: {BLUE}; color: white; border: none; border-radius: 4px;
    padding: 6px 12px; }}
QPushButton:hover, QToolButton#Primary:hover {{ background: #3A6BC4; }}
QPushButton:disabled {{ background: #A6B4CC; }}
QPushButton#Secondary {{ background: {LIGHT}; color: {NAVY}; border: 1px solid #C5CFE0; }}
QPushButton#Secondary:hover {{ background: #DCE4F2; }}
QPushButton#Danger {{ background: #C0504D; }}
QPushButton#Danger:hover {{ background: #D46A67; }}
QFrame#Header QPushButton#Light, QFrame#Header QToolButton {{ background: white; color: {NAVY}; border: none;
    border-radius: 4px; padding: 6px 14px; font-weight: 600; }}
QFrame#Header QPushButton#Light:hover, QFrame#Header QToolButton:hover {{ background: #DCE4F2; }}
QTabWidget::pane {{ border: 1px solid #C5CFE0; background: white; border-radius: 4px; }}
QTabBar::tab {{ background: {LIGHT}; color: {NAVY}; padding: 8px 18px; border: 1px solid #C5CFE0;
    border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: white; border-top: 3px solid {BLUE}; font-weight: 600; }}
QTableView, QTreeWidget {{ background: white; alternate-background-color: #F7F9FC; gridline-color: #E3E7EE;
    selection-background-color: #CFE0FF; selection-color: black; border: 1px solid #D6DCE6; }}
QHeaderView::section {{ background: {NAVY}; color: white; padding: 5px; border: none;
    border-right: 1px solid #3A4F7A; font-weight: 600; }}
QGroupBox {{ font-weight: 600; color: {NAVY}; border: 1px solid #C5CFE0; border-radius: 6px; margin-top: 14px;
    background: white; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QComboBox, QLineEdit {{ padding: 4px 6px; border: 1px solid #C5CFE0; border-radius: 4px; background: white; }}
QPlainTextEdit {{ background: white; border: 1px solid #D6DCE6; font-family: Consolas; font-size: 11px; }}
QStatusBar {{ background: {LIGHT}; color: {NAVY}; }}
"""
ALL = '(all)'


class PandasModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame | None = None, color=None):
        super().__init__()
        self.df = df if df is not None else pd.DataFrame()
        self.color = color

    def set(self, df: pd.DataFrame):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.df)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.df.columns)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            v = self.df.iat[idx.row(), idx.column()]
            return '' if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)
        if role == Qt.BackgroundRole and self.color:
            c = self.color(self.df, idx.row())
            return QBrush(QColor(c)) if c else None
        if role == Qt.FontRole and 'Changed' in self.df.columns and self.df.at[idx.row(), 'Changed']:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.TextAlignmentRole and pd.api.types.is_numeric_dtype(self.df.dtypes.iloc[idx.column()]):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, s, orient, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        return str(self.df.columns[s]).replace('_', ' ') if orient == Qt.Horizontal else str(s + 1)

    def sort(self, col, order=Qt.AscendingOrder):
        if not len(self.df.columns):
            return
        self.layoutAboutToBeChanged.emit()
        c = self.df.columns[col]
        key = None if pd.api.types.is_numeric_dtype(self.df[c]) else (lambda s: s.astype(str).str.upper())
        self.df = self.df.sort_values(c, ascending=order == Qt.AscendingOrder, kind='mergesort', key=key) \
            .reset_index(drop=True)
        self.layoutChanged.emit()


def table(model: PandasModel, hidden=()) -> QTableView:
    v = QTableView()
    v.setModel(model)
    v.setSortingEnabled(True)
    v.setAlternatingRowColors(True)
    v.setSelectionBehavior(QAbstractItemView.SelectRows)
    v.setSelectionMode(QAbstractItemView.ExtendedSelection)
    v.verticalHeader().setDefaultSectionSize(22)
    v.verticalHeader().setVisible(False)
    v.horizontalHeader().setStretchLastSection(True)
    v.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    v.setWordWrap(False)
    return v


def button(text, slot, kind='', tip=''):
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:      # shown to the user
            self.failed.emit(f'{type(e).__name__}: {e}')


class MainWindow(QMainWindow):
    def __init__(self, model: TestPackageModel):
        super().__init__()
        self.m = model
        self._workers: list[Worker] = []
        self._doc_loading: set[str] = set()
        self._et_generation = 0
        self._et_file_token = 0
        self._et_elements = []
        self.setWindowTitle(APP)
        self.resize(1600, 950)
        self.base_map = model.structs.groupby('Base').Structure.apply(list).to_dict()
        page = QWidget(objectName='Page')
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._header())
        body = QSplitter(Qt.Horizontal)
        body.addWidget(self._filters())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._structures_tab(), 'Structures')
        self.tabs.addTab(self._branch_tab(), 'Branch explorer')
        self.tabs.addTab(self._config_tab(), 'Test configs')
        self.tabs.addTab(self._changes_tab(), 'Changes')
        self.tabs.addTab(self._compare_tab(), 'Compare')
        self.tabs.addTab(self._validation_tab(), 'Validation')
        self.tabs.addTab(self._etest_tab(), 'Etest XML / TestSpec')
        self.tabs.currentChanged.connect(lambda i: self.refresh_tab())
        body.addWidget(self.tabs)
        body.setSizes([270, 1330])
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 8, 10, 8)
        wl.addWidget(body)
        lay.addWidget(wrap, 1)
        self.setCentralWidget(page)
        undo = QAction('Undo', self, shortcut=QKeySequence.Undo, triggered=self.undo)
        self.addAction(undo)
        self.refresh_all(rebuild_tree=True)

    def _populate_revisions(self, selected_path: Path | None = None):
        node = self.node_cb.currentText() if hasattr(self, 'node_cb') else self.m.superflow_source.node
        sources = [source for source in self.m.sources if source.node == node]
        selected = (Path(selected_path) if selected_path else self.m.superflow_path).resolve()
        self.revision_cb.blockSignals(True)
        self.revision_cb.clear()
        selected_index = 0
        for index, source in enumerate(sources):
            revision = f'R{source.revision}' if source.revision >= 0 else 'Rev ?'
            self.revision_cb.addItem(f'{revision} · {source.path.name}', str(source.path))
            if source.path == selected:
                selected_index = index
        if sources:
            self.revision_cb.setCurrentIndex(selected_index)
        self.revision_cb.blockSignals(False)

    def _replace_source_model(self, model: TestPackageModel):
        if self.m.package in model.flows:
            model.set_package(self.m.package)
        window = MainWindow(model)
        window.show()
        self._replacement_window = window
        self.close()

    def _confirm_source_switch(self) -> bool:
        if not self.m.history:
            return True
        return QMessageBox.question(
            self, APP, f'Discard {len(self.m.history)} unsaved action(s) on {self.m.package} before switching data?') \
            == QMessageBox.Yes

    def change_node(self, node: str):
        sources = [source for source in self.m.sources if source.node == node]
        if not sources:
            return
        self._populate_revisions(sources[0].path)
        self.change_revision(self.revision_cb.currentIndex())

    def change_revision(self, index: int):
        path_text = self.revision_cb.itemData(index)
        if not path_text:
            return
        path = Path(path_text)
        if path.resolve() == self.m.superflow_path:
            return
        if not self._confirm_source_switch():
            self.node_cb.blockSignals(True)
            self.node_cb.setCurrentText(self.m.superflow_source.node)
            self.node_cb.blockSignals(False)
            self._populate_revisions(self.m.superflow_path)
            return
        try:
            model = TestPackageModel(data_root=self.m.data_root, superflow_path=path,
                                     sharepoint_docs=self.m.sharepoint_docs,
                                     superflow_paths=self.m.superflow_paths)
        except Exception as exc:
            QMessageBox.critical(self, APP, f'Could not load {path.name}: {exc}')
            self.node_cb.blockSignals(True)
            self.node_cb.setCurrentText(self.m.superflow_source.node)
            self.node_cb.blockSignals(False)
            self._populate_revisions(self.m.superflow_path)
            return
        self._replace_source_model(model)

    def choose_data_root(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose the local SharePoint / OneDrive sync folder',
                                                str(self.m.data_root))
        if not path:
            return
        if not self._confirm_source_switch():
            return
        root = Path(path)
        try:
            model = TestPackageModel(data_root=root, node=self.m.superflow_source.node,
                                     sharepoint_docs=self.m.sharepoint_docs,
                                     superflow_paths=self.m.superflow_paths)
        except Exception as exc:
            QMessageBox.critical(self, APP, f'No readable superflow CSV was found in {root}: {exc}')
            return
        QSettings('Intel', 'TestPackageBuilder').setValue('data_root', str(root))
        self._replace_source_model(model)

    def refresh_data_sources(self):
        if not self._confirm_source_switch():
            return
        try:
            model = TestPackageModel(data_root=self.m.data_root, node=self.m.superflow_source.node,
                                     sharepoint_docs=self.m.sharepoint_docs,
                                     superflow_paths=self.m.superflow_paths)
        except Exception as exc:
            QMessageBox.critical(self, APP, f'Could not refresh files in {self.m.data_root}: {exc}')
            return
        self._replace_source_model(model)

    @staticmethod
    def open_sharepoint():
        QDesktopServices.openUrl(QUrl(SHAREPOINT_URL))

    def _save_sharepoint_docs(self):
        QSettings('Intel', 'TestPackageBuilder').setValue('sharepoint_docs', list(self.m.sharepoint_docs))

    @staticmethod
    def _save_superflow_paths(paths):
        QSettings('Intel', 'TestPackageBuilder').setValue('superflow_files', [str(p) for p in paths])

    def add_superflow_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add superflow CSV files', str(self.m.data_root),
                                                 'Superflow CSV (*.csv)')
        if not paths or not self._confirm_source_switch():
            return
        extra = list(dict.fromkeys([*self.m.superflow_paths, *(Path(p).resolve() for p in paths)]))
        try:
            model = TestPackageModel(data_root=self.m.data_root, node=self.m.superflow_source.node,
                                     sharepoint_docs=self.m.sharepoint_docs, superflow_paths=extra)
        except Exception as exc:
            QMessageBox.critical(self, APP, f'Could not load selected superflow CSVs: {exc}')
            return
        self._save_superflow_paths(extra)
        self._replace_source_model(model)

    def remove_superflow_file(self):
        if not self.m.superflow_paths:
            QMessageBox.information(self, APP, 'No individually added superflow files to remove.')
            return
        names = [f'{p.name}  ({p.parent})' for p in self.m.superflow_paths]
        label, ok = QInputDialog.getItem(self, APP, 'Remove individually added superflow:', names, 0, False)
        if not ok or not label or not self._confirm_source_switch():
            return
        index = names.index(label)
        extra = [p for i, p in enumerate(self.m.superflow_paths) if i != index]
        try:
            model = TestPackageModel(data_root=self.m.data_root, node=self.m.superflow_source.node,
                                     sharepoint_docs=self.m.sharepoint_docs, superflow_paths=extra)
        except Exception as exc:
            QMessageBox.critical(self, APP, f'Could not refresh remaining superflow files: {exc}')
            return
        self._save_superflow_paths(extra)
        self._replace_source_model(model)

    def refresh_doc_list(self, select: str = ''):
        current = select or self.doc_cb.currentText()
        self.doc_cb.blockSignals(True)
        self.doc_cb.clear()
        self.doc_cb.addItems(list(self.m.docs) or ['(no P12xx_Test_Configs*.xlsx found)'])
        if current in self.m.docs:
            self.doc_cb.setCurrentText(current)
        self.doc_cb.blockSignals(False)
        self.show_config()

    def add_sharepoint_workbook(self):
        url, ok = QInputDialog.getText(
            self, APP, 'SharePoint workbook URL (in Excel: File > Info > Copy path, or a /:x:/r/ link):')
        if not ok or not url.strip():
            return
        try:
            name = self.m.add_sharepoint_doc(url)
        except ValueError as exc:
            QMessageBox.warning(self, APP, str(exc))
            return
        self._save_sharepoint_docs()
        self.tabs.setCurrentIndex(2)
        self.refresh_doc_list(name)

    def remove_sharepoint_workbook(self):
        names = [n for n in self.m.docs if self.m.is_sharepoint_doc(n)]
        if not names:
            QMessageBox.information(self, APP, 'No SharePoint workbooks have been added.')
            return
        name, ok = QInputDialog.getItem(self, APP, 'Remove SharePoint workbook:', names, 0, False)
        if ok and name:
            self.m.remove_sharepoint_doc(name)
            self._save_sharepoint_docs()
            self.refresh_doc_list()

    def reload_sharepoint_workbooks(self):
        self.m.reload_sharepoint_docs()
        self.refresh_doc_list()
        self.statusBar().showMessage('SharePoint workbooks will be re-read from SharePoint when next shown', 6000)

    # ----- layout ---------------------------------------------------------------------------------------------------
    def _header(self) -> QFrame:
        h = QFrame(objectName='Header')
        root = QVBoxLayout(h)
        root.setContentsMargins(14, 8, 14, 8)
        root.setSpacing(6)
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(QLabel(APP, objectName='Title'))
        col.addWidget(QLabel(f'{self.m.superflow_source.node} · {self.m.superflow_source.path.name}'))
        top.addLayout(col)
        top.addStretch(1)
        top.addWidget(QLabel('Node'))
        self.node_cb = QComboBox()
        self.node_cb.addItems(sorted({source.node for source in self.m.sources}))
        self.node_cb.setCurrentText(self.m.superflow_source.node)
        self.node_cb.currentTextChanged.connect(self.change_node)
        self.node_cb.setMaximumWidth(105)
        top.addWidget(self.node_cb)
        top.addWidget(QLabel('Revision'))
        self.revision_cb = QComboBox()
        self._populate_revisions()
        self.revision_cb.activated.connect(self.change_revision)
        self.revision_cb.setMinimumWidth(130)
        self.revision_cb.setMaximumWidth(220)
        top.addWidget(self.revision_cb)
        sources = QToolButton(text='Data  ▾')
        sources.setPopupMode(QToolButton.InstantPopup)
        sources.setToolTip('Choose a local OneDrive-synced SharePoint folder; refresh after OneDrive sync completes')
        sources_menu = QMenu(sources)
        sources_menu.addAction('Choose local / OneDrive-synced folder...', self.choose_data_root)
        sources_menu.addAction('Refresh SharePoint / OneDrive sources', self.refresh_data_sources)
        sources_menu.addAction('Add superflow CSV file(s)...', self.add_superflow_files)
        sources_menu.addAction('Remove individually added superflow...', self.remove_superflow_file)
        sources_menu.addSeparator()
        sources_menu.addAction('Open SharePoint document library', self.open_sharepoint)
        sources_menu.addAction('Add SharePoint workbook (read-only, via Excel)...', self.add_sharepoint_workbook)
        sources_menu.addAction('Remove SharePoint workbook...', self.remove_sharepoint_workbook)
        sources_menu.addAction('Reload SharePoint workbooks', self.reload_sharepoint_workbooks)
        sources.setMenu(sources_menu)
        top.addWidget(sources)
        top.addWidget(QLabel('Package'))
        self.pkg = QComboBox()
        self.pkg.addItems(self.m.flows)
        self.pkg.setCurrentText(self.m.package)
        self.pkg.setMinimumWidth(110)
        self.pkg.setMaximumWidth(170)
        self.pkg.currentTextChanged.connect(self.change_package)
        top.addWidget(self.pkg)
        root.addLayout(top)
        bottom = QHBoxLayout()
        self.cards = {}
        for key, label in [('tests', 'Tests'), ('structures', 'Structures'), ('rows', 'Rows'), ('configs', 'Configs'),
                           ('added', 'Added tests'), ('dropped', 'Dropped tests')]:
            card = QFrame(objectName='Card')
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 3, 8, 3)
            v = QLabel('0', objectName='CardValue')
            cl.addWidget(v)
            cl.addWidget(QLabel(label, objectName='CardLabel'))
            self.cards[key] = v
            bottom.addWidget(card)
        bottom.addStretch(1)
        bottom.addWidget(button('New package', self.new_package, 'Secondary', 'Start a new package column'))
        bottom.addWidget(button('Undo', self.undo, 'Secondary', 'Undo the last add / drop (Ctrl+Z)'))
        bottom.addWidget(button('Reset', self.reset, 'Secondary', 'Back to the superflow package'))
        bottom.addWidget(button('Validate', self.run_validation, 'Light', 'Check the selection'))
        exp = QToolButton(text='Export  ▾')
        exp.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(exp)
        menu.addAction('Package tests (Excel, superflow format)', lambda: self.export(True, 'xlsx'))
        menu.addAction('Full superflow with this package (Excel)', lambda: self.export(False, 'xlsx'))
        menu.addAction('Full superflow with this package (CSV)', lambda: self.export(False, 'csv'))
        exp.setMenu(menu)
        bottom.addWidget(exp)
        root.addLayout(bottom)
        return h

    def _filters(self) -> QWidget:
        box = QGroupBox('Filters')
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        self.search = QLineEdit(placeholderText='Filter structures and branch nodes')
        self.search.textChanged.connect(self.refresh_structures)
        self.search.textChanged.connect(self.refresh_tree)
        form.addRow('Search', self.search)
        st = self.m.structs
        self.filt = {}
        specs = [('Package', ['in package', 'full', 'partial', 'not in package', 'changed']),
                 ('Family', st.Family), ('CH', st.CH), ('LNR (nm)', st.LNR_nm), ('Z', st.Z), ('Polarity', st.POL),
                 ('VT', st.VT), ('Legs / ADF', st.Legs), ('Env', st.Env), ('Library', st.Library),
                 ('Owner', st.Owner), ('Row', st.Row.str.replace(r'_TC\d+[A-Z]?_\d+$', '', regex=True)),
                 ('Config', pd.Series(self.m.config_index)), ('Status', st.Status)]
        for name, vals in specs:
            cb = QComboBox()
            items = list(vals) if isinstance(vals, list) else sorted({v for v in vals if v},
                                                                      key=lambda x: (len(x) if x.isdigit() else 0, x))
            cb.addItems([ALL] + items)
            cb.setMaxVisibleItems(25)
            cb.currentTextChanged.connect(self.refresh_structures)
            form.addRow(name, cb)
            self.filt[name] = cb
        form.addRow(button('Clear filters', self.clear_filters, 'Secondary'))
        self.count_lbl = QLabel()
        form.addRow(self.count_lbl)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(box)
        area.setMinimumWidth(250)
        return area

    def _structures_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(button('Add selected', lambda: self.act_structures(True, False), '',
                             'Add every test of the selected structures'))
        bar.addWidget(button('Drop selected', lambda: self.act_structures(False, False), 'Danger'))
        bar.addSpacing(16)
        bar.addWidget(button('Add all filtered', lambda: self.act_structures(True, True), 'Secondary'))
        bar.addWidget(button('Drop all filtered', lambda: self.act_structures(False, True), 'Secondary'))
        bar.addStretch(1)
        bar.addWidget(QLabel('Green = whole structure in package, amber = part of its tests, bold = changed'))
        lay.addLayout(bar)
        split = QSplitter(Qt.Horizontal)
        self.st_model = PandasModel(color=lambda d, r: STATE_COLOR.get(d.at[r, 'State'], ''))
        self.st_view = table(self.st_model)
        self.st_view.selectionModel().selectionChanged.connect(self.show_details)
        split.addWidget(self.st_view)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(6, 0, 0, 0)
        self.detail = QLabel('Select a structure')
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail.setAlignment(Qt.AlignTop)
        box = QGroupBox('Structure')
        QVBoxLayout(box).addWidget(self.detail)
        sl.addWidget(box)
        cbox = QGroupBox('Test configs of the selected structures')
        cl = QVBoxLayout(cbox)
        self.sc_model = PandasModel(color=lambda d, r: STATE_COLOR.get(d.at[r, 'State'], ''))
        self.sc_view = table(self.sc_model)
        cl.addWidget(self.sc_view)
        row = QHBoxLayout()
        row.addWidget(button('Add config', lambda: self.act_struct_configs(True), '', 'Add the selected configs '
                                                                                     'for the selected structures'))
        row.addWidget(button('Drop config', lambda: self.act_struct_configs(False), 'Danger'))
        row.addWidget(button('View config', self.view_config, 'Secondary'))
        cl.addLayout(row)
        sl.addWidget(cbox, 1)
        split.addWidget(side)
        split.setSizes([950, 420])
        lay.addWidget(split, 1)
        return w

    def _branch_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        t = self.m.tree
        self.bf = {}
        for name, col in [('Root', 'Role'), ('CH', 'CH'), ('LNR (nm)', 'LNR_nm'), ('Polarity', 'POL'), ('VT', 'VT'),
                          ('Legs', 'LEGS')]:
            vals = t[t.Level == '0'][col] if len(t) else pd.Series(dtype=str)
            cb = QComboBox()
            cb.addItems([ALL] + sorted({v for v in vals if v}, key=lambda x: (len(x), x)))
            cb.currentTextChanged.connect(self.refresh_tree)
            top.addWidget(QLabel(name))
            top.addWidget(cb)
            self.bf[name] = cb
        top.addStretch(1)
        lay.addLayout(top)
        bar = QHBoxLayout()
        bar.addWidget(button('Add branch (POR)', lambda: self.act_branch(True, 1), '',
                             'ROS / ROSPFT with its ROSPFT, PFT, MLR, FK POR partners'))
        bar.addWidget(button('Add branch + supporting', lambda: self.act_branch(True, 2), '',
                             'Also the skews / variants and ISO / KLV of the same device'))
        bar.addWidget(button('Add selected nodes', lambda: self.act_branch(True, -1), 'Secondary'))
        bar.addWidget(button('Drop selected nodes', lambda: self.act_branch(False, -1), 'Danger'))
        bar.addWidget(button('Drop branch', lambda: self.act_branch(False, 2), 'Danger'))
        bar.addStretch(1)
        self.tree_lbl = QLabel()
        bar.addWidget(self.tree_lbl)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Structure', 'Role', 'In package', 'CH', 'Z', 'LNR', 'VT', 'POL', 'Legs',
                                   'Library', 'Rows', 'Configs'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemExpanded.connect(self.expand_branch)
        self.tree.header().resizeSection(0, 430)
        self.tree.header().resizeSection(1, 120)
        lay.addWidget(self.tree, 1)
        return w

    def _config_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.cfg_model = PandasModel(color=lambda d, r: STATE_COLOR.get(d.at[r, 'State'], ''))
        self.cfg_view = table(self.cfg_model)
        self.cfg_view.selectionModel().selectionChanged.connect(self.show_config)
        ll.addWidget(QLabel('Configs of the filtered structures'))
        ll.addWidget(self.cfg_view, 1)
        row = QHBoxLayout()
        row.addWidget(button('Add for filtered', lambda: self.act_configs(True), '',
                             'Add the selected configs for every filtered structure that has them'))
        row.addWidget(button('Drop for filtered', lambda: self.act_configs(False), 'Danger'))
        ll.addLayout(row)
        right = QSplitter(Qt.Vertical)
        tbox = QGroupBox('Tests in the superflow')
        tl = QVBoxLayout(tbox)
        self.ct_model = PandasModel()
        self.ct_view = table(self.ct_model)
        tl.addWidget(self.ct_view)
        right.addWidget(tbox)
        dbox = QGroupBox('Test config document')
        dl = QVBoxLayout(dbox)
        drow = QHBoxLayout()
        self.doc_cb = QComboBox()
        self.doc_cb.addItems(list(self.m.docs) or ['(no P12xx_Test_Configs*.xlsx found)'])
        self.doc_cb.currentTextChanged.connect(self.show_config)
        drow.addWidget(self.doc_cb, 1)
        dl.addLayout(drow)
        self.doc_lbl = QLabel()
        dl.addWidget(self.doc_lbl)
        self.doc_tabs = QTabWidget()
        dl.addWidget(self.doc_tabs, 1)
        right.addWidget(dbox)
        right.setSizes([300, 500])
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([520, 820])
        lay.addWidget(split)
        return w

    def _changes_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        summary = QWidget()
        summary_lay = QVBoxLayout(summary)
        summary_lay.setContentsMargins(0, 0, 0, 0)
        summary_lay.addWidget(QLabel('Changes are edits relative to the package column currently selected in the header. '
                                     'Compare shows differences against another package or superflow.'))
        self.sum_txt = QPlainTextEdit(readOnly=True)
        self.sum_txt.setMaximumWidth(460)
        summary_lay.addWidget(self.sum_txt, 1)
        lay.addWidget(summary)
        self.ch_model = PandasModel(color=lambda d, r: '#E2F0D9' if d.at[r, 'Change'] == 'added' else '#F8CBAD')
        lay.addWidget(table(self.ch_model), 1)
        return w

    def _compare_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Compare current selection with'))
        self.compare_mode = QComboBox()
        self.compare_mode.addItem('Package in this superflow', 'package')
        self.compare_mode.addItem('Package in another superflow', 'superflow')
        self.compare_mode.currentIndexChanged.connect(self.refresh_compare_targets)
        controls.addWidget(self.compare_mode)
        self.compare_target = QComboBox()
        self.compare_target.currentIndexChanged.connect(self.refresh_compare_packages)
        controls.addWidget(self.compare_target, 1)
        self.compare_package = QComboBox()
        self.compare_package.setMinimumWidth(180)
        controls.addWidget(self.compare_package)
        self.compare_browse = button('Browse...', self.browse_compare_superflow, 'Secondary',
                                     'Choose a superflow CSV not listed in the current data folder')
        controls.addWidget(self.compare_browse)
        controls.addWidget(button('Compare', self.run_compare, 'Primary'))
        lay.addLayout(controls)
        lay.addWidget(QLabel('The current side uses the active package selection, including edits not yet exported.'))
        split = QSplitter(Qt.Vertical)
        self.compare_summary = QPlainTextEdit(readOnly=True)
        self.compare_summary.setMaximumHeight(160)
        self.compare_summary.setPlaceholderText('Choose a comparison and select Compare')
        split.addWidget(self.compare_summary)
        self.compare_model = PandasModel(color=lambda d, r: '#E2F0D9' if d.at[r, 'Change'] in
                         ('Only in current', 'More in current') else '#F8CBAD')
        self.compare_table = table(self.compare_model)
        split.addWidget(self.compare_table)
        split.setSizes([150, 650])
        lay.addWidget(split, 1)
        self.refresh_compare_targets()
        return w

    def refresh_compare_targets(self, *_):
        if not hasattr(self, 'compare_target'):
            return
        mode = self.compare_mode.currentData()
        self.compare_target.blockSignals(True)
        self.compare_target.clear()
        if mode == 'package':
            for package in self.m.flows:
                self.compare_target.addItem(package, package)
            other_packages = [package for package in self.m.flows if package != self.m.package]
            target = other_packages[0] if other_packages else self.m.package
            index = self.compare_target.findData(target)
            if index >= 0:
                self.compare_target.setCurrentIndex(index)
        else:
            sources = [source for source in self.m.sources if source.path != self.m.superflow_path]
            for source in sources:
                self.compare_target.addItem(source.label, str(source.path))
            if not sources:
                self.compare_target.addItem('(no other superflows found)', '')
        self.compare_target.blockSignals(False)
        is_superflow = mode == 'superflow'
        self.compare_package.setVisible(is_superflow)
        self.compare_browse.setVisible(is_superflow)
        if is_superflow:
            self.refresh_compare_packages()

    def refresh_compare_packages(self, *_):
        if not hasattr(self, 'compare_package') or self.compare_mode.currentData() != 'superflow':
            return
        path_text = self.compare_target.currentData()
        self.compare_package.blockSignals(True)
        self.compare_package.clear()
        if path_text:
            try:
                packages = TestPackageModel.source_packages(Path(path_text))
                self.compare_package.addItems(packages)
                if self.m.package in packages:
                    self.compare_package.setCurrentText(self.m.package)
            except Exception as exc:
                self.compare_package.addItem(f'Unavailable: {exc}')
        self.compare_package.blockSignals(False)

    def browse_compare_superflow(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose comparison superflow', str(self.m.data_root),
                                               'Superflow CSV (*.csv)')
        if not path:
            return
        try:
            TestPackageModel.source_packages(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, APP, f'This file is not a supported superflow: {exc}')
            return
        for index in range(self.compare_target.count()):
            if self.compare_target.itemData(index) == path:
                self.compare_target.setCurrentIndex(index)
                break
        else:
            self.compare_target.addItem(Path(path).name, path)
            self.compare_target.setCurrentIndex(self.compare_target.count() - 1)
        self.refresh_compare_packages()

    def run_compare(self):
        mode = self.compare_mode.currentData()
        target = self.compare_target.currentData()
        try:
            if mode == 'package':
                summary, changes = self.m.compare_package(target)
            else:
                package = self.compare_package.currentText()
                if not target or not package or package.startswith('Unavailable:'):
                    raise ValueError('Choose a readable superflow and package column')
                summary, changes = self.m.compare_superflow(Path(target), package)
        except Exception as exc:
            QMessageBox.warning(self, APP, f'Comparison failed: {exc}')
            return
        self.compare_summary.setPlainText(summary)
        self.compare_model.set(changes)
        self.compare_table.resizeColumnsToContents()

    def _validation_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(button('Run validation', self.run_validation))
        self.val_lbl = QLabel('Not run yet')
        row.addWidget(self.val_lbl)
        row.addStretch(1)
        lay.addLayout(row)
        self.val_model = PandasModel(color=lambda d, r: SEV_COLOR.get(d.at[r, 'Severity'], ''))
        self.val_view = table(self.val_model)
        lay.addWidget(self.val_view, 1)
        return w

    def _etest_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel('Location'))
        self.et_loc = QComboBox()
        self.et_loc.addItems(list(etest_files.DEFAULT_ROOTS))
        self.et_loc.currentTextChanged.connect(self.et_location_changed)
        top.addWidget(self.et_loc)
        self.et_path = QLineEdit()
        self.et_path.returnPressed.connect(self.et_open_root)
        top.addWidget(self.et_path, 1)
        top.addWidget(button('Open', self.et_open_root, '', 'List this folder (read-only)'))
        top.addWidget(button('Open file...', self.et_browse_file, 'Secondary', 'Open a single file read-only'))
        lay.addLayout(top)
        info = QHBoxLayout()
        self.et_filter = QLineEdit(placeholderText='Filter file names in opened folders')
        self.et_filter.textChanged.connect(self.et_apply_filter)
        info.addWidget(self.et_filter, 1)
        info.addWidget(QLabel('Read-only: folders are listed and files opened for reading only; nothing is written.'))
        lay.addLayout(info)
        split = QSplitter(Qt.Horizontal)
        self.et_tree = QTreeWidget()
        self.et_tree.setHeaderLabels(['Name', 'Size', 'Modified'])
        self.et_tree.header().resizeSection(0, 300)
        self.et_tree.itemExpanded.connect(self.et_expand)
        self.et_tree.itemSelectionChanged.connect(self.et_open_selected)
        split.addWidget(self.et_tree)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.et_lbl = QLabel('Choose a location and select Open')
        self.et_lbl.setWordWrap(True)
        self.et_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self.et_lbl)
        self.et_tabs = QTabWidget()
        self.et_xml = QTreeWidget()
        self.et_xml.setHeaderLabels(['Element', 'Attributes', 'Text'])
        self.et_xml.header().resizeSection(0, 240)
        self.et_xml.header().resizeSection(1, 420)
        self.et_xml.itemExpanded.connect(self.et_xml_expand)
        self.et_tabs.addTab(self.et_xml, 'XML structure')
        text_page = QWidget()
        tl = QVBoxLayout(text_page)
        tl.setContentsMargins(0, 4, 0, 0)
        find = QHBoxLayout()
        self.et_find = QLineEdit(placeholderText='Find in file')
        self.et_find.returnPressed.connect(self.et_find_next)
        find.addWidget(self.et_find, 1)
        find.addWidget(button('Find next', self.et_find_next, 'Secondary'))
        tl.addLayout(find)
        self.et_text = QPlainTextEdit(readOnly=True)
        self.et_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        tl.addWidget(self.et_text, 1)
        self.et_tabs.addTab(text_page, 'Text')
        self.et_sheets = QTabWidget()
        self.et_tabs.addTab(self.et_sheets, 'Table')
        refs_page = QWidget()
        rfl = QVBoxLayout(refs_page)
        rfl.setContentsMargins(0, 4, 0, 0)
        rfl.addWidget(QLabel('Superflow names found in this file; green = used by the current package. '
                             'Double-click a structure or config to filter on it.'))
        self.et_refs_model = PandasModel(color=lambda d, r: STATE_COLOR['full'] if d.at[r, 'In_Package'] else '')
        self.et_refs_view = table(self.et_refs_model)
        self.et_refs_view.doubleClicked.connect(self.et_ref_activated)
        rfl.addWidget(self.et_refs_view, 1)
        self.et_tabs.addTab(refs_page, 'Package references')
        rl.addWidget(self.et_tabs, 1)
        split.addWidget(right)
        split.setSizes([420, 900])
        lay.addWidget(split, 1)
        self.et_location_changed(self.et_loc.currentText())
        return w

    # ----- Etest XML / TestSpec (read-only) --------------------------------------------------------------------------
    def _run_background(self, fn, done, failed):
        worker = Worker(fn)
        worker.done.connect(done)
        worker.failed.connect(failed)
        self._workers.append(worker)
        worker.start()

    def et_location_changed(self, label: str):
        saved = QSettings('Intel', 'TestPackageBuilder').value(f'etest_root/{label}', '')
        self.et_path.setText(saved or etest_files.DEFAULT_ROOTS.get(label, ''))

    def _et_item(self, name: str, path: str, is_dir: bool, size: int = 0, modified: float = 0.0) -> QTreeWidgetItem:
        stamp = datetime.fromtimestamp(modified).strftime('%Y-%m-%d %H:%M') if modified else ''
        it = QTreeWidgetItem([name, '' if is_dir else f'{size / 1024:,.0f} KB', stamp])
        it.setData(0, Qt.UserRole, path)
        it.setData(0, Qt.UserRole + 1, is_dir)
        if is_dir:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return it

    def et_open_root(self):
        path = self.et_path.text().strip().strip('"')
        if not path:
            return
        self._et_generation += 1
        self.et_tree.clear()
        item = self._et_item(path, path, True)
        item.setData(0, Qt.UserRole + 3, self.et_loc.currentText())
        self.et_tree.addTopLevelItem(item)
        item.setExpanded(True)

    def et_expand(self, item: QTreeWidgetItem):
        if not item.data(0, Qt.UserRole + 1) or item.data(0, Qt.UserRole + 2):
            return
        item.setData(0, Qt.UserRole + 2, True)
        path, gen = item.data(0, Qt.UserRole), self._et_generation
        item.takeChildren()
        item.addChild(QTreeWidgetItem(['Loading ...']))

        def done(entries):
            if gen != self._et_generation:
                return
            if label := item.data(0, Qt.UserRole + 3):
                QSettings('Intel', 'TestPackageBuilder').setValue(f'etest_root/{label}', path)
            item.takeChildren()
            for e in entries:
                item.addChild(self._et_item(e.name, e.path, e.is_dir, e.size, e.modified))
            if not entries:
                item.addChild(QTreeWidgetItem(['(empty)']))
            self.et_apply_filter()

        def failed(err):
            if gen != self._et_generation:
                return
            item.takeChildren()
            item.addChild(QTreeWidgetItem([f'Cannot read: {err}']))
            item.setData(0, Qt.UserRole + 2, False)
            label = item.data(0, Qt.UserRole + 3)
            default = etest_files.DEFAULT_ROOTS.get(label or '', '')
            if default and default != path:
                QSettings('Intel', 'TestPackageBuilder').remove(f'etest_root/{label}')
                self.et_lbl.setText(f'{path} could not be read; the default location for {label} was restored.')
                self.et_path.setText(default)

        self._run_background(lambda: etest_files.list_dir(path), done, failed)

    def et_apply_filter(self, *_):
        q = self.et_filter.text().strip().lower()
        stack = [self.et_tree.topLevelItem(i) for i in range(self.et_tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            stack += [it.child(i) for i in range(it.childCount())]
            is_file = bool(it.data(0, Qt.UserRole)) and not it.data(0, Qt.UserRole + 1)
            it.setHidden(bool(q and is_file and q not in it.text(0).lower()))

    def et_browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open Etest XML / TestSpec (read-only)', self.et_path.text())
        if path:
            self.et_load_file(path)

    def et_open_selected(self):
        items = self.et_tree.selectedItems()
        if items and items[0].data(0, Qt.UserRole) and not items[0].data(0, Qt.UserRole + 1):
            self.et_load_file(items[0].data(0, Qt.UserRole))

    def et_load_file(self, path: str):
        self._et_file_token += 1
        token = self._et_file_token
        self.et_lbl.setText(f'Reading {path} (read-only) ...')

        def load():
            data = etest_files.read_bytes(path)
            kind = etest_files.file_kind(path, data)
            root = etest_files.parse_xml(data) if kind == 'xml' else None
            sheets = etest_files.read_excel(data) if kind == 'excel' else None
            text = '' if kind == 'excel' else etest_files.decode(data)
            parsed = etest_files.read_table(text) if kind == 'text' else None
            counts = etest_files.reference_counts(kind, data, root, sheets)
            return path, len(data), kind, root, sheets, text, parsed, counts

        def done(result):
            if token == self._et_file_token:
                self.et_show_file(*result)

        def failed(err):
            if token == self._et_file_token:
                self.et_lbl.setText(f'Could not read {path}: {err}')

        self._run_background(load, done, failed)

    def et_show_file(self, path, size, kind, root, sheets, text, parsed, counts):
        limit = 5_000_000
        if kind == 'excel':
            self.et_text.setPlainText('Excel workbook: see the Table tab')
        elif len(text) > limit:
            self.et_text.setPlainText(text[:limit] + f'\n... truncated: showing {limit:,} of {len(text):,} characters')
        else:
            self.et_text.setPlainText(text)
        self.et_xml.clear()
        self._et_elements = []
        if root is not None:
            top = self._et_xml_item(root)
            self.et_xml.addTopLevelItem(top)
            top.setExpanded(True)
        self.et_sheets.clear()
        frames = sheets or ({'Parsed table': parsed} if parsed is not None else {})
        for name, df in frames.items():
            self.et_sheets.addTab(table(PandasModel(df.fillna(''))), str(name))
        refs = self.m.reference_matches(counts)
        self.et_refs_model.set(refs)
        self.et_refs_view.resizeColumnsToContents()
        used = int((refs.In_Package == 'yes').sum()) if len(refs) else 0
        self.et_lbl.setText(f'<b>{Path(path).name}</b> &nbsp;·&nbsp; {kind.upper()} &nbsp;·&nbsp; {size / 1024:,.0f} KB '
                            f'&nbsp;·&nbsp; {len(refs):,} superflow names referenced ({used:,} used by '
                            f'{self.m.package})<br><small>{path}</small>')
        self.et_tabs.setCurrentIndex(0 if kind == 'xml' else 2 if frames else 1)

    def _et_xml_item(self, el) -> QTreeWidgetItem:
        it = QTreeWidgetItem(list(etest_files.element_row(el)))
        it.setToolTip(1, it.text(1))
        it.setToolTip(2, it.text(2))
        self._et_elements.append(el)
        it.setData(0, Qt.UserRole, len(self._et_elements) - 1)
        if len(el):
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        return it

    def et_xml_expand(self, item: QTreeWidgetItem):
        if item.childCount() or item.data(0, Qt.UserRole) is None:
            return
        children = list(self._et_elements[item.data(0, Qt.UserRole)])
        limit = 5000
        for child in children[:limit]:
            item.addChild(self._et_xml_item(child))
        if len(children) > limit:
            item.addChild(QTreeWidgetItem([f'... {len(children) - limit:,} more elements (see the Text tab)']))

    def et_find_next(self):
        q = self.et_find.text()
        if not q:
            return
        if not self.et_text.find(q):
            cursor = self.et_text.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.et_text.setTextCursor(cursor)
            if not self.et_text.find(q):
                self.statusBar().showMessage(f"'{q}' not found in this file", 4000)

    def et_ref_activated(self, idx):
        ref = self.et_refs_model.df.iloc[idx.row()]
        if ref.Kind in ('Structure', 'New structure name'):
            self.search.setText(ref.Name)
            self.tabs.setCurrentIndex(0)
        elif ref.Kind == 'Test config':
            self.filt['Config'].setCurrentText(ref.Name)
            self.tabs.setCurrentIndex(2)

    # ----- refresh --------------------------------------------------------------------------------------------------
    def filtered(self) -> pd.DataFrame:
        st = self.m.struct_state()
        f = {k: cb.currentText() for k, cb in self.filt.items()}
        pk = f.pop('Package')
        if pk == 'in package':
            st = st[st.In_Package > 0]
        elif pk in ('full', 'partial'):
            st = st[st.State == pk]
        elif pk == 'not in package':
            st = st[st.In_Package == 0]
        elif pk == 'changed':
            st = st[st.Changed]
        cols = {'Family': 'Family', 'CH': 'CH', 'LNR (nm)': 'LNR_nm', 'Z': 'Z', 'Polarity': 'POL', 'VT': 'VT',
                'Legs / ADF': 'Legs', 'Env': 'Env', 'Library': 'Library', 'Owner': 'Owner', 'Status': 'Status'}
        for k, c in cols.items():
            if f[k] != ALL:
                st = st[st[c] == f[k]]
        if f['Row'] != ALL:
            st = st[st.Row.str.replace(r'_TC\d+[A-Z]?_\d+$', '', regex=True) == f['Row']]
        if f['Config'] != ALL:
            m = self.m.lines(configs=[f['Config']])
            st = st[st.index.isin(np.unique(self.m.struct_codes[m]))]
        q = self.search.text().strip().upper()
        if q:
            st = st[st.Structure.str.upper().str.contains(q, regex=False)
                    | st.Corrected_Name.str.upper().str.contains(q, regex=False)]
        return st

    def refresh_structures(self):
        st = self.filtered()
        self.cur = st
        self.st_model.set(st[STRUCT_COLS + ['Changed']])
        self.st_view.setColumnHidden(len(STRUCT_COLS), True)
        for i, wdt in enumerate([60, 200, 140, 80, 330, 330, 70, 45, 40, 55, 45, 40, 50, 60, 60, 55, 80, 80]):
            self.st_view.setColumnWidth(i, wdt)
        self.count_lbl.setText(f'{len(st):,} of {len(self.m.structs):,} structures')
        if self.tabs.currentIndex() == 2:
            self.refresh_configs()

    def refresh_header(self):
        s = self.m.summary()
        for k in ('tests', 'structures', 'rows', 'configs'):
            d = s[k] - s[f'{k}_orig']
            self.cards[k].setText(f'{s[k]:,}' + (f'  ({d:+,})' if d else ''))
        self.cards['added'].setText(f"+{s['tests_added']:,}")
        self.cards['dropped'].setText(f"-{s['tests_dropped']:,}")
        self.statusBar().showMessage(
            f"Package {s['package']}: {s['tests']:,} tests, {s['structures']:,} structures, {s['rows']:,} rows, "
            f"{s['configs']:,} configs  |  {len(s['history'])} actions")

    def refresh_configs(self):
        cs = self.m.config_state(structures=self.cur.Structure if hasattr(self, 'cur') else None)
        self.cfg_model.set(cs[['State', 'Config', 'Structures', 'Tests', 'In_Package', 'Originally']])
        self.cfg_view.setColumnWidth(1, 260)
        if len(self.cfg_model.df):
            selected = 0
            matched_doc = ''
            for row, cfg in enumerate(self.cfg_model.df.Config):
                # SharePoint workbooks open in Excel, so only ones already loaded are searched here
                for doc_name in [n for n in self.m.docs
                                 if not self.m.is_sharepoint_doc(n) or n in self.m._doc_cache]:
                    doc = self.m.doc(doc_name)
                    if doc and doc.lookup(cfg)[1]:
                        selected, matched_doc = row, doc_name
                        break
                if matched_doc:
                    break
            if matched_doc:
                self.doc_cb.setCurrentText(matched_doc)
            self.cfg_view.selectRow(selected)
            self.show_config()
        else:
            self.ct_model.set(pd.DataFrame())
            self.doc_tabs.clear()
            self.doc_lbl.setText('No test configurations match the current structure filter')

    def refresh_changes(self):
        self.sum_txt.setPlainText(self.m.summary_text())
        ch = self.m.changes()
        if len(ch):
            ch = ch.groupby(['Change', 'Test Row Alias', 'Test Structure Alias', 'Test Configuration Name']) \
                .size().rename('Tests').reset_index()
        self.ch_model.set(ch)

    def refresh_tab(self):
        i = self.tabs.currentIndex()
        if i == 1:
            self.update_tree_states()
        elif i == 2:
            self.refresh_configs()
        elif i == 3:
            self.refresh_changes()
        elif i == 4 and not self.compare_summary.toPlainText():
            self.compare_summary.setPlainText(
                f'Current: {self.m.superflow_source.label} / {self.m.package}\n'
                'Choose a comparison target and select Compare.')

    def refresh_all(self, rebuild_tree: bool = False):
        self.refresh_structures()
        self.refresh_header()
        if rebuild_tree:
            self.refresh_tree()
        else:
            self.update_tree_states()
        self.refresh_tab()

    def after_change(self, n: int, label: str):
        self.statusBar().showMessage(f'{label}: {n:,} tests changed' if n else f'{label}: nothing to change', 6000)
        if n:
            sel = self.selected_structures()
            self.refresh_structures()
            self.refresh_header()
            self.refresh_tab()
            self.reselect(sel)
            self.show_details()

    # ----- structures -----------------------------------------------------------------------------------------------
    def selected_structures(self) -> list[str]:
        rows = sorted({i.row() for i in self.st_view.selectionModel().selectedRows()})
        return list(self.st_model.df.Structure.iloc[rows]) if rows else []

    def reselect(self, names: list[str]):
        if not names:
            return
        sm = self.st_view.selectionModel()
        idx = self.st_model.df.index[self.st_model.df.Structure.isin(names)]
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        for r in idx[:500]:
            sm.select(self.st_model.index(int(r), 0), flags)

    def show_details(self, *_):
        names = self.selected_structures()
        if not names:
            self.detail.setText('Select one or more structures')
            self.sc_model.set(pd.DataFrame())
            return
        st = self.m.structs.set_index('Structure').loc[names[0]]
        issues = st.Issue_Codes or '-'
        self.detail.setText(
            f"<b>{names[0]}</b><br>New name: <b>{st.Corrected_Name or '-'}</b><br>"
            f"Family {st.Family} &nbsp; CH {st.CH} &nbsp; {st.Z} (LNR {st.LNR_nm or '-'} nm) &nbsp; POL {st.POL} "
            f"&nbsp; VT {st.VT} &nbsp; {st.Legs} &nbsp; {st.Env}<br>"
            f"Row {st.Row} &nbsp; Library {st.Library or '-'} &nbsp; Owner {st.Owner or '-'}<br>"
            f"Row status: {st.Row_Status} &nbsp; Validator: {st.Status}<br><small>Issues: {issues}</small>"
            + (f'<br><i>{len(names)} structures selected</i>' if len(names) > 1 else ''))
        cs = self.m.config_state(structures=names)
        self.sc_model.set(cs[['State', 'Config', 'Structures', 'Tests', 'In_Package']])
        self.sc_view.setColumnWidth(1, 220)

    def act_structures(self, value: bool, filtered: bool):
        names = list(self.cur.Structure) if filtered else self.selected_structures()
        if not names:
            return
        if filtered and len(names) > 200 and QMessageBox.question(
                self, APP, f"{'Add' if value else 'Drop'} all tests of {len(names):,} filtered structures?") \
                != QMessageBox.Yes:
            return
        label = f"{'Add' if value else 'Drop'} {len(names)} structure(s)"
        self.after_change(self.m.apply(label, self.m.lines(structures=names), value), label)

    def act_struct_configs(self, value: bool):
        names = self.selected_structures()
        rows = sorted({i.row() for i in self.sc_view.selectionModel().selectedRows()})
        if not names or not rows:
            QMessageBox.information(self, APP, 'Select structures and one or more configs on the right.')
            return
        cfgs = list(self.sc_model.df.Config.iloc[rows])
        label = f"{'Add' if value else 'Drop'} {', '.join(cfgs[:3])}{'...' if len(cfgs) > 3 else ''} " \
                f"for {len(names)} structure(s)"
        self.after_change(self.m.apply(label, self.m.lines(structures=names, configs=cfgs), value), label)

    def view_config(self):
        rows = sorted({i.row() for i in self.sc_view.selectionModel().selectedRows()})
        if not rows:
            return
        cfg = self.sc_model.df.Config.iloc[rows[0]]
        self.tabs.setCurrentIndex(2)
        idx = self.cfg_model.df.index[self.cfg_model.df.Config == cfg]
        if len(idx):
            self.cfg_view.selectRow(int(idx[0]))
            self.cfg_view.scrollTo(self.cfg_model.index(int(idx[0]), 0))

    def clear_filters(self):
        for cb in self.filt.values():
            cb.blockSignals(True)
            cb.setCurrentIndex(0)
            cb.blockSignals(False)
        self.search.clear()
        self.refresh_structures()

    # ----- branch explorer ------------------------------------------------------------------------------------------
    def node_state(self, base: str) -> str:
        names = self.base_map.get(base, [])
        if not names:
            return 'not in superflow'
        st = self.state_by_name
        n = sum(st.get(x, 0) for x in names)
        return 'yes' if n else ''

    def refresh_tree(self):
        expanded = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.UserRole)
            if item.isExpanded() and data:
                expanded.add(data[0])
        self.tree.clear()
        t = self.m.tree
        if not len(t):
            if self.m.validator_available:
                self.tree_lbl.setText('Run ros_matching_list.py to build OUTPUT/ROS_Structure_Tree.csv')
            else:
                self.tree_lbl.setText(f'No ROS branch tree is available for {self.m.superflow_source.node}; '
                                      'X81Z-only branch data is not reused for another node.')
            return
        st = self.m.struct_state()
        self.state_by_name = dict(zip(st.Structure, st.In_Package))
        roots = t[t.Level == '0']
        cols = {'Root': 'Role', 'CH': 'CH', 'LNR (nm)': 'LNR_nm', 'Polarity': 'POL', 'VT': 'VT', 'Legs': 'LEGS'}
        for k, c in cols.items():
            v = self.bf[k].currentText()
            if v != ALL:
                roots = roots[roots[c] == v]
        query = self.search.text().strip()
        if query:
            searchable = ['Structure', 'Role', 'CH', 'Z', 'LNR_nm', 'VT', 'POL', 'LEGS', 'Libraries', 'Rows', 'Configs']
            matching = t[searchable].fillna('').astype(str).apply(
                lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1)
            matched_groups = set(t.loc[matching, 'Group'])
            roots = roots[roots.Group.isin(matched_groups)]
        self.groups = t.groupby('Group')
        for r in roots.itertuples(index=False):
            it = self.tree_item(r)
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.tree.addTopLevelItem(it)
            if r.Group in expanded:
                it.setExpanded(True)
        self.tree_lbl.setText(f'{len(roots):,} ROS / ROSPFT roots')

    def tree_item(self, r) -> QTreeWidgetItem:
        it = QTreeWidgetItem([r.Structure, r.Role, self.node_state(r.Structure), r.CH, r.Z, r.LNR_nm, r.VT, r.POL,
                              r.LEGS, r.Libraries, r.Rows, r.Configs])
        it.setData(0, Qt.UserRole, (r.Group, r.Level))
        it.setToolTip(11, r.Configs)
        if it.text(2) == 'yes':
            for c in range(3):
                it.setBackground(c, QBrush(QColor(STATE_COLOR['full'])))
        if r.Level == '0':
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
        return it

    def expand_branch(self, item: QTreeWidgetItem):
        grp, lvl = item.data(0, Qt.UserRole)
        if lvl != '0' or item.childCount():
            return
        g = self.groups.get_group(grp)
        query = self.search.text().strip()
        if query:
            searchable = ['Structure', 'Role', 'CH', 'Z', 'LNR_nm', 'VT', 'POL', 'LEGS', 'Libraries', 'Rows', 'Configs']
            matching = g[searchable].fillna('').astype(str).apply(
                lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1)
            root_match = g.loc[g.Level == '0', searchable].fillna('').astype(str).apply(
                lambda col: col.str.contains(query, case=False, regex=False)).any(axis=1).any()
            if not root_match:
                g = g[matching]
        heads = {}
        for r in g[g.Level != '0'].itertuples(index=False):
            parent = heads.get(r.Role)
            if parent is None:
                parent = QTreeWidgetItem([r.Role, '', '', '', '', '', '', '', '', '', '', ''])
                parent.setData(0, Qt.UserRole, (grp, 'role:' + r.Role))
                f = parent.font(0)
                f.setItalic(True)
                parent.setFont(0, f)
                item.addChild(parent)
                heads[r.Role] = parent
            parent.addChild(self.tree_item(r))
        for p in heads.values():
            p.setText(1, f'{p.childCount()} structures')
            p.setExpanded(not p.text(0).endswith('supporting'))

    def update_tree_states(self):
        st = self.m.struct_state()
        self.state_by_name = dict(zip(st.Structure, st.In_Package))
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            stack += [it.child(i) for i in range(it.childCount())]
            if it.data(0, Qt.UserRole) and not str(it.data(0, Qt.UserRole)[1]).startswith('role:'):
                s = self.node_state(it.text(0))
                it.setText(2, s)
                for c in range(3):
                    it.setBackground(c, QBrush(QColor(STATE_COLOR['full'] if s == 'yes' else '#FFFFFF')))

    def act_branch(self, value: bool, depth: int):
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, APP, 'Select one or more ROS / ROSPFT roots or nodes in the tree.')
            return
        bases = set()
        for it in items:
            grp, lvl = it.data(0, Qt.UserRole)
            if depth < 0:
                if str(lvl).startswith('role:'):
                    bases |= {it.child(i).text(0) for i in range(it.childCount())}
                else:
                    bases.add(it.text(0))
            else:
                g = self.groups.get_group(grp)
                bases |= set(g[g.Level.astype(int) <= depth].Structure)
        names = [n for b in bases for n in self.base_map.get(b, [])]
        label = f"{'Add' if value else 'Drop'} branch ({len(bases)} structures)"
        n = self.m.apply(label, self.m.lines(structures=names), value)
        self.after_change(n, label)
        self.update_tree_states()

    # ----- configs --------------------------------------------------------------------------------------------------
    def show_config(self, *_):
        rows = sorted({i.row() for i in self.cfg_view.selectionModel().selectedRows()})
        if not rows:
            return
        cfg = self.cfg_model.df.Config.iloc[rows[0]]
        self.ct_model.set(self.m.config_tests(cfg))
        self.ct_view.resizeColumnsToContents()
        self.doc_tabs.clear()
        doc_name = self.doc_cb.currentText()
        if doc_name not in self.m.docs:
            self.doc_lbl.setText('No test config document found next to the scripts')
            return
        if self.m._doc_cache.get(doc_name) is None:
            if self.m.is_sharepoint_doc(doc_name):
                self._load_sharepoint_doc(doc_name)
                return
            self.doc_lbl.setText(f'Loading {doc_name} ...')
            QApplication.processEvents()
        try:
            doc = self.m.doc(doc_name)
        except Exception as exc:
            self.doc_lbl.setText(f'Could not read {doc_name}: {exc}')
            return
        hit, blocks = doc.lookup(cfg)
        if not blocks:
            self.doc_lbl.setText(f"<b>{cfg}</b> is not in {doc_name}; the superflow tests are listed above")
            return
        how = 'exact' if hit == cfg.upper() else f"as <b>{hit}</b> (closest entry - check the bias / variant)"
        self.doc_lbl.setText(f'<b>{cfg}</b> found {how} in {len(blocks)} sheet(s) of {doc_name}')
        for sheet, blk in blocks:
            v = table(PandasModel(blk))
            v.resizeColumnsToContents()
            self.doc_tabs.addTab(v, sheet)

    def _load_sharepoint_doc(self, name: str):
        self.doc_lbl.setText(f'Opening {name} read-only through Excel (uses your Office sign-in) ...')
        if name in self._doc_loading:
            return
        self._doc_loading.add(name)

        def done(doc):
            self._doc_loading.discard(name)
            if name in self.m.docs:
                self.m._doc_cache[name] = doc
            if self.doc_cb.currentText() == name:
                self.show_config()

        def failed(err):
            self._doc_loading.discard(name)
            if self.doc_cb.currentText() == name:
                self.doc_lbl.setText(f'Could not open {name} from SharePoint: {err}')

        self._run_background(lambda: self.m.load_doc(name), done, failed)

    def act_configs(self, value: bool):
        rows = sorted({i.row() for i in self.cfg_view.selectionModel().selectedRows()})
        if not rows:
            return
        cfgs = list(self.cfg_model.df.Config.iloc[rows])
        label = f"{'Add' if value else 'Drop'} {', '.join(cfgs[:3])}{'...' if len(cfgs) > 3 else ''} " \
                f"for {len(self.cur):,} filtered structures"
        self.after_change(self.m.apply(label, self.m.lines(structures=self.cur.Structure, configs=cfgs), value), label)

    # ----- package actions ------------------------------------------------------------------------------------------
    def change_package(self, name: str):
        if self.m.history and QMessageBox.question(
                self, APP, f'Discard {len(self.m.history)} unsaved action(s) on {self.m.package}?') != QMessageBox.Yes:
            self.pkg.blockSignals(True)
            self.pkg.setCurrentText(self.m.package)
            self.pkg.blockSignals(False)
            return
        self.m.set_package(name)
        self.val_model.set(pd.DataFrame())
        self.val_lbl.setText('Not run yet')
        self.refresh_all()

    def new_package(self):
        name, ok = QInputDialog.getText(self, APP, 'New package column name (e.g. MY_PKG_01):')
        name = name.strip().upper().replace(' ', '_')
        if not ok or not name:
            return
        if name in self.m.flows:
            QMessageBox.warning(self, APP, f'{name} already exists')
            return
        start = QMessageBox.question(self, APP, f'Start {name} from the current selection of {self.m.package}?\n'
                                                '(No = start empty)')
        keep = self.m.sel.copy() if start == QMessageBox.Yes else None
        self.m.set_package(name, new=True)
        if keep is not None:
            self.m.apply(f'Copy selection into {name}', keep, True)
        self.pkg.blockSignals(True)
        self.pkg.addItem(name)
        self.pkg.setCurrentText(name)
        self.pkg.blockSignals(False)
        self.refresh_all()

    def undo(self):
        label = self.m.undo()
        if label:
            self.after_change(1, f'Undone: {label}')

    def reset(self):
        if self.m.history and QMessageBox.question(self, APP, 'Discard every change of this package?') \
                == QMessageBox.Yes:
            self.m.reset()
            self.refresh_all()

    def run_validation(self):
        v = self.m.validate()
        self.val_model.set(v)
        self.val_lbl.setText(' | '.join(f'{k}: {n}' for k, n in v.Severity.value_counts().items())
                             or 'No findings - the selection is consistent')
        for i, wdt in enumerate([70, 140, 230, 380]):
            self.val_view.setColumnWidth(i, wdt)
        self.tabs.setCurrentIndex(5)

    def export(self, only_package: bool, fmt: str):
        out_dir = HERE / 'OUTPUT' / 'TestPackages'
        out_dir.mkdir(parents=True, exist_ok=True)
        default = out_dir / f"{self.m.package}{'' if only_package else '_SUPERFLOW'}.{fmt}"
        path, _ = QFileDialog.getSaveFileName(self, 'Export', str(default),
                                              'Excel (*.xlsx)' if fmt == 'xlsx' else 'CSV (*.csv)')
        if not path:
            return
        dlg = QProgressDialog('Exporting ...', None, 0, 0, self)
        dlg.setWindowTitle(APP)
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.show()
        fn = (lambda: self.m.export(Path(path), only_package)) if fmt == 'xlsx' else \
            (lambda: self.m.export_csv(Path(path)))
        self.worker = Worker(fn)
        self.worker.done.connect(lambda n: (dlg.close(), QMessageBox.information(
            self, APP, f'{n:,} tests written to\n{path}')))
        self.worker.failed.connect(lambda e: (dlg.close(), QMessageBox.critical(self, APP, e)))
        self.worker.start()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setFont(QFont('Segoe UI', 9))
    dlg = QProgressDialog('Loading ...', None, 0, 0)
    dlg.setWindowTitle(APP)
    dlg.setMinimumWidth(380)
    dlg.show()

    def progress(msg):
        dlg.setLabelText(msg)
        app.processEvents()

    try:
        settings = QSettings('Intel', 'TestPackageBuilder')
        data_root = Path(settings.value('data_root', str(HERE)))
        sharepoint_docs = settings.value('sharepoint_docs', [], type=list)
        superflow_files = [Path(p) for p in settings.value('superflow_files', [], type=list)]
        model = TestPackageModel(progress, data_root=data_root, sharepoint_docs=sharepoint_docs,
                     superflow_paths=superflow_files)
    except FileNotFoundError as e:
        QMessageBox.critical(None, APP, f'Missing input: {e.filename}\nRun rebuild_all.py first.')
        return 1
    dlg.close()
    w = MainWindow(model)
    w.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
