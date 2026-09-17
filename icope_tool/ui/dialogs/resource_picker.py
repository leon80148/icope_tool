from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHeaderView, QLineEdit, QTableView, QWidget

from icope_tool.domains import DOMAIN_BY_ID, domain_names
from icope_tool.models import Resource, normalize_text
from icope_tool.ui.dialogs.base import BaseDialog
from icope_tool.ui.theme import C, icon
from icope_tool.ui.widgets import button, hbox, label

ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_SEARCH = Qt.ItemDataRole.UserRole + 2
ROLE_TYPE = Qt.ItemDataRole.UserRole + 3


class _Filter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.keyword = ""
        self.type_name = ""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        index = self.sourceModel().index(source_row, 0, source_parent)
        if self.type_name and index.data(ROLE_TYPE) != self.type_name:
            return False
        return not self.keyword or self.keyword in (index.data(ROLE_SEARCH) or "")


class ResourcePickerDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, resources: Iterable[Resource], domain_id: str,
                 already: Iterable[str]):
        domain = DOMAIN_BY_ID[domain_id]
        super().__init__(parent, f"從資源庫加入：{domain.title}",
                         "搜尋全部轉介資源（包含沒有標記這個項目的），勾選後加入這份轉介單。", "search", width=960)
        self.resize(1000, 640)
        already = set(already)
        self.selected_ids: list[str] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋名稱、地址、電話或類型")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("搜尋資源")
        self.type_box = QComboBox()
        self.type_box.setAccessibleName("依類型篩選")
        self.type_box.setMinimumWidth(200)
        items = [r for r in resources if r.enabled]
        types = sorted({r.type for r in items if r.type})
        self.type_box.addItem("全部類型", "")
        for type_name in types:
            self.type_box.addItem(type_name, type_name)
        self.body.addLayout(hbox(self.search, self.type_box, spacing=8))

        self.model = QStandardItemModel(0, 4)
        self.model.setHorizontalHeaderLabels(["名稱", "類型", "適用項目", "電話／地址"])
        for resource in sorted(items, key=lambda r: (domain_id not in r.domains, r.type, r.name)):
            name = QStandardItem(resource.name)
            name.setCheckable(True)
            name.setData(resource.id, ROLE_ID)
            name.setData(resource.type, ROLE_TYPE)
            name.setData(normalize_text(" ".join([resource.name, resource.type, resource.address, *resource.phones])),
                         ROLE_SEARCH)
            name.setEditable(False)
            if resource.id in already:
                name.setCheckState(Qt.CheckState.Checked)
                name.setEnabled(False)
                name.setToolTip("已經在這個項目裡")
            row = [name, QStandardItem(resource.type), QStandardItem(domain_names(resource.domains) or "—"),
                   QStandardItem("　".join(filter(None, ["、".join(resource.phones), resource.address])))]
            for item in row[1:]:
                item.setEditable(False)
                item.setEnabled(resource.id not in already)
            self.model.appendRow(row)
        self.proxy = _Filter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 250)
        self.table.setAccessibleName("資源清單，按空白鍵勾選")
        self.body.addWidget(self.table, 1)
        self.empty = label("找不到符合的資源，試試其他關鍵字，或到「設定 › 轉介資源庫」新增。", "subtle")
        self.empty.hide()
        self.body.addWidget(self.empty)

        self.count_label = label("", "subtle")
        self.buttons.insertWidget(0, self.count_label)
        self.add_cancel()
        self.confirm = self.add_button(button("加入", "plus", "primary", on_click=self._confirm))

        self.search.textChanged.connect(self._apply_filter)
        self.type_box.currentIndexChanged.connect(self._apply_filter)
        self.model.itemChanged.connect(self._update_count)
        self.table.doubleClicked.connect(self._toggle_row)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self.table, activated=lambda: self._toggle_row(self.table.currentIndex()),
                  context=Qt.ShortcutContext.WidgetShortcut)      # 游標在任何一欄按空白鍵都能勾選整列
        self._update_count()
        self.search.setFocus()

    def _apply_filter(self) -> None:
        self.proxy.keyword = normalize_text(self.search.text())
        self.proxy.type_name = self.type_box.currentData() or ""
        self.proxy.invalidate()
        self.empty.setVisible(self.proxy.rowCount() == 0)

    def _toggle_row(self, proxy_index: QModelIndex) -> None:
        source = self.proxy.mapToSource(proxy_index)
        item = self.model.item(source.row(), 0)
        if item is not None and item.isEnabled():
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)

    def _checked_ids(self) -> list[str]:
        ids = []
        for row in range(self.model.rowCount()):
            item = self.model.item(row, 0)
            if item.isEnabled() and item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(ROLE_ID))
        return ids

    def _update_count(self, *_args) -> None:
        count = len(self._checked_ids())
        self.count_label.setText(f"已勾選 {count} 筆" if count else "勾選要加入的資源")
        self.confirm.setText(f"加入 {count} 筆" if count else "加入")
        self.confirm.setEnabled(count > 0)

    def _confirm(self) -> None:
        self.selected_ids = self._checked_ids()
        self.accept()
