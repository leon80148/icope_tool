"""篩檢查詢：輸入身分證或讀健保卡 → 查國健署今年是否已做 ICOPE。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from icope_tool.audit import pid_tag
from icope_tool.idcheck import check_person_id, mask_person_id
from icope_tool.services.hpdcs.client import SITE_URL, CaptchaManualRequired, IcopeResult, QueryCancelled
from icope_tool.services.hpdcs.plans import plans_summary
from icope_tool.ui.context import HISTORY_LIMIT, AppContext, HistoryEntry
from icope_tool.ui.dialogs.captcha import CaptchaDialog
from icope_tool.ui.messages import CARD_HINTS, describe_age, describe_query_error
from icope_tool.ui.setup_checklist import SetupChecklist
from icope_tool.ui.theme import C, icon, mono_font, pixmap
from icope_tool.ui.widgets import (
    Banner, Card, EmptyState, PageHeader, ScrollPage, Spinner, button, clear_layout, hbox, label, link_button,
    set_invalid, set_prop, status_chip,
)
from icope_tool.ui.workers import run_in_background

try:
    from icope_tool.services.card import nhi_card
    CARD_IMPORT_ERROR = ""
except Exception as exc:   # pragma: no cover - pyscard 載入失敗時停用讀卡
    nhi_card = None
    CARD_IMPORT_ERROR = str(exc)

CARD_WAIT_SECONDS = 15

VERDICT_VIEW = {
    "can_assess": ("success", "circle-check", "今年還沒做，可以進行評估", "國健署系統查無今年的評估紀錄。"),
    "done": ("info", "clipboard-check", "今年已經做過評估", ""),
    "blocked": ("warning", "ban", "今年無法進行評估", ""),
}
PLAN_STATUS_VIEW = {
    "can_assess": ("success", "可以評估"),
    "done": ("info", "已登錄"),
    "done_other": ("info", "已在其他計畫登錄"),
    "blocked": ("warning", "無法評估"),
    "unavailable": ("neutral", "找不到查詢頁"),
}
HISTORY_CHIP = {"can_assess": ("success", "可以評估"), "done": ("info", "今年已做"), "blocked": ("warning", "無法評估")}


def done_verdict_text(result: IcopeResult) -> str:
    """「今年已做過」的說明。done_other 只代表「在別的計畫登錄過」，國健署沒說是哪個計畫，不能寫成已登錄於本計畫。"""
    registered = [p.label for p in result.plans if p.status == "done"]
    if registered:
        return "已登錄於：" + "、".join(registered) + "。不需要重複評估。"
    return "國健署顯示這位長者今年已在其他計畫登錄過評估，不需要重複評估。"


def unavailable_note(result: IcopeResult) -> str:
    """有計畫找不到查詢頁時接在判定說明後面的提醒（今年已做過的不必提：已做就是已做）。"""
    missing = result.unavailable_plans
    if not missing or result.verdict == "done":
        return ""
    return ("　" + "、".join(p.label for p in missing)
            + "找不到查詢頁（可能尚未開放），這次沒有查到；按「重新查詢」會再試一次。")


def open_hpdcs_site() -> None:
    QDesktopServices.openUrl(QUrl(SITE_URL))


def describe_when(moment: datetime) -> str:
    today = datetime.now().date()
    if moment.date() == today:
        return f"今天 {moment:%H:%M}"
    return f"{moment.year - 1911}/{moment.month}/{moment.day} {moment:%H:%M}"


@dataclass
class QueryRequest:
    """查詢開始時就固定下來的內容：回傳時只用這份快照，不讀畫面上可能已改變的狀態。"""
    rid: int
    pid: str
    name: str
    plans: tuple[str, ...]
    force: bool
    cancel: threading.Event
    birth_roc: str = ""          # 和 name 一樣跟著讀卡當下的身分證走


class VerdictPanel(QFrame):
    """大字的查詢結論。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon = QLabel()
        self._icon.setFixedSize(52, 52)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._text = QLabel()
        self._text.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(self._title)
        texts.addWidget(self._text)
        layout.addLayout(texts, 1)

    def set_verdict(self, kind: str, icon_name: str, title: str, text: str) -> None:
        fg, bg, border = {
            "success": (C["success"], C["success_bg"], C["success_border"]),
            "info": (C["info"], C["info_bg"], C["info_border"]),
            "warning": (C["warning"], C["warning_bg"], C["warning_border"]),
        }[kind]
        self.setStyleSheet(f"VerdictPanel {{ background: {bg}; border: 2px solid {border}; border-radius: 14px; }}"
                           "VerdictPanel QLabel { background: transparent; border: none; }")
        self._icon.setPixmap(pixmap(icon_name, fg, 40, stroke=2.2))
        self._title.setText(title)
        self._title.setStyleSheet(f"color: {fg}; font-size: 17pt; font-weight: 800;")
        self._text.setText(text)
        self._text.setStyleSheet(f"color: {C['text']}; font-size: 11pt;")
        self._text.setVisible(bool(text))
        self.setAccessibleName(f"查詢結果：{title}。{text}")


