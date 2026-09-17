"""只印衛教單張：獨立的小工作，有自己的勾選，不讀寫製作中的轉介單。

輸出的三個入口（列印、重試列印、用 PDF 閱讀器開啟）走同一套保護：
- 每次請求綁定一份不可變的工作快照（資料夾、單張的 id／檔名／登錄雜湊、清單順序）與遞增的請求編號
- 合併在背景做，合併用的就是驗過雜湊的那份內容；重試與外部開啟前，再到背景重讀來源比對內容
  （檔案長度與修改時間相同不代表內容相同）
- _busy 涵蓋「背景工作 → 檢查 → 系統列印視窗」整段，背景工作回來（on_finished）不會解除它，
  所以列印視窗開著時收到的資料更新與重複點擊都不會重入
- 關閉（取消、Esc、X）讓請求作廢；還有背景工作沒回來時先不銷毀，等它回來清掉自己的暫存檔
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from icope_tool.models import Material
from icope_tool.paths import temp_output_dir
from icope_tool.services.pdf.referral_sheet import (
    LeafletSource, MaterialChanged, MergedLeaflets, merge_material_pdfs, verify_material_sources,
)
from icope_tool.store import StoreError
from icope_tool.ui.context import AppContext
from icope_tool.ui.dialogs.base import BaseDialog
from icope_tool.ui.printing import PrintError, print_pdf
from icope_tool.ui.referral.steps import MaterialRow, new_list_container
from icope_tool.ui.widgets import EmptyState, button, label
from icope_tool.ui.workers import run_in_background

CHANGED_TEXT = "單張剛被更新，請再按一次列印。"


def open_in_viewer(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


@dataclass(frozen=True)
class _Job:
    token: int
    root: str
    sources: tuple[LeafletSource, ...]
    output: Path


class LeafletPrintDialog(BaseDialog):
    def __init__(self, parent: QWidget | None, ctx: AppContext,
                 printer: Callable[[QWidget, Path], bool] | None = None,
                 opener: Callable[[Path], bool] | None = None):
        super().__init__(parent, "只印衛教單張", "只列印勾選的 PDF，不加轉介單首頁；製作中的轉介單不受影響。",
                         "book-open", width=640)
        self.resize(700, 560)
        self.ctx = ctx
        self._printer = printer or print_pdf
        self._opener = opener or open_in_viewer
        self.rows: list[MaterialRow] = []
        self._materials: list[Material] = []
        self._checked: set[str] = set()
        self._request = 0                 # 每個輸出請求 +1；關閉時也 +1，還在路上的回呼一律作廢
        self._busy = False
        self._workers = 0                 # 還沒回來的背景工作（和 _busy 分開算：關閉後要等它們回來才能銷毀）
        self._printing = False
        self._closed = False
        self._refresh_pending = False
        self._merged: tuple[_Job, MergedLeaflets] | None = None     # 送印失敗後留給「重試列印／用閱讀器開啟」
        self._handed_over: set[Path] = set()                         # 已交給外部閱讀器：留給一天清理，不自己刪
        self.summary: tuple[int, int] | None = None                  # （份數, 頁數）：送印成功才有
        self.go_settings = False

        self.list_area = QScrollArea()
        self.list_area.setWidgetResizable(True)
        self.list_area.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_layout = new_list_container(self.list_area)
        self.body.addWidget(self.list_area, 1)
        self.empty = EmptyState("book-open", "還沒有衛教單張", "到設定把要給長者的衛教 PDF 加進來，就能在這裡勾選列印。",
                                button("到設定加入 PDF", "upload", "primary", on_click=self._go_settings))
        self.empty.hide()
        self.body.addWidget(self.empty, 1)

        self.count_label = label("", "subtle")
        self.buttons.insertWidget(0, self.count_label)
        self.cancel_button = self.add_cancel()
        self.print_button = self.add_button(button("列印", "printer", "primary", on_click=self.print_selected))
        self.print_button.setDefault(True)

        ctx.materials_changed.connect(self._on_materials_changed)
        self.reload()

    # ------------------------------------------------------------------ 清單
    def reload(self) -> None:
        try:
            materials = self.ctx.store.list_materials()
        except StoreError as exc:
            # 讀不到不等於沒有單張：清單與勾選都留著
            self._show_message("danger", "無法讀取衛教單張", f"{exc}。資料夾恢復後再試一次。")
            return
        self._apply(materials)

    def _apply(self, materials: list[Material]) -> None:
        if self._merged is not None and not self._matches(self._merged[0], materials):
            self._withdraw_recovery(reload=False)
        self._materials = [m for m in materials if m.enabled]
        self._checked &= {m.id for m in self._materials}
        self.rows_layout = new_list_container(self.list_area)
        self.rows = []
        for material in self._materials:
            row = MaterialRow(material, [], material.id in self._checked)
            row.toggled.connect(self._on_toggle)
            row.preview.connect(self._preview)
            self.rows_layout.addWidget(row)
            self.rows.append(row)
        self.rows_layout.addStretch(1)
        self.list_area.setVisible(bool(self._materials))
        self.empty.setVisible(not self._materials)
        self._update_footer()

    def _on_materials_changed(self) -> None:
        if self._busy:
            self._refresh_pending = True      # 忙碌時不重建清單；回到閒置再補
        else:
            self.reload()

    def _on_toggle(self, material_id: str, checked: bool) -> None:
        if checked:
            self._checked.add(material_id)
        else:
            self._checked.discard(material_id)
        self._discard_merged()                # 舊的合併檔不能拿來輸出另一組勾選
        self.clear_error()
        self._update_footer()

    def _update_footer(self) -> None:
        chosen = [m for m in self._materials if m.id in self._checked]
        pages = sum(m.pages for m in chosen)
        self.count_label.setText(f"已選 {len(chosen)} 份，共 {pages} 頁" if chosen else "勾選要列印的單張")
        self.print_button.setText("正在準備…" if self._busy else "列印")
        self.print_button.setEnabled(bool(chosen) and not self._busy)

    def _preview(self, material: Material) -> None:
        path = self.ctx.store.material_file(material)
        if not path.exists() or not self._opener(path):
            self._show_message("warning", "無法開啟這份 PDF", "檔案不存在，或電腦沒有 PDF 閱讀器。")

    def _go_settings(self) -> None:
        self.go_settings = True
        self.reject()

    # ------------------------------------------------------------------ 輸出
    def print_selected(self) -> None:
        if self._busy or self._closed or not self._checked:
            return
        try:
            materials = self.ctx.store.list_materials()
        except StoreError as exc:
            self._show_message("danger", "無法讀取衛教單張", f"{exc}。資料夾恢復後再按一次列印。")
            return
        enabled = [m for m in materials if m.enabled]
        if not self._checked <= {m.id for m in enabled}:
            self._apply(materials)
            self._show_message("warning", "有單張剛被停用或刪除", "清單已更新，請確認勾選後再按一次列印。")
            return
        store = self.ctx.store
        sources = tuple(LeafletSource(m.id, m.name, store.material_file(m), m.sha256)
                        for m in enabled if m.id in self._checked)
        self._discard_merged()
        self.clear_error()
        self._request += 1
        output = temp_output_dir() / f"ICOPE衛教單張_{datetime.now():%Y%m%d-%H%M%S-%f}_{self._request}.pdf"
        job = _Job(self._request, str(store.root), sources, output)
        self._set_busy(True)
        self._run(lambda: merge_material_pdfs(job.sources, job.output),
                  lambda result: self._merged_ok(job, result), lambda exc: self._failed(job, exc, merging=True))

    def _verify_then(self, action: Callable[[_Job, MergedLeaflets], None]) -> None:
        """重試列印、用閱讀器開啟：清單沒變（只比 JSON）才到背景重讀來源比對內容，通過才輸出同一份合併檔。"""
        if self._busy or self._closed:
            return
        if self._merged is None:              # 看得到的復原按鈕不能按了沒反應：沒有可用的檔案就明說
            self._withdraw_recovery()
            return
        old_job, result = self._merged
        if not result.path.exists() or not self._still_valid(old_job):
            self._withdraw_recovery()
            return
        self._request += 1
        job = replace(old_job, token=self._request)
        self._merged = (job, result)
        self._set_busy(True)
        self._run(lambda: verify_material_sources(job.sources, result.hashes),
                  lambda _none: self._verified(job, result, action), lambda exc: self._failed(job, exc, merging=False))

    def _run(self, work: Callable[[], object], on_success: Callable, on_error: Callable) -> None:
        self._workers += 1
        run_in_background(self, work, on_success, on_error, self._worker_returned)

    def _worker_returned(self) -> None:
        # on_finished 比成功／失敗回呼先執行：這裡只記「背景工作回來了」，忙碌狀態由整段流程的結尾解除
        self._workers -= 1

    def _is_current(self, job: _Job) -> bool:
        return not self._closed and job.token == self._request

    def _merged_ok(self, job: _Job, result: MergedLeaflets) -> None:
        if not self._is_current(job):
            _unlink(result.path)
            self._delete_when_done()
            return
        if not self._still_valid(job):
            _unlink(result.path)
            self._idle()
            self._show_message("warning", "沒有送出列印", CHANGED_TEXT)
            return
        self._merged = (job, result)
        self._send(job, result)

    def _verified(self, job: _Job, result: MergedLeaflets, action: Callable[[_Job, MergedLeaflets], None]) -> None:
        if not self._is_current(job):
            self._delete_when_done()
            return
        if not self._still_valid(job):
            # 驗證進行中清單變了（別台電腦停用、刪除、換檔或調順序）：忙碌時的資料更新只會延後刷新，
            # 內容雜湊也照樣會過，所以回來後要再核對一次快照
            self._idle()
            self._withdraw_recovery()
            return
        action(job, result)

    def _failed(self, job: _Job, exc: BaseException, merging: bool) -> None:
        if not self._is_current(job):
            self._delete_when_done()
            return
        self._idle()
        if isinstance(exc, MaterialChanged) or not merging:
            self._withdraw_recovery(CHANGED_TEXT if isinstance(exc, MaterialChanged) else str(exc))
        else:
            self._show_message("danger", "無法準備要列印的檔案", str(exc),
                               [button("重試", "refresh-cw", "primary", size="sm", on_click=self.print_selected)])

    def _send(self, job: _Job, result: MergedLeaflets) -> None:
        self._printing = True                 # 系統列印視窗開著時可能處理其他事件：這份檔案此時不能被刪
        accepted: bool | None = None
        error = ""
        try:
            accepted = self._printer(self, result.path)
        except PrintError as exc:
            error = str(exc)
        finally:
            self._printing = False
        if not self._is_current(job):         # 送印期間被關掉：現在才可以清
            self._discard_merged()
            self._delete_when_done()
            return
        if accepted:
            self.summary = (len(job.sources), result.pages)
            self.ctx.audit.record("leaflet_print", leaflets=len(job.sources), pages=result.pages)
            self.accept()
            return
        self._idle()
        if accepted is None:
            self._show_message("danger", "列印失敗，單張還沒印出",
                               f"{error}。請確認印表機後重試，或改用 PDF 閱讀器開啟後列印。", self._recovery_actions())
        # 系統列印視窗按了取消：不算完成。合併檔留著——畫面上若還有「重試列印／用閱讀器開啟」就要繼續有作用
        # （每次使用前都會重新驗證來源）；改勾選、再按列印或關閉時才丟掉。

    def _open(self, job: _Job, result: MergedLeaflets) -> None:
        opened = self._opener(result.path)
        if not self._is_current(job):
            return
        self._idle()
        if opened:
            self._handed_over.add(result.path)
            self._show_message("success", "已用 PDF 閱讀器開啟", "可以在閱讀器裡列印；這裡按「取消」就能關閉。",
                               self._recovery_actions())
        else:
            self._show_message("warning", "找不到可以開啟 PDF 的程式", "請確認電腦有安裝 PDF 閱讀器，或修好印表機後按「重試列印」。",
                               self._recovery_actions())

    def _recovery_actions(self) -> list[QWidget]:
        return [button("重試列印", "printer", "primary", size="sm", on_click=lambda: self._verify_then(self._send)),
                button("用 PDF 閱讀器開啟", "external-link", "secondary", size="sm",
                       on_click=lambda: self._verify_then(self._open))]

    # ------------------------------------------------------------------ 狀態
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.list_area.setEnabled(not busy)   # 取消鈕不停用：等共用資料夾回應時也能關掉
        self._update_footer()

    def _idle(self) -> None:
        """整段輸出流程結束。忙碌時收到的資料更新在這裡補做（之後才顯示這次的結果訊息，不會被刷新蓋掉）。"""
        self._set_busy(False)
        if self._refresh_pending:
            self._refresh_pending = False
            self.reload()

    def _still_valid(self, job: _Job) -> bool:
        try:
            return self._matches(job, self.ctx.store.list_materials())
        except StoreError:
            return False                      # 讀不到清單：不能沿用舊檔輸出

    def _matches(self, job: _Job, materials: list[Material]) -> bool:
        """同一個資料夾、同一批仍啟用的單張、檔名與登錄雜湊沒變、相對順序一樣。沒勾選的單張怎麼變都不影響。"""
        store = self.ctx.store
        wanted = {source.id for source in job.sources}
        current = tuple((m.id, store.material_file(m), m.sha256) for m in materials if m.enabled and m.id in wanted)
        return str(store.root) == job.root and current == tuple((s.id, s.path, s.sha256) for s in job.sources)

    def _withdraw_recovery(self, text: str = CHANGED_TEXT, reload: bool = True) -> None:
        self._discard_merged()
        self._show_message("warning", "沒有送出列印", text)
        if reload and not self._busy:
            try:
                self._apply(self.ctx.store.list_materials())
            except StoreError:
                pass                          # 清單留著；訊息已經說明要再按一次

    def _discard_merged(self) -> None:
        if self._merged is None or self._printing:
            return
        path = self._merged[1].path
        self._merged = None
        if path not in self._handed_over:
            _unlink(path)

    def _show_message(self, kind: str, title: str, text: str, actions: list[QWidget] | None = None) -> None:
        self.error_banner.set_content(kind, title, text, actions or [])
        self.error_banner.show()

    # ------------------------------------------------------------------ 關閉
    def done(self, result: int) -> None:
        if not self._closed:
            self._closed = True
            self._request += 1
            self.ctx.materials_changed.disconnect(self._on_materials_changed)
            self._discard_merged()
        super().done(result)
        self._delete_when_done()

    def _delete_when_done(self) -> None:
        if self._closed and self._workers == 0 and not self._printing:
            self.deleteLater()


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass          # 清不掉交給一天清理；不能蓋掉原本的錯誤，也不能讓關閉流程出錯
