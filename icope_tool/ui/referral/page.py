"""轉介衛教單頁：四個步驟＋右側摘要。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from icope_tool.domains import DOMAIN_BY_ID
from icope_tool.idcheck import mask_person_id
from icope_tool.models import Material, Resource
from icope_tool.services.pdf.referral_sheet import ReferralJob, ReferralSection
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.leaflet_print import LeafletPrintDialog
from icope_tool.ui.dialogs.resource_picker import ResourcePickerDialog
from icope_tool.ui.referral.confirm import ConfirmStep
from icope_tool.ui.referral.state import ReferralState, ordered_resources_for_domain
from icope_tool.ui.referral.steps import DomainStep, MaterialStep, ResourceStep, StepIndicator, SummaryPanel
from icope_tool.ui.widgets import Banner, PageHeader, Toast, button, hbox


class ReferralPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.state = ReferralState(assessor=ctx.local.last_assessor)
        self.resources: list[Resource] = []
        self.materials: list[Material] = []
        self.current = 0
        self._resources_dirty = True
        self._data_ok = False         # 資源與單張是否來自同一次成功的讀取（否則不能拿來帶入院所預設）
        self._clinic_name = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 18)
        root.setSpacing(14)
        header = PageHeader("轉介衛教單", "勾選需要關注的項目，選好轉介資源與衛教單張，一次印出給長者帶回家。")
        self.leaflet_button = button("只印衛教單張…", "book-open", "secondary",
                                     tooltip="只列印勾選的衛教 PDF，不加轉介單首頁；不會動到製作中的轉介單",
                                     on_click=self.open_leaflet_dialog)
        header.action_bar.addWidget(self.leaflet_button)
        self._leaflet_dialog: LeafletPrintDialog | None = None
        root.addWidget(header)

        self.load_error = Banner("danger")
        self.load_error.hide()
        root.addWidget(self.load_error)

        self.patient_banner = Banner("warning", icon_name="user")
        self.patient_banner.hide()
        root.addWidget(self.patient_banner)

        self.stepper = StepIndicator()
        self.stepper.step_clicked.connect(self.go_to)
        root.addWidget(self.stepper)

        middle = QHBoxLayout()
        middle.setSpacing(16)
        root.addLayout(middle, 1)
        self.stack = QStackedWidget()
        middle.addWidget(self.stack, 1)

        self.domain_step = DomainStep(self.state)
        self.resource_step = ResourceStep(self.state)
        self.material_step = MaterialStep(self.state)
        self.confirm_step = ConfirmStep(self.state, self._build_job, lambda: self.ctx.local.recent_assessors,
                                        self._card_name)
        self.confirm_step.bind_patient_label(self._patient_caption)
        for step in (self.domain_step, self.resource_step, self.material_step, self.confirm_step):
            self.stack.addWidget(step)

        self.summary = SummaryPanel()
        self.summary.reset_requested.connect(self.confirm_reset)
        self.summary.jump.connect(self.go_to)
        self.summary.apply_defaults_requested.connect(self.apply_defaults)
        self.summary.go_settings.connect(lambda: ctx.navigate.emit("settings", "resources"))
        middle.addWidget(self.summary)

        self.back_button = button("上一步", "chevron-left", "secondary", "lg", tooltip="Alt+←", on_click=self.back)
        self.next_button = button("下一步", "chevron-right", "primary", "lg", tooltip="Alt+→", on_click=self.next)
        self.next_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        # 位置與樣式固定（下一步＝主要、捷徑＝次要），不隨內容變換強調：內容已齊時可以直接跳到確認頁，只導覽、不送印
        self.confirm_shortcut = button("確認與列印", "printer", "secondary", "lg",
                                       tooltip="直接前往最後一步，預覽後再列印", on_click=lambda: self.go_to(3))
        root.addLayout(hbox(self.back_button, None, self.next_button, self.confirm_shortcut, spacing=8))

        self.toast = Toast(self)

        self.domain_step.changed.connect(self._on_changed)
        self.resource_step.changed.connect(self._on_changed)
        self.resource_step.set_picker(self._open_picker)
        self.resource_step.go_settings.connect(lambda: ctx.navigate.emit("settings", "resources"))
        self.resource_step.go_back.connect(self.back)
        self.resource_step.leaflets_requested.connect(self.open_leaflet_dialog)
        self.material_step.changed.connect(self._on_changed)
        self.material_step.go_settings.connect(lambda: ctx.navigate.emit("settings", "materials"))
        self.material_step.preview_requested.connect(self._preview_material)
        self.confirm_step.changed.connect(self._on_form_changed)
        self.confirm_step.go_settings.connect(lambda tab: ctx.navigate.emit("settings", tab))
        self.confirm_step.reset_requested.connect(self.reset)
        self.confirm_step.finished_job.connect(self._on_finished)

        QShortcut(QKeySequence("Alt+Right"), self, activated=self.next)
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.back)
        QShortcut(QKeySequence.StandardKey.Print, self, activated=self.print_now)

        ctx.resources_changed.connect(self.reload_data)
        ctx.materials_changed.connect(self.reload_data)
        ctx.settings_changed.connect(self._refresh_confirm)
        ctx.data_dir_changed.connect(self.reload_data)
        ctx.patient_changed.connect(self._on_patient)
        self.reload_data()
        self.go_to(0)

    # ------------------------------------------------------------------ 資料
    def reload_data(self) -> None:
        try:
            resources = self.ctx.store.list_resources()
            materials = self.ctx.store.list_materials()
        except StoreError as exc:
            # 兩份清單要嘛一起換新、要嘛都留著上一份：新資源配舊單張不能拿來帶入院所預設
            self._data_ok = False
            self.load_error.set_content("danger", "無法讀取轉介資源或衛教單張",
                                        f"{exc}。已勾選的內容仍保留；資料夾恢復後按「重試」。",
                                        [button("重試", "refresh-cw", "primary", size="sm", on_click=self.reload_data),
                                         button("檢查資料存放位置", "settings", "secondary", size="sm",
                                                on_click=lambda: self.ctx.navigate.emit("settings", "local"))])
            self.load_error.show()
            self._refresh_defaults()
            return
        self.resources, self.materials, self._data_ok = resources, materials, True
        self.load_error.hide()
        self._load_clinic_name()
        before = (len(self.state.unique_resource_ids()), len(self.state.materials))
        if self.state.prune(self.resources, self.materials):
            after = (len(self.state.unique_resource_ids()), len(self.state.materials))
            removed = (before[0] - after[0]) + (before[1] - after[1])
            if removed > 0:
                self.toast.show_message(f"有 {removed} 個已選的資源或單張已被刪除或停用，已從這份轉介單移除")
        # 資源的名稱、電話或單張檔案可能改了：既有 PDF 一律視為過期
        self.confirm_step.mark_stale()
        self.summary.invalidate()
        self.domain_step.set_counts(self.resources)
        self.resource_step.resources = self.resources
        self._resources_dirty = True
        if self.current == 1:
            self.resource_step.rebuild()
            self._resources_dirty = False
        self.material_step.set_materials(self.materials)
        self._refresh_all()

    def _build_job(self) -> tuple[ReferralJob, dict[str, Path]]:
        resources = {r.id: r for r in self.ctx.store.list_resources() if r.enabled}
        settings = self.ctx.store.load_settings()
        sections = [ReferralSection(d, ordered_resources_for_domain(self.state, d, resources))
                    for d in self.state.domains]
        # 單張只讀一次：順序、是否啟用與檔案對照都來自同一份清單
        chosen = [m for m in self.ctx.store.list_materials() if m.enabled and m.id in self.state.materials]
        files = {m.id: self.ctx.store.material_file(m) for m in chosen}
        job = ReferralJob(clinic=settings.clinic, sections=sections, materials=chosen,
                          patient_name=self.state.patient_name, assessor=self.state.assessor, notes=self.state.notes,
                          domain_notes=dict(settings.sheet.domain_notes))      # 這份工作專用的複本
        return job, files

    # ------------------------------------------------------------------ 導覽
    def go_to(self, index: int) -> None:
        index = max(0, min(3, index))
        self.current = index
        if index == 1 and self._resources_dirty:
            self.resource_step.rebuild()
            self._resources_dirty = False
        elif index == 2:
            self.material_step.rebuild()
        self.stack.setCurrentIndex(index)
        self.summary.setVisible(index != 3)
        self._refresh_all()
        if index == 3:
            self.confirm_step.ensure_preview()

    def print_now(self) -> None:
        if not self.state.can_print():
            self.toast.show_message("請先勾選異常項目或衛教單張")
            return
        self.go_to(3)
        if self.confirm_step.notes_over:
            self.toast.show_message("給長者的叮嚀超過字數，請先縮短再列印")
            self.confirm_step.notes.setFocus()
            return
        self.confirm_step.request_print()

    def has_unfinished_work(self) -> bool:
        return self.confirm_step.has_unfinished_work()

    def next(self) -> None:
        if self.current < 3:
            self.go_to(self.current + 1)

    def back(self) -> None:
        if self.current > 0:
            self.go_to(self.current - 1)

    # ------------------------------------------------------------------ 更新
    def _on_changed(self) -> None:
        self.confirm_step.mark_stale()
        if self.current == 0:
            self._resources_dirty = True
        self._refresh_all()

    def _on_form_changed(self) -> None:
        self.confirm_step.mark_stale()
        self._update_stepper()
        self.summary.update_view(self.state, {r.id: r for r in self.resources if r.enabled},
                                 {m.id: m for m in self.materials})

    def _refresh_confirm(self) -> None:
        self._load_clinic_name()
        self.confirm_step.mark_stale()
        self._refresh_all()

    def _load_clinic_name(self) -> None:
        try:
            self._clinic_name = self.ctx.store.load_settings().clinic.name
        except StoreError:
            self._clinic_name = ""

    def _update_stepper(self) -> None:
        state = self.state
        missing = state.domains_without_resources()
        resources_count = len(state.unique_resource_ids())
        details = [
            f"已選 {len(state.domains)} 項" if state.domains else "尚未勾選",
            (f"已選 {resources_count} 個" + (f"，{len(missing)} 項未選" if missing else ""))
            if state.domains else "需先勾選項目",
            f"已選 {len(state.materials)} 份" if state.materials else ("可略過" if self.materials else "尚無單張"),
            ("叮嚀超過字數" if self.confirm_step.notes_over else "可以列印") if state.can_print() else "尚未完成",
        ]
        done = [bool(state.domains), bool(state.domains) and not missing, bool(state.materials), False]
        self.stepper.update_state(self.current, done, details)

    def _refresh_all(self) -> None:
        state = self.state
        self._update_stepper()
        self.domain_step.sync()
        if self.current == 1:
            self.resource_step.sync_rows()
        else:
            self.resource_step.refresh_tabs()
        self.summary.update_view(state, {r.id: r for r in self.resources if r.enabled},
                                 {m.id: m for m in self.materials})
        self._refresh_defaults()
        self.confirm_step.sync_from_state(self._clinic_name)

        self.back_button.setVisible(self.current > 0)
        self.confirm_shortcut.setVisible(self.current < 2)       # 第三步的下一步本來就是確認與列印
        self.confirm_shortcut.setEnabled(state.can_print())
        titles = ["下一步：選轉介資源", "下一步：選衛教單張", "下一步：確認與列印"]
        self.next_button.setVisible(self.current < 3)
        if self.current < 3:
            self.next_button.setText(titles[self.current])
            if self.current == 0:
                self.next_button.setEnabled(bool(state.domains) or bool(self.materials))
                self.next_button.setToolTip("先勾選至少一個需要關注的項目（只印衛教單張可以直接按下一步）"
                                            if not state.domains else "Alt+→")
            else:
                self.next_button.setEnabled(True)
                self.next_button.setToolTip("Alt+→")

    # ------------------------------------------------------------------ 院所預設
    def _refresh_defaults(self) -> None:
        state = self.state
        pending = state.pending_default_domains(self.resources, self.materials)
        applied = [d for d in state.domains if d in state.defaults_applied]
        without = [d for d in state.domains if d not in pending and d not in state.defaults_applied]
        clinic_has_defaults = (any(r.enabled and r.include_by_default and r.domains for r in self.resources)
                               or any(m.enabled and m.include_by_default and m.domains for m in self.materials))
        self.summary.update_defaults(pending, applied, without, clinic_has_defaults, bool(state.domains),
                                     available=self._data_ok)

    def apply_defaults(self) -> None:
        """使用者按「帶入院所預設」。用的是畫面上這份（同一次讀取的）清單，和各步驟看到的一致。"""
        if not self._data_ok:
            return
        result = self.state.apply_defaults(self.resources, self.materials)
        if not result.domains:
            return                     # 沒有待帶入的項目：草稿與 PDF 都不動
        if self.current == 2:
            self.material_step.rebuild()          # 重建列來同步勾選；不能切換核取方塊（那算人工勾選）
        self._resources_dirty = True
        self._on_changed()
        parts = [text for count, text in ((result.resources, f"{result.resources} 個轉介資源"),
                                          (result.materials, f"{result.materials} 份衛教單張")) if count]
        self.toast.show_message("已帶入 " + "、".join(parts) if parts else "沒有新增內容，原本就選好了")

    # ------------------------------------------------------------------ 只印衛教單張
    def open_leaflet_dialog(self) -> None:
        """獨立的小工作：有自己的勾選，不讀寫這份轉介單。用 open()（不阻塞），結果在 finished 時取一次。"""
        if self._leaflet_dialog is not None:
            self._leaflet_dialog.raise_()
            self._leaflet_dialog.activateWindow()
            return
        dialog = LeafletPrintDialog(self, self.ctx)
        dialog.finished.connect(lambda _code: self._leaflet_dialog_closed(dialog))
        self._leaflet_dialog = dialog
        dialog.open()

    def _leaflet_dialog_closed(self, dialog: LeafletPrintDialog) -> None:
        self._leaflet_dialog = None           # 對話框自己決定何時銷毀（可能還在等背景工作回來清暫存檔）
        if dialog.summary is not None:
            count, pages = dialog.summary
            self.toast.show_message(f"已依列印設定送出；所選文件共 {count} 份、{pages} 頁")
        if dialog.go_settings:
            self.ctx.navigate.emit("settings", "materials")

    # ------------------------------------------------------------------ 動作
    def _open_picker(self, domain_id: str) -> None:
        dialog = ResourcePickerDialog(self, self.resources, domain_id, self.state.resources.get(domain_id, []))
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_ids:
            added = self.state.add_to_domain(dialog.selected_ids, domain_id)
            self.resource_step.rebuild()
            self._on_changed()
            self.toast.show_message(f"已加入 {added} 個資源到「{DOMAIN_BY_ID[domain_id].title}」")

    def _preview_material(self, material: Material) -> None:
        path = self.ctx.store.material_file(material)
        if not path.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.toast.show_message("無法開啟這份 PDF（檔案不存在，或電腦沒有 PDF 閱讀器）")

    def _card_name(self) -> str:
        patient = self.ctx.current_patient
        if patient and patient.pid == self.state.patient_pid:
            return patient.name
        return ""

    def _patient_caption(self) -> str:
        if not self.state.patient_pid:
            return ""
        return f"身分證 {mask_person_id(self.state.patient_pid)}（用來核對長者，不會印在單上）"

    def _on_patient(self) -> None:
        """讀到卡或從查詢結果帶入長者。轉介單已有其他長者的內容時，先問清楚，不默默混用。"""
        patient = self.ctx.current_patient
        if patient is None:
            return
        if patient.pid == self.state.patient_pid:
            if patient.name and not self.state.patient_name:
                self.state.patient_name = patient.name
                self.confirm_step.mark_stale()
                self._refresh_all()
            self.patient_banner.hide()
            return
        if not self.state.has_selections() and not self.state.patient_name:
            self._bind_patient(keep_selections=True)
            return
        who_new = patient.name or mask_person_id(patient.pid)
        who_old = self.state.patient_name or (mask_person_id(self.state.patient_pid) if self.state.patient_pid
                                              else "未填姓名的長者")
        action_new = "讀到健保卡" if patient.source == "card" else "從查詢結果帶入"
        self.patient_banner.set_content(
            "warning", f"要改成 {who_new} 的轉介單嗎？",
            f"剛才{action_new}的長者是 {who_new}（{mask_person_id(patient.pid)}），"
            f"但這份轉介單目前是 {who_old} 的內容。為避免把兩位長者的資料混在一起，請先選擇：",
            [button(f"清除，開始 {who_new} 的轉介單", "rotate-ccw", "primary", size="sm",
                    on_click=lambda: self._bind_patient(keep_selections=False)),
             button("保留勾選，只換成這位長者", kind="secondary", size="sm",
                    on_click=lambda: self._bind_patient(keep_selections=True)),
             button(f"繼續編輯 {who_old} 的", kind="ghost", size="sm", on_click=self.patient_banner.hide)],
            icon_name="user")
        self.patient_banner.show()

    def _bind_patient(self, keep_selections: bool) -> None:
        patient = self.ctx.current_patient
        if patient is None:
            return
        if not keep_selections:
            self.state.reset(keep_assessor=True)
            self.confirm_step.release_document()
            self.go_to(0)
        self.state.parked.clear()      # 取消勾選暫存的資源只屬於上一位長者，不能帶到新長者
        self.state.patient_pid = patient.pid
        self.state.patient_name = patient.name
        self.patient_banner.hide()
        self.confirm_step.mark_stale()
        self.summary.invalidate()
        self._refresh_all()
        if not keep_selections:
            self.toast.show_message(f"已開始 {patient.name or mask_person_id(patient.pid)} 的轉介單")

    def _on_finished(self, kind: str) -> None:
        assessor = self.state.assessor
        if assessor:
            recent = [assessor, *[a for a in self.ctx.local.recent_assessors if a != assessor]][:8]
            if not self.ctx.update_local(last_assessor=assessor, recent_assessors=recent):
                self.toast.show_message("無法記住評估人員名單（這台電腦的設定檔無法寫入），下次開啟需要重新輸入")
        self.ctx.audit.record("referral_pdf", action=kind, domains=len(self.state.domains),
                              resources=len(self.state.unique_resource_ids()), materials=len(self.state.materials))

    def confirm_reset(self) -> None:
        if self.state.is_empty():
            return
        answer = QMessageBox.question(self, "清除這份轉介單？", "會清除目前勾選的項目、資源、單張與輸入的姓名、叮嚀。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                      QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.reset()

    def reset(self) -> None:
        self.state.reset(keep_assessor=True)
        self.patient_banner.hide()
        self.confirm_step.release_document()
        self.reload_data()
        self.go_to(0)
        self.toast.show_message("已清除，可以開始下一位長者")
