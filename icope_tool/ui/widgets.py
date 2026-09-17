"""共用 UI 元件。"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from icope_tool.ui.theme import C, icon, pixmap, repolish

BANNER_STYLES = {
    "info": (C["info"], C["info_bg"], C["info_border"], "info"),
    "success": (C["success"], C["success_bg"], C["success_border"], "circle-check"),
    "warning": (C["warning"], C["warning_bg"], C["warning_border"], "triangle-alert"),
    "danger": (C["danger"], C["danger_bg"], C["danger_border"], "circle-x"),
    "neutral": (C["subtle"], C["surface_alt"], C["border_strong"], "info"),
}


def label(text: str = "", role: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def button(text: str = "", icon_name: str | None = None, kind: str = "secondary", size: str | None = None,
           tooltip: str | None = None, on_click: Callable | None = None) -> QPushButton:
    widget = QPushButton(text)
    widget.setProperty("kind", kind)
    if size:
        widget.setProperty("size", size)
    if icon_name:
        color = "#FFFFFF" if kind == "primary" else (C["danger"] if kind == "danger" else C["primary"])
        widget.setIcon(icon(icon_name, color, 20, "#E2E8F0" if kind == "primary" else C["placeholder"]))
        widget.setIconSize(QSize(18, 18) if size != "lg" else QSize(20, 20))
    if tooltip:
        widget.setToolTip(tooltip)
    if not text and tooltip:
        widget.setAccessibleName(tooltip)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        widget.clicked.connect(on_click)
    return widget


def write_with_retry(parent: QWidget, title: str, fn: Callable[[], object]) -> tuple[bool, object]:
    """寫入共用資料夾。暫時性的讀寫錯誤問「重試／取消」（要寫的內容在呼叫端手上，重試不必重新輸入）；
    重複、已被刪除這類重試也沒用的錯誤直接說明原因。回傳（成功與否, 結果）。"""
    from icope_tool.store import DataFolderUnavailable, StoreError, StoreIOError

    while True:
        try:
            return True, fn()
        except StoreError as exc:
            if not isinstance(exc, (StoreIOError, DataFolderUnavailable)):
                QMessageBox.warning(parent, title, str(exc))
                return False, None
            answer = QMessageBox.warning(
                parent, title, f"{exc}\n\n剛才輸入的內容還在；確認資料夾可以存取後按「重試」就會儲存。",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Retry)
            if answer != QMessageBox.StandardButton.Retry:
                return False, None


class FitScrollArea(QScrollArea):
    """預設大小等於內容（QScrollArea 原本最多只給約 24 行高）；螢幕放不下時才出現捲軸。

    內容裡的元件顯示或隱藏時發出 content_changed，讓外層視窗依新內容調整大小。
    """

    content_changed = Signal()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.widget() and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
            self.content_changed.emit()
        return super().eventFilter(watched, event)

    def sizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:
            return super().sizeHint()
        hint = inner.sizeHint()
        height = hint.height()
        width = self.viewport().width()
        if inner.hasHeightForWidth() and width > 120:
            height = inner.heightForWidth(width)      # 已經排版過：依實際寬度算換行後需要的高度
        return QSize(hint.width() + self.verticalScrollBar().sizeHint().width(), height)

    def minimumSizeHint(self) -> QSize:
        inner = self.widget()
        if inner is None:
            return super().minimumSizeHint()
        return QSize(inner.minimumSizeHint().width() + self.verticalScrollBar().sizeHint().width(),
                     min(200, inner.sizeHint().height()))


def format_size(size: int) -> str:
    """檔案大小：小於 0.1 MB 用 KB，避免出現「0.0 MB」。"""
    if size >= 100 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def recovery_actions(retry: Callable, check_folder: Callable) -> list[QPushButton]:
    """資料夾讀寫失敗時的兩個出路：重試，或去確認資料存放位置。"""
    return [button("重試", "refresh-cw", "primary", size="sm", on_click=retry),
            button("檢查資料存放位置", "hard-drive", "secondary", size="sm", on_click=check_folder)]


def link_button(text: str, on_click: Callable | None = None) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName("LinkButton")
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    if on_click:
        widget.clicked.connect(on_click)
    return widget


def set_invalid(widget: QWidget, invalid: bool) -> None:
    if bool(widget.property("invalid")) != invalid:
        widget.setProperty("invalid", invalid)
        repolish(widget)


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        child = item.widget()
        if child is not None:
            child.hide()
            child.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class ScrollFade(QWidget):
    """捲動區底部的淡出漸層：下面還有內容時才出現，讓切到一半的內容看得出「可以往下捲」而不是被裁掉。"""

    HEIGHT = 28

    def __init__(self, area: QScrollArea, color: str):
        super().__init__(area)
        self._area = area
        self._color = QColor(color)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bar = area.verticalScrollBar()
        bar.valueChanged.connect(self._sync)
        bar.rangeChanged.connect(self._sync)
        area.installEventFilter(self)
        self._sync()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._area and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._sync()
        return False

    def _sync(self, *_args) -> None:
        bar = self._area.verticalScrollBar()
        viewport = self._area.viewport().geometry()
        self.setGeometry(viewport.x(), viewport.bottom() - self.HEIGHT + 1, viewport.width(), self.HEIGHT)
        self.setVisible(bar.maximum() > 0 and bar.value() < bar.maximum())
        self.raise_()

    def paintEvent(self, _event) -> None:
        clear = QColor(self._color)
        clear.setAlpha(0)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, clear)
        gradient.setColorAt(1, self._color)
        QPainter(self).fillRect(self.rect(), gradient)


def scroll_area(inner: QWidget, fade: str | None = None) -> QScrollArea:
    """內容比可用高度高時改為捲動（小螢幕或上方出現提示時，元件才不會被擠到重疊）；背景沿用外層。

    fade：外層背景色；給了就在下方還有內容時顯示淡出漸層。
    """
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    if not inner.objectName():
        inner.setObjectName("ScrollBody")
    area.setWidget(inner)
    if fade:
        ScrollFade(area, fade)
    return area


class Card(QFrame):
    def __init__(self, title: str | None = None, subtitle: str | None = None, icon_name: str | None = None,
                 parent: QWidget | None = None, margins: tuple[int, int, int, int] = (20, 18, 20, 20)):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(12)
        self.header_actions: QHBoxLayout | None = None
        if title:
            header = QHBoxLayout()
            header.setSpacing(10)
            if icon_name:
                badge = QLabel()
                badge.setPixmap(pixmap(icon_name, C["primary"], 22))
                badge.setFixedSize(26, 26)
                header.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            self.title_label = label(title, "cardTitle")
            texts.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = label(subtitle, "subtle", wrap=True)
                texts.addWidget(self.subtitle_label)
            header.addLayout(texts, 1)
            self.header_actions = QHBoxLayout()
            self.header_actions.setSpacing(8)
            header.addLayout(self.header_actions)
            self.body.addLayout(header)

    def add(self, widget_or_layout, stretch: int = 0):
        if isinstance(widget_or_layout, QLayout):
            self.body.addLayout(widget_or_layout, stretch)
        else:
            self.body.addWidget(widget_or_layout, stretch)
        return widget_or_layout


class Divider(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Divider")
        self.setFrameShape(QFrame.Shape.NoFrame)


class Banner(QFrame):
    """狀態提示：info / success / warning / danger / neutral。"""

    closed = Signal()

    def __init__(self, kind: str = "info", title: str = "", text: str = "", actions: Iterable[QWidget] = (),
                 closable: bool = False, parent: QWidget | None = None, icon_name: str | None = None):
        super().__init__(parent)
        self._icon = QLabel()
        self._icon.setFixedSize(24, 24)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        self._actions.setContentsMargins(0, 4, 0, 0)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 12, 12)
        outer.setSpacing(12)
        outer.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addWidget(self._title)
        column.addWidget(self._text)
        column.addLayout(self._actions)
        outer.addLayout(column, 1)
        if closable:
            close = QPushButton()
            close.setProperty("kind", "ghost")
            close.setIcon(icon("x", C["subtle"], 18))
            close.setToolTip("關閉提示")
            close.setAccessibleName("關閉提示")
            close.setFixedSize(32, 32)
            close.clicked.connect(self._close)
            outer.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.set_content(kind, title, text, actions, icon_name)

    def _close(self):
        self.hide()
        self.closed.emit()

    def set_content(self, kind: str, title: str = "", text: str = "", actions: Iterable[QWidget] = (),
                    icon_name: str | None = None) -> None:
        fg, bg, border, default_icon = BANNER_STYLES.get(kind, BANNER_STYLES["info"])
        self.setStyleSheet(
            f"Banner {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}"
            f"Banner QLabel {{ background: transparent; border: none; }}"
        )
        self._icon.setPixmap(pixmap(icon_name or default_icon, fg, 22))
        self._title.setText(title)
        self._title.setStyleSheet(f"color: {fg if kind != 'neutral' else C['ink']}; font-weight: 700; font-size: 11pt;")
        self._title.setVisible(bool(title))
        self._text.setText(text)
        self._text.setStyleSheet(f"color: {C['text']};")
        self._text.setVisible(bool(text))
        clear_layout(self._actions)
        actions = list(actions)
        for action in actions:
            self._actions.addWidget(action)
        self._actions.addStretch(1)
        self.setAccessibleName(f"{title}：{text}" if title else text)


class Chip(QLabel):
    """小標籤。tone：neutral / success / info / warning / danger / domain-<id> / domain-<id>-outline。"""

    def __init__(self, text: str, tone: str = "neutral", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)


def domain_chip(domain, active: bool = True) -> Chip:
    return Chip(domain.name, f"domain-{domain.id}" if active else f"domain-{domain.id}-outline")


def status_chip(kind: str, text: str) -> Chip:
    return Chip(text, kind if kind in BANNER_STYLES else "neutral")


def set_prop(widget: QWidget, name: str, value) -> None:
    """設定動態屬性；值有變才重新套用樣式。"""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        repolish(widget)


class FlowLayout(QLayout):
    """自動換行的水平排列（標籤、晶片用）。"""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._layout(QRect(0, 0, width, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, test=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _layout(self, rect, test):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str = "", action: QWidget | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(8)
        layout.addStretch(1)
        badge = QLabel()
        badge.setPixmap(pixmap(icon_name, C["primary"], 34))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(64, 64)
        badge.setStyleSheet(f"background: {C['primary_tint']}; border-radius: 32px;")
        layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = label(title, "section")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        # 換行文字不加版面對齊旗標：加了會用預設寬度估高度，長句會被截斷
        self.text = label(text, "subtle", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text)
        if action is not None:
            layout.addSpacing(6)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)


class Spinner(QWidget):
    def __init__(self, size: int = 28, color: str = C["primary"], parent: QWidget | None = None):
        super().__init__(parent)
        self._angle = 0
        self._color = QColor(color)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setAccessibleName("處理中")

    def _tick(self):
        self._angle = (self._angle + 12) % 360
        self.update()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(3, self.width() // 9)
        rect = self.rect().adjusted(width, width, -width, -width)
        track = QPen(QColor(C["border"]), width)
        painter.setPen(track)
        painter.drawEllipse(rect)
        pen = QPen(self._color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


class Toast(QLabel):
    """視窗底部短暫提示（例如「已儲存」），不打斷操作。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(
            f"QLabel {{ background: {C['ink']}; color: #FFFFFF; border-radius: 10px; padding: 10px 18px;"
            " font-weight: 600; }"
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, msec: int = 2600) -> None:
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            x = (parent.width() - self.width()) // 2
            y = parent.height() - self.height() - 48
            self.move(max(12, x), max(12, y))
        self.raise_()
        self.show()
        self._timer.start(msec)


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(label(title, "pageTitle"))
        if subtitle:
            texts.addWidget(label(subtitle, "pageSubtitle", wrap=True))
        layout.addLayout(texts, 1)
        self.action_bar = QHBoxLayout()
        self.action_bar.setSpacing(8)
        layout.addLayout(self.action_bar)


class ScrollPage(QScrollArea):
    """頁面內容可捲動，並限制最大寬度讓大螢幕不會拉得太開。"""

    def __init__(self, max_width: int = 1280, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PageScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("PageBody")
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(28, 24, 28, 28)
        self.content = QWidget()
        self.content.setObjectName("PageBody")
        self.content.setMaximumWidth(max_width)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        outer.addWidget(self.content, 100)
        outer.addStretch(1)
        self.setWidget(holder)


def hbox(*widgets, spacing: int = 8, stretch_last: bool = False, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, QLayout):
            layout.addLayout(widget)
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        else:
            layout.addWidget(widget)
    if stretch_last:
        layout.addStretch(1)
    return layout


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(spacing)
    layout.setContentsMargins(*margins)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        elif isinstance(widget, QLayout):
            layout.addLayout(widget)
        elif isinstance(widget, int):
            layout.addSpacing(widget)
        else:
            layout.addWidget(widget)
    return layout
