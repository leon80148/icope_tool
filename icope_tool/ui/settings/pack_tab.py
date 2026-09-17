from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QVBoxLayout, QWidget

from icope_tool.paths import temp_output_dir
from icope_tool.services.pack import PackError, apply_pack, export_pack, read_pack, validate_pack_materials
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.importing import PackImportDialog
from icope_tool.ui.widgets import Banner, Card, Spinner, Toast, button, format_size, hbox, label
from icope_tool.ui.workers import run_in_background


class PackTab(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.toast = toast
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)

        export_card = Card("匯出設定包", "把轉介資源庫與衛教單張打包成一個 zip 檔，分享給其他診所，或當作備份。", "file-up")
        layout.addWidget(export_card)
        self.include_resources = QCheckBox()
        self.include_resources.setChecked(True)
        self.include_materials = QCheckBox()
        self.include_materials.setChecked(True)
        export_card.add(self.include_resources)
        export_card.add(self.include_materials)
        export_card.add(Banner("neutral", "不會包含",
                               "國健署系統帳號密碼、登入紀錄、診所名稱電話等資訊不會放進設定包。\n"
                               "院所衛教文字（各項衛教重點）儲存在共用資料夾，可供同院電腦使用；資源與單張設定包不包含這些文字，"
                               "匯入設定包也不會改到它。", icon_name="shield-check"))
        self.export_button = button("匯出…", "file-up", "primary", on_click=self.export)
        export_card.add(hbox(self.export_button, None))

        import_card = Card("匯入設定包", "匯入其他診所或先前備份的設定包；可以選擇合併或取代現有資料。", "file-down")
        layout.addWidget(import_card)
        self.import_button = button("選擇設定包…", "folder-open", "primary", on_click=self.choose_pack)
        import_card.add(hbox(self.import_button, label("支援 .zip 設定包，以及只含轉介資源的 .json", "caption"), None,
                             spacing=12))

        self.busy = QWidget()
        self.busy_text = label("", "subtle")
        self.busy.setLayout(hbox(Spinner(22), self.busy_text, None, spacing=10))
        self.busy.hide()
        layout.addWidget(self.busy)
        self.result = Banner("success")
        self.result.hide()
        layout.addWidget(self.result)
        layout.addStretch(1)

        self.include_resources.toggled.connect(self._update)
        self.include_materials.toggled.connect(self._update)
        ctx.resources_changed.connect(self._update)
        ctx.materials_changed.connect(self._update)
        self._update()

    def _update(self) -> None:
        try:
            resources = len(self.ctx.store.list_resources())
            materials = self.ctx.store.list_materials()
        except StoreError:
            resources, materials = 0, []
        size_text = f"約 {format_size(sum(m.size for m in materials))}"
        self.include_resources.setText(f"轉介資源庫（{resources} 筆）")
        self.include_materials.setText(f"衛教單張（{len(materials)} 份，{size_text}）" if materials
                                       else "衛教單張（尚未加入）")
        self.export_button.setEnabled(self.include_resources.isChecked() or self.include_materials.isChecked())

    def _set_busy(self, text: str | None) -> None:
        self.busy.setVisible(text is not None)
        self.busy_text.setText(text or "")
        for widget in (self.export_button, self.import_button):
            widget.setEnabled(text is None)
        if text is None:
            self._update()

    def export(self) -> None:
        default = Path.home() / "Documents" / f"ICOPE設定包_{datetime.now():%Y%m%d}.zip"
        target, _ = QFileDialog.getSaveFileName(self, "匯出設定包", str(default), "設定包 (*.zip)")
        if not target:
            return
        with_resources = self.include_resources.isChecked()
        with_materials = self.include_materials.isChecked()
        self.result.hide()
        self._set_busy("正在匯出…")

        def done(counts):
            self.result.set_content("success", "匯出完成",
                                    f"{Path(target).name}：轉介資源 {counts[0]} 筆、衛教單張 {counts[1]} 份。")
            self.result.show()
            self.ctx.audit.record("pack_export", resources=counts[0], materials=counts[1])

        def failed(exc):
            self.result.set_content("danger", "匯出失敗", str(exc))
            self.result.show()

        run_in_background(self, lambda: export_pack(self.ctx.store, Path(target), with_resources, with_materials),
                          done, failed, lambda: self._set_busy(None))

    def choose_pack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇設定包", str(Path.home()), "設定包 (*.zip *.json)")
        if not path:
            return
        self.result.hide()
        self._set_busy("正在讀取設定包…")

        def load():
            pack = read_pack(Path(path))
            validate_pack_materials(pack, temp_output_dir() / "pack-check")
            return pack

        def failed(exc):
            self.result.set_content("danger", "無法讀取設定包", str(exc) if isinstance(exc, PackError) else repr(exc))
            self.result.show()

        run_in_background(self, load, self._confirm_import, failed, lambda: self._set_busy(None))

    def _confirm_import(self, pack) -> None:
        dialog = PackImportDialog(self, self.ctx.store, pack)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        mode = dialog.mode
        self._set_busy("正在匯入…")

        def done(result):
            self.ctx.mark_saved("resources", "materials")
            verb = "取代為" if mode == "replace" else "新增"
            if mode == "replace" and not pack.material_count:
                material_text = "；衛教單張保留原本的內容"
            else:
                material_text = (f"；衛教單張{verb} {result.materials_added} 份"
                                 + (f"（略過重複 {result.materials_skipped} 份）" if result.materials_skipped else ""))
            text = (f"轉介資源{verb} {result.resources_added} 筆"
                    + (f"（略過重複 {result.resources_skipped} 筆）" if result.resources_skipped else "")
                    + material_text + "。")
            self.result.set_content("success", "匯入完成", text)
            self.result.show()
            self.ctx.audit.record("pack_import", mode=mode, resources=result.resources_added,
                                  materials=result.materials_added)
            self.toast.show_message("匯入完成")

        def failed(exc):
            self.ctx.mark_saved("resources", "materials")
            self.result.set_content("danger", "匯入失敗", str(exc))
            self.result.show()

        run_in_background(self, lambda: apply_pack(self.ctx.store, pack, mode), done, failed,
                          lambda: self._set_busy(None))
