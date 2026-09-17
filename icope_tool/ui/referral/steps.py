"""轉介衛教單的步驟元件。

樣式集中在 theme.component_stylesheet()，這裡只切換 objectName 與動態屬性，
避免上百個清單列各自 setStyleSheet 造成切換卡頓。
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from icope_tool.domains import DOMAIN_BY_ID, DOMAINS, domain_names
from icope_tool.models import Material, Resource, normalize_text
from icope_tool.ui.referral.state import ReferralState, group_by_type, suggested_resources
from icope_tool.ui.theme import C, icon, pixmap
from icope_tool.ui.widgets import (
    Card, Chip, Divider, EmptyState, button, clear_layout, domain_chip, hbox, label, link_button, scroll_area, set_prop,
    status_chip,
)

STEPS = ["選異常項目", "選轉介資源", "選衛教單張", "確認與列印"]
FIRST_BATCH = 24      # 切換項目時先畫這麼多列，其餘分批補上，切換不卡頓
BATCH = 30


def _named(widget: QLabel, name: str) -> QLabel:
    widget.setObjectName(name)
    return widget


def new_list_container(area: QScrollArea) -> QVBoxLayout:
    """每次重建清單都換新容器：QScrollArea.setWidget 會重算內容高度並刪掉舊容器。"""
    holder = QWidget()
    holder.setObjectName("PageBody")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 6, 0)
    layout.setSpacing(6)
    area.setWidget(holder)
    return layout


# =============================================================================
# 步驟指示器
# =============================================================================
class _StepButton(QPushButton):
    def __init__(self, index: int, title: str):
        super().__init__()
        self.setObjectName("StepButton")
        self.index = index
        self.step_title = title
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        self.badge = _named(QLabel(str(index + 1)), "StepBadge")
        self.badge.setFixedSize(32, 32)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.badge)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.title_label = _named(QLabel(title), "StepTitle")
        self.detail_label = _named(QLabel(), "StepDetail")
        texts.addWidget(self.title_label)
        texts.addWidget(self.detail_label)
        layout.addLayout(texts, 1)
        self._state = ""

    def update_state(self, state: str, detail: str) -> None:
        if state != self._state:
            self._state = state
            if state == "done":
                self.badge.setText("")
                self.badge.setPixmap(pixmap("check", C["primary"], 18, stroke=3))
            else:
                self.badge.setText(str(self.index + 1))
            for widget in (self, self.badge, self.title_label):
                set_prop(widget, "state", state)
        self.detail_label.setText(detail)
        status = {"current": "目前步驟", "done": "已完成", "todo": "尚未進行"}[state]
        self.setAccessibleName(f"第 {self.index + 1} 步 {self.step_title}，{status}。{detail}")


class StepIndicator(QWidget):
    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.buttons: list[_StepButton] = []
        for index, title in enumerate(STEPS):
            step = _StepButton(index, title)
            step.clicked.connect(lambda _=False, i=index: self.step_clicked.emit(i))
            layout.addWidget(step, 1)
            self.buttons.append(step)

    def update_state(self, current: int, done: list[bool], details: list[str]) -> None:
        for index, step in enumerate(self.buttons):
            state = "current" if index == current else ("done" if done[index] else "todo")
            step.update_state(state, details[index])


# =============================================================================
# 步驟 1：異常項目
# =============================================================================
class DomainCard(QPushButton):
    def __init__(self, domain, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DomainCard")
        self.setProperty("domain", domain.id)
        self.domain = domain
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        top = QHBoxLayout()
        badge = _named(QLabel(), "DomainBadge")
        badge.setProperty("domain", domain.id)
        badge.setFixedSize(42, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(pixmap(domain.icon, domain.color, 24))
        top.addWidget(badge)
        top.addStretch(1)
        self.check = _named(QLabel(), "DomainCheck")
        self.check.setProperty("domain", domain.id)
        self.check.setFixedSize(26, 26)
        self.check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.check, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        layout.addWidget(_named(QLabel(domain.title), "DomainTitle"))
        desc = _named(QLabel(domain.description), "DomainDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addStretch(1)
        self.count_label = _named(QLabel(), "DomainCount")
        layout.addWidget(self.count_label)
        self._count = -1
        self._checked_view: bool | None = None
        self.toggled.connect(lambda _checked: self.refresh())
        self.set_count(0)

    def set_count(self, count: int) -> None:
        if count == self._count:
            return
        self._count = count
        self.count_label.setText(f"建議資源 {count} 個" if count else "尚無建議資源")
        set_prop(self.count_label, "domain", self.domain.id if count else "")
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        checked = self.isChecked()
        if checked != self._checked_view or force:
            self._checked_view = checked
            if checked:
                self.check.setPixmap(pixmap("check", "#FFFFFF", 16, stroke=3))
            else:
                self.check.setPixmap(pixmap("plus", C["muted"], 16))
            set_prop(self.check, "on", checked)
        self.setAccessibleName(f"{self.domain.title}，{self.domain.description}，"
                               f"{'已勾選' if checked else '未勾選'}，建議資源 {max(self._count, 0)} 個")


class DomainStep(QWidget):
    changed = Signal()

    def __init__(self, state: ReferralState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addLayout(hbox(label("勾選這次評估結果「需要關注」的項目，可以複選。", "subtle"), None,
                              button("全部取消", "x", "ghost", size="sm", on_click=self._clear_all), spacing=8))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.cards: dict[str, DomainCard] = {}
        for domain in DOMAINS:
            card = DomainCard(domain)
            card.toggled.connect(lambda checked, d=domain.id: self._toggle(d, checked))
            self.cards[domain.id] = card
        self._columns = 0
        self._layout_cards(4)
        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setContentsMargins(0, 0, 4, 4)
        cards_layout.addLayout(self.grid)
        cards_layout.addStretch(1)
        layout.addWidget(scroll_area(cards, fade=C["bg"]), 1)

    def _layout_cards(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards.values():
            self.grid.removeWidget(card)
        for index, domain in enumerate(DOMAINS):
            self.grid.addWidget(self.cards[domain.id], index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_cards(4 if self.width() >= 760 else 2)

    def _toggle(self, domain_id: str, checked: bool) -> None:
        if (domain_id in self.state.domains) == checked:
            return
        self.state.set_domain(domain_id, checked)
        self.changed.emit()

    def _clear_all(self) -> None:
        if not self.state.domains:
            return
        if self.state.unique_resource_ids():
            answer = QMessageBox.question(
                self, "取消全部異常項目？",
                "已選的轉介資源會一起取消；之後在這份轉介單再勾回同一個項目，會自動還原當時選的資源。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        for domain_id in list(self.state.domains):
            self.state.set_domain(domain_id, False)
        self.sync()
        self.changed.emit()

    def set_counts(self, resources: list[Resource]) -> None:
        for domain in DOMAINS:
            self.cards[domain.id].set_count(len(suggested_resources(resources, domain.id)))

    def sync(self) -> None:
        for domain_id, card in self.cards.items():
            wanted = domain_id in self.state.domains
            if card.isChecked() != wanted:
                card.blockSignals(True)
                card.setChecked(wanted)
                card.blockSignals(False)
                card.refresh()


# =============================================================================
# 步驟 2：轉介資源
# =============================================================================
class ClickableRow(QFrame):
    """整列可點選的勾選列（資源、單張）。

    選取、滑過、鍵盤焦點的外框自己畫：只重繪不重新排版，上百列時勾選也不會卡。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SelectableRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover = False
        self.checkbox = QCheckBox()
        self.checkbox.toggled.connect(lambda _checked: self.update())
        self.checkbox.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is self.checkbox and event.type() in (event.Type.FocusIn, event.Type.FocusOut):
            self.update()
        return super().eventFilter(watched, event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        selected = self.checkbox.isChecked()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(C["primary_tint"] if selected else C["surface"])
        if selected:
            pen = QPen(QColor(C["primary"]), 2)
        elif self._hover:
            pen = QPen(QColor(C["primary"]), 1)
        else:
            pen = QPen(QColor(C["border"]), 1)
        # 外圈留 3px 給鍵盤焦點框：選取框與焦點框同時出現時仍分得出來
        body = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        inset = pen.widthF() / 2
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(body.adjusted(inset, inset, -inset, -inset), 9, 9)
        if self.checkbox.hasFocus():
            painter.setPen(QPen(QColor(C["focus"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.checkbox.toggle()
            self.checkbox.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mouseReleaseEvent(event)


class ResourceRow(ClickableRow):
    toggled = Signal(object, bool)

    def __init__(self, resource: Resource, domain_id: str, state: ReferralState, parent: QWidget | None = None):
        super().__init__(parent)
        self.resource = resource
        self.domain_id = domain_id
        self.state = state
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        self.checkbox.setAccessibleName(resource.name)
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        if resource.pinned:
            star = QLabel()
            star.setPixmap(pixmap("star", C["warning"], 16))
            star.setToolTip("常用資源")
            title_row.addWidget(star)
        name = _named(QLabel(resource.name), "RowTitle")
        name.setWordWrap(True)
        title_row.addWidget(name, 1)
        self._name = name
        self._title_row = title_row
        self._chip = Chip(resource.type) if resource.type else None
        self._chip_below = False
        self._chip_row = QHBoxLayout()
        self._chip_row.setContentsMargins(0, 2, 0, 2)
        if self._chip is not None:
            title_row.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignTop)
        texts.addLayout(title_row)
        texts.addLayout(self._chip_row)
        details = []
        if resource.phones:
            details.append("電話 " + "、".join(resource.phones))
        if resource.hours:
            details.append("時間 " + resource.hours)
        if resource.address:
            details.append(resource.address)
        if details:
            texts.addWidget(label("　｜　".join(details), "caption", wrap=True))
        if resource.note:
            texts.addWidget(label("備註：" + resource.note, "caption", wrap=True))
        self.also = _named(QLabel(), "RowAlso")
        self.also.setWordWrap(True)
        self.also.hide()
        texts.addWidget(self.also)
        layout.addLayout(texts, 1)
        self.sync()
        self.checkbox.toggled.connect(lambda checked: self.toggled.emit(self.resource, checked))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        chip = self._chip
        if chip is None or not self._name.width():
            return
        # 名稱與類型標籤排不進同一行時，把標籤移到名稱下方：名稱用整欄寬度，避免只剩一兩個字落到第二行
        room = self._name.width() + (0 if self._chip_below else chip.width() + self._title_row.spacing())
        needs_below = self._name.fontMetrics().horizontalAdvance(self._name.text()) > room - chip.sizeHint().width() - 8
        if needs_below == self._chip_below:
            return
        self._chip_below = needs_below
        if needs_below:
            self._title_row.removeWidget(chip)
            self._chip_row.addWidget(chip)
            self._chip_row.addStretch(1)
        else:
            while self._chip_row.count():
                self._chip_row.takeAt(0)
            self._title_row.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)

    def sync(self) -> None:
        selected = self.state.is_selected(self.resource.id, self.domain_id)
        if self.checkbox.isChecked() != selected:
            self.checkbox.blockSignals(True)
            self.checkbox.setChecked(selected)
            self.checkbox.blockSignals(False)
            self.update()
        others = [d for d in self.state.applicable_domains(self.resource) if d != self.domain_id]
        text = f"也適用已選的：{domain_names(others)}（勾選會一起套用）" if others else ""
        if self.also.text() != text:
            self.also.setText(text)
            self.also.setVisible(bool(others))


class _DomainTab(QPushButton):
    def __init__(self, domain_id: str):
        super().__init__()
        self.setObjectName("DomainTab")
        self.setProperty("domain", domain_id)
        self.domain_id = domain_id
        domain = DOMAIN_BY_ID[domain_id]
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(icon(domain.icon, domain.color, 20))
        self.setIconSize(QSize(18, 18))

    def update_count(self, count: int) -> None:
        domain = DOMAIN_BY_ID[self.domain_id]
        suffix = f"（{count}）" if count else "（未選）"
        self.setText(f"  {domain.title}{suffix}")
        self.setToolTip(f"{domain.title}：已選 {count} 個資源" if count else f"{domain.title}：還沒選轉介資源")
        self.setAccessibleName(f"{domain.title}，{'已選 ' + str(count) + ' 個資源' if count else '還沒選轉介資源'}")


class ResourceStep(QWidget):
    changed = Signal()
    go_settings = Signal()
    go_back = Signal()
    leaflets_requested = Signal()

    def __init__(self, state: ReferralState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.resources: list[Resource] = []
        self.active: str | None = None
        self.rows: list[ResourceRow] = []
        self.tabs: list[_DomainTab] = []
        self._pick_more: Callable[[str], None] | None = None
        self._pending: list[tuple[str, object]] = []
        self._build_token = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.tabs_holder = QWidget()
        self.tabs_holder.setFixedWidth(196)
        self.tabs_layout = QVBoxLayout(self.tabs_holder)
        self.tabs_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs_layout.setSpacing(6)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        root.addWidget(self.tabs_holder)

        right = QVBoxLayout()
        right.setSpacing(8)
        root.addLayout(right, 1)
        self.heading = label("", "cardTitle")
        self.subheading = label("", "subtle", wrap=True)
        right.addWidget(self.heading)
        right.addWidget(self.subheading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜尋名稱、地址、電話")
        self.search.setToolTip("在這個項目的建議資源中搜尋")
        self.search.setMinimumWidth(200)
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["muted"]), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName("搜尋建議資源")
        self.search.textChanged.connect(self._filter_rows)
        self.more_button = button("從資源庫加入…", "plus", "secondary",
                                  tooltip="搜尋全部資源，加入沒有標記這個項目的資源", on_click=self._pick)
        right.addLayout(hbox(self.search, self.more_button, spacing=8))
        self.no_match = label("", "subtle")
        self.no_match.hide()
        right.addWidget(self.no_match)

        self.list_area = QScrollArea()
        self.list_area.setWidgetResizable(True)
        self.list_area.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_layout = new_list_container(self.list_area)
        right.addWidget(self.list_area, 1)

        self.empty_holder = QWidget()
        self.empty_layout = QVBoxLayout(self.empty_holder)
        self.empty_layout.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.empty_holder, 1)

    def set_picker(self, fn: Callable[[str], None]) -> None:
        self._pick_more = fn

    def set_resources(self, resources: list[Resource]) -> None:
        self.resources = resources
        self.rebuild()

    def rebuild(self) -> None:
        for tab in self.tabs:
            self.tab_group.removeButton(tab)
        clear_layout(self.tabs_layout)
        self.tabs = []
        if self.active not in self.state.domains:
            self.active = self.state.domains[0] if self.state.domains else None
        for domain_id in self.state.domains:
            tab = _DomainTab(domain_id)
            tab.setChecked(domain_id == self.active)
            tab.clicked.connect(lambda _=False, d=domain_id: self.show_domain(d))
            self.tab_group.addButton(tab)
            self.tabs_layout.addWidget(tab)
            self.tabs.append(tab)
        self.tabs_layout.addStretch(1)
        self.refresh_tabs()
        self._rebuild_rows()

    def refresh_tabs(self) -> None:
        for tab in self.tabs:
            tab.update_count(len(self.state.resources.get(tab.domain_id, [])))

    def sync_rows(self) -> None:
        """狀態從外部改變（例如從資源庫加入、清除）時，讓畫面上的列跟上。"""
        for row in self.rows:
            row.sync()
        self._update_subheading()
        self.refresh_tabs()

    def show_domain(self, domain_id: str) -> None:
        if domain_id == self.active and self.rows:
            return
        self.active = domain_id
        for tab in self.tabs:
            tab.setChecked(tab.domain_id == domain_id)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh_tabs()
        self._rebuild_rows()

    def _update_subheading(self) -> None:
        if self.active is None:
            return
        chosen = len(self.state.resources.get(self.active, []))
        self.subheading.setText(
            f"已選 {chosen} 個。勾選要列在轉介單上的服務；不需要轉介也可以不選，單上會請長者與醫護人員討論。")

    def _rebuild_rows(self) -> None:
        self.rows_layout = new_list_container(self.list_area)
        clear_layout(self.empty_layout)
        self.rows = []
        self.no_match.hide()
        has_domain = self.active is not None
        self.tabs_holder.setVisible(has_domain)
        self.search.setVisible(has_domain)
        self.more_button.setVisible(has_domain)
        if not has_domain:
            self.heading.setText("還沒有選異常項目")
            self.subheading.setText("")
            self.list_area.hide()
            self.empty_holder.show()
            actions = QWidget()
            actions.setLayout(hbox(
                button("回上一步勾選項目", "chevron-left", "primary", on_click=self.go_back.emit),
                button("只印衛教單張…", "book-open", "secondary", on_click=self.leaflets_requested.emit), spacing=8))
            self.empty_layout.addWidget(EmptyState(
                "clipboard-list", "還沒有勾選需要關注的項目",
                "勾選項目後，這裡會列出每個項目建議的轉介資源。只想印衛教單張的話，按「只印衛教單張…」直接勾選列印。",
                actions))
            return
        domain = DOMAIN_BY_ID[self.active]
        self.heading.setText(f"{domain.title}的轉介資源")
        self._update_subheading()
        suggested = suggested_resources(self.resources, self.active)
        suggested_ids = {r.id for r in suggested}
        chosen_ids = set(self.state.resources.get(self.active, []))
        extra = [r for r in self.resources if r.id in chosen_ids and r.id not in suggested_ids]
        if not suggested and not extra:
            self.list_area.hide()
            self.empty_holder.show()
            actions = QWidget()
            actions.setLayout(hbox(
                button("從資源庫加入…", "plus", "primary", on_click=self._pick),
                button("到設定新增資源", "settings", "secondary", on_click=self.go_settings.emit), spacing=8))
            self.empty_layout.addWidget(EmptyState(
                "link", f"還沒有標記「{domain.name}」的資源",
                "可以從資源庫加入其他資源，或到設定替資源勾選適用項目。", actions))
            return
        self.empty_holder.hide()
        self.list_area.show()
        groups = group_by_type(suggested)
        if extra:
            groups.append(("從資源庫加入", extra))
        self._pending = []
        for title, items in groups:
            self._pending.append(("title", f"{title}（{len(items)}）"))
            self._pending.extend(("row", resource) for resource in items)
        self._build_token += 1
        self._append_batch(self._build_token, FIRST_BATCH)

    def _append_batch(self, token: int, size: int) -> None:
        if token != self._build_token or self.active is None:
            return
        keyword = normalize_text(self.search.text())
        layout = self.rows_layout
        if layout.count() and layout.itemAt(layout.count() - 1).spacerItem() is not None:
            layout.takeAt(layout.count() - 1)
        added = 0
        while self._pending and added < size:
            kind, payload = self._pending.pop(0)
            if kind == "title":
                layout.addWidget(_named(QLabel(str(payload)), "GroupTitle"))
                continue
            row = ResourceRow(payload, self.active, self.state)
            row.toggled.connect(self._on_row_toggled)
            if keyword and keyword not in self._haystack(row.resource):
                row.hide()
            layout.addWidget(row)
            self.rows.append(row)
            added += 1
        layout.addStretch(1)
        if self._pending:
            QTimer.singleShot(0, lambda: self._append_batch(token, BATCH))
        elif keyword:
            self._filter_rows(self.search.text())

    def _on_row_toggled(self, resource: Resource, checked: bool) -> None:
        if self.active is None:
            return
        if checked or self.active in resource.domains:
            self.state.set_resource(resource, self.active, checked)
        else:
            self.state.remove_from_domain(resource.id, self.active)
        for row in self.rows:
            if row.resource.id == resource.id:
                row.sync()
        self._update_subheading()
        self.refresh_tabs()
        self.changed.emit()

    @staticmethod
    def _haystack(r: Resource) -> str:
        return normalize_text(" ".join([r.name, r.type, r.address, *r.phones, r.note]))

    def _filter_rows(self, text: str) -> None:
        keyword = normalize_text(text)
        shown = 0
        for row in self.rows:
            visible = not keyword or keyword in self._haystack(row.resource)
            row.setVisible(visible)
            shown += visible
        self.no_match.setText(f"找不到「{text.strip()}」，可以按「從資源庫加入…」搜尋全部資源。")
        self.no_match.setVisible(bool(keyword) and shown == 0 and not self._pending)

    def _pick(self) -> None:
        if self.active and self._pick_more:
            self._pick_more(self.active)


# =============================================================================
# 步驟 3：衛教單張
# =============================================================================
class MaterialRow(ClickableRow):
    toggled = Signal(str, bool)
    preview = Signal(object)

    def __init__(self, material: Material, suggested_for: list[str], selected: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.material = material
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        self.checkbox.setAccessibleName(material.name)
        self.checkbox.setChecked(selected)
        self.checkbox.toggled.connect(lambda checked: self.toggled.emit(material.id, checked))
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name = _named(QLabel(material.name), "RowTitle")
        name.setWordWrap(True)
        title_row.addWidget(name, 1)
        if suggested_for:
            title_row.addWidget(status_chip("success", "建議：" + domain_names(suggested_for)), 0,
                                Qt.AlignmentFlag.AlignTop)
        title_row.addWidget(Chip(f"{material.pages} 頁"), 0, Qt.AlignmentFlag.AlignTop)
        texts.addLayout(title_row)
        if material.description:
            texts.addWidget(label(material.description, "caption", wrap=True))
        layout.addLayout(texts, 1)
        view = button("預覽", "eye", "ghost", size="sm", tooltip=f"用 PDF 閱讀器開啟「{material.name}」")
        view.clicked.connect(lambda: self.preview.emit(material))
        layout.addWidget(view, 0, Qt.AlignmentFlag.AlignTop)

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)


class MaterialStep(QWidget):
    changed = Signal()
    go_settings = Signal()
    preview_requested = Signal(object)

    def __init__(self, state: ReferralState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self.materials: list[Material] = []
        self.rows: list[MaterialRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.intro = label("勾選要一起印給長者的衛教資料，會依清單順序附在轉介單後面。", "subtle", wrap=True)
        self.suggest_button = button("勾選所有建議的單張", "sparkles", "secondary", size="sm",
                                     on_click=self._select_suggested)
        header = hbox(self.intro, self.suggest_button, spacing=12)
        header.setStretch(0, 1)
        layout.addLayout(header)
        self.list_area = QScrollArea()
        self.list_area.setWidgetResizable(True)
        self.list_area.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_layout = new_list_container(self.list_area)
        layout.addWidget(self.list_area, 1)
        self.empty = EmptyState(
            "book-open", "還沒有衛教單張",
            "到設定把衛教單張的 PDF 加進來，之後就能在這裡勾選、跟轉介單一起列印。這一步也可以直接略過。",
            button("到設定加入 PDF", "upload", "primary", on_click=self.go_settings.emit))
        layout.addWidget(self.empty, 1)

    def set_materials(self, materials: list[Material]) -> None:
        self.materials = [m for m in materials if m.enabled]
        self.rebuild()

    def rebuild(self) -> None:
        self.rows_layout = new_list_container(self.list_area)
        self.rows = []
        has = bool(self.materials)
        self.list_area.setVisible(has)
        self.empty.setVisible(not has)
        self.intro.setVisible(has)
        wanted = set(self.state.domains)
        any_suggested = False
        for material in self.materials:
            suggested_for = [d for d in material.domains if d in wanted]
            any_suggested = any_suggested or bool(suggested_for)
            row = MaterialRow(material, suggested_for, material.id in self.state.materials)
            row.toggled.connect(self._on_toggle)
            row.preview.connect(self.preview_requested.emit)
            self.rows_layout.addWidget(row)
            self.rows.append(row)
        self.rows_layout.addStretch(1)
        self.suggest_button.setVisible(any_suggested)

    def _on_toggle(self, material_id: str, checked: bool) -> None:
        self.state.set_material(material_id, checked)
        self.changed.emit()

    def _select_suggested(self) -> None:
        # 直接改狀態再重建列：已經勾著的列不會發出變更訊號，靠核取方塊同步會漏掉「改成人工選取」
        self.state.select_materials(self.state.suggested_material_ids(self.materials))
        self.rebuild()
        self.changed.emit()


# =============================================================================
# 右側摘要
# =============================================================================
class SummaryPanel(Card):
    reset_requested = Signal()
    jump = Signal(int)
    apply_defaults_requested = Signal()
    go_settings = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("這份轉介單", icon_name="file-text", parent=parent)
        self.setFixedWidth(300)
        # 院所預設：固定在捲動區外，內容再長也按得到；一定先寫出這次會處理哪些項目
        self.defaults_caption = label("", "caption", wrap=True)
        self.defaults_button = button("帶入院所預設", "clipboard-check", "secondary", size="sm",
                                      tooltip="加入這些項目的院所預設資源與單張",
                                      on_click=self.apply_defaults_requested.emit)
        self.defaults_link = link_button("前往設定", self.go_settings.emit)
        self.add(self.defaults_caption)
        self.add(hbox(self.defaults_button, self.defaults_link, None))
        self.add(Divider())
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 6, 0)
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        holder_layout.addLayout(self.content)
        holder_layout.addStretch(1)
        self.add(scroll_area(holder, fade=C["surface"]), 1)
        self.reset_button = button("清除重來", "rotate-ccw", "ghost", size="sm",
                                   tooltip="清除這份轉介單的所有勾選與輸入", on_click=self.reset_requested.emit)
        self.add(hbox(None, self.reset_button))
        self._structure: tuple | None = None
        self._counts: dict[str, QLabel] = {}
        self._names: dict[str, QLabel] = {}
        self._patient: QLabel | None = None

    def update_view(self, state: ReferralState, resources_by_id: dict[str, Resource],
                    materials_by_id: dict[str, Material]) -> None:
        """結構（項目、單張、是否有姓名）沒變時只更新數字，避免每次勾選都重建元件。"""
        structure = (tuple(state.domains), tuple(state.materials), bool(state.patient_name or state.patient_pid))
        if structure != self._structure:
            self._structure = structure
            self._rebuild(state, materials_by_id)
        if self._patient is not None:
            from icope_tool.idcheck import mask_person_id
            masked = f"　{mask_person_id(state.patient_pid)}" if state.patient_pid else ""
            self._patient.setText(f"長者：{state.patient_name or '（未填姓名）'}{masked}")
        for domain_id, count_label in self._counts.items():
            chosen = [resources_by_id[i] for i in state.resources.get(domain_id, []) if i in resources_by_id]
            count = len(chosen)
            text = f"{count} 個資源" if count else "未選資源"
            if count_label.text() != text:
                count_label.setText(text)
                set_prop(count_label, "role", "caption" if count else "warning")
            names = "\n".join(f"・{r.name}" for r in chosen[:3])
            if count > 3:
                names += f"\n…還有 {count - 3} 個"
            names_label = self._names[domain_id]
            if names_label.text() != names:
                names_label.setText(names)
                names_label.setVisible(bool(names))
        self.reset_button.setEnabled(not state.is_empty())

    def _rebuild(self, state: ReferralState, materials_by_id: dict[str, Material]) -> None:
        clear_layout(self.content)
        self._counts = {}
        self._names = {}
        self._patient = None
        if state.patient_name or state.patient_pid:
            self._patient = label("", "fieldLabel")
            self.content.addWidget(self._patient)
        self.content.addWidget(self._section_title(f"異常項目（{len(state.domains)}）", 0))
        if not state.domains:
            self.content.addWidget(label("尚未勾選", "caption"))
        for domain_id in state.domains:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(domain_chip(DOMAIN_BY_ID[domain_id]))
            count_label = label("", "caption")
            self._counts[domain_id] = count_label
            row.addWidget(count_label)
            row.addStretch(1)
            self.content.addLayout(row)
            names_label = label("", "caption", wrap=True)
            names_label.setContentsMargins(4, 0, 0, 2)
            names_label.hide()
            self._names[domain_id] = names_label
            self.content.addWidget(names_label)
        self.content.addSpacing(4)
        self.content.addWidget(self._section_title(f"衛教單張（{len(state.materials)}）", 2))
        if not state.materials:
            self.content.addWidget(label("未勾選", "caption"))
        pages = 0
        for material_id in state.materials:
            material = materials_by_id.get(material_id)
            if material:
                pages += material.pages
                self.content.addWidget(label(f"・{material.name}", wrap=True))
        if pages:
            self.content.addWidget(label(f"單張共 {pages} 頁", "caption"))

    def update_defaults(self, pending: list[str], applied: list[str], without: list[str],
                        clinic_has_defaults: bool, has_domains: bool, available: bool = True) -> None:
        """每次刷新都要呼叫（不受上面的結構快取影響）：只改旗標時項目與單張的結構不變，文案卻要跟著變。"""
        if not clinic_has_defaults:
            lines = ["可在設定把常用的資源與單張設為「院所預設」，之後勾選項目就能一鍵帶入。"]
        elif not has_domains:
            lines = ["勾選異常項目後，可以一鍵帶入院所預設的資源與單張。"]
        else:
            lines = []
            if pending:
                lines.append(f"本次帶入：{domain_names(pending)}")
            if applied:
                lines.append(f"已帶入過院所預設：{domain_names(applied)}")
            if without:
                lines.append(f"{domain_names(without)}目前沒有院所預設，可自行選擇")
        self.defaults_caption.setText("\n".join(lines))
        self.defaults_link.setVisible(not clinic_has_defaults)
        self.defaults_button.setVisible(clinic_has_defaults and bool(pending))
        self.defaults_button.setEnabled(available)
        self.defaults_button.setToolTip("加入這些項目的院所預設資源與單張" if available
                                        else "資料讀取失敗，請先按上方的「重試」")

    def invalidate(self) -> None:
        self._structure = None

    def _section_title(self, text: str, step: int) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label(text, "section"))
        row.addStretch(1)
        row.addWidget(button("修改", kind="ghost", size="sm", tooltip=f"回到第 {step + 1} 步修改",
                             on_click=lambda: self.jump.emit(step)))
        return holder
