from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from icope_tool.ui.theme import C, pixmap
from icope_tool.ui.widgets import Banner, FitScrollArea, ScrollFade, button, label


class BaseDialog(QDialog):
    """標題列＋內容＋錯誤提示＋底部按鈕的一致外觀。"""

    def __init__(self, parent: QWidget | None, title: str, subtitle: str = "", icon_name: str | None = None,
                 width: int = 520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setStyleSheet(f"QDialog {{ background: {C['surface']}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(12)
        if icon_name:
            badge = QLabel()
            badge.setPixmap(pixmap(icon_name, C["primary"], 22))
            badge.setFixedSize(40, 40)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"background: {C['primary_tint']}; border-radius: 20px;")
            header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(label(title, "cardTitle"))
        if subtitle:
            texts.addWidget(label(subtitle, "subtle", wrap=True))
        header.addLayout(texts, 1)
        root.addLayout(header)

        # 內容放在可捲動區：小螢幕放不下時捲動內容，錯誤提示與按鈕固定在可見的底部
        body_holder = QWidget()
        body_holder.setObjectName("ScrollBody")
        self.body = QVBoxLayout(body_holder)
        self.body.setContentsMargins(0, 0, 6, 0)
        self.body.setSpacing(12)
        self.body_area = FitScrollArea()
        self.body_area.setWidgetResizable(True)
        self.body_area.setFrameShape(self.body_area.Shape.NoFrame)
        self.body_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_area.viewport().setAutoFillBackground(False)
        self.body_area.setWidget(body_holder)
        ScrollFade(self.body_area, C["surface"])
        self._body_closed = False
        self._explicit_size = False       # 子類別自己 resize 過（例如大表格、說明）：只長高、不收回
        self._grow_timer = QTimer(self)          # 合併同一輪的多次版面變更；對話框刪除時一起消失
        self._grow_timer.setSingleShot(True)
        self._grow_timer.setInterval(0)
        self._grow_timer.timeout.connect(self._grow_to_content)
        self.body_area.content_changed.connect(self._grow_timer.start)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_to_content)
        root.addWidget(self.body_area, 1)

        self.error_banner = Banner("danger")
        self.error_banner.hide()
        root.addWidget(self.error_banner)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.addStretch(1)
        root.addLayout(self.buttons)

    def showEvent(self, event) -> None:
        # 先定好高度再交給 QDialog 置中：內容能放下就完整顯示（Qt 預設最多只給螢幕高度的 2/3），
        # 放不下時以可用高度為上限，內容區捲動、按鈕固定在底部
        if not self._body_closed:
            # 可伸展的空白放最後：多出的高度留在底部，不會平均撐開欄位之間的距離
            self.body.addStretch(0)
            self._body_closed = True
            self._explicit_size = self.testAttribute(Qt.WidgetAttribute.WA_Resized)
        screen = self.screen() or QApplication.primaryScreen()
        limit = max(360, screen.availableGeometry().height() - 60)
        self.setMaximumHeight(limit)
        wanted = min(limit, max(self.height(), self.sizeHint().height()))
        if wanted != self.height():
            self.resize(self.width(), wanted)
        super().showEvent(event)
        self._fit_timer.start()          # 排版完成後依實際寬度再算一次，收回多估的高度

    def _fit_to_content(self) -> None:
        if not self.isVisible():
            return
        self.body_area.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        wanted = max(self.minimumSizeHint().height(), min(self.maximumHeight(), self.sizeHint().height()))
        if wanted < self.height() and not self._explicit_size:
            delta = self.height() - wanted
            self.resize(self.width(), wanted)
            self.move(self.x(), self.y() + delta // 2)
        elif wanted > self.height():
            self._grow_to_content()

    def _grow_to_content(self) -> None:
        """內容變多（例如出現警告或確認勾選）時加高到放得下為止，並保持整個視窗在螢幕內。"""
        if not self.isVisible():
            return
        wanted = min(self.maximumHeight(), self.sizeHint().height())
        if wanted <= self.height():
            return
        self.resize(self.width(), wanted)
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        if frame.bottom() > available.bottom():
            self.move(frame.x(), max(available.top(), available.bottom() - frame.height()))

    def add_button(self, widget: QPushButton) -> QPushButton:
        self.buttons.addWidget(widget)
        return widget

    def add_cancel(self, text: str = "取消") -> QPushButton:
        return self.add_button(button(text, kind="secondary", on_click=self.reject))

    def show_error(self, title: str, text: str = "", kind: str = "danger") -> None:
        self.error_banner.set_content(kind, title, text)
        self.error_banner.show()

    def clear_error(self) -> None:
        self.error_banner.hide()


def field_label(text: str, required: bool = False, buddy: QWidget | None = None) -> QWidget:
    """欄位標題。給 buddy 時會把標題設成該欄位的無障礙名稱（螢幕報讀會念出）並支援點標題聚焦。"""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(3)
    title = label(text, "fieldLabel")
    row.addWidget(title)
    if buddy is not None:
        title.setBuddy(buddy)
        if not buddy.accessibleName():
            buddy.setAccessibleName(text)
        if required:
            buddy.setAccessibleDescription("必填")
    if required:
        star = label("*", "required")
        star.setToolTip("必填")
        row.addWidget(star)
    row.addStretch(1)
    return holder
