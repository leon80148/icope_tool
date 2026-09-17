"""視覺設計系統：色票、字級、QSS 與圖示載入。

色彩皆以 WCAG 2.2 AA 為準：一般文字與背景對比 ≥ 4.5:1，大字與圖示 ≥ 3:1。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from icope_tool.paths import resource_path, temp_output_dir

C = {
    "primary": "#0F766E", "primary_hover": "#115E59", "primary_pressed": "#134E4A",
    "primary_soft": "#CCFBF1", "primary_tint": "#F0FDFA", "primary_border": "#5EEAD4",
    "ink": "#0F172A", "text": "#1E293B", "subtle": "#475569", "muted": "#5B6B80",
    "placeholder": "#64748B", "border": "#E2E8F0", "border_strong": "#CBD5E1", "input_border": "#7A8699",
    "bg": "#F1F5F9", "surface": "#FFFFFF", "surface_alt": "#F8FAFC",
    "sidebar": "#0B3B37", "sidebar_hover": "#12524C", "sidebar_active": "#176B63",
    "sidebar_text": "#D5F5EF", "sidebar_muted": "#9FD9CF", "sidebar_accent": "#5EEAD4",
    "success": "#15803D", "success_bg": "#F0FDF4", "success_border": "#86EFAC",
    "info": "#1D4ED8", "info_bg": "#EFF6FF", "info_border": "#93C5FD",
    "warning": "#B45309", "warning_bg": "#FFFBEB", "warning_border": "#FCD34D",
    "danger": "#B91C1C", "danger_bg": "#FEF2F2", "danger_border": "#FCA5A5",
    "focus": "#0F172A",          # 焦點框用近黑色：與選取狀態的青綠色、各項目色都分得開
}

FONT_FAMILIES = ["Noto Sans TC", "Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC",
                 "Heiti TC", "Segoe UI", "sans-serif"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Menlo", "monospace"]
BASE_PT = 10.5

ICON_DIR = resource_path("icons")


# ---------------------------------------------------------------------------- 圖示
@lru_cache(maxsize=512)
def _svg_bytes(name: str, color: str) -> bytes:
    path = ICON_DIR / f"{name}.svg"
    text = path.read_text(encoding="utf-8") if path.exists() else (ICON_DIR / "circle-dot.svg").read_text("utf-8")
    return text.replace("currentColor", color).encode("utf-8")


def pixmap(name: str, color: str = C["text"], size: int = 20, ratio: float = 2.0,
           stroke: float | None = None) -> QPixmap:
    data = _svg_bytes(name, color)
    if stroke is not None:
        data = data.replace(b'stroke-width="2"', f'stroke-width="{stroke}"'.encode())
    renderer = QSvgRenderer(QByteArray(data))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    pm.setDevicePixelRatio(ratio)
    return pm


@lru_cache(maxsize=512)
def icon(name: str, color: str = C["text"], size: int = 20, disabled_color: str = C["placeholder"]) -> QIcon:
    result = QIcon()
    for s in {16, 20, 24, size}:
        result.addPixmap(pixmap(name, color, s), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, color, s), QIcon.Mode.Active)
        result.addPixmap(pixmap(name, disabled_color, s), QIcon.Mode.Disabled)
    return result


# ---------------------------------------------------------------------------- QSS 小圖（勾選框）
def _write_indicator_images() -> dict[str, str]:
    folder = temp_output_dir() / "theme"
    folder.mkdir(parents=True, exist_ok=True)
    check = '<path d="M6 12.5l4 4 8-9" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>'
    images = {
        "cb_off": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="1.5" y="1.5" width="21" height="21" rx="5" fill="#FFFFFF" stroke="#7B8BA1" stroke-width="1.6"/></svg>',
        "cb_off_hover": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="1.5" y="1.5" width="21" height="21" rx="5" fill="{C["primary_tint"]}" stroke="{C["primary"]}" stroke-width="1.8"/></svg>',
        "cb_on": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="5" fill="{C["primary"]}"/>{check}</svg>',
        "cb_off_disabled": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="1.5" y="1.5" width="21" height="21" rx="5" fill="#F1F5F9" stroke="#CBD5E1" stroke-width="1.6"/></svg>',
        "cb_on_disabled": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="1" y="1" width="22" height="22" rx="5" fill="#94A3B8"/>{check}</svg>',
        "rb_off": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10.2" fill="#FFFFFF" stroke="#7B8BA1" stroke-width="1.6"/></svg>',
        "rb_on": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10.2" fill="#FFFFFF" stroke="{C["primary"]}" stroke-width="2"/><circle cx="12" cy="12" r="5.5" fill="{C["primary"]}"/></svg>',
        "chevron_down": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" fill="none" stroke="{C["subtle"]}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    }
    paths = {}
    for key, svg in images.items():
        path = folder / f"{key}.svg"
        try:
            if not path.exists() or path.read_text(encoding="utf-8") != svg:
                path.write_text(svg, encoding="utf-8")
        except OSError:
            pass
        paths[key] = path.as_posix()
    return paths


def tint(hex_color: str, amount: float) -> str:
    """與白色混色（amount=0.9 表示 90% 白）。"""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02X}{:02X}{:02X}".format(*(round(c + (255 - c) * amount) for c in (r, g, b)))


def component_stylesheet() -> str:
    """元件樣式集中在全域 QSS，以動態屬性切換狀態（比每個元件各自 setStyleSheet 快很多）。"""
    from icope_tool.domains import DOMAINS
    rules = [f"""
