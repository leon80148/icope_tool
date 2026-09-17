from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from icope_tool.ui.context import AppContext
from icope_tool.ui.settings.clinic_tab import ClinicTab
from icope_tool.ui.settings.hpdcs_tab import HpdcsTab
from icope_tool.ui.settings.local_tab import LocalTab
from icope_tool.ui.settings.materials_tab import MaterialsTab
from icope_tool.ui.settings.pack_tab import PackTab
from icope_tool.ui.settings.resources_tab import ResourcesTab
from icope_tool.ui.theme import C, icon
from icope_tool.ui.widgets import PageHeader, ScrollPage, Toast

TABS = [
    ("clinic", "診所與轉介單", "building"),
    ("hpdcs", "國健署帳號", "key-round"),
    ("resources", "轉介資源庫", "link"),
    ("materials", "衛教單張", "book-open"),
    ("pack", "匯出／匯入", "package"),
    ("local", "本機設定", "monitor"),
]


class SettingsPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 18)
        root.setSpacing(10)
        root.addWidget(PageHeader("設定", "診所資訊與轉介單上的衛教重點、國健署帳號、轉介資源與衛教單張都在這裡管理。"))
        self.toast = Toast(self)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setIconSize(QSize(18, 18))
        root.addWidget(self.tabs, 1)
        self.pages: dict[str, QWidget] = {}
        builders = {
            "clinic": ClinicTab, "hpdcs": HpdcsTab, "resources": ResourcesTab,
            "materials": MaterialsTab, "pack": PackTab, "local": LocalTab,
        }
        for key, title, icon_name in TABS:
            page = builders[key](ctx, self.toast)
            self.pages[key] = page
            if key in ("clinic", "resources", "materials"):      # 這幾頁自己管捲動（儲存列、表格要固定在可見位置）
                holder = page
            else:
                scroll = ScrollPage(max_width=980)
                scroll.body.setContentsMargins(0, 0, 0, 0)
                scroll.widget().layout().setContentsMargins(0, 0, 8, 0)
                scroll.body.addWidget(page)
                holder = scroll
            self.tabs.addTab(holder, icon(icon_name, C["primary"]), title)
        self.pages["local"].unsaved_tabs = self.unsaved_tabs

    def select(self, key: str) -> None:
        keys = [k for k, _t, _i in TABS]
        if key in keys:
            self.tabs.setCurrentIndex(keys.index(key))

    def unsaved_tabs(self) -> list[str]:
        titles = dict((k, t) for k, t, _i in TABS)
        return [titles[k] for k, page in self.pages.items()
                if hasattr(page, "has_unsaved_changes") and page.has_unsaved_changes()]
