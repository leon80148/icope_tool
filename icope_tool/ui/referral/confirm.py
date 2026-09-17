"""步驟 4：填寫長者姓名等資訊、預覽 PDF、列印或另存。"""
from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QCompleter, QFileDialog, QLineEdit, QPlainTextEdit, QStackedWidget, QVBoxLayout, QWidget,
)

from icope_tool.paths import temp_output_dir
from icope_tool.services.pdf.referral_sheet import BuildResult, PdfBuildError, ReferralJob, build_referral_pdf
from icope_tool.ui.dialogs.base import field_label
from icope_tool.ui.printing import PrintError, print_pdf
from icope_tool.ui.referral.state import ReferralState
from icope_tool.ui.theme import C
from icope_tool.ui.widgets import (
    Banner, Card, EmptyState, Spinner, button, hbox, label, link_button, scroll_area, set_prop, vbox,
)
from icope_tool.ui.workers import run_in_background

NOTES_LIMIT = 400


def cleanup_old_outputs(folder: Path, keep_seconds: int = 24 * 3600) -> None:
    """刪掉一天前產生的暫存 PDF（轉介單含病患姓名，不應長留；單張合併檔交給外部閱讀器後也靠這裡清）。"""
    try:
        now = time.time()
        for pattern in ("ICOPE轉介單_*.pdf", "ICOPE衛教單張_*.pdf"):
            for path in folder.glob(pattern):
                if now - path.stat().st_mtime > keep_seconds:
                    path.unlink(missing_ok=True)
    except OSError:
        pass