/* ---- 可勾選的列（資源、單張）---- */
QFrame#SelectableRow {{ background: transparent; border: none; }}
QFrame#SelectableRow QLabel {{ background: transparent; }}
QFrame#SelectableRow QCheckBox:focus {{ border: 2px solid transparent; background: transparent; }}
QLabel#RowTitle {{ color: {C['ink']}; font-weight: 700; font-size: 11pt; }}
QLabel#RowAlso {{ color: {C['primary_pressed']}; font-size: 9.5pt; }}
QLabel#GroupTitle {{ color: {C['subtle']}; font-weight: 700; font-size: 10pt; padding: 6px 2px 0 2px; }}

/* ---- 查詢紀錄 ---- */
QPushButton#HistoryRow {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px;
    text-align: left; padding: 0; min-height: 50px; }}
QPushButton#HistoryRow:hover {{ border-color: {C['primary']}; background: {C['primary_tint']}; }}
QPushButton#HistoryRow:focus {{ border: 2px solid {C['focus']}; }}
QPushButton#HistoryRow QLabel {{ background: transparent; }}

/* ---- 步驟指示 ---- */
QPushButton#StepButton {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px;
    text-align: left; padding: 0; min-height: 58px; }}
QPushButton#StepButton:hover {{ border-color: {C['primary']}; }}
QPushButton#StepButton[state="current"] {{ background: {C['primary_tint']}; border: 2px solid {C['primary']}; }}
QPushButton#StepButton:focus {{ border: 2px solid {C['focus']}; }}
QPushButton#StepButton QLabel {{ background: transparent; border: none; }}
QLabel#StepBadge {{ border-radius: 16px; font-weight: 700; font-size: 11pt; background: {C['bg']};
    color: {C['subtle']}; border: 1px solid {C['border_strong']}; }}
QPushButton#StepButton QLabel#StepBadge[state="current"] {{ background: {C['primary']}; color: #FFFFFF; border: none; }}
QPushButton#StepButton QLabel#StepBadge[state="done"] {{ background: {C['primary_soft']}; border: none; }}
QLabel#StepTitle {{ font-weight: 700; font-size: 11pt; color: {C['subtle']}; }}
QLabel#StepTitle[state="current"] {{ color: {C['ink']}; }}
QLabel#StepTitle[state="done"] {{ color: {C['text']}; }}
QLabel#StepDetail {{ color: {C['muted']}; font-size: 9.5pt; }}

