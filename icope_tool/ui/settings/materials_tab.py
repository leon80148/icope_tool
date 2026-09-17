from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QColor, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut, QStandardItem, QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHeaderView, QMessageBox, QTableView, QVBoxLayout, QWidget,
)

from icope_tool.domains import domain_names
from icope_tool.models import Material
from icope_tool.store import StoreError, inspect_pdf
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.editors import MaterialDialog
from icope_tool.ui.progress import run_with_progress
from icope_tool.ui.theme import C
from icope_tool.ui.widgets import Banner, Card, EmptyState, Toast, button, hbox, label, recovery_actions, write_with_retry

ROLE_ID = Qt.ItemDataRole.UserRole + 1


class MaterialsTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        self.materials: list[Material] = []
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        self.error = Banner("danger")
        self.error.hide()
        layout.addWidget(self.error)

        card = Card("衛教單張", "要給長者的衛教 PDF。列印轉介單時勾選，會依這裡的順序附在轉介單後面。", "book-open")
        layout.addWidget(card, 1)
        add = button("加入 PDF…", "upload", "primary", on_click=self.add_files)
        card.add(hbox(add, label("也可以把 PDF 檔直接拖曳到這裡", "caption"), None, spacing=12))

        self.model = QStandardItemModel(0, 6)
        self.model.setHorizontalHeaderLabels(["順序", "名稱", "建議搭配項目", "頁數", "預設", "狀態"])
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setAccessibleName("衛教單張清單")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 104)
        self.missing_banner = Banner("warning")
        self.missing_banner.hide()
        card.add(self.missing_banner)
        card.add(self.table, 1)
        self.empty = EmptyState("book-open", "還沒有衛教單張",
                                "按「加入 PDF…」選擇衛教單張，或把 PDF 檔拖曳到這個視窗。",
                                button("加入 PDF…", "upload", "primary", on_click=self.add_files))
        card.add(self.empty, 1)

        self.count = label("", "caption")
        self.up_button = button("上移", "arrow-up", "secondary", size="sm", tooltip="往前移（Ctrl+↑）",
                                on_click=lambda: self.move_selected(-1))
        self.down_button = button("下移", "arrow-down", "secondary", size="sm", tooltip="往後移（Ctrl+↓）",
                                  on_click=lambda: self.move_selected(1))
        self.preview_button = button("預覽", "eye", "secondary", size="sm", on_click=self.preview)
        self.relink_button = button("重新指定 PDF…", "file-up", "secondary", size="sm",
                                    tooltip="換成另一個 PDF 檔；名稱、建議項目與順序都保留",
                                    on_click=self.relink_selected)
        self.edit_button = button("編輯", "pencil", "secondary", size="sm", tooltip="編輯選取的單張（Enter）",
                                  on_click=self.edit_selected)
        self.default_button = button("設為院所預設", "clipboard-check", "secondary", size="sm",
                                     tooltip="院所預設：製作轉介單時按「帶入院所預設」，會自動附上建議搭配項目的單張",
                                     on_click=self.toggle_default)
        self.enable_button = button("停用", "ban", "secondary", size="sm", on_click=self.toggle_enabled)
        self.delete_button = button("刪除", "trash", "danger", size="sm", tooltip="刪除選取的單張（Delete）",
                                    on_click=self.delete_selected)
        card.add(hbox(self.count, None, self.up_button, self.down_button, 12, self.preview_button,
                      self.relink_button, self.edit_button, self.default_button, self.enable_button,
                      self.delete_button, spacing=8))

        self.table.doubleClicked.connect(lambda _i: self.edit_selected())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())
        # 與資源表一致；只在表格有焦點時作用
        for keys, action in ((QKeySequence.StandardKey.Delete, self.delete_selected),
                             (QKeySequence(Qt.Key.Key_Return), self.edit_selected),
                             (QKeySequence(Qt.Key.Key_Enter), self.edit_selected),
                             (QKeySequence("Ctrl+Up"), lambda: self.move_selected(-1)),
                             (QKeySequence("Ctrl+Down"), lambda: self.move_selected(1))):
            QShortcut(keys, self.table, activated=action, context=Qt.ShortcutContext.WidgetShortcut)
        ctx.materials_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        try:
            self.materials = self.ctx.store.list_materials()
            self.error.hide()
        except StoreError as exc:
            self.error.set_content("danger", "無法讀取衛教單張", f"{exc}。資料夾恢復後按「重試」。",
                                   recovery_actions(self.reload, lambda: self.ctx.navigate.emit("settings", "local")))
            self.error.show()
            return
        selected = self._selected()
        self.model.removeRows(0, self.model.rowCount())
        for order, material in enumerate(self.materials, start=1):
            missing = not self.ctx.store.material_file(material).exists()
            name = QStandardItem(material.name + (f"　—　{material.description}" if material.description else ""))
            name.setData(material.id, ROLE_ID)
            status = "檔案遺失" if missing else ("啟用" if material.enabled else "已停用")
            cells = [QStandardItem(str(order)), name, QStandardItem(domain_names(material.domains) or "—"),
                     QStandardItem(f"{material.pages} 頁"),
                     QStandardItem("預設" if material.include_by_default else ""), QStandardItem(status)]
            if missing:
                cells[5].setForeground(QColor(C["danger"]))
                cells[5].setToolTip("PDF 檔案不見了：選取後按「重新指定 PDF…」換回檔案，名稱與設定都會保留")
            elif not material.enabled:
                for cell in cells:
                    cell.setForeground(QColor(C["muted"]))
            self.model.appendRow(cells)
        lost = [m.name for m in self.materials if not self.ctx.store.material_file(m).exists()]
        if lost:
            names = "、".join(lost[:3]) + ("…" if len(lost) > 3 else "")
            self.missing_banner.set_content(
                "warning", f"有 {len(lost)} 份單張的 PDF 檔案不見了：{names}",
                "列印時無法附上這些單張。選取該列後按「重新指定 PDF…」換回檔案，名稱、建議項目與順序都會保留。")
        self.missing_banner.setVisible(bool(lost))
        total = len(self.materials)
        pages = sum(m.pages for m in self.materials)
        self.count.setText(f"共 {total} 份，{pages} 頁" if total else "")
        self.table.setVisible(bool(total))
        self.empty.setVisible(not total)
        if selected:
            self._select(selected.id)
        self._update_actions()

    def _selected(self) -> Material | None:
        rows = self.table.selectionModel().selectedRows(1) if hasattr(self, "table") else []
        if not rows:
            return None
        material_id = rows[0].data(ROLE_ID)
        return next((m for m in self.materials if m.id == material_id), None)

    def _select(self, material_id: str) -> None:
        for row in range(self.model.rowCount()):
            if self.model.index(row, 1).data(ROLE_ID) == material_id:
                self.table.selectRow(row)
                return

    def _update_actions(self) -> None:
        material = self._selected()
        has = material is not None
        index = self.materials.index(material) if material else -1
        self.up_button.setEnabled(has and index > 0)
        self.down_button.setEnabled(has and index < len(self.materials) - 1)
        for widget in (self.preview_button, self.relink_button, self.edit_button, self.default_button,
                       self.enable_button, self.delete_button):
            widget.setEnabled(has)
        self.default_button.setText("取消院所預設" if material and material.include_by_default else "設為院所預設")
        self.enable_button.setText("啟用" if material and not material.enabled else "停用")
        missing = material is not None and not self.ctx.store.material_file(material).exists()
        self.preview_button.setEnabled(has and not missing)

    def _saved(self, message: str, select: str | None = None) -> None:
        self.ctx.mark_saved("materials")
        self.reload()
        if select:
            self._select(select)
        self.toast.show_message(message)

    # ------------------------------------------------------------------ 加入
    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "選擇衛教單張 PDF", str(Path.home()), "PDF 檔案 (*.pdf)")
        self.add_paths([Path(p) for p in paths])

    def add_paths(self, paths: list[Path]) -> None:
        paths = [p for p in paths if p.suffix.lower() == ".pdf"]
        if not paths:
            return
        if len(paths) == 1:
            path = paths[0]
            try:
                pages = inspect_pdf(path)
            except StoreError as exc:
                QMessageBox.warning(self, "無法加入這個 PDF", str(exc))
                return
            dialog = MaterialDialog(self, None, path.name, pages)
            dialog.name.setText(path.stem)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.material is None:
                return
            draft = dialog.material

            def add():
                added = self.ctx.store.add_material_file(path, draft.name, draft.description, draft.domains,
                                                         draft.include_by_default)
                if not draft.enabled:
                    self.ctx.store.patch_materials([added.id], enabled=False)
                return added

            ok, material = write_with_retry(self, "無法加入這個 PDF", add)
            if not ok:
                self.reload()
                return
            self.ctx.audit.record("material_added", pages=material.pages)
            self._saved(f"已加入「{material.name}」", material.id)
            return
        store = self.ctx.store
        total = len(paths)

        def work(report, cancel):
            added, problems = [], []
            for index, path in enumerate(paths):
                if cancel.is_set():
                    break
                report(index, f"正在加入第 {index + 1} / {total} 份：{path.name}")
                try:
                    added.append(store.add_material_file(path))
                except StoreError as exc:
                    problems.append(f"{path.name}：{exc}")
            report(total, "完成")
            return added, problems, cancel.is_set()

        def done(value):
            added, problems, cancelled = value
            if added:
                self.ctx.audit.record("material_added", count=len(added))
                prefix = f"已取消，先加入的 {len(added)} 份已保留" if cancelled else f"已加入 {len(added)} 份"
                self._saved(f"{prefix}，可點兩下設定名稱與建議搭配項目", added[-1].id)
            elif cancelled:
                self.toast.show_message("已取消，沒有加入任何檔案")
            if problems:
                QMessageBox.warning(self, "部分檔案沒有加入", "\n".join(problems))

        def failed(exc):
            self.reload()
            QMessageBox.warning(self, "加入 PDF 時發生錯誤", f"{type(exc).__name__}：{exc}")

        run_with_progress(self, "加入衛教單張", f"正在加入 {total} 份 PDF…", total, work, done, failed)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.toLocalFile().lower().endswith(".pdf") for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        event.acceptProposedAction()
        self.add_paths(paths)

    # ------------------------------------------------------------------ 其他動作
    def move_selected(self, delta: int) -> None:
        material = self._selected()
        if not material:
            return
        try:
            moved = self.ctx.store.move_material(material.id, delta)
        except StoreError as exc:
            QMessageBox.warning(self, "無法調整順序", str(exc))
            return
        if moved:
            self.ctx.mark_saved("materials")
            self.reload()
            self._select(material.id)
            self.table.setFocus()

    def preview(self) -> None:
        material = self._selected()
        if not material:
            return
        path = self.ctx.store.material_file(material)
        if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.toast.show_message("無法開啟：檔案不存在，或電腦沒有 PDF 閱讀器")

    def relink_selected(self) -> None:
        material = self._selected()
        if not material:
            return
        path, _ = QFileDialog.getOpenFileName(self, f"重新指定「{material.name}」的 PDF", str(Path.home()),
                                              "PDF 檔案 (*.pdf)")
        if not path:
            return
        try:
            updated = self.ctx.store.replace_material_file(material.id, Path(path))
        except StoreError as exc:
            QMessageBox.warning(self, "無法使用這個 PDF", str(exc))
            return
        self.ctx.audit.record("material_relinked", pages=updated.pages)
        self._saved(f"已更新「{updated.name}」的 PDF（{updated.pages} 頁）", material.id)

    def edit_selected(self) -> None:
        material = self._selected()
        if not material:
            return
        dialog = MaterialDialog(self, material)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.material:
            edited = dialog.material
            ok, _result = write_with_retry(self, "無法儲存衛教單張", lambda: self.ctx.store.update_material(edited))
            if not ok:
                self.reload()
                return
            self._saved(f"已更新「{dialog.material.name}」", material.id)

    def toggle_default(self) -> None:
        material = self._selected()
        if not material:
            return
        value = not material.include_by_default
        try:
            self.ctx.store.patch_materials([material.id], include_by_default=value)
        except StoreError as exc:
            QMessageBox.warning(self, "無法儲存", str(exc))
            return
        self._saved(f"已將「{material.name}」{'設為' if value else '取消'}院所預設", material.id)

    def toggle_enabled(self) -> None:
        material = self._selected()
        if not material:
            return
        try:
            self.ctx.store.patch_materials([material.id], enabled=not material.enabled)
        except StoreError as exc:
            QMessageBox.warning(self, "無法儲存", str(exc))
            return
        self._saved(f"已{'停用' if material.enabled else '啟用'}「{material.name}」", material.id)

    def delete_selected(self) -> None:
        material = self._selected()
        if not material:
            return
        answer = QMessageBox.question(
            self, "刪除衛教單張？", f"確定要刪除「{material.name}」？\nPDF 檔會一起從資料夾刪除，無法復原。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.store.delete_materials([material.id])
        except StoreError as exc:
            QMessageBox.warning(self, "無法刪除", str(exc))
            return
        self._saved(f"已刪除「{material.name}」")
