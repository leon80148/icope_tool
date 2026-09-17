"""設定 › 診所與轉介單：診所資訊，以及院所自己寫、印在轉介單上的各項衛教重點。

兩張卡片共用一個「儲存」，固定在捲動區外（內容變長也按得到）。儲存只改 clinic 與 sheet.domain_notes 兩塊
（update_settings：重讀 → 套用 → 原子寫入），不會蓋掉其他電腦剛改的其他設定。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout, QWidget,
)

from icope_tool.domains import DOMAIN_IDS, DOMAINS
from icope_tool.models import NOTE_MAX_CHARS, NOTE_MAX_LINES, Settings, normalize_note, note_problem
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.base import field_label
from icope_tool.ui.theme import C
from icope_tool.ui.widgets import (
    Banner, Card, Toast, button, hbox, label, recovery_actions, scroll_area, set_invalid, set_prop,
)

Values = tuple[str, str, str, str, tuple[tuple[str, str], ...]]


def _notes_key(notes: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """比對用：正規化後、依表單順序、去掉空白項目。編輯器、已儲存內容與衝突判斷都用同一種形式。"""
    cleaned = ((d, normalize_note(notes.get(d, ""))) for d in DOMAIN_IDS)
    return tuple((d, text) for d, text in cleaned if text)


def _stored_values(settings: Settings) -> Values:
    clinic = settings.clinic
    return (clinic.name, clinic.phone, clinic.address, clinic.footer_note, _notes_key(settings.sheet.domain_notes))


class ClinicTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        self._loaded: Values = ("", "", "", "", ())
        self._notes: dict[str, str] = {}          # 八項的編輯中草稿（原文）；每次打字就更新，不是切換項目時才存
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        self.error = Banner("danger")
        self.error.hide()
        layout.addWidget(self.error)
        self.conflict = Banner("warning")
        self.conflict.hide()
        layout.addWidget(self.conflict)

        content = QWidget()
        content.setMaximumWidth(980)
        cards = QVBoxLayout(content)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(16)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 8, 0)
        row.addWidget(content, 100)
        row.addStretch(1)
        self.scroll_view = scroll_area(holder, fade=C["bg"])
        layout.addWidget(self.scroll_view, 1)

        card = Card("診所資訊", "印在轉介衛教單的頁首與頁尾，讓長者知道有問題可以找誰。", "building")
        cards.addWidget(card)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：幸福診所")
        self.name.setAccessibleName("診所名稱")
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("例如：05-1234567")
        self.phone.setAccessibleName("診所電話")
        self.address = QLineEdit()
        self.address.setPlaceholderText("例如：幸福市中正路 100 號")
        self.address.setAccessibleName("診所地址")
        self.footer = QLineEdit()
        self.footer.setPlaceholderText("例如：本單僅供轉介參考，服務內容以各單位公告為準")
        self.footer.setAccessibleName("頁尾附註")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        grid.addWidget(field_label("診所名稱", required=True, buddy=self.name), 0, 0)
        grid.addWidget(field_label("電話", buddy=self.phone), 0, 1)
        grid.addWidget(self.name, 1, 0)
        grid.addWidget(self.phone, 1, 1)
        self.name_error = label("", "error")
        self.name_error.hide()
        grid.addWidget(self.name_error, 2, 0)
        grid.addWidget(field_label("地址", buddy=self.address), 3, 0, 1, 2)
        grid.addWidget(self.address, 4, 0, 1, 2)
        grid.addWidget(field_label("頁尾附註", buddy=self.footer), 5, 0, 1, 2)
        grid.addWidget(self.footer, 6, 0, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        card.add(grid)

        card.add(label("轉介單上會這樣顯示", "section"))
        self.preview = Banner("neutral", icon_name="file-text")
        card.add(self.preview)

        notes_card = Card("各項衛教重點（選填）",
                          "院所自己寫給長者與家屬的話，印在轉介單上該異常項目的標題下方；沒有填的項目不會印。", "notebook-pen")
        cards.addWidget(notes_card)
        self.note_domain = QComboBox()
        for domain in DOMAINS:
            self.note_domain.addItem(domain.title, domain.id)
        self.note_text = QPlainTextEdit()
        self.note_text.setFixedHeight(128)
        self.note_text.setPlaceholderText("例如：每天做腿部肌力與平衡運動；家裡走道保持明亮、移開雜物，預防跌倒。")
        self.note_count = label("", "caption")
        notes_card.add(field_label("異常項目", buddy=self.note_domain))
        notes_card.add(hbox(self.note_domain, None))
        notes_card.add(field_label("衛教重點", buddy=self.note_text))
        notes_card.add(self.note_text)
        notes_card.add(self.note_count)
        cards.addStretch(1)

        self.dirty_label = label("", "caption")
        self.revert_button = button("復原", kind="ghost", on_click=self.load)
        self.save_button = button("儲存", "save", "primary", on_click=self.save)
        layout.addLayout(hbox(self.dirty_label, None, self.revert_button, self.save_button, spacing=8,
                              margins=(0, 0, 8, 0)))

        for widget in (self.name, self.phone, self.address, self.footer):
            widget.textChanged.connect(self._on_edit)
            widget.returnPressed.connect(self.save)
        self.note_text.textChanged.connect(self._on_note_edited)
        self.note_domain.currentIndexChanged.connect(self._show_current_note)
        ctx.settings_changed.connect(self._external_reload)
        ctx.data_dir_changed.connect(self.load)
        self.load()

    # ------------------------------------------------------------------ 讀取
    def _values(self) -> Values:
        return (self.name.text().strip(), self.phone.text().strip(), self.address.text().strip(),
                self.footer.text().strip(), _notes_key(self._notes))

    def _check_folder(self) -> None:
        self.ctx.navigate.emit("settings", "local")

    def _stored(self) -> Values | None:
        try:
            return _stored_values(self.ctx.store.load_settings())
        except StoreError:
            return None

    def load(self) -> None:
        try:
            settings = self.ctx.store.load_settings()
            self.error.hide()
        except StoreError as exc:
            self.error.set_content("danger", "無法讀取診所資訊", f"{exc}。資料夾恢復後按「重試」。",
                                   recovery_actions(self.load, self._check_folder))
            self.error.show()
            return
        self.conflict.hide()
        self._adopt(settings)

    def _adopt(self, settings: Settings) -> None:
        """畫面改成這份已儲存的內容（載入與復原）。儲存成功時只更新 _loaded，不改寫正在編輯的文字框。"""
        self._loaded = _stored_values(settings)
        for widget, value in zip((self.name, self.phone, self.address, self.footer), self._loaded[:4]):
            widget.blockSignals(True)
            widget.setText(value)
            widget.blockSignals(False)
        self._notes = dict(settings.sheet.domain_notes)
        self._show_current_note()
        self._on_edit()

    def _external_reload(self) -> None:
        if self._values() == self._loaded:
            self.load()
            return
        stored = self._stored()
        if stored is not None and stored != self._loaded:
            self.conflict.set_content(
                "warning", "其他電腦剛更新了診所資訊或衛教重點",
                "你在這台電腦的修改還沒儲存。可以載入最新內容（捨棄你的修改），或保留你的修改，儲存時會覆蓋對方的內容。",
                [button("載入最新內容", "refresh-cw", "primary", size="sm", on_click=self.load),
                 button("保留我的修改", kind="secondary", size="sm", on_click=self.conflict.hide)])
            self.conflict.show()

    # ------------------------------------------------------------------ 衛教重點
    def _show_current_note(self, *_args) -> None:
        """把目前項目的草稿放進文字框。過程中不能觸發「使用者打字」，否則會把文字寫到別的項目。"""
        self.note_text.blockSignals(True)
        self.note_text.setPlainText(self._notes.get(self.note_domain.currentData(), ""))
        self.note_text.blockSignals(False)
        self._refresh_notes_view()

    def _on_note_edited(self) -> None:
        self._notes[self.note_domain.currentData()] = self.note_text.toPlainText()
        self._refresh_notes_view()
        self._on_edit()

    def _refresh_notes_view(self) -> None:
        self.note_domain.blockSignals(True)
        for index, domain in enumerate(DOMAINS):
            filled = bool(normalize_note(self._notes.get(domain.id, "")))
            self.note_domain.setItemText(index, domain.title + ("（已填）" if filled else ""))
        self.note_domain.blockSignals(False)
        text = normalize_note(self.note_text.toPlainText())          # 只拿來計數，不改寫文字框（游標不會跳）
        problem = note_problem(text)
        lines = text.count("\n") + 1 if text else 0
        counts = f"{len(text.replace(chr(10), ''))} / {NOTE_MAX_CHARS} 字，{lines} / {NOTE_MAX_LINES} 行"
        self.note_count.setText(f"{counts}　{problem}，請縮短後才能儲存" if problem else counts)
        set_prop(self.note_count, "role", "error" if problem else "caption")

    # ------------------------------------------------------------------ 編輯與儲存
    def _on_edit(self) -> None:
        name, phone, address, footer, _notes = self._values()
        dirty = self._values() != self._loaded
        self.save_button.setEnabled(dirty)       # 有變更就能按；不能存的原因按下去會標出來，不默默停用
        self.revert_button.setVisible(dirty)
        self.dirty_label.setText("有尚未儲存的變更" if dirty else "")
        if name:
            set_invalid(self.name, False)
            self.name_error.hide()
        head = name or "（診所名稱）"
        contact = "　".join(p for p in (name, f"電話 {phone}" if phone else "", address) if p)
        lines = []
        if phone or address:
            lines.append("標題下方：有問題請聯絡本院　" + "　".join(
                p for p in (f"電話 {phone}" if phone else "", f"地址 {address}" if address else "") if p))
        lines.append(f"頁尾：{contact or '（未填寫）'}" + (f"\n　　　{footer}" if footer else ""))
        self.preview.set_content("neutral", f"頁首：{head}　長者功能評估（ICOPE）轉介與衛教單", "\n".join(lines),
                                 icon_name="file-text")

    def save(self) -> None:
        name, phone, address, footer, notes = self._values()
        if not name:
            set_invalid(self.name, True)
            self.name_error.setText("請填寫診所名稱")
            self.name_error.show()
            self.scroll_view.ensureWidgetVisible(self.name)
            self.name.setFocus()
            return
        over = next((d for d, text in notes if note_problem(text)), None)
        if over is not None:
            self.note_domain.setCurrentIndex(self.note_domain.findData(over))
            self.scroll_view.ensureWidgetVisible(self.note_count)
            self.note_text.setFocus()
            return

        stored = self._stored()
        if stored is not None and stored != self._loaded and stored != self._values():
            answer = QMessageBox.question(
                self, "診所資訊已被其他電腦修改", "你編輯的期間，其他電腦已經儲存了不同的診所資訊或衛教重點。要用你的內容覆蓋嗎？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return

        def mutate(settings):
            settings.clinic.name, settings.clinic.phone = name, phone
            settings.clinic.address, settings.clinic.footer_note = address, footer
            settings.sheet.domain_notes = dict(notes)

        try:
            saved = self.ctx.store.update_settings(mutate)
        except StoreError as exc:
            self.error.set_content("danger", "無法儲存診所資訊", f"{exc}。你輸入的內容還在，資料夾恢復後可以再按一次儲存。",
                                   recovery_actions(self.save, self._check_folder))
            self.error.show()
            return
        self.error.hide()
        self.conflict.hide()
        self._loaded = _stored_values(saved)     # 以實際存下來的結果為準：不會一存完又顯示「尚未儲存」
        self._on_edit()
        self._refresh_notes_view()
        self.ctx.mark_saved("settings")
        self.toast.show_message("已儲存診所資訊與衛教重點")

    def has_unsaved_changes(self) -> bool:
        return self._values() != self._loaded