/* ---- 異常項目卡片與分頁 ---- */
QPushButton#DomainCard {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 14px;
    text-align: left; padding: 0; min-height: 150px; }}
QPushButton#DomainCard QLabel {{ background: transparent; border: none; }}
QLabel#DomainTitle {{ color: {C['ink']}; font-size: 12.5pt; font-weight: 800; }}
QLabel#DomainDesc {{ color: {C['subtle']}; font-size: 10pt; }}
QLabel#DomainCount {{ font-size: 9.5pt; font-weight: 700; color: {C['muted']}; }}
QPushButton#DomainCard QLabel#DomainCheck {{ background: {C['bg']}; border-radius: 13px; }}
QPushButton#DomainTab {{ text-align: left; padding: 8px 12px; border-radius: 10px; font-weight: 700;
    background: {C['surface']}; border: 1px solid {C['border']}; color: {C['ink']}; min-height: 26px; }}
"""]
    for d in DOMAINS:
        rules.append(f"""
QPushButton#DomainCard[domain="{d.id}"]:hover {{ border: 2px solid {d.color}; }}
QPushButton#DomainCard[domain="{d.id}"]:checked {{ background: {tint(d.color, 0.93)}; border: 2px solid {d.color}; }}
QPushButton#DomainCard QLabel#DomainBadge[domain="{d.id}"] {{ background: {tint(d.color, 0.86)}; border-radius: 21px; }}
QLabel#DomainCount[domain="{d.id}"] {{ color: {d.color}; }}
QPushButton#DomainCard QLabel#DomainCheck[domain="{d.id}"][on="true"] {{ background: {d.color}; }}
QPushButton#DomainTab[domain="{d.id}"]:hover {{ border: 2px solid {d.color}; }}
QPushButton#DomainTab[domain="{d.id}"]:checked {{ background: {tint(d.color, 0.9)}; border: 2px solid {d.color}; }}
QLabel#Chip[tone="domain-{d.id}"] {{ color: #FFFFFF; background: {d.color}; border: none; font-weight: 700; }}
QLabel#Chip[tone="domain-{d.id}-outline"] {{ color: {d.color}; background: {C['surface']}; border: 1px solid {d.color}; }}
""")
    rules.append(f"""
QPushButton#DomainCard[domain]:focus {{ border: 3px solid {C['focus']}; }}
QPushButton#DomainTab[domain]:focus {{ border: 2px solid {C['focus']}; }}

/* ---- 標籤晶片 ---- */
QLabel#Chip {{ border-radius: 10px; padding: 2px 9px; font-size: 9.5pt; font-weight: 600;
    color: {C['subtle']}; background: {C['surface_alt']}; border: 1px solid {C['border']}; }}
QLabel#Chip[tone="success"] {{ color: {C['success']}; background: {C['success_bg']}; border-color: {C['success_border']}; }}
QLabel#Chip[tone="info"] {{ color: {C['info']}; background: {C['info_bg']}; border-color: {C['info_border']}; }}
QLabel#Chip[tone="warning"] {{ color: {C['warning']}; background: {C['warning_bg']}; border-color: {C['warning_border']}; }}
QLabel#Chip[tone="danger"] {{ color: {C['danger']}; background: {C['danger_bg']}; border-color: {C['danger_border']}; }}
""")
    return "".join(rules)


def build_stylesheet() -> str:
    img = _write_indicator_images()
    return f"""