class ConfirmStep(QWidget):
    changed = Signal()
    finished_job = Signal(str)            # print / save / open
    reset_requested = Signal()
    go_settings = Signal(str)

    def __init__(self, state: ReferralState, build_job: Callable[[], tuple[ReferralJob, dict[str, Path]]],
                 recent_assessors: Callable[[], list[str]], card_name: Callable[[], str],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._build_job = build_job
        self._card_name = card_name
        self._recent_assessors = recent_assessors
        self._pdf_path: Path | None = None
        self._version = 0             # 內容每變一次 +1；PDF 只在版本相同時才算最新
        self._pdf_version = -1
        self._done_version = -1       # 最近一次列印／另存／開啟時的版本
        self._session = 0             # 清除重來後 +1；舊長者的背景結果一律丟棄
        self._generating = False
        self._pending: Callable[[Path], None] | None = None

        root = vbox(spacing=0)
        self.setLayout(root)
        columns = hbox(spacing=16)
        root.addLayout(columns, 1)

        # ---- 左：資訊與動作 ----
        left = QWidget()
        form = QVBoxLayout(left)
        form.setContentsMargins(0, 0, 10, 0)
        form.setSpacing(8)
        left_column = QWidget()
        left_column.setMinimumWidth(350)
        left_column.setMaximumWidth(440)
        column_layout = QVBoxLayout(left_column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(8)
        column_layout.addWidget(scroll_area(left, fade=C["bg"]), 1)
        columns.addWidget(left_column)

        self.clinic_banner = Banner(
            "warning", "還沒填診所名稱", "轉介單頁首會顯示診所名稱與電話，建議先填寫。",
            actions=[button("填寫診所資訊", "settings", "secondary", size="sm",
                            on_click=lambda: self.go_settings.emit("clinic"))])
        form.addWidget(self.clinic_banner)

        self.patient = QLineEdit()
        form.addWidget(field_label("長者姓名", buddy=self.patient))
        self.patient.setPlaceholderText("選填，會印在轉介單上")
        self.patient.setAccessibleName("長者姓名")
        self.patient.textEdited.connect(self._on_patient)
        form.addWidget(self.patient)
        self.card_hint = link_button("", self._use_card_name)
        form.addWidget(self.card_hint)
        self.patient_caption = label("", "caption")
        self.patient_caption.hide()
        form.addWidget(self.patient_caption)
        self._patient_caption_fn: Callable[[], str] = lambda: ""

        self.assessor = QLineEdit()
        form.addWidget(field_label("評估人員", buddy=self.assessor))
        self.assessor.setPlaceholderText("選填，例如：林美華 護理師")
        self.assessor.setAccessibleName("評估人員")
        self._assessor_model = QStringListModel()
        completer = QCompleter(self._assessor_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.assessor.setCompleter(completer)
        self.assessor.textEdited.connect(self._on_assessor)
        form.addWidget(self.assessor)

        self.notes = QPlainTextEdit()
        form.addWidget(field_label("給長者的叮嚀", buddy=self.notes))
        self.notes.setPlaceholderText("選填，例如：下次回診請帶所有藥袋；起身時放慢速度。")
        self.notes.setAccessibleName("給長者的叮嚀")
        self.notes.setFixedHeight(96)
        self.notes.textChanged.connect(self._on_notes)
        form.addWidget(self.notes)
        self.notes_count = label("", "caption")
        form.addWidget(self.notes_count)
        self.notes_over = False

        self.summary = label("", "subtle", wrap=True)
        form.addWidget(self.summary)

        self.print_button = button("列印", "printer", "primary", "lg", tooltip="產生最新內容並選擇印表機（Ctrl+P）",
                                   on_click=lambda: self._with_pdf(self._print))
        self.save_button = button("另存 PDF…", "download", "secondary",
                                  on_click=lambda: self._with_pdf(self._save_as))
        self.open_button = button("用 PDF 閱讀器開啟", "external-link", "ghost",
                                  on_click=lambda: self._with_pdf(self._open_external))
        for action_button in (self.print_button, self.save_button, self.open_button):
            action_button.setProperty("default_tip", action_button.toolTip())
        form.addStretch(1)
        # 主要動作固定在左欄底部：小螢幕或上方出現提示時也不必捲動才找得到「列印」
        column_layout.addWidget(self.print_button)
        column_layout.addLayout(hbox(self.save_button, self.open_button, spacing=8))
        self.result_banner = Banner("success")
        self.result_banner.hide()
        column_layout.addWidget(self.result_banner)

        # ---- 右：預覽 ----
        self.preview_card = Card("預覽", icon_name="eye", margins=(16, 14, 16, 16))
        columns.addWidget(self.preview_card, 1)
        self.refresh_button = button("更新預覽", "refresh-cw", "ghost", size="sm",
                                     on_click=lambda: self._generate(None))
        self.fit_page = button("整頁", kind="ghost", size="sm", tooltip="一次看到整頁",
                               on_click=lambda: self._set_zoom(True))
        self.fit_width = button("頁寬", kind="ghost", size="sm", tooltip="放大到頁面寬度，方便看清楚文字",
                                on_click=lambda: self._set_zoom(False))
        for zoom_button in (self.fit_page, self.fit_width):
            zoom_button.setCheckable(True)
        assert self.preview_card.header_actions is not None
        self.preview_card.header_actions.addWidget(self.fit_page)
        self.preview_card.header_actions.addWidget(self.fit_width)
        self.preview_card.header_actions.addWidget(self.refresh_button)
        self.preview_status = label("", "caption")
        self.preview_card.add(self.preview_status)
        self.preview_stack = QStackedWidget()
        self.preview_card.add(self.preview_stack, 1)
        self.preview_empty = EmptyState("file-text", "還沒有預覽", "按「更新預覽」看看印出來的樣子。")
        self.preview_stack.addWidget(self.preview_empty)
        loading = QWidget()
        loading.setLayout(vbox(None, Spinner(36), label("正在產生 PDF…", "subtle"), None, spacing=10))
        loading.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_stack.addWidget(loading)
        self.document = QPdfDocument(self)
        self.view = QPdfView()
        self.view.setDocument(self.document)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView)
        self.fit_page.setChecked(True)
        self.view.setPageSpacing(12)
        self.view.setAccessibleName("轉介衛教單預覽")
        self.preview_stack.addWidget(self.view)
        self.error_banner = Banner("danger")
        error_holder = QWidget()
        error_holder.setLayout(vbox(self.error_banner, None))
        self.preview_stack.addWidget(error_holder)

        temp_output_dir().mkdir(parents=True, exist_ok=True)
        cleanup_old_outputs(temp_output_dir())

    def bind_patient_label(self, fn: Callable[[], str]) -> None:
        self._patient_caption_fn = fn

    # ------------------------------------------------------------------ 同步
    def sync_from_state(self, clinic_name: str) -> None:
        for widget, value in ((self.patient, self.state.patient_name), (self.assessor, self.state.assessor)):
            if widget.text() != value:
                widget.setText(value)
        if self.notes.toPlainText() != self.state.notes:
            self.notes.blockSignals(True)
            self.notes.setPlainText(self.state.notes)
            self.notes.blockSignals(False)
        self._assessor_model.setStringList(self._recent_assessors())
        self.clinic_banner.setVisible(not clinic_name)
        caption = self._patient_caption_fn()
        self.patient_caption.setText(caption)
        self.patient_caption.setVisible(bool(caption))
        self._update_card_hint()
        self._update_summary()
        self._update_notes_count()
        self._update_actions()
        if not self.state.can_print():
            self.preview_status.setText("請至少勾選一個異常項目或衛教單張")

    def _update_card_hint(self) -> None:
        name = self._card_name()
        show = bool(name) and name != self.state.patient_name
        self.card_hint.setText(f"帶入剛讀卡的姓名：{name}")
        self.card_hint.setVisible(show)

    def _update_summary(self) -> None:
        domains = len(self.state.domains)
        resources = len(self.state.unique_resource_ids())
        materials = len(self.state.materials)
        missing = self.state.domains_without_resources()
        parts = [f"{domains} 個異常項目、{resources} 個轉介資源、{materials} 份衛教單張"]
        if missing:
            parts.append(f"其中 {len(missing)} 個項目沒有選轉介資源，單上會請長者與醫護人員討論。")
        self.summary.setText("。".join(parts))

    @property
    def is_current(self) -> bool:
        return self._pdf_path is not None and self._pdf_version == self._version and self._pdf_path.exists()

    def mark_stale(self) -> None:
        self._version += 1
        self.result_banner.hide()
        if self._pdf_path is not None:
            self.preview_status.setText("內容有變更，列印或另存時會自動更新；也可以按「更新預覽」。")

    def has_unfinished_work(self) -> bool:
        return self.state.has_selections() and self._done_version != self._version

    def ensure_preview(self) -> None:
        if self.state.can_print() and not self.is_current and not self._generating:
            self._generate(None)

    def request_print(self) -> None:
        self._with_pdf(self._print)

    def _set_zoom(self, whole_page: bool) -> None:
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView if whole_page else QPdfView.ZoomMode.FitToWidth)
        self.fit_page.setChecked(whole_page)
        self.fit_width.setChecked(not whole_page)

    def _update_actions(self) -> None:
        ready = self.state.can_print() and not self._generating and not self.notes_over
        for widget in (self.print_button, self.save_button, self.open_button, self.refresh_button):
            widget.setEnabled(ready)
        tip = f"叮嚀超過 {NOTES_LIMIT} 字，請先縮短" if self.notes_over else ""
        for widget in (self.print_button, self.save_button, self.open_button):
            widget.setToolTip(tip or widget.property("default_tip") or "")

    def _update_notes_count(self) -> None:
        length = len(self.state.notes)
        self.notes_over = length > NOTES_LIMIT
        if self.notes_over:
            self.notes_count.setText(f"{length} / {NOTES_LIMIT} 字，超過 {length - NOTES_LIMIT} 字，請縮短後才能列印")
        else:
            self.notes_count.setText(f"{length} / {NOTES_LIMIT} 字")
        set_prop(self.notes_count, "role", "error" if self.notes_over else "caption")

    # ------------------------------------------------------------------ 輸入
    def _on_patient(self, text: str) -> None:
        self.state.patient_name = text.strip()
        self._update_card_hint()
        self.changed.emit()

    def _use_card_name(self) -> None:
        name = self._card_name()
        self.patient.setText(name)
        self._on_patient(name)

    def _on_assessor(self, text: str) -> None:
        self.state.assessor = text.strip()
        self.changed.emit()

    def _on_notes(self) -> None:
        self.state.notes = self.notes.toPlainText()
        self._update_notes_count()
        self._update_actions()
        self.changed.emit()

    # ------------------------------------------------------------------ 產生 PDF
    def _with_pdf(self, action: Callable[[Path], None]) -> None:
        if not self.state.can_print() or self.notes_over:
            self._explain_blocked()
            return
        path = self._pdf_path
        if path is not None and self.is_current and not self._generating:
            action(path)
        else:
            self._generate(action)

    def _generate(self, then: Callable[[Path], None] | None) -> None:
        if self._generating:
            self._pending = then or self._pending
            return
        try:
            job, files = self._build_job()
        except Exception as exc:   # noqa: BLE001 - 資料讀取失敗要顯示
            self._show_error("無法讀取轉介資料", str(exc))
            return
        self._generating = True
        self._pending = then
        version, session = self._version, self._session
        self.preview_stack.setCurrentIndex(1)
        self.preview_status.setText("正在產生 PDF…")
        self._update_actions()
        output = temp_output_dir() / f"ICOPE轉介單_{datetime.now():%Y%m%d-%H%M%S-%f}.pdf"
        run_in_background(self, lambda: build_referral_pdf(job, files, output),
                          lambda result: self._generated(result, version, session),
                          lambda exc: self._generate_failed(exc, session), self._generate_done)

    def _generated(self, result: BuildResult, version: int, session: int) -> None:
        # 清除重來時 release_document 已丟掉舊的待辦動作，所以這裡拿到的一定屬於目前這份轉介單
        pending, self._pending = self._pending, None
        if session != self._session or version != self._version:
            # 這份 PDF 已過期（清除重來或產生期間內容有變更）：不換上預覽、也不拿去輸出
            result.path.unlink(missing_ok=True)
            self._continue_with_current(pending, same_session=session == self._session)
            return
        self.document.close()
        self.document.load(str(result.path))
        self._pdf_path = result.path
        self._pdf_version = version
        self.preview_stack.setCurrentWidget(self.view)
        extra = result.total_pages - result.sheet_pages
        detail = f"，含衛教單張 {extra} 頁" if extra else ""
        self.preview_status.setText(f"共 {result.total_pages} 頁（轉介單 {result.sheet_pages} 頁{detail}）")
        if pending is not None:
            pending(result.path)

    def _continue_with_current(self, pending: Callable[[Path], None] | None, same_session: bool) -> None:
        """舊的產生工作結束後，用目前內容接著做：待辦的輸出重新檢查資格，看得到預覽時更新預覽。"""
        if pending is not None:
            self._with_pdf(pending)
        elif self.state.can_print() and self.isVisible():
            self._generate(None)
        elif same_session:
            self.preview_stack.setCurrentWidget(self.view if self._pdf_path else self.preview_empty)
            self.preview_status.setText("內容有變更，列印或另存時會自動更新；也可以按「更新預覽」。")

    def _explain_blocked(self) -> None:
        if self.notes_over:
            reason = f"給長者的叮嚀超過 {NOTES_LIMIT} 字，請縮短後再列印或另存。"
        else:
            reason = "這份轉介單沒有勾選任何異常項目或衛教單張，請先勾選再列印或另存。"
        self.result_banner.set_content("warning", "沒有輸出", reason)
        self.result_banner.show()

    def _generate_failed(self, exc: BaseException, session: int) -> None:
        pending, self._pending = self._pending, None
        if session != self._session:
            self._continue_with_current(pending, same_session=False)
            return
        if isinstance(exc, PdfBuildError):
            self._show_error("無法產生 PDF", str(exc))
        else:
            self._show_error("產生 PDF 時發生錯誤", f"{type(exc).__name__}：{exc}")

    def _generate_done(self) -> None:
        self._generating = False
        self._update_actions()

    def _show_error(self, title: str, text: str) -> None:
        self.error_banner.set_content("danger", title, text)
        self.preview_stack.setCurrentIndex(3)
        self.preview_status.setText("")

    # ------------------------------------------------------------------ 動作
    def _print(self, path: Path) -> None:
        version = self._pdf_version               # 送印期間內容可能又變：完成時只算這個版本
        try:
            if print_pdf(self, path):
                self._done("print", "已送出列印", "印表機會依序印出轉介單與衛教單張。", version)
        except PrintError as exc:
            self.result_banner.set_content(
                "danger", "列印失敗，這份轉介單還沒印出", f"{exc}。請確認印表機後重試，或先另存 PDF。",
                [button("重試列印", "printer", "primary", size="sm", on_click=lambda: self._with_pdf(self._print)),
                 button("另存 PDF…", "download", "secondary", size="sm",
                        on_click=lambda: self._with_pdf(self._save_as))])
            self.result_banner.show()

    def _save_as(self, path: Path) -> None:
        default_name = f"轉介衛教單_{self.state.patient_name or '長者'}_{datetime.now():%Y%m%d}.pdf"
        version = self._pdf_version
        target, _ = QFileDialog.getSaveFileName(self, "另存轉介衛教單", str(Path.home() / "Documents" / default_name),
                                                "PDF 檔案 (*.pdf)")
        if not target:
            return
        try:
            shutil.copyfile(path, target)
        except OSError as exc:
            self.result_banner.set_content("danger", "無法儲存檔案", exc.strerror or str(exc))
            self.result_banner.show()
            return
        self._done("save", "已儲存 PDF", target, version)

    def _open_external(self, path: Path) -> None:
        version = self._pdf_version
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.result_banner.set_content("warning", "找不到可以開啟 PDF 的程式",
                                           "請改用「另存 PDF…」存到桌面後再開啟。")
            self.result_banner.show()
            return
        self._done("open", "已用 PDF 閱讀器開啟", "可以在閱讀器裡列印或另存。", version)

    def _done(self, kind: str, title: str, text: str, version: int | None = None) -> None:
        self._done_version = self._version if version is None else version
        self.finished_job.emit(kind)
        next_button = button("開始下一位長者", "rotate-ccw", "secondary", size="sm",
                             tooltip="清除這份轉介單的勾選與輸入（評估人員會保留）",
                             on_click=self.reset_requested.emit)
        self.result_banner.set_content("success", title, text, [next_button])
        self.result_banner.show()

    def release_document(self) -> None:
        self.document.close()
        self._pdf_path = None
        self._pdf_version = -1
        self._session += 1
        self._pending = None
        self._version += 1
        self._done_version = self._version
        self.preview_stack.setCurrentWidget(self.preview_empty)
        self.preview_status.setText("")
        self.result_banner.hide()
