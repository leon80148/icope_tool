from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QHeaderView, QLineEdit, QMenu, QMessageBox,
    QTableView, QVBoxLayout, QWidget,
)

from icope_tool.domains import DOMAINS, domain_names
from icope_tool.models import Resource, normalize_text
from icope_tool.paths import app_dir
from icope_tool.services import importer
from icope_tool.services.pack import PackError, apply_pack, read_pack
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.editors import ResourceDialog
from icope_tool.ui.dialogs.importing import ExcelImportDialog, PackImportDialog
from icope_tool.ui.theme import C, icon
from icope_tool.ui.widgets import Banner, Card, EmptyState, Toast, button, hbox, label, recovery_actions, write_with_retry

ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_SORT = Qt.ItemDataRole.UserRole + 2
ROLE_DOMAINS = Qt.ItemDataRole.UserRole + 3
ROLE_SEARCH = Qt.ItemDataRole.UserRole + 4
ROLE_ENABLED = Qt.ItemDataRole.UserRole + 5
ROLE_TYPE = Qt.ItemDataRole.UserRole + 6

COLUMNS = ["常用", "名稱", "類型", "適用項目", "電話", "地址", "預設", "狀態"]


def example_pack_path() -> Path | None:
    for base in (app_dir() / "examples", Path(__file__).resolve().parents[3] / "examples"):
        candidate = base / "chiayi-city-resources.json"
        if candidate.exists():
            return candidate
    return None


class _Filter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.keyword = ""
        self.domain = ""
        self.type_name = ""
        self.show_disabled = True
        self.setSortRole(ROLE_SORT)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        index = self.sourceModel().index(row, 1, parent)
        if not self.show_disabled and not index.data(ROLE_ENABLED):
            return False
        domains = index.data(ROLE_DOMAINS) or []
        if self.domain == "__none__" and domains:
            return False
        if self.domain and self.domain != "__none__" and self.domain not in domains:
            return False
        if self.type_name and index.data(ROLE_TYPE) != self.type_name:
            return False
        return not self.keyword or self.keyword in (index.data(ROLE_SEARCH) or "")


class ResourcesTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        self.resources: list[Resource] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        self.error = Banner("danger")
        self.error.hide()
        layout.addWidget(self.error)

        card = Card("轉介資源庫", "選轉介時會依「適用項目」列出建議資源；「常用」的排在最上面，「院所預設」的可以一鍵帶入。",
                    "link")
        layout.addWidget(card, 1)

        add = button("新增資源", "plus", "primary", on_click=self.add_resource)
        import_button = button("匯入", "file-down", "secondary", tooltip="從 Excel 或示範資料匯入")
        menu = QMenu(import_button)
        menu.addAction(icon("file-spreadsheet", C["primary"]), "從 Excel 匯入…", self.import_excel)
        menu.addAction(icon("download", C["primary"]), "下載 Excel 範本…", self.save_template)
        self.example_action = menu.addAction(icon("sparkles", C["primary"]), "匯入嘉義市示範資料（271 筆）",
                                             self.import_example)
        import_button.setMenu(menu)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋名稱、地址、電話")
        self.search.setMinimumWidth(210)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("搜尋資源")
        self.domain_filter = QComboBox()
        self.domain_filter.setAccessibleName("依適用項目篩選")
        self.domain_filter.addItem("全部項目", "")
        for domain in DOMAINS:
            self.domain_filter.addItem(domain.title, domain.id)
        self.domain_filter.addItem("沒有標記適用項目", "__none__")
        self.type_filter = QComboBox()
        self.type_filter.setAccessibleName("依類型篩選")
        self.type_filter.setMinimumWidth(170)
        self.type_filter.setMaximumWidth(260)
        self.show_disabled = QCheckBox("顯示已停用")
        self.show_disabled.setChecked(True)
        card.add(hbox(add, import_button, 12, self.search, self.domain_filter, self.type_filter, self.show_disabled,
                      spacing=8))

        self.model = QStandardItemModel(0, len(COLUMNS))
        self.model.setHorizontalHeaderLabels(COLUMNS)
        self.proxy = _Filter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setAccessibleName("轉介資源清單")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(7, 84)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 170)
        self.table.setColumnWidth(4, 130)
        card.add(self.table, 1)
        self.empty = EmptyState("link", "資源庫還是空的",
                                "新增常用的轉介單位，或從衛生局的社區資源盤點表 Excel 匯入。",
                                button("從 Excel 匯入…", "file-spreadsheet", "primary", on_click=self.import_excel))
        self.empty.hide()
        card.add(self.empty, 1)

        self.count = label("", "caption")
        self.edit_button = button("編輯", "pencil", "secondary", size="sm", tooltip="編輯選取的資源（Enter）",
                                  on_click=self.edit_selected)
        self.pin_button = button("設為常用", "star", "secondary", size="sm", on_click=self.toggle_pin)
        self.default_button = button("設為院所預設", "clipboard-check", "secondary", size="sm",
                                     tooltip="院所預設：製作轉介單時按「帶入院所預設」，會自動加入適用項目",
                                     on_click=self.toggle_default)
        self.enable_button = button("停用", "ban", "secondary", size="sm", on_click=self.toggle_enabled)
        self.delete_button = button("刪除", "trash", "danger", size="sm", tooltip="刪除選取的資源（Delete）",
                                    on_click=self.delete_selected)
        card.add(hbox(self.count, None, self.edit_button, self.pin_button, self.default_button, self.enable_button,
                      self.delete_button, spacing=8))

        self.search.textChanged.connect(self._apply_filter)
        self.domain_filter.currentIndexChanged.connect(self._apply_filter)
        self.type_filter.currentIndexChanged.connect(self._apply_filter)
        self.show_disabled.toggled.connect(self._apply_filter)
        self.table.doubleClicked.connect(lambda _i: self.edit_selected())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())
        self.table.customContextMenuRequested.connect(self._context_menu)
        # 只在表格有焦點時作用：在搜尋框按 Delete／Enter 不會動到資料
        for keys, action in ((QKeySequence.StandardKey.Delete, self.delete_selected),
                             (QKeySequence(Qt.Key.Key_Return), self.edit_selected),
                             (QKeySequence(Qt.Key.Key_Enter), self.edit_selected)):
            QShortcut(keys, self.table, activated=action, context=Qt.ShortcutContext.WidgetShortcut)
        ctx.resources_changed.connect(self.reload)
        self.reload()

    # ------------------------------------------------------------------ 資料
    def reload(self) -> None:
        try:
            self.resources = self.ctx.store.list_resources()
            self.error.hide()
        except StoreError as exc:
            self.error.set_content("danger", "無法讀取轉介資源", f"{exc}。資料夾恢復後按「重試」。",
                                   recovery_actions(self.reload, lambda: self.ctx.navigate.emit("settings", "local")))
            self.error.show()
            return
        selected = set(self._selected_ids())
        self.model.removeRows(0, self.model.rowCount())
        for resource in self.resources:
            self.model.appendRow(self._row(resource))
        current_type = self.type_filter.currentData()
        self.type_filter.blockSignals(True)
        self.type_filter.clear()
        self.type_filter.addItem("全部類型", "")
        for type_name in sorted({r.type for r in self.resources if r.type}):
            self.type_filter.addItem(type_name, type_name)
        index = self.type_filter.findData(current_type)
        self.type_filter.setCurrentIndex(max(0, index))
        self.type_filter.blockSignals(False)
        self._apply_filter()
        if selected:
            self._select_ids(selected)
        self.example_action.setVisible(example_pack_path() is not None)

    def _row(self, resource: Resource) -> list[QStandardItem]:
        pinned = QStandardItem()
        if resource.pinned:
            pinned.setIcon(icon("star", C["warning"]))
            pinned.setToolTip("常用")
            pinned.setAccessibleText("常用")
        pinned.setData(0 if resource.pinned else 1, ROLE_SORT)
        name = QStandardItem(resource.name)
        name.setData(resource.id, ROLE_ID)
        name.setData(resource.name, ROLE_SORT)
        name.setData(list(resource.domains), ROLE_DOMAINS)
        name.setData(resource.type, ROLE_TYPE)
        name.setData(resource.enabled, ROLE_ENABLED)
        name.setData(normalize_text(" ".join([resource.name, resource.type, resource.address, *resource.phones,
                                              resource.contact, resource.note])), ROLE_SEARCH)
        cells = [
            pinned, name, QStandardItem(resource.type), QStandardItem(domain_names(resource.domains) or "—"),
            QStandardItem("、".join(resource.phones)), QStandardItem(resource.address),
            QStandardItem("預設" if resource.include_by_default else ""),
            QStandardItem("啟用" if resource.enabled else "已停用"),
        ]
        for cell in cells[2:]:
            cell.setData(cell.text(), ROLE_SORT)
        if not resource.enabled:
            for cell in cells:
                cell.setForeground(QColor(C["muted"]))
        cells[1].setToolTip(resource.name)
        cells[5].setToolTip(resource.address)
        return cells

    def _apply_filter(self) -> None:
        self.proxy.keyword = normalize_text(self.search.text())
        self.proxy.domain = self.domain_filter.currentData() or ""
        self.proxy.type_name = self.type_filter.currentData() or ""
        self.proxy.show_disabled = self.show_disabled.isChecked()
        self.proxy.invalidate()
        total = len(self.resources)
        shown = self.proxy.rowCount()
        disabled = sum(1 for r in self.resources if not r.enabled)
        text = f"共 {total} 筆" + (f"（{disabled} 筆停用）" if disabled else "")
        if shown != total:
            text += f"，符合條件 {shown} 筆"
        self.count.setText(text)
        self.table.setVisible(total > 0)
        self.empty.setVisible(total == 0)
        self._update_actions()

    def _selected_ids(self) -> list[str]:
        ids = []
        if not hasattr(self, "table"):
            return ids
        for index in self.table.selectionModel().selectedRows(1):
            ids.append(index.data(ROLE_ID))
        return ids

    def _select_ids(self, ids: set[str]) -> None:
        selection = self.table.selectionModel()
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 1)
            if index.data(ROLE_ID) in ids:
                selection.select(index, selection.SelectionFlag.Select | selection.SelectionFlag.Rows)

    def _selected_resources(self) -> list[Resource]:
        wanted = set(self._selected_ids())
        return [r for r in self.resources if r.id in wanted]

    def _update_actions(self) -> None:
        chosen = self._selected_resources()
        count = len(chosen)
        self.edit_button.setEnabled(count == 1)
        for widget in (self.pin_button, self.default_button, self.enable_button, self.delete_button):
            widget.setEnabled(count > 0)
        all_pinned = bool(chosen) and all(r.pinned for r in chosen)
        all_default = bool(chosen) and all(r.include_by_default for r in chosen)
        all_enabled = bool(chosen) and all(r.enabled for r in chosen)
        self.pin_button.setText("取消常用" if all_pinned else "設為常用")
        self.default_button.setText("取消院所預設" if all_default else "設為院所預設")
        self.enable_button.setText("停用" if all_enabled or not chosen else "啟用")
        self.delete_button.setText(f"刪除 {count} 筆" if count > 1 else "刪除")

    # ------------------------------------------------------------------ 動作
    def _context_menu(self, pos) -> None:
        if not self._selected_ids():
            return
        menu = QMenu(self)
        menu.addAction(icon("pencil", C["primary"]), "編輯", self.edit_selected).setEnabled(len(self._selected_ids()) == 1)
        menu.addAction(icon("star", C["warning"]), self.pin_button.text(), self.toggle_pin)
        menu.addAction(icon("clipboard-check", C["primary"]), self.default_button.text(), self.toggle_default)
        menu.addAction(icon("ban", C["subtle"]), self.enable_button.text(), self.toggle_enabled)
        menu.addSeparator()
        menu.addAction(icon("trash", C["danger"]), self.delete_button.text(), self.delete_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _types(self) -> list[str]:
        return sorted({r.type for r in self.resources if r.type})

    def _save(self, fn, message: str) -> bool:
        ok, _result = write_with_retry(self, "無法儲存轉介資源", fn)
        if not ok:
            self.reload()
            return False
        self.ctx.mark_saved("resources")
        self.reload()
        self.toast.show_message(message)
        return True

    def add_resource(self) -> None:
        dialog = ResourceDialog(self, self._types())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.resource:
            resource = dialog.resource
            if self._save(lambda: self.ctx.store.add_resource(resource), f"已新增「{resource.name}」"):
                self._select_ids({resource.id})

    def edit_selected(self) -> None:
        chosen = self._selected_resources()
        if len(chosen) != 1:
            return
        dialog = ResourceDialog(self, self._types(), chosen[0])
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.resource:
            resource = dialog.resource
            if self._save(lambda: self.ctx.store.update_resource(resource), f"已更新「{resource.name}」"):
                self._select_ids({resource.id})

    def toggle_pin(self) -> None:
        chosen = self._selected_resources()
        if not chosen:
            return
        value = not all(r.pinned for r in chosen)
        ids = [r.id for r in chosen]
        if self._save(lambda: self.ctx.store.patch_resources(ids, pinned=value),
                      f"已將 {len(ids)} 筆{'設為常用' if value else '取消常用'}"):
            self._select_ids(set(ids))

    def toggle_default(self) -> None:
        chosen = self._selected_resources()
        if not chosen:
            return
        value = not all(r.include_by_default for r in chosen)
        ids = [r.id for r in chosen]
        if self._save(lambda: self.ctx.store.patch_resources(ids, include_by_default=value),
                      f"已將 {len(ids)} 筆{'設為' if value else '取消'}院所預設"):
            self._select_ids(set(ids))

    def toggle_enabled(self) -> None:
        chosen = self._selected_resources()
        if not chosen:
            return
        value = not all(r.enabled for r in chosen)
        ids = [r.id for r in chosen]
        if self._save(lambda: self.ctx.store.patch_resources(ids, enabled=value),
                      f"已{'啟用' if value else '停用'} {len(ids)} 筆"):
            self._select_ids(set(ids))

    def delete_selected(self) -> None:
        chosen = self._selected_resources()
        if not chosen:
            return
        names = "、".join(r.name for r in chosen[:3]) + ("…" if len(chosen) > 3 else "")
        answer = QMessageBox.question(
            self, "刪除轉介資源？",
            f"確定要刪除 {len(chosen)} 筆：{names}？\n刪除後無法復原；只是暫時不用的話，可以改用「停用」。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        ids = [r.id for r in chosen]
        self._save(lambda: self.ctx.store.delete_resources(ids), f"已刪除 {len(ids)} 筆")

    def import_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇 Excel 檔", str(Path.home()), "Excel 活頁簿 (*.xlsx)")
        if not path:
            return
        dialog = ExcelImportDialog(self, self.ctx.store, Path(path))
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.new_items:
            items = dialog.new_items
            result_holder = {}

            def run():
                result_holder["r"] = self.ctx.store.import_resources(items)

            if self._save(run, "匯入完成"):
                result = result_holder["r"]
                self.toast.show_message(f"已匯入 {result.added} 筆" + (f"，略過重複 {result.skipped} 筆" if result.skipped else ""))
                self.ctx.audit.record("resources_import_excel", added=result.added, skipped=result.skipped)

    def save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "儲存 Excel 範本", str(Path.home() / "Documents" / "轉介資源匯入範本.xlsx"),
                                              "Excel 活頁簿 (*.xlsx)")
        if not path:
            return
        try:
            importer.write_template_xlsx(Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "無法儲存範本", f"{exc.strerror or exc}（檔案是否正被 Excel 開著？）")
            return
        self.toast.show_message("已儲存範本，填好後用「從 Excel 匯入」匯入")

    def import_example(self) -> None:
        path = example_pack_path()
        if path is None:
            return
        try:
            pack = read_pack(path)
        except PackError as exc:
            QMessageBox.warning(self, "無法讀取示範資料", str(exc))
            return
        dialog = PackImportDialog(self, self.ctx.store, pack, "匯入嘉義市示範資料")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mode = dialog.mode
        holder = {}

        def run():
            holder["r"] = apply_pack(self.ctx.store, pack, mode)

        if self._save(run, "匯入完成"):
            result = holder["r"]
            self.ctx.mark_saved("materials")
            self.toast.show_message(f"已匯入 {result.resources_added} 筆示範資源")