QAbstractItemView {{ outline: none; }}
QWidget {{ color: {C['text']}; }}
QMainWindow, QDialog {{ background: {C['bg']}; }}
QWidget#PageBody, QWidget#PageScroll, QScrollArea#PageScroll > QWidget > QWidget {{ background: {C['bg']}; }}
QToolTip {{ background: {C['ink']}; color: #FFFFFF; border: none; padding: 6px 8px; border-radius: 6px; }}

/* ---- 側邊導覽 ---- */
QFrame#Sidebar {{ background: {C['sidebar']}; border: none; }}
QLabel#SidebarTitle {{ color: #FFFFFF; font-size: 13pt; font-weight: 700; }}
QLabel#SidebarSubtitle {{ color: {C['sidebar_muted']}; font-size: 9.5pt; }}
QLabel#SidebarFooter {{ color: {C['sidebar_muted']}; font-size: 9pt; }}
QPushButton#NavButton {{
    color: {C['sidebar_text']}; background: transparent; border: none; border-radius: 10px;
    text-align: left; padding: 10px 14px; font-size: 11pt; min-height: 26px;
}}
QPushButton#NavButton:hover {{ background: {C['sidebar_hover']}; color: #FFFFFF; }}
QPushButton#NavButton:checked {{ background: {C['sidebar_active']}; color: #FFFFFF; font-weight: 700; }}
QPushButton#NavButton:focus {{ border: 2px solid {C['sidebar_accent']}; }}

/* ---- 卡片與文字角色 ---- */
QFrame#Card {{ background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 12px; }}
QWidget#ScrollBody {{ background: transparent; }}
QFrame#Panel {{ background: {C['surface_alt']}; border: 1px solid {C['border']}; border-radius: 10px; }}
QFrame#Divider {{ background: {C['border']}; border: none; max-height: 1px; min-height: 1px; }}
QLabel[role="pageTitle"] {{ font-size: 18pt; font-weight: 700; color: {C['ink']}; }}
QLabel[role="pageSubtitle"] {{ font-size: 10.5pt; color: {C['subtle']}; }}
QLabel[role="cardTitle"] {{ font-size: 12.5pt; font-weight: 700; color: {C['ink']}; }}
QLabel[role="section"] {{ font-size: 11pt; font-weight: 700; color: {C['ink']}; }}
QLabel[role="caption"] {{ font-size: 9.5pt; color: {C['muted']}; }}
QLabel[role="subtle"] {{ color: {C['subtle']}; }}
QLabel[role="fieldLabel"] {{ font-weight: 600; color: {C['text']}; }}
QLabel[role="required"] {{ color: {C['danger']}; font-weight: 700; }}
QLabel[role="error"] {{ color: {C['danger']}; }}
QLabel[role="success"] {{ color: {C['success']}; }}
QLabel[role="warning"] {{ color: {C['warning']}; }}
QLabel a {{ color: {C['primary']}; }}

