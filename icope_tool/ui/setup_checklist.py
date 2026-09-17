"""首次使用的設定清單：四項設定的完成狀態與直接前往的按鈕。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.messages import local_save_failed
from icope_tool.ui.theme import C, pixmap
from icope_tool.ui.widgets import Card, button, label

# 每一項寫清楚「做什麼用」：只印單張的院所不必設帳號，只查詢的院所不必加單張
ITEMS = [
    ("hpdcs", "國健署系統帳號", "只有篩檢查詢需要", "設定帳號"),
    ("clinic", "診所資訊", "印在轉介單頁首，讓長者知道找誰", "填寫"),
    ("resources", "轉介資源", "轉介單上要列的服務單位", "匯入或新增"),
    ("materials", "衛教單張", "附在轉介單後面，或單獨列印", "加入 PDF"),
]


def setup_progress(ctx: AppContext) -> dict[str, bool]:
    try:
        clinic = bool(ctx.store.load_settings().clinic.name)
        resources = len(ctx.store.list_resources()) > 1        # 預設只有「本院門診追蹤」
        materials = bool(ctx.store.list_materials())
    except StoreError:
        clinic = resources = materials = False
    return {"hpdcs": ctx.credentials.is_configured(), "clinic": clinic,
            "resources": resources, "materials": materials}


class SetupChecklist(Card):
    dismissed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__("依需要完成設定", "用得到的再設定就好：只印衛教單張不需要帳號；常用的資源與單張可以設為「院所預設」，"
                         "製作轉介單時一鍵帶入。", "list-checks", parent)
        self.ctx = ctx
        assert self.header_actions is not None
        hide = button("先隱藏", kind="ghost", size="sm", tooltip="隱藏這張清單（之後可以在說明中找到設定步驟）",
                      on_click=self._hide)
        self.header_actions.addWidget(hide, 0, Qt.AlignmentFlag.AlignTop)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.add(self.grid)
        self.tiles: dict[str, tuple[QLabel, QLabel, QWidget]] = {}
        for index, (key, title, desc, action) in enumerate(ITEMS):
            tile = QWidget()
            tile.setObjectName("SetupTile")
            row = QHBoxLayout(tile)
            row.setContentsMargins(12, 10, 12, 10)
            row.setSpacing(10)
            mark = QLabel()
            mark.setFixedSize(24, 24)
            row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(1)
            name = label(f"{index + 1}. {title}", "fieldLabel")
            texts.addWidget(name)
            status = label(desc, "caption")
            texts.addWidget(status)
            row.addLayout(texts, 1)
            go = button(action, kind="secondary", size="sm",
                        on_click=lambda _=False, k=key: self.ctx.navigate.emit("settings", k))
            go.setAccessibleName(f"{title}：{action}")
            row.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
            self.tiles[key] = (mark, status, go)
            self.grid.addWidget(tile, index // 2, index % 2)
        self.setStyleSheet(f"QWidget#SetupTile {{ background: {C['surface_alt']}; border: 1px solid {C['border']};"
                           " border-radius: 10px; }")
        self.refresh()

    def refresh(self) -> bool:
        """更新狀態；回傳是否還需要顯示。"""
        progress = setup_progress(self.ctx)
        done = sum(progress.values())
        for key, _title, desc, _action in ITEMS:
            mark, status, go = self.tiles[key]
            if progress[key]:
                mark.setPixmap(pixmap("circle-check", C["success"], 22))
                status.setText("已完成")
                go.setVisible(False)
            else:
                mark.setPixmap(pixmap("circle-dot", C["warning"], 22))
                status.setText(desc)
                go.setVisible(True)
        return done < 4 and not self.ctx.local.hide_setup_checklist

    def _hide(self) -> None:
        if not self.ctx.update_local(hide_setup_checklist=True):
            QMessageBox.warning(self, "無法記住這個選擇", local_save_failed(self.ctx.local_store.path))
        self.hide()
        self.dismissed.emit()
