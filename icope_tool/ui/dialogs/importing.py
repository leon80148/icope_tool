"""Excel 匯入預覽與設定包匯入確認。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QRadioButton, QTableView, QWidget

from icope_tool.domains import domain_names
from icope_tool.services.importer import ImportError_, ImportPreview, list_sheets, parse_resources_xlsx
from icope_tool.services.pack import LoadedPack
from icope_tool.store import DataStore
from icope_tool.ui.dialogs.base import BaseDialog, field_label
from icope_tool.ui.theme import C
from icope_tool.ui.widgets import Banner, button, hbox, label, vbox
from icope_tool.ui.workers import run_in_background

PREVIEW_ROWS = 200


class ExcelImportDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, store: DataStore, path: Path):
        super().__init__(parent, "從 Excel 匯入轉介資源", f"檔案：{path.name}", "file-spreadsheet", width=900)
        self.resize(940, 640)
        self.store = store
        self.path = path
        self.preview: ImportPreview | None = None
        self.new_items = []

        self.sheet = QComboBox()
        self.sheet.setAccessibleName("工作表")
        self.sheet.setMinimumWidth(220)
        self.format_label = label("", "subtle")
        self.body.addLayout(hbox(field_label("工作表", buddy=self.sheet), self.sheet, 12, self.format_label, None,
                                 spacing=8))
        self.stats = Banner("info")
        self.body.addWidget(self.stats)
        self.warnings = Banner("warning")
        self.warnings.hide()
        self.body.addWidget(self.warnings)

        self.model = QStandardItemModel(0, 5)
        self.model.setHorizontalHeaderLabels(["狀態", "名稱", "類型", "適用項目", "電話／地址"])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 120), (2, 180), (3, 150)):
            self.table.setColumnWidth(column, width)
        self.body.addWidget(self.table, 1)
        self.more = label("", "caption")
        self.body.addWidget(self.more)

        self.add_cancel()
        self.import_button = self.add_button(button("匯入", "file-down", "primary", on_click=self.accept))
        self.table.setAccessibleName("匯入預覽")
        self._parse_seq = 0

        # 大型盤點表解析需要幾秒：在背景讀取，視窗先出現並顯示讀取中
        self._set_loading("正在讀取 Excel…")
        self.sheet.setEnabled(False)

        def first_load():
            sheets = list_sheets(path)
            return sheets, parse_resources_xlsx(path)

        def loaded(value):
            sheets, first = value
            self.sheet.blockSignals(True)
            self.sheet.addItems(sheets)
            self.sheet.setCurrentText(first.sheet)
            self.sheet.blockSignals(False)
            self.sheet.setEnabled(True)
            self._show(first)

        run_in_background(self, first_load, loaded, self._parse_failed)
        self.sheet.currentTextChanged.connect(self._reparse)

    def _set_loading(self, text: str) -> None:
        self.stats.set_content("info", text, "檔案較大時需要幾秒鐘。", icon_name="loader-circle")
        self.warnings.hide()
        self.import_button.setEnabled(False)
        self.import_button.setText("匯入")

    def _parse_failed(self, exc: BaseException) -> None:
        self.sheet.setEnabled(self.sheet.count() > 0)
        self._fail(str(exc) if isinstance(exc, ImportError_) else f"{type(exc).__name__}：{exc}")

    def _fail(self, message: str) -> None:
        self.preview = None
        self.new_items = []
        self.model.removeRows(0, self.model.rowCount())
        self.stats.set_content("danger", "無法匯入這個工作表", message)
        self.warnings.hide()
        self.format_label.setText("")
        self.more.setText("")
        self.import_button.setEnabled(False)
        self.import_button.setText("匯入")

    def _reparse(self, sheet: str) -> None:
        self._parse_seq += 1
        seq = self._parse_seq
        self._set_loading(f"正在讀取「{sheet}」…")

        def shown(preview):
            if seq == self._parse_seq:
                self._show(preview)

        def failed(exc):
            if seq == self._parse_seq:
                self._parse_failed(exc)

        run_in_background(self, lambda: parse_resources_xlsx(self.path, sheet), shown, failed)

    def _show(self, preview: ImportPreview) -> None:
        self.preview = preview
        existing = {r.dedupe_key() for r in self.store.list_resources()}
        seen = set()
        self.new_items = []
        self.model.removeRows(0, self.model.rowCount())
        duplicates = 0
        for index, resource in enumerate(preview.resources):
            key = resource.dedupe_key()
            duplicate = key in existing or key in seen
            seen.add(key)
            if duplicate:
                duplicates += 1
            else:
                self.new_items.append(resource)
            if index < PREVIEW_ROWS:
                status = QStandardItem("已存在，略過" if duplicate else "新增")
                status.setForeground(QColor(C["subtle"] if duplicate else C["success"]))
                detail = "　".join(filter(None, ["、".join(resource.phones), resource.address]))
                cells = [status, QStandardItem(resource.name), QStandardItem(resource.type),
                         QStandardItem(domain_names(resource.domains) or "—"), QStandardItem(detail)]
                for cell in cells:
                    cell.setToolTip(cell.text())      # 欄寬不夠時滑過可以看完整內容
                self.model.appendRow(cells)
        for column, floor in ((0, 120), (2, 180), (3, 150)):
            self.table.resizeColumnToContents(column)
            self.table.setColumnWidth(column, max(floor, min(self.table.columnWidth(column), 260)))
        self.format_label.setText(f"辨識為：{preview.format_label}")
        total = len(preview.resources)
        text = f"讀到 {total} 筆，新增 {len(self.new_items)} 筆"
        if duplicates:
            text += f"，{duplicates} 筆和現有資源同名同地址會略過"
        if preview.skipped_rows:
            text += f"，{preview.skipped_rows} 列缺少名稱已略過"
        self.stats.set_content("info" if self.new_items else "warning", text + "。",
                               "匯入後可以到資源庫逐筆調整類型、適用項目或設為常用。")
        if preview.warnings:
            self.warnings.set_content("warning", "請留意", "\n".join(preview.warnings))
            self.warnings.show()
        else:
            self.warnings.hide()
        self.more.setText(f"只顯示前 {PREVIEW_ROWS} 筆預覽" if total > PREVIEW_ROWS else "")
        self.import_button.setEnabled(bool(self.new_items))
        self.import_button.setText(f"匯入 {len(self.new_items)} 筆" if self.new_items else "沒有可匯入的資料")


class PackImportDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, store: DataStore, pack: LoadedPack, title: str = "匯入設定包"):
        super().__init__(parent, title, f"檔案：{pack.path.name}", "package", width=600)
        self.mode = "merge"
        duplicates = store.count_duplicate_resources(pack.manifest.resources)
        lines = [f"轉介資源 {pack.resource_count} 筆"
                 + (f"（其中 {duplicates} 筆和現有資源重複，合併時會略過）" if duplicates else ""),
                 f"衛教單張 {pack.material_count} 份"]
        if pack.manifest.exported_at and pack.manifest.app_version != "example":
            lines.append(f"匯出時間 {pack.manifest.exported_at.replace('T', ' ')}")
        self.body.addWidget(Banner("info", "設定包內容", "\n".join(lines), icon_name="package"))

        self.body.addWidget(field_label("匯入方式"))
        self.merge = QRadioButton("合併（建議）：保留現有資料，只加入新的")
        self.merge.setChecked(True)
        current_resources, current_materials = len(store.list_resources()), len(store.list_materials())
        if pack.material_count:
            scope = f"現有的 {current_resources} 筆轉介資源與 {current_materials} 份衛教單張"
            replace_text = "取代：刪除現有的轉介資源與衛教單張，改用設定包的內容"
        else:
            scope = f"現有的 {current_resources} 筆轉介資源"
            replace_text = "取代：刪除現有的轉介資源，改用設定包的資源（衛教單張保留不動）"
        self.replace = QRadioButton(replace_text)
        self.body.addLayout(vbox(self.merge, self.replace, spacing=8))
        self.replace_warning = Banner(
            "danger", f"{scope}會被刪除",
            "取代後無法復原。建議先到「設定 › 匯出／匯入」匯出設定包備份。"
            + ("" if pack.material_count else f"\n設定包沒有衛教單張，目前的 {current_materials} 份單張會保留。"))
        self.replace_warning.hide()
        self.body.addWidget(self.replace_warning)
        self.confirm = QCheckBox(f"我了解{scope}會被刪除")
        self.confirm.hide()
        self.body.addWidget(self.confirm)

        self.add_cancel()
        self.import_button = self.add_button(button("匯入", "file-down", "primary", on_click=self._accept))
        self.merge.toggled.connect(self._update)
        self.confirm.toggled.connect(self._update)
        self._update()

    def _update(self) -> None:
        replace = self.replace.isChecked()
        self.mode = "replace" if replace else "merge"
        self.replace_warning.setVisible(replace)
        self.confirm.setVisible(replace)
        self.import_button.setEnabled(not replace or self.confirm.isChecked())
        self.import_button.setProperty("kind", "danger" if replace else "primary")
        self.import_button.style().unpolish(self.import_button)
        self.import_button.style().polish(self.import_button)

    def _accept(self) -> None:
        self.accept()