/* ---- 按鈕 ---- */
QPushButton {{
    background: {C['surface']}; color: {C['text']}; border: 1px solid {C['border_strong']};
    border-radius: 8px; padding: 7px 14px; font-weight: 600;
}}
QPushButton[kind] {{ min-height: 22px; }}
QPushButton:hover {{ background: {C['surface_alt']}; border-color: {C['primary']}; color: {C['primary_pressed']}; }}
QPushButton:pressed {{ background: {C['primary_tint']}; }}
QPushButton:focus {{ border: 2px solid {C['focus']}; padding: 6px 13px; }}
QPushButton:disabled {{ color: {C['placeholder']}; background: {C['surface_alt']}; border-color: {C['border']}; }}
QPushButton[kind="primary"] {{ background: {C['primary']}; color: #FFFFFF; border: 1px solid {C['primary']}; }}
QPushButton[kind="primary"]:hover {{ background: {C['primary_hover']}; border-color: {C['primary_hover']}; color: #FFFFFF; }}
QPushButton[kind="primary"]:pressed {{ background: {C['primary_pressed']}; }}
QPushButton[kind="primary"]:focus {{ border: 2px solid {C['ink']}; padding: 6px 13px; }}
QPushButton[kind="primary"]:disabled {{ background: #A7C4C0; border-color: #A7C4C0; color: #F8FAFC; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {C['primary']}; }}
QPushButton[kind="ghost"]:hover {{ background: {C['primary_tint']}; border-color: transparent; color: {C['primary_pressed']}; }}
QPushButton[kind="ghost"]:focus {{ border: 2px solid {C['focus']}; }}
QPushButton[kind="ghost"]:checked {{ background: {C['primary_soft']}; color: {C['primary_pressed']}; }}
QPushButton[kind="danger"] {{ color: {C['danger']}; border-color: {C['danger_border']}; }}
QPushButton[kind="danger"]:hover {{ background: {C['danger_bg']}; border-color: {C['danger']}; color: {C['danger']}; }}
QPushButton[kind="ghost"]:disabled {{ color: {C['placeholder']}; background: transparent; border-color: transparent; }}
QPushButton[kind="danger"]:disabled, QPushButton[kind="secondary"]:disabled {{ color: {C['placeholder']}; background: {C['surface_alt']}; border-color: {C['border']}; }}
QPushButton[size="lg"] {{ padding: 10px 20px; font-size: 11.5pt; min-height: 26px; border-radius: 10px; }}
QPushButton[size="lg"]:focus {{ padding: 9px 19px; }}
QPushButton[size="sm"] {{ padding: 4px 10px; font-size: 9.5pt; min-height: 18px; }}
QPushButton[size="sm"]:focus {{ padding: 3px 9px; }}
QPushButton#LinkButton {{ background: transparent; border: none; color: {C['primary']}; padding: 2px 4px; text-decoration: underline; font-weight: 600; }}
QPushButton#LinkButton:hover {{ color: {C['primary_pressed']}; background: transparent; }}
QPushButton#LinkButton:focus {{ border: 1px dashed {C['focus']}; padding: 1px 3px; }}

/* ---- 輸入 ---- */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
    background: {C['surface']}; border: 1px solid {C['input_border']}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {C['primary_soft']}; selection-color: {C['ink']};
    color: {C['text']}; placeholder-text-color: {C['placeholder']};
}}
QLineEdit {{ min-height: 22px; }}
QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QComboBox:hover {{ border-color: {C['subtle']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {C['focus']}; padding: 5px 9px; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {C['surface_alt']}; color: {C['placeholder']}; }}
QLineEdit[invalid="true"] {{ border: 2px solid {C['danger']}; padding: 5px 9px; }}
QLineEdit#IdInput {{ font-size: 20pt; font-weight: 600; padding: 8px 14px; border-radius: 10px; }}
QLineEdit#IdInput:focus {{ padding: 7px 13px; }}
QLineEdit#IdInput[invalid="true"] {{ padding: 7px 13px; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({img['chevron_down']}); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {C['surface']}; border: 1px solid {C['border_strong']}; selection-background-color: {C['primary_tint']}; selection-color: {C['ink']}; padding: 4px; }}

QCheckBox, QRadioButton {{ spacing: 9px; padding: 3px 8px 3px 4px; border: 2px solid transparent; border-radius: 8px; }}
QCheckBox:focus, QRadioButton:focus {{ border: 2px solid {C['focus']}; background: {C['primary_tint']}; color: {C['ink']}; }}
QCheckBox::indicator {{ width: 20px; height: 20px; image: url({img['cb_off']}); }}
QCheckBox::indicator:hover {{ image: url({img['cb_off_hover']}); }}
QCheckBox::indicator:checked {{ image: url({img['cb_on']}); }}
QCheckBox::indicator:disabled {{ image: url({img['cb_off_disabled']}); }}
QCheckBox::indicator:checked:disabled {{ image: url({img['cb_on_disabled']}); }}
QRadioButton::indicator {{ width: 20px; height: 20px; image: url({img['rb_off']}); }}
QRadioButton::indicator:checked {{ image: url({img['rb_on']}); }}

/* ---- 分頁 ---- */
QTabWidget::pane {{ border: none; background: transparent; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {C['subtle']}; padding: 9px 16px; margin-right: 4px;
    border: none; border-bottom: 3px solid transparent; font-weight: 600; font-size: 10.5pt;
}}
QTabBar::tab:hover {{ color: {C['primary_pressed']}; border-bottom-color: {C['border_strong']}; }}
QTabBar::tab:selected {{ color: {C['primary_pressed']}; border-bottom-color: {C['primary']}; }}
QTabBar::tab:selected:focus {{ background: {C['primary_tint']}; border: 2px solid {C['focus']}; border-bottom: 3px solid {C['primary']}; border-top-left-radius: 8px; border-top-right-radius: 8px; }}

/* ---- 表格與清單 ---- */
QTableView, QTreeView, QListView {{
    background: {C['surface']}; border: 1px solid {C['border']}; border-radius: 10px;
    gridline-color: {C['border']}; selection-background-color: {C['primary_tint']}; selection-color: {C['ink']};
    alternate-background-color: {C['surface_alt']};
}}
QTableView::item {{ padding: 6px 8px; border: none; }}
QAbstractItemView::indicator {{ width: 20px; height: 20px; image: url({img['cb_off']}); }}
QAbstractItemView::indicator:checked {{ image: url({img['cb_on']}); }}
QAbstractItemView::indicator:disabled {{ image: url({img['cb_off_disabled']}); }}
QAbstractItemView::indicator:checked:disabled {{ image: url({img['cb_on_disabled']}); }}
QTableView::item:selected {{ background: {C['primary_soft']}; color: {C['ink']}; }}
QTableView:focus {{ border: 2px solid {C['focus']}; }}
QHeaderView::section {{
    background: {C['surface_alt']}; color: {C['subtle']}; border: none; border-bottom: 1px solid {C['border']};
    padding: 8px; font-weight: 700;
}}

/* ---- 捲軸 ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3CDD9; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #94A3B8; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C3CDD9; border-radius: 4px; min-width: 36px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- 狀態列 ---- */
QStatusBar {{ background: {C['surface']}; border-top: 1px solid {C['border']}; color: {C['subtle']}; }}
QStatusBar QLabel {{ color: {C['subtle']}; padding: 0 6px; }}
QStatusBar::item {{ border: none; }}
QMenu {{ background: {C['surface']}; border: 1px solid {C['border_strong']}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['primary_tint']}; color: {C['ink']}; }}
QProgressBar {{ background: {C['border']}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {C['primary']}; border-radius: 3px; }}
""" + component_stylesheet()


def ui_font(point_size: float = BASE_PT, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setBold(bold)
    return font


def mono_font(point_size: float = BASE_PT) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setPointSizeF(point_size)
    return font


def load_bundled_fonts() -> None:
    """UI 與 PDF 共用內附的 Noto Sans TC，讓每台電腦的中文字形一致。"""
    from PySide6.QtGui import QFontDatabase
    from icope_tool.services.pdf.referral_sheet import FONT_DIR
    for name in ("NotoSansTC-Regular.ttf", "NotoSansTC-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(FONT_DIR / name))


def apply_theme(app: QApplication) -> None:
    load_bundled_fonts()
    app.setStyle("Fusion")
    app.setFont(ui_font())
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(C["bg"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(C["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(C["surface_alt"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(C["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(C["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(C["placeholder"]))
    palette.setColor(QPalette.ColorRole.Link, QColor(C["primary"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(C["ink"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    app.setPalette(palette)
    app.setStyleSheet(build_stylesheet())


def repolish(widget) -> None:
    """動態屬性（如 invalid、kind）改變後重新套用樣式。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def platform_is_windows() -> bool:
    return sys.platform.startswith("win")


def app_icon() -> QIcon:
    path = resource_path("app.ico")
    if Path(path).exists():
        return QIcon(str(path))
    return icon("heart-pulse", C["primary"], 32)