class HistoryRow(QPushButton):
    def __init__(self, entry: HistoryEntry, current: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("HistoryRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(1)
        ident = label(mask_person_id(entry.person_id))
        ident.setFont(mono_font(10.5))
        texts.addWidget(ident)
        texts.addWidget(label(f"{describe_when(entry.when)}　{entry.name}".rstrip(), "caption"))
        layout.addLayout(texts, 1)
        if entry.result is None:
            kind, text, hint = "danger", "查詢失敗", "查詢失敗，點一下重新查詢"
        elif not current:
            kind, text, hint = "neutral", "需重新查詢", "不是今天或目前計畫的結果，點一下重新查詢"
        else:
            kind, text = HISTORY_CHIP[entry.result.verdict]
            hint = "點一下再看一次結果"
        layout.addWidget(status_chip(kind, text))
        self.setToolTip(hint)
        self.setAccessibleName(f"{mask_person_id(entry.person_id)} {entry.name} {text}，{hint}")


class QueryPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._request: QueryRequest | None = None
        self._request_seq = 0
        self._reading_card = False
        self._card_cancel = threading.Event()
        self._started_at = 0.0
        self._pending_name = ""           # 讀卡帶入、尚未查詢的姓名（只對應 _pending_pid）
        self._pending_birth = ""          # 同一張卡的出生日期：換了身分證就一起丟掉，不能貼到別人身上
        self._pending_pid = ""
        self._shown: HistoryEntry | None = None
        self._card_seq = 0

        page = ScrollPage(max_width=1240)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(page)
        body = page.body
        body.addWidget(PageHeader("篩檢查詢", "查詢長者今年是否已做過國健署長者功能評估（ICOPE），不必再登入國健署網站。"))

        self.checklist = SetupChecklist(ctx)
        self.checklist.dismissed.connect(self.refresh_status)
        body.addWidget(self.checklist)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        body.addLayout(columns, 1)
        left = QVBoxLayout()
        left.setSpacing(16)
        columns.addLayout(left, 3)

        # ---- 輸入 ----
        input_card = Card("輸入身分證字號", "也可以直接插入健保卡，按「讀健保卡」自動帶入並查詢。", "id-card")
        left.addWidget(input_card)
        self.id_input = QLineEdit()
        self.id_input.setObjectName("IdInput")
        self.id_input.setPlaceholderText("例如 A123456789")
        self.id_input.setMaxLength(14)
        self.id_input.setAccessibleName("身分證字號")
        self.id_input.setClearButtonEnabled(True)
        self.id_input.textChanged.connect(self._on_id_changed)
        self.id_input.returnPressed.connect(self.start_query)
        self.card_button = button("讀健保卡 F2", "credit-card", "secondary", "lg", tooltip="插入健保卡後按這裡",
                                  on_click=self.toggle_card_read)
        self.query_button = button("查詢", "search", "primary", "lg", tooltip="查詢國健署系統（Enter）",
                                   on_click=self.start_query)
        self.query_button.setMinimumWidth(116)
        input_row = hbox(self.id_input, self.card_button, self.query_button, spacing=10)
        input_row.setStretch(0, 1)
        input_card.add(input_row)

        self.feedback = label("", "caption")
        self.feedback.setMinimumHeight(22)
        input_card.add(self.feedback)

        self.card_status = QWidget()
        self.card_status_text = label("", "subtle")
        self.card_status.setLayout(hbox(Spinner(20), self.card_status_text, None, spacing=10))
        self.card_status.hide()
        input_card.add(self.card_status)

        self.card_error = Banner("warning")
        self.card_error.hide()
        input_card.add(self.card_error)

        if nhi_card is None:
            self.card_button.setEnabled(False)
            input_card.add(Banner(
                "warning", "這台電腦無法使用讀卡機",
                f"請直接輸入身分證字號查詢。若需要讀卡，請把下列訊息提供給管理者：{CARD_IMPORT_ERROR}"))

        self.meta = label("", "caption")
        change = link_button("變更", lambda: ctx.navigate.emit("settings", "hpdcs"))
        self.meta_link = change
        change.setAccessibleName("變更國健署帳號或查詢計畫")
        input_card.add(hbox(self.meta, change, None, spacing=6))

        # ---- 結果 ----
        self.result_card = Card("查詢結果", icon_name="clipboard-list")
        left.addWidget(self.result_card)
        self.result_stack = QStackedWidget()
        self.result_card.add(self.result_stack)

        self.idle_view = EmptyState(
            "search", "還沒有查詢", "輸入身分證字號後按 Enter，或插入健保卡按「讀健保卡」。\n"
                                "同一天重複查同一位長者會直接顯示暫存結果，不必再等。")
        self.result_stack.addWidget(self.idle_view)

        loading = QWidget()
        loading_layout = QVBoxLayout(loading)
        loading_layout.setContentsMargins(24, 28, 24, 28)
        loading_layout.setSpacing(10)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(Spinner(40), 0, Qt.AlignmentFlag.AlignHCenter)
        self.loading_title = label("正在查詢國健署系統…", "section")
        self.loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self.loading_title)
        self.loading_text = label("", "subtle", wrap=True)
        self.loading_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self.loading_text)
        self.cancel_button = button("取消查詢（Esc）", "x", "secondary", on_click=self.cancel_query)
        loading_layout.addLayout(hbox(None, self.cancel_button,
                                      button("改用國健署網站", "external-link", "ghost", on_click=open_hpdcs_site),
                                      None, spacing=8))
        self.result_stack.addWidget(loading)

        self.result_view = QWidget()
        result_layout = QVBoxLayout(self.result_view)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(12)
        ident_row = QHBoxLayout()
        ident_row.setSpacing(10)
        self.result_ident = label("")
        self.result_ident.setFont(mono_font(13))
        self.result_ident.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_name = label("", "section")
        self.result_meta = label("", "caption")
        ident_row.addWidget(self.result_ident)
        ident_row.addWidget(self.result_name)
        ident_row.addStretch(1)
        ident_row.addWidget(self.result_meta)
        result_layout.addLayout(ident_row)
        self.age_note = label("", "caption", wrap=True)      # 年齡核對：只是提醒，判定與按鈕都不因它改變
        result_layout.addWidget(self.age_note)
        self.mismatch = Banner("neutral", icon_name="info")
        self.mismatch.hide()
        result_layout.addWidget(self.mismatch)
        self.verdict = VerdictPanel()
        result_layout.addWidget(self.verdict)
        self.verdict_actions = QHBoxLayout()
        self.verdict_actions.setSpacing(8)
        result_layout.addLayout(self.verdict_actions)
        result_layout.addWidget(label("各計畫查詢結果", "section"))
        self.plan_grid = QGridLayout()
        self.plan_grid.setHorizontalSpacing(14)
        self.plan_grid.setVerticalSpacing(8)
        plans_frame = QFrame()
        plans_frame.setObjectName("Panel")
        plans_frame.setLayout(self.plan_grid)
        self.plan_grid.setContentsMargins(14, 12, 14, 12)
        result_layout.addWidget(plans_frame)
        result_layout.addStretch(1)
        self.result_stack.addWidget(self.result_view)

        self.error_view = QWidget()
        error_layout = QVBoxLayout(self.error_view)
        error_layout.setContentsMargins(0, 0, 0, 0)
        self.error_banner = Banner("danger")
        error_layout.addWidget(self.error_banner)
        error_layout.addStretch(1)
        self.result_stack.addWidget(self.error_view)
        left.addStretch(1)

        # ---- 查詢紀錄 ----
        self.history_card = Card("本次查詢紀錄", f"關閉程式即清除，不會存到硬碟。\n最多保留最近 {HISTORY_LIMIT} 筆。",
                                 "clock")
        self.history_card.setMinimumWidth(300)
        self.history_card.setMaximumWidth(400)
        self.history_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.clear_history_button = button("清除", "trash", "ghost", size="sm", tooltip="清除本次查詢紀錄",
                                           on_click=ctx.clear_history)
        assert self.history_card.header_actions is not None
        self.history_card.header_actions.addWidget(self.clear_history_button, 0, Qt.AlignmentFlag.AlignTop)
        self.history_list = QVBoxLayout()
        self.history_list.setSpacing(8)
        self.history_card.add(self.history_list)
        self.history_empty = label("查詢過的長者會列在這裡，點一下可以再看一次結果。", "caption", wrap=True)
        self.history_card.add(self.history_empty)
        right = QVBoxLayout()
        right.addWidget(self.history_card, 0, Qt.AlignmentFlag.AlignTop)   # 靠上：與左欄對齊，高度依內容計算
        right.addStretch(1)
        columns.addLayout(right, 2)

        # ---- 快捷鍵與事件 ----
        QShortcut(QKeySequence(Qt.Key.Key_F2), self, activated=self.toggle_card_read)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._escape)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._history_day = datetime.now().date()
        self._history_plans = ctx.active_plans()
        self._day_timer = QTimer(self)             # 跨日或改了計畫：紀錄與顯示中的結果要標成需重新查詢
        self._day_timer.setInterval(60_000)
        self._day_timer.timeout.connect(self._revalidate)
        self._day_timer.start()
        ctx.settings_changed.connect(self._revalidate)
        for signal in (ctx.hpdcs_changed, ctx.settings_changed, ctx.data_dir_changed, ctx.resources_changed,
                       ctx.materials_changed):
            signal.connect(self.refresh_status)
        ctx.history_changed.connect(self.refresh_history)

        self.result_stack.setCurrentWidget(self.idle_view)
        self._on_id_changed("")
        self.refresh_status()
        self.refresh_history()

    # ------------------------------------------------------------------ 狀態
    @property
    def querying(self) -> bool:
        return self._request is not None

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_status()
        self.refresh_history()
        QTimer.singleShot(0, self.id_input.setFocus)

    def refresh_status(self) -> None:
        self.checklist.setVisible(self.checklist.refresh())
        configured = self.ctx.credentials.is_configured()
        plans = self.ctx.active_plans()
        if configured:
            self.meta.setText(f"國健署帳號 {self.ctx.credentials.masked_account()}　·　查詢{plans_summary(plans)}")
            set_prop(self.meta, "role", "caption")
            self.meta_link.setText("變更")
        else:
            self.meta.setText("尚未設定國健署帳號，暫時無法查詢")
            set_prop(self.meta, "role", "warning")
            self.meta_link.setText("設定帳號")
        self._update_buttons()

    def _update_buttons(self) -> None:
        check = check_person_id(self.id_input.text())
        busy = self.querying
        self.query_button.setEnabled(check.ok and not busy)
        self.id_input.setReadOnly(busy)
        if nhi_card is not None:
            self.card_button.setEnabled(not busy)

    def _on_id_changed(self, text: str) -> None:
        check = check_person_id(text)
        if self.querying:
            self.feedback.setText("正在查詢，請稍候…")
            set_prop(self.feedback, "role", "caption")
        elif check.state == "empty":
            self.feedback.setText("身分證字號共 10 碼，英文字母大小寫都可以。")
            set_prop(self.feedback, "role", "caption")
        elif check.state == "typing":
            self.feedback.setText(check.message)
            set_prop(self.feedback, "role", "caption")
        elif check.state in ("checksum", "format"):
            self.feedback.setText(check.message)
            set_prop(self.feedback, "role", "error")
        else:
            self.feedback.setText("格式正確，按 Enter 開始查詢")
            set_prop(self.feedback, "role", "success")
        set_invalid(self.id_input, check.state in ("checksum", "format"))
        if check.value != self._pending_pid:
            self._pending_name = self._pending_birth = ""
        self._update_mismatch()
        self._update_buttons()

    def _update_mismatch(self) -> None:
        entry = self._shown
        typed = check_person_id(self.id_input.text()).value
        if entry is None or self.querying or not typed or typed == entry.person_id:
            self.mismatch.hide()
            return
        self.mismatch.set_content("neutral", "輸入框的號碼還沒查詢",
                                  f"下方仍是 {mask_person_id(entry.person_id)} {entry.name} 的結果。按 Enter 查詢新的號碼。",
                                  icon_name="info")
        self.mismatch.show()

    def _escape(self) -> None:
        if self.querying:
            self.cancel_query()
        elif self._reading_card:
            self.toggle_card_read()

    # ------------------------------------------------------------------ 讀卡
    def toggle_card_read(self) -> None:
        if nhi_card is None or self.querying:
            return
        if self._reading_card:
            self._card_cancel.set()
            self.card_status_text.setText("正在取消…")
            return
        self._reading_card = True
        self._card_cancel.clear()
        self._card_seq += 1
        seq = self._card_seq
        self.card_error.hide()
        self.card_status_text.setText(f"請把健保卡插入讀卡機（等待 {CARD_WAIT_SECONDS} 秒；按 Esc 或再按一次可取消）")
        self.card_status.show()
        self.card_button.setText("取消讀卡")
        self.card_button.setIcon(icon("x", C["primary"]))
        hint = self.ctx.local.reader_hint
        cancel = self._card_cancel.is_set
        run_in_background(self, lambda: nhi_card.read_basic(CARD_WAIT_SECONDS, hint, cancel=cancel),
                          lambda fields: self._on_card_read(seq, fields), lambda exc: self._on_card_error(seq, exc),
                          lambda: self._card_finished(seq))

    def _card_finished(self, seq: int | None = None) -> None:
        if seq is not None and seq != self._card_seq:
            return
        self._reading_card = False
        self.card_status.hide()
        self.card_button.setText("讀健保卡 F2")
        self.card_button.setIcon(icon("credit-card", C["primary"]))
        self._update_buttons()

    def _on_card_read(self, seq: int, fields: dict) -> None:
        if seq != self._card_seq or self._card_cancel.is_set():
            return          # 已取消或已改成手動查詢：晚到的讀卡結果不覆寫輸入、長者與紀錄
        pid = fields.get("pid", "")
        name = fields.get("name", "")
        self.ctx.set_patient(pid, name, "card")
        self.ctx.audit.record("card_read", pid=pid_tag(pid))
        self.id_input.setText(pid)
        self._pending_pid, self._pending_name, self._pending_birth = pid, name, fields.get("birth_roc", "")
        self.start_query()

    def _on_card_error(self, seq: int, exc: BaseException) -> None:
        code = getattr(exc, "code", "READ_ERROR")
        if code == "CANCELLED" or seq != self._card_seq or self._card_cancel.is_set():
            return
        message = getattr(exc, "message", str(exc))
        self.card_error.set_content("warning", message, CARD_HINTS.get(code, ""))
        self.card_error.show()

    # ------------------------------------------------------------------ 查詢
    def start_query(self, force: bool = False, captcha_token: str | None = None, captcha_text: str | None = None,
                    name: str | None = None, birth: str | None = None) -> None:
        if self.querying:
            return
        check = check_person_id(self.id_input.text())
        if check.ok and self._reading_card:
            self._card_cancel.set()          # 使用者改成手動輸入查詢：停止等待插卡
        if not check.ok:
            self._on_id_changed(self.id_input.text())
            self.id_input.setFocus()
            return
        pid = check.value
        if name is None:
            name = self._pending_name if pid == self._pending_pid else ""
        if birth is None:
            birth = self._pending_birth if pid == self._pending_pid else ""
        if self.id_input.text() != pid:
            self.id_input.blockSignals(True)
            self.id_input.setText(pid)
            self.id_input.blockSignals(False)

        self._request_seq += 1
        request = QueryRequest(self._request_seq, pid, name, self.ctx.active_plans(), force, threading.Event(), birth)
        self._request = request
        self._started_at = time.monotonic()
        self.card_error.hide()
        self._on_id_changed(self.id_input.text())
        first_login = not self.ctx.hpdcs.status()["logged_in"]
        self.loading_title.setText("正在查詢國健署系統…")
        self.loading_text.setText(("第一次查詢需要先登入，通常 5–15 秒。" if first_login else "通常幾秒內完成。")
                                  + "國健署網站很慢時，可以取消或改用網站查詢。")
        self.result_stack.setCurrentIndex(1)
        self._elapsed_timer.start()
        client = self.ctx.hpdcs
        run_in_background(
            self,
            lambda: client.query_icope(pid, captcha_token=captcha_token, captcha_text=captcha_text, force=force,
                                       cancel=request.cancel),
            lambda result: self._on_result(request, result),
            lambda exc: self._on_query_error(request, exc),
            lambda: self._query_finished(request),
        )

    def cancel_query(self) -> None:
        request = self._request
        if request is None:
            return
        request.cancel.set()
        self._query_finished(request)
        self.ctx.audit.record("icope_query", pid=pid_tag(request.pid), outcome="cancelled")
        self._show_error("warning", "已取消查詢", "可以重新查詢，或先到國健署網站人工查詢。", "retry")

    def _tick_elapsed(self) -> None:
        seconds = int(time.monotonic() - self._started_at)
        if seconds >= 3:
            self.loading_title.setText(f"正在查詢國健署系統…（已等 {seconds} 秒）")

    def _query_finished(self, request: QueryRequest) -> None:
        if self._request is not request:
            return
        self._request = None
        self._elapsed_timer.stop()
        self._on_id_changed(self.id_input.text())
        self.ctx.hpdcs_changed.emit()

    def _is_stale(self, request: QueryRequest) -> bool:
        return request.cancel.is_set() or request.rid != self._request_seq

    def _on_result(self, request: QueryRequest, result: IcopeResult) -> None:
        if self._is_stale(request):
            return
        self.ctx.audit.record("icope_query", pid=pid_tag(request.pid), verdict=result.verdict, cached=result.cached)
        entry = HistoryEntry(datetime.now(), request.pid, request.name, result, plans=request.plans,
                             birth_roc=request.birth_roc)
        self.ctx.add_history(entry)
        self.show_entry(entry)

    def show_entry(self, entry: HistoryEntry) -> None:
        if entry.result is None:
            return
        if not self.ctx.entry_is_current(entry):
            self._requery(entry)            # 跨日或計畫已變更：舊結果不能當成今年的答案
            return
        result = entry.result
        self._shown = entry
        self.result_ident.setText(mask_person_id(entry.person_id))
        self.result_name.setText(entry.name)
        checked = result.checked_at if result.cached else entry.when
        self.result_meta.setText(f"{describe_when(checked)} 查詢" + ("（今天稍早的暫存結果）" if result.cached else "")
                                 + f"　·　{plans_summary(entry.plans).strip()}")
        role, text = describe_age(entry.birth_roc, datetime.now().year)
        self.age_note.setText(text)
        set_prop(self.age_note, "role", role)
        kind, icon_name, title, text = VERDICT_VIEW[result.verdict]
        if result.verdict == "done":
            text = done_verdict_text(result)
        elif result.verdict == "blocked":
            reasons = sorted({p.message for p in result.plans if p.answered and p.message})
            text = "國健署系統說明：" + "；".join(reasons) if reasons else "國健署系統顯示今年無法評估。"
        self.verdict.set_verdict(kind, icon_name, title, text + unavailable_note(result))

        clear_layout(self.verdict_actions)
        self.verdict_actions.addWidget(button(
            "製作轉介衛教單", "file-text", "primary" if result.verdict == "can_assess" else "secondary",
            tooltip="帶著這位長者到轉介衛教單", on_click=lambda: self._go_referral(entry)))
        self.verdict_actions.addWidget(button("開啟國健署網站", "external-link", "ghost",
                                              tooltip="在瀏覽器開啟 hpdcs.hpa.gov.tw", on_click=open_hpdcs_site))
        self.verdict_actions.addStretch(1)
        self.verdict_actions.addWidget(button(
            "重新查詢", "refresh-cw", "ghost", tooltip="不用暫存結果，重新向國健署查詢這位長者",
            on_click=lambda: self._requery(entry)))

        clear_layout(self.plan_grid)
        for row, plan in enumerate(result.plans):
            chip_kind, chip_text = PLAN_STATUS_VIEW.get(plan.status, ("neutral", plan.status))
            self.plan_grid.addWidget(label(plan.label, "fieldLabel"), row, 0)
            self.plan_grid.addWidget(status_chip(chip_kind, chip_text), row, 1)
            detail = label("" if plan.status == "can_assess" else plan.message, "subtle", wrap=True, selectable=True)
            self.plan_grid.addWidget(detail, row, 2)
        self.plan_grid.setColumnStretch(2, 1)
        self.result_stack.setCurrentWidget(self.result_view)
        self._update_mismatch()

    def _open_history(self, entry: HistoryEntry) -> None:
        if entry.result is not None and self.ctx.entry_is_current(entry):
            self.show_entry(entry)
        else:
            self._requery(entry)

    def _revalidate(self) -> None:
        """跨日或查詢計畫變更後：更新紀錄標示，畫面上的舊結果改成提示重新查詢。其他時候不動畫面。"""
        today, plans = datetime.now().date(), self.ctx.active_plans()
        if (today, plans) == (self._history_day, self._history_plans):
            return
        self._history_day, self._history_plans = today, plans
        self.refresh_history()
        shown = self._shown
        if shown is not None and not self.querying and not self.ctx.entry_is_current(shown):
            # 不動輸入框：使用者可能已經在輸入下一位；重新查詢按鈕只查這筆結果的長者
            masked = mask_person_id(shown.person_id)
            self._show_error("warning", "這筆結果已過期，請重新查詢",
                             f"{masked} {shown.name} 的結果不是今天或目前查詢計畫的資料。", "retry",
                             retry=lambda: self._requery(shown), retry_label=f"重新查詢 {masked}")

    def _requery(self, entry: HistoryEntry) -> None:
        if self.querying:
            return
        self.id_input.setText(entry.person_id)
        self.start_query(force=True, name=entry.name, birth=entry.birth_roc)

    def _on_query_error(self, request: QueryRequest, exc: BaseException) -> None:
        if self._is_stale(request) or isinstance(exc, QueryCancelled):
            return
        if isinstance(exc, CaptchaManualRequired):
            self.ctx.audit.record("icope_query", pid=pid_tag(request.pid), outcome="need_captcha")
            dialog = CaptchaDialog(self, exc, self.ctx.hpdcs.refresh_captcha)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                token, code = dialog.token, dialog.code
                QTimer.singleShot(0, lambda: self._resume_with_captcha(request, token, code))
                return
            self._show_error("warning", "已取消查詢", "需要輸入驗證碼才能登入國健署系統。", "retry")
            return
        view = describe_query_error(exc, self.ctx.credentials.is_configured())
        self.ctx.audit.record("icope_query", pid=pid_tag(request.pid), outcome=type(exc).__name__)
        kept = next((h for h in self.ctx.history if h.person_id == request.pid and h.result is not None
                     and self.ctx.entry_is_current(h)), None)
        text = view.text
        if kept is None:
            self.ctx.add_history(HistoryEntry(datetime.now(), request.pid, request.name, None, view.title,
                                              plans=request.plans, birth_roc=request.birth_roc))
        else:
            # 今天已有成功的結果：保留在紀錄裡，失敗只說明在這裡
            text += f"\n今天 {kept.when:%H:%M} 的查詢結果仍保留在右側紀錄，點一下可以再看。"
        self._show_error(view.kind, view.title, text, view.action)

    def _resume_with_captcha(self, request: QueryRequest, token: str, code: str) -> None:
        self.id_input.setText(request.pid)
        self.start_query(request.force, token, code, name=request.name, birth=request.birth_roc)

    def _show_error(self, kind: str, title: str, text: str, action: str, retry=None, retry_label: str = "重試") -> None:
        actions: list[QWidget] = []
        if action == "retry":
            actions.append(button(retry_label, "refresh-cw", "primary",
                                  on_click=retry or (lambda: self.start_query(force=True))))
            actions.append(button("改用國健署網站", "external-link", "ghost", on_click=open_hpdcs_site))
        elif action == "settings_hpdcs":
            actions.append(button("前往設定", "settings", "primary",
                                  on_click=lambda: self.ctx.navigate.emit("settings", "hpdcs")))
        elif action == "settings_local":
            actions.append(button("檢查資料存放位置", "hard-drive", "primary",
                                  on_click=lambda: self.ctx.navigate.emit("settings", "local")))
        elif action == "open_site":
            actions.append(button("開啟國健署網站", "external-link", "primary", on_click=open_hpdcs_site))
        elif action == "open_site_or_settings":
            actions.append(button("開啟國健署網站", "external-link", "primary", on_click=open_hpdcs_site))
            actions.append(button("前往設定", "settings", "ghost",
                                  on_click=lambda: self.ctx.navigate.emit("settings", "hpdcs")))
        self.error_banner.set_content(kind, title, text, actions)
        self._shown = None
        self.mismatch.hide()
        self.result_stack.setCurrentWidget(self.error_view)

    def _go_referral(self, entry: HistoryEntry) -> None:
        self.ctx.set_patient(entry.person_id, entry.name, "query")
        self.ctx.navigate.emit("referral", "")

    # ------------------------------------------------------------------ 紀錄
    def refresh_history(self) -> None:
        focus = QApplication.focusWidget()
        focused_pid = focus.entry.person_id if isinstance(focus, HistoryRow) else None
        clear_layout(self.history_list)
        for entry in self.ctx.history:
            row = HistoryRow(entry, self.ctx.entry_is_current(entry))
            row.clicked.connect(lambda _=False, e=entry: self._open_history(e))   # 點的當下才判斷是否仍有效
            self.history_list.addWidget(row)
            if entry.person_id == focused_pid:
                row.show()                                           # 新加入的列要先顯示才能取得焦點
                row.setFocus(Qt.FocusReason.OtherFocusReason)       # 鍵盤正停在這一列：重建後留在原處
        has = bool(self.ctx.history)
        self.history_empty.setVisible(not has)
        self.clear_history_button.setVisible(has)
