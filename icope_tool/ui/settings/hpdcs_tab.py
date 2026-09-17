from __future__ import annotations

from datetime import date, datetime

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from icope_tool.services.hpdcs.plans import parse_custom_plans, plan_label, roc_year
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.base import field_label
from icope_tool.ui.dialogs.credentials import CredentialsDialog
from icope_tool.ui.query_page import open_hpdcs_site
from icope_tool.ui.widgets import Banner, Card, Toast, button, hbox, label, set_invalid, status_chip, clear_layout


class HpdcsTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        self._loaded = (True, True, "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)

        # ---- 帳號 ----
        account_card = Card("國健署系統帳號", "「成人預防保健暨慢性疾病防治資訊系統（hpdcs）」的登入帳號，用來查詢 ICOPE 紀錄。",
                            "key-round")
        layout.addWidget(account_card)
        self.status_grid = QGridLayout()
        self.status_grid.setHorizontalSpacing(16)
        self.status_grid.setVerticalSpacing(8)
        account_card.add(self.status_grid)
        self.disabled_banner = Banner(
            "danger", "自動登入已暫停",
            "上次登入國健署系統時帳號或密碼錯誤。為避免帳號被鎖，程式不會再自動重試；重新儲存正確的帳號密碼後就會恢復。")
        self.disabled_banner.hide()
        account_card.add(self.disabled_banner)
        self.edit_button = button("設定帳號密碼", "key-round", "primary", on_click=self.edit_credentials)
        self.clear_button = button("清除帳號密碼", "trash", "danger", on_click=self.clear_credentials)
        account_card.add(hbox(self.edit_button, button("開啟國健署網站", "external-link", "ghost",
                                                       on_click=open_hpdcs_site), None, self.clear_button, spacing=8))

        # ---- 計畫 ----
        plan_card = Card("要查詢的計畫", "國健署依年度與計畫分開登錄。每年 1 月 1 日會自動換成新年度，不必改設定。", "list-checks")
        layout.addWidget(plan_card)
        year = roc_year(date.today())
        self.official = QCheckBox(f"正式計畫（{plan_label(f'EFA_{year}')}，代碼 EFA_{year}）")
        self.pilot = QCheckBox(f"試辦計畫（{plan_label(f'EFA_Pilot_{year}')}，代碼 EFA_Pilot_{year}）")
        plan_card.add(self.official)
        plan_card.add(self.pilot)
        plan_card.add(label("只要任一計畫顯示已登錄，就會判定「今年已經做過」。沒有參與試辦計畫的診所可以取消勾選。",
                            "caption", wrap=True))
        self.advanced_toggle = button("進階：自訂計畫代碼", "chevron-right", "ghost", size="sm",
                                      on_click=self._toggle_advanced)
        plan_card.add(hbox(self.advanced_toggle, None))
        self.advanced = QWidget()
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(6)
        self.custom = QLineEdit()
        advanced_layout.addWidget(field_label("自訂計畫代碼", buddy=self.custom))
        self.custom.setPlaceholderText("例如：EFA_116, EFA_Pilot_116（留空＝使用上面的勾選）")
        self.custom.setAccessibleName("自訂計畫代碼")
        advanced_layout.addWidget(self.custom)
        self.custom_hint = label("只有國健署改了網址命名方式時才需要填寫；填了會取代上面的勾選。", "caption", wrap=True)
        advanced_layout.addWidget(self.custom_hint)
        self.advanced.hide()
        plan_card.add(self.advanced)
        self.plan_error = label("", "error")
        self.plan_error.hide()
        plan_card.add(self.plan_error)
        self.conflict = Banner("warning")
        self.conflict.hide()
        plan_card.add(self.conflict)
        self.dirty_label = label("", "caption")
        self.save_button = button("儲存", "save", "primary", on_click=self.save_plans)
        plan_card.add(hbox(self.dirty_label, None, self.save_button, spacing=8))
        layout.addStretch(1)

        self.official.toggled.connect(self._on_edit)
        self.pilot.toggled.connect(self._on_edit)
        self.custom.textChanged.connect(self._on_edit)
        ctx.hpdcs_changed.connect(self.refresh_status)
        ctx.settings_changed.connect(self._external_reload)
        ctx.data_dir_changed.connect(self.load_plans)
        self.load_plans()
        self.refresh_status()

    # ------------------------------------------------------------------ 帳號
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_status()

    def refresh_status(self) -> None:
        status = self.ctx.hpdcs.status()
        clear_layout(self.status_grid)
        rows = []
        if status["configured"]:
            rows.append(("帳號", status["account_masked"], None))
            if status["auto_login_disabled"]:
                rows.append(("登入狀態", "", status_chip("danger", "已暫停自動登入")))
            elif status["logged_in"]:
                when = status.get("last_login_at")
                text = f"已登入（{datetime.fromisoformat(when):%H:%M}）" if when else "已登入"
                rows.append(("登入狀態", "", status_chip("success", text)))
            else:
                rows.append(("登入狀態", "", status_chip("neutral", "查詢時自動登入")))
        else:
            rows.append(("帳號", "", status_chip("warning", "尚未設定")))
        rows.append(("驗證碼辨識", "", status_chip("success", "自動辨識") if status["ocr_available"]
                     else status_chip("warning", "需人工輸入")))
        for row, (title, text, chip) in enumerate(rows):
            self.status_grid.addWidget(label(title, "fieldLabel"), row, 0)
            self.status_grid.addWidget(chip if chip is not None else label(text), row, 1)
        self.status_grid.setColumnStretch(2, 1)
        self.disabled_banner.setVisible(status["auto_login_disabled"])
        configured = status["configured"]
        self.edit_button.setText("修改帳號密碼" if configured else "設定帳號密碼")
        self.clear_button.setVisible(configured)

    def edit_credentials(self) -> None:
        dialog = CredentialsDialog(self, self.ctx)
        dialog.exec()
        self.refresh_status()
        if dialog.saved:
            self.toast.show_message("已儲存國健署系統帳號")

    def clear_credentials(self) -> None:
        answer = QMessageBox.question(self, "清除帳號密碼？", "清除後就無法在程式裡查詢，直到重新設定。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.ctx.credentials.delete()
        self.ctx.hpdcs.invalidate()
        self.ctx.audit.record("hpdcs_credentials_deleted")
        self.ctx.mark_saved("hpdcs")
        self.refresh_status()
        self.toast.show_message("已清除國健署系統帳號")

    # ------------------------------------------------------------------ 計畫
    def _values(self) -> tuple[bool, bool, str]:
        return self.official.isChecked(), self.pilot.isChecked(), self.custom.text().strip()

    def load_plans(self) -> None:
        prefs = self.ctx.hpdcs_prefs()
        self._loaded = (prefs.official, prefs.pilot, ", ".join(prefs.custom_plans))
        for widget, value in ((self.official, prefs.official), (self.pilot, prefs.pilot)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)
        self.custom.blockSignals(True)
        self.custom.setText(self._loaded[2])
        self.custom.blockSignals(False)
        if self._loaded[2]:
            self.advanced.show()
        self.conflict.hide()
        self._on_edit()

    def _stored(self) -> tuple[bool, bool, str] | None:
        try:
            prefs = self.ctx.store.load_settings().hpdcs
        except StoreError:
            return None
        return (prefs.official, prefs.pilot, ", ".join(prefs.custom_plans))

    def _external_reload(self) -> None:
        if self._values() == self._loaded:
            self.load_plans()
            return
        stored = self._stored()
        if stored is not None and stored != self._loaded:
            self.conflict.set_content(
                "warning", "其他電腦剛更新了查詢計畫",
                "你的修改還沒儲存。可以載入最新設定（捨棄你的修改），或保留你的修改，儲存時會覆蓋對方的設定。",
                [button("載入最新設定", "refresh-cw", "primary", size="sm", on_click=self.load_plans),
                 button("保留我的修改", kind="secondary", size="sm", on_click=self.conflict.hide)])
            self.conflict.show()

    def _toggle_advanced(self) -> None:
        self.advanced.setVisible(not self.advanced.isVisible())

    def _validate(self) -> str:
        official, pilot, custom = self._values()
        good, bad = parse_custom_plans(custom)
        if bad:
            return f"看不懂的代碼：{'、'.join(bad)}（只能有英文、數字與底線）"
        if not good and not official and not pilot:
            return "至少要勾選一個計畫，否則無法查詢"
        return ""

    def _on_edit(self) -> None:
        dirty = self._values() != self._loaded
        problem = self._validate()
        self.plan_error.setText(problem)
        self.plan_error.setVisible(bool(problem))
        set_invalid(self.custom, problem.startswith("看不懂"))
        good, _ = parse_custom_plans(self.custom.text())
        self.official.setEnabled(not good)
        self.pilot.setEnabled(not good)
        self.save_button.setEnabled(dirty and not problem)
        self.dirty_label.setText("有尚未儲存的變更" if dirty else "")

    def save_plans(self) -> None:
        if self._validate():
            return
        official, pilot, custom = self._values()
        good, _ = parse_custom_plans(custom)
        stored = self._stored()
        if stored is not None and stored != self._loaded and stored != (official, pilot, ", ".join(good)):
            answer = QMessageBox.question(
                self, "查詢計畫已被其他電腦修改", "你編輯的期間，其他電腦已經儲存了不同的查詢計畫。要用你的設定覆蓋嗎？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return

        def mutate(settings):
            settings.hpdcs.official, settings.hpdcs.pilot, settings.hpdcs.custom_plans = official, pilot, good

        try:
            self.ctx.store.update_settings(mutate)
        except StoreError as exc:
            self.plan_error.setText(f"無法儲存：{exc}")
            self.plan_error.show()
            return
        self.ctx.hpdcs.clear_cache()
        self.conflict.hide()
        self._loaded = (official, pilot, ", ".join(good))
        self.custom.setText(", ".join(good))
        self._on_edit()
        self.ctx.mark_saved("settings", "hpdcs")
        self.toast.show_message("已儲存查詢計畫")

    def has_unsaved_changes(self) -> bool:
        return self._values() != self._loaded
