from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLineEdit, QWidget

from icope_tool.services.hpdcs.client import CaptchaManualRequired, HpdcsError
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.base import BaseDialog, field_label
from icope_tool.ui.dialogs.captcha import CaptchaDialog
from icope_tool.ui.theme import C, icon
from icope_tool.ui.widgets import Banner, Spinner, button, hbox, label
from icope_tool.ui.workers import run_in_background


class CredentialsDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, ctx: AppContext):
        super().__init__(parent, "國健署系統帳號",
                         "用來自動登入「成人預防保健暨慢性疾病防治資訊系統（hpdcs）」查詢 ICOPE 紀錄。",
                         "key-round", width=540)
        self.ctx = ctx
        self.saved = False
        creds = ctx.credentials.get()

        self.account = QLineEdit(creds[0] if creds else "")
        self.account.setPlaceholderText("登入國健署系統用的帳號")
        self.account.setAccessibleName("帳號")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("已設定過，重新輸入才會更新" if creds else "密碼")
        self.password.setAccessibleName("密碼")
        self._toggle = QAction(icon("eye", C["subtle"]), "顯示密碼", self.password)
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._toggle_password)
        self.password.addAction(self._toggle, QLineEdit.ActionPosition.TrailingPosition)

        self.body.addWidget(field_label("帳號", required=True, buddy=self.account))
        self.body.addWidget(self.account)
        self.body.addWidget(field_label("密碼", required=True, buddy=self.password))
        self.body.addWidget(self.password)
        self.body.addWidget(Banner(
            "neutral", "帳號密碼存放位置",
            "存放在資料夾內的 auth 子資料夾，只用來登入國健署系統，不會出現在畫面、紀錄或設定包中。"
            "若資料夾放在共用磁碟，請確認只有診所同仁能存取。", icon_name="shield-check"))

        self.progress = hbox(Spinner(20), label("正在登入國健署系統測試…", "subtle"), None)
        self._progress_holder = QWidget()
        self._progress_holder.setLayout(self.progress)
        self._progress_holder.hide()
        self.body.addWidget(self._progress_holder)

        self.add_cancel("關閉")
        self.save_only = self.add_button(button("只儲存", kind="secondary", on_click=lambda: self._save(test=False)))
        self.save_test = self.add_button(button("儲存並測試登入", "log-in", "primary",
                                                on_click=lambda: self._save(test=True)))
        self.save_test.setDefault(True)
        self.password.returnPressed.connect(lambda: self._save(test=True))
        (self.password if creds else self.account).setFocus()

    def _toggle_password(self, visible: bool) -> None:
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self._toggle.setText("隱藏密碼" if visible else "顯示密碼")

    def _save(self, test: bool) -> None:
        self.clear_error()
        account = self.account.text().strip()
        password = self.password.text()
        existing = self.ctx.credentials.get()
        if not password and existing and account == existing[0]:
            password = existing[1]          # 只想重測或沒改密碼
        if not account:
            self.show_error("請輸入帳號", kind="warning")
            self.account.setFocus()
            return
        if not password:
            self.show_error("請輸入密碼", kind="warning")
            self.password.setFocus()
            return
        try:
            self.ctx.credentials.save(account, password)
        except (OSError, StoreError, ValueError) as exc:
            self.show_error("無法儲存帳號密碼", str(exc))
            return
        self.saved = True
        self.ctx.hpdcs.invalidate()
        self.ctx.audit.record("hpdcs_credentials_saved")
        self.ctx.mark_saved("hpdcs")
        if not test:
            self.accept()
            return
        self._run_login()

    def _run_login(self, token: str | None = None, code: str | None = None) -> None:
        self._set_busy(True)
        run_in_background(self, lambda: self.ctx.hpdcs.login(token, code),
                          self._login_ok, self._login_failed, lambda: self._set_busy(False))

    def _set_busy(self, busy: bool) -> None:
        self._progress_holder.setVisible(busy)
        for widget in (self.save_only, self.save_test, self.account, self.password):
            widget.setEnabled(not busy)

    def _login_ok(self, _result) -> None:
        self.ctx.mark_saved("hpdcs")
        self.error_banner.set_content("success", "登入成功", "帳號密碼正確，可以開始查詢了。")
        self.error_banner.show()
        self.save_test.setText("完成")
        try:
            self.save_test.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.save_test.clicked.connect(self.accept)

    def _login_failed(self, exc: BaseException) -> None:
        self.ctx.mark_saved("hpdcs")
        if isinstance(exc, CaptchaManualRequired):
            dialog = CaptchaDialog(self, exc, self.ctx.hpdcs.refresh_captcha)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._run_login(dialog.token, dialog.code)
            else:
                self.show_error("已取消測試登入", "帳號密碼已儲存；查詢時會再嘗試登入。", kind="warning")
            return
        if isinstance(exc, HpdcsError):
            self.show_error("測試登入失敗", f"{exc}。帳號密碼已儲存，請確認後重新輸入。")
        else:
            self.show_error("測試登入時發生錯誤", f"{type(exc).__name__}：{exc}")
        self.password.setFocus(Qt.FocusReason.OtherFocusReason)
