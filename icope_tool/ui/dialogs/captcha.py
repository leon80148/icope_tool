from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QImage, QPixmap, QRegularExpressionValidator
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from icope_tool.services.hpdcs.client import CaptchaManualRequired
from icope_tool.ui.dialogs.base import BaseDialog, field_label
from icope_tool.ui.theme import C
from icope_tool.ui.widgets import button, hbox, label
from icope_tool.ui.workers import run_in_background


class CaptchaDialog(BaseDialog):
    """國健署驗證碼無法自動辨識時，請使用者看圖輸入 5 位數字。"""

    def __init__(self, parent: QWidget | None, challenge: CaptchaManualRequired,
                 refresh: Callable[[str], CaptchaManualRequired]):
        super().__init__(parent, "請輸入驗證碼", "國健署系統的驗證碼這次無法自動辨識，請輸入圖片中的 5 位數字。",
                         "lock", width=460)
        self.token = challenge.token
        self.code = ""
        self._refresh = refresh

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(96)
        self.image.setStyleSheet(f"background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 10px;")
        self.image.setAccessibleName("驗證碼圖片")
        self.refresh_button = button("看不清楚，換一張", "refresh-cw", "ghost", on_click=self._on_refresh)

        self.input = QLineEdit()
        self.input.setObjectName("IdInput")
        self.input.setPlaceholderText("5 位數字")
        self.input.setMaxLength(5)
        self.input.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,5}")))
        self.input.setAccessibleName("驗證碼")
        self.input.textChanged.connect(self._update_state)
        self.input.returnPressed.connect(self._submit)

        self.body.addWidget(self.image)
        self.body.addLayout(hbox(None, self.refresh_button))
        self.body.addWidget(field_label("驗證碼", required=True, buddy=self.input))
        self.body.addWidget(self.input)
        self.body.addWidget(label("驗證碼 3 分鐘內有效；逾時請重新查詢。", "caption"))

        self.add_cancel("取消查詢")
        self.submit_button = self.add_button(button("送出", "log-in", "primary", on_click=self._submit))
        self.submit_button.setDefault(True)
        self._show_image(challenge)
        self._update_state()
        self.input.setFocus()

    def _show_image(self, challenge: CaptchaManualRequired) -> None:
        image = QImage.fromData(challenge.image_bytes)
        if image.isNull():
            self.image.setText("驗證碼圖片無法顯示，請按「換一張」")
            return
        scaled = QPixmap.fromImage(image).scaled(image.width() * 3, image.height() * 3,
                                                  Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation)
        self.image.setPixmap(scaled)

    def _update_state(self) -> None:
        self.submit_button.setEnabled(len(self.input.text()) == 5)

    def _submit(self) -> None:
        if len(self.input.text()) != 5:
            self.show_error("驗證碼是 5 位數字", kind="warning")
            return
        self.code = self.input.text()
        self.accept()

    def _on_refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("換圖中…")
        self.clear_error()

        def done(challenge: CaptchaManualRequired):
            self.token = challenge.token
            self._show_image(challenge)
            self.input.clear()
            self.input.setFocus()

        def failed(exc: BaseException):
            self.show_error("無法更換驗證碼", f"{exc}。請取消後重新查詢。")

        def finished():
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("看不清楚，換一張")

        token = self.token
        run_in_background(self, lambda: self._refresh(token), done, failed, finished)
