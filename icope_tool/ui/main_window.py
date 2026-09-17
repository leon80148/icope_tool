from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from icope_tool import APP_DISPLAY_NAME, __version__
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.help_dialog import HelpDialog
from icope_tool.ui.query_page import QueryPage
from icope_tool.ui.referral.page import ReferralPage
from icope_tool.ui.settings.page import SettingsPage
from icope_tool.ui.theme import C, app_icon, icon, pixmap

NAV = [
    ("query", "篩檢查詢", "search", "Ctrl+1"),
    ("referral", "轉介衛教單", "file-text", "Ctrl+2"),
    ("settings", "設定", "settings", "Ctrl+3"),
]
EXTERNAL_CHECK_MS = 15_000


class _StatusItem(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        self.icon = QLabel()
        self.text = QLabel()
        layout.addWidget(self.icon)
        layout.addWidget(self.text)

    def set(self, icon_name: str, text: str, color: str = C["subtle"], tooltip: str = "") -> None:
        self.icon.setPixmap(pixmap(icon_name, color, 16))
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {color};")
        self.setToolTip(tooltip)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1100, 700)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 20, 14, 16)
        side.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(pixmap("heart-pulse", C["sidebar_accent"], 30))
        logo.setFixedSize(40, 40)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(f"background: {C['sidebar_hover']}; border-radius: 12px;")
        brand.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        brand_name, _, brand_tagline = APP_DISPLAY_NAME.partition(" ")     # 「ICOPE」＋「小幫手」分兩行
        title = QLabel(brand_name)
        title.setObjectName("SidebarTitle")
        subtitle = QLabel(brand_tagline)
        subtitle.setObjectName("SidebarSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        brand.addLayout(titles, 1)
        side.addLayout(brand)
        side.addSpacing(22)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text, icon_name, shortcut in NAV:
            nav = QPushButton(f"  {text}")
            nav.setObjectName("NavButton")
            nav.setCheckable(True)
            nav.setIcon(icon(icon_name, C["sidebar_text"], 20))
            nav.setIconSize(QSize(20, 20))
            nav.setCursor(Qt.CursorShape.PointingHandCursor)
            nav.setToolTip(f"{text}（{shortcut}）")
            nav.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav_group.addButton(nav)
            self.nav_buttons[key] = nav
            side.addWidget(nav)
            QShortcut(QKeySequence(shortcut), self, activated=lambda k=key: self.go(k))
        side.addStretch(1)
        help_button = QPushButton("  使用說明")
        help_button.setObjectName("NavButton")
        help_button.setIcon(icon("circle-question-mark", C["sidebar_text"], 20))
        help_button.setIconSize(QSize(20, 20))
        help_button.setToolTip("使用說明（F1）")
        help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        help_button.clicked.connect(self.show_help)
        side.addWidget(help_button)
        QShortcut(QKeySequence(Qt.Key.Key_F1), self, activated=self.show_help)
        footer = QLabel(f"版本 {__version__}")
        footer.setObjectName("SidebarFooter")
        side.addWidget(footer)
        root.addWidget(sidebar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.pages = {
            "query": QueryPage(ctx),
            "referral": ReferralPage(ctx),
            "settings": SettingsPage(ctx),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.folder_status = _StatusItem()
        self.account_status = _StatusItem()
        bar.addWidget(self.folder_status)
        bar.addWidget(self.account_status)

        ctx.navigate.connect(self.go)
        ctx.settings_changed.connect(self.refresh_status)
        ctx.hpdcs_changed.connect(self.refresh_status)
        ctx.data_dir_changed.connect(self.refresh_status)

        self._external_timer = QTimer(self)
        self._external_timer.setInterval(EXTERNAL_CHECK_MS)
        self._external_timer.timeout.connect(self._check_external)
        self._external_timer.start()

        self.refresh_status()
        self.go("query")

    def go(self, key: str, sub: str = "") -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)
        if key == "settings" and sub:
            self.pages["settings"].select(sub)
        self.ctx.check_external_changes()

    def show_help(self) -> None:
        HelpDialog(self).exec()

    def refresh_status(self) -> None:
        try:
            clinic = self.ctx.store.load_settings().clinic.name
        except StoreError:
            clinic = ""
        self.setWindowTitle(f"{APP_DISPLAY_NAME}　{clinic}".strip())
        root = str(self.ctx.store.root)
        if self.ctx.data_dir_available():
            self.folder_status.set("hard-drive", f"資料存放位置：{root}", tooltip="診所資訊、轉介資源與衛教單張存放的資料夾")
        else:
            self.folder_status.set("wifi-off", f"無法存取資料存放位置：{root}", C["danger"],
                                   "請確認網路磁碟或資料夾是否可用，或到「設定 › 本機設定」變更")
        if self.ctx.credentials.is_configured():
            status = self.ctx.hpdcs.status()
            if status["auto_login_disabled"]:
                self.account_status.set("triangle-alert", "國健署帳號：登入失敗，已暫停", C["danger"])
            else:
                state = "已登入" if status["logged_in"] else "查詢時自動登入"
                self.account_status.set("key-round", f"國健署帳號 {status['account_masked']}（{state}）")
        else:
            self.account_status.set("key-round", "國健署帳號：尚未設定", C["warning"])

    def _check_external(self) -> None:
        changed = self.ctx.check_external_changes()
        if changed or not self.ctx.data_dir_available():
            self.refresh_status()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.pages["referral"].has_unfinished_work():
            answer = QMessageBox.question(
                self, "轉介單還沒印出", "這份轉介衛教單還沒有列印或存成 PDF，關閉後勾選的內容會消失。確定要關閉嗎？",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Close:
                self.go("referral")
                event.ignore()
                return
        unsaved = self.pages["settings"].unsaved_tabs()
        if unsaved:
            answer = QMessageBox.question(
                self, "有尚未儲存的設定", f"「{'、'.join(unsaved)}」有變更還沒儲存，確定要關閉嗎？",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        event.accept()
