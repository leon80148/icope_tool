from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QMessageBox, QVBoxLayout, QWidget

from icope_tool import __version__
from icope_tool.idcheck import mask_person_id
from icope_tool.services.hpdcs.client import ocr_available
from icope_tool.store import DataStore
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.data_folder import CopyCancelled, DataFolderDialog, copy_data_dir, data_files
from icope_tool.ui.messages import CARD_HINTS, local_save_failed
from icope_tool.ui.progress import run_with_progress
from icope_tool.ui.widgets import Banner, Card, Spinner, Toast, button, hbox, label, status_chip
from icope_tool.ui.workers import run_in_background

try:
    from icope_tool.services.card import nhi_card
except Exception:   # pragma: no cover
    nhi_card = None


class LocalTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        self.unsaved_tabs: Callable[[], list[str]] = lambda: []    # 由設定頁提供
        self._cancel = threading.Event()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(Banner("neutral", "這一頁的設定只影響這台電腦",
                                "其他頁的設定存在「資料存放位置」，指到同一個位置的電腦都會共用。", icon_name="monitor"))

        folder_card = Card("資料存放位置", "診所資訊、轉介資源、衛教單張與國健署帳號都存在這個資料夾。"
                                       "多台電腦要共用時，把它指到 NAS 或共用磁碟。", "hard-drive")
        layout.addWidget(folder_card)
        folder_card.add(label("資料夾路徑", "fieldLabel"))
        self.folder_path = label("", selectable=True, wrap=True)
        folder_card.add(self.folder_path)
        self.folder_status_holder = QVBoxLayout()
        folder_card.add(self.folder_status_holder)
        folder_card.add(hbox(button("開啟資料夾", "folder-open", "secondary", on_click=self.open_folder),
                             button("變更資料夾…", "hard-drive", "secondary", on_click=self.change_folder), None,
                             spacing=8))

        reader_card = Card("健保卡讀卡機", "通常不用設定。若這台電腦接了兩台讀卡機（例如健保卡與醫事人員卡），可以指定健保卡用的那台。",
                           "credit-card")
        layout.addWidget(reader_card)
        self.reader_box = QComboBox()
        self.reader_box.setAccessibleName("健保卡讀卡機")
        self.reader_box.setMinimumWidth(320)
        refresh = button("重新偵測", "refresh-cw", "ghost", on_click=self.refresh_readers)
        self.test_button = button("測試讀卡", "scan-line", "secondary", on_click=self.test_card)
        reader_card.add(hbox(self.reader_box, refresh, None, self.test_button, spacing=8))
        self.reader_note = label("", "caption", wrap=True)
        reader_card.add(self.reader_note)
        self.test_busy = QWidget()
        self.test_text = label("請插入健保卡…", "subtle")
        self.test_busy.setLayout(hbox(Spinner(20), self.test_text, None, spacing=10))
        self.test_busy.hide()
        reader_card.add(self.test_busy)
        self.test_result = Banner("success")
        self.test_result.hide()
        reader_card.add(self.test_result)

        about = Card("關於", icon_name="info")
        layout.addWidget(about)
        info = QGridLayout()
        info.setHorizontalSpacing(16)
        info.setVerticalSpacing(8)
        info.addWidget(label("版本", "fieldLabel"), 0, 0)
        info.addWidget(label(__version__), 0, 1)
        info.addWidget(label("驗證碼自動辨識", "fieldLabel"), 1, 0)
        info.addWidget(status_chip("success", "可用") if ocr_available() else status_chip("warning", "不可用，需人工輸入"),
                       1, 1)
        info.addWidget(label("查詢紀錄", "fieldLabel"), 2, 0)
        info.addWidget(label("只記錄時間、事件與身分證雜湊碼，不含身分證字號與姓名。", "caption", wrap=True), 2, 1)
        info.setColumnStretch(1, 1)
        about.add(info)
        about.add(hbox(button("開啟紀錄資料夾", "folder-open", "ghost", on_click=self.open_logs), None))
        layout.addStretch(1)

        self.save_warning = Banner("warning")
        self.save_warning.hide()
        layout.insertWidget(1, self.save_warning)
        self.reader_box.currentIndexChanged.connect(self._reader_changed)
        ctx.data_dir_changed.connect(self.refresh_folder)
        self.refresh_folder()
        self.refresh_readers()

    # ------------------------------------------------------------------ 資料夾
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_folder()

    def refresh_folder(self) -> None:
        self.folder_path.setText(str(self.ctx.store.root))
        self.folder_path.setToolTip(str(self.ctx.store.root))
        while self.folder_status_holder.count():
            item = self.folder_status_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        ok = self.ctx.data_dir_available()
        chip = (status_chip("success", "可以正常存取") if ok
                else status_chip("danger", "無法存取：請確認網路磁碟已連線，或按「變更資料夾…」"))
        self.folder_status_holder.addLayout(hbox(chip, None))

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ctx.store.root)))

    def open_logs(self) -> None:
        folder = self.ctx.audit.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def change_folder(self) -> None:
        unsaved = self.unsaved_tabs()
        if unsaved:
            answer = QMessageBox.question(
                self, "有尚未儲存的設定",
                f"「{'、'.join(unsaved)}」有變更還沒儲存。換資料夾後會改讀新資料夾的設定，"
                "這些變更會捨棄，不會寫到新資料夾。要繼續嗎？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        dialog = DataFolderDialog(self, "change", self.ctx.store.root)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
            return
        target, copy_current, source = dialog.chosen, dialog.copy_current, self.ctx.store.root
        total = len(data_files(source)) if copy_current and not DataStore.looks_initialized(target) else 0

        def work(report, cancel):
            if total:
                copy_data_dir(source, target, report, cancel)
            DataStore(target).ensure_ready(create=True)

        def done(_value):
            saved = self.ctx.switch_data_dir(target)
            self.ctx.audit.record("data_dir_changed", copied=copy_current)
            self.refresh_folder()
            self._show_save_result(saved, "已改用新的資料存放位置")

        def failed(exc):
            if isinstance(exc, CopyCancelled):
                self.toast.show_message("已取消，仍使用原本的資料存放位置")
                return
            message = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
            QMessageBox.warning(self, "無法使用這個資料夾", f"{message}\n目前仍使用原本的資料存放位置。")

        text = "正在複製資料到新位置…" if total else "正在檢查資料夾…"
        run_with_progress(self, "變更資料存放位置", text, total, work, done, failed, cancellable=bool(total))

    # ------------------------------------------------------------------ 讀卡機
    def refresh_readers(self) -> None:
        readers = nhi_card.list_readers() if nhi_card else []
        hint = self.ctx.local.reader_hint
        self.reader_box.blockSignals(True)
        self.reader_box.clear()
        self.reader_box.addItem("自動偵測（建議）", "")
        for name in readers:
            self.reader_box.addItem(name, name)
        if hint and hint not in readers:
            self.reader_box.addItem(f"{hint}（目前未連接）", hint)
        index = self.reader_box.findData(hint)
        self.reader_box.setCurrentIndex(max(0, index))
        self.reader_box.blockSignals(False)
        if nhi_card is None:
            self.reader_note.setText("這台電腦無法載入讀卡元件。")
            self.test_button.setEnabled(False)
        elif readers:
            self.reader_note.setText(f"偵測到 {len(readers)} 台讀卡機。")
        else:
            self.reader_note.setText("目前沒有偵測到讀卡機；接上後按「重新偵測」。")

    def _reader_changed(self) -> None:
        saved = self.ctx.update_local(reader_hint=self.reader_box.currentData() or "")
        self._show_save_result(saved, "已更新讀卡機設定")

    def _show_save_result(self, saved: bool, message: str) -> None:
        if saved:
            self.save_warning.hide()
            self.toast.show_message(message)
        else:
            self.save_warning.set_content("warning", "設定沒有存起來", local_save_failed(self.ctx.local_store.path))
            self.save_warning.show()

    def test_card(self) -> None:
        if nhi_card is None:
            return
        if self.test_busy.isVisible():
            self._cancel.set()
            return
        self._cancel.clear()
        self.test_result.hide()
        self.test_busy.show()
        self.test_button.setText("取消測試")
        hint = self.ctx.local.reader_hint
        cancel = self._cancel.is_set

        def done(fields):
            self.test_result.set_content("success", "讀卡成功",
                                         f"讀到：{fields.get('name', '')}　{mask_person_id(fields.get('pid', ''))}")
            self.test_result.show()

        def failed(exc):
            code = getattr(exc, "code", "")
            if code == "CANCELLED":
                return
            self.test_result.set_content("warning", getattr(exc, "message", str(exc)), CARD_HINTS.get(code, ""))
            self.test_result.show()

        def finished():
            self.test_busy.hide()
            self.test_button.setText("測試讀卡")
            self.refresh_readers()

        run_in_background(self, lambda: nhi_card.read_basic(15, hint, cancel=cancel), done, failed, finished)
