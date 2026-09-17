"""產生使用說明（F1）與 docs/GUIDE.md 用的圖片：icope_tool/resources/guide/*.png。

用法：python tools/guide_images.py [--only 檔名片段]      （--only 沒有對到任何圖片會列出可用名稱並失敗）

- 和 ui_snapshots.py 一樣，用示範資料在離屏視窗操作真正的畫面：不連網、不碰讀卡機、沒有真實個資，
  也不會出現這台電腦的資料夾路徑（狀態列會裁掉）。診所與長者是虛構的；轉介資源是 examples/ 裡公開的嘉義市社區資源。
- 直接執行時固定用 Qt 的離屏平台與 100% 縮放，圖片不受這台電腦螢幕縮放的影響；選到的圖沒有全部產生會以非零結束。
- 標註依元件的實際位置畫（橘色框＋編號圓點），不是寫死的像素座標：版面調整後重跑就會跟著移動。
- 畫面改了就重跑一次，把新的 PNG 一起 commit，再逐張打開檢查：字看得清楚、圓點沒有蓋到字、沒有真實個資，
  並核對 help_dialog.py、docs/GUIDE.md 圖說裡引用的文字與數字。IMAGES 要和這兩份引用的一致（tests/ 會檢查）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

OUT = ROOT / "icope_tool" / "resources" / "guide"
WINDOW = (1280, 720)          # 審查過的最小螢幕尺寸；再窄的話設定頁的工具列會擠在一起
MAX_WIDTH = 860               # 使用說明視窗（980 寬）裡剛好 1:1 顯示的寬度
ACCENT = "#EA580C"

# 會產生的圖片（完整檔名，不含 .png）。--only、下面每個區塊的守衛與 save() 都只認這裡的名稱。
IMAGES = (
    "setup-1-checklist", "setup-7-account", "query-to-referral",
    "daily-1-tick-and-apply", "daily-2-confirm-shortcut", "daily-4-switch-elder", "daily-3-print",
    "adjust-resources", "adjust-leaflets", "leaflets-1-entry", "leaflets-2-dialog",
    "setup-2-clinic-and-notes", "setup-3-resources-default", "setup-4-leaflets",
    "setup-5-import-pack", "setup-6-import-dialog",
)


def selected(only: str) -> tuple[str, ...]:
    return tuple(name for name in IMAGES if only in name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只輸出檔名含此字串的圖片")
    args = parser.parse_args(argv)
    chosen = selected(args.only)
    if not chosen:
        print(f"沒有檔名含「{args.only}」的圖片，什麼都沒有產生。可用的名稱：\n  " + "\n  ".join(IMAGES))
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    from ui_snapshots import DEMO_IDS, FakeHpdcs, make_demo_store

    app = QApplication(sys.argv[:1])
    import icope_tool.ui.referral.confirm as confirm_module
    from icope_tool.audit import AuditLog
    from icope_tool.services.hpdcs.client import IcopeResult, PlanResult
    from icope_tool.services.pack import read_pack
    from icope_tool.store import DataStore, LocalConfigStore
    from icope_tool.ui.context import AppContext, HistoryEntry
    from icope_tool.ui.dialogs.credentials import CredentialsDialog
    from icope_tool.ui.dialogs.importing import PackImportDialog
    from icope_tool.ui.dialogs.leaflet_print import LeafletPrintDialog
    from icope_tool.ui.main_window import MainWindow
    from icope_tool.ui.theme import apply_theme, ui_font
    from icope_tool.ui.workers import keep_garbage_collection_on_main_thread
    apply_theme(app)
    gc_timer = keep_garbage_collection_on_main_thread(app)   # noqa: F841 - 與正式程式相同

    work = Path(tempfile.mkdtemp(prefix="icope-guide-"))
    saved: list[str] = []

    def pump(seconds: float = 0.3) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def wanted(*names: str) -> bool:
        unknown = [name for name in names if name not in IMAGES]
        if unknown:                       # 寫成 "daily-3" 這種片段的話，--only daily-3-print 會整段跳過卻正常結束
            raise KeyError(f"不在 IMAGES 裡的圖片名稱：{unknown}")
        return any(name in chosen for name in names)

    def open_window(ctx) -> MainWindow:
        window = MainWindow(ctx)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.resize(*WINDOW)
        window.show()
        return window

    def show_dialog(dialog) -> None:
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        dialog.show()
        pump()

    def find_button(parent: QWidget, text: str) -> QPushButton:
        return next(b for b in parent.findChildren(QPushButton) if b.text().strip() == text and b.isVisible())

    def content_rect(window: MainWindow, height: int | None = None) -> QRect:
        """主畫面去掉左側選單與狀態列（狀態列有這台電腦的資料夾路徑）。"""
        page = window.stack
        origin = page.mapTo(window, QPoint(0, 0))
        return QRect(origin.x(), 0, page.width(), height or origin.y() + page.height())

    def window_rect(window: MainWindow) -> QRect:
        page = window.stack
        return QRect(0, 0, window.width(), page.mapTo(window, QPoint(0, 0)).y() + page.height())

    def bottom_of(widget: QWidget, window: MainWindow, margin: int = 20) -> int:
        return widget.mapTo(window, QPoint(0, widget.height())).y() + margin

    def save(name: str, top: QWidget, marks: list[tuple], crop: QRect | None = None) -> None:
        if not wanted(name):
            return
        pump(0.5)
        area = crop or top.rect()
        grabbed: QPixmap = top.grab()
        if grabbed.devicePixelRatio() != 1.0:      # 裁切範圍與標註都用邏輯座標：螢幕縮放 125%／150% 時會裁錯位置
            raise RuntimeError(f"畫面縮放是 {grabbed.devicePixelRatio()} 倍，圖片會裁錯。請直接執行這個檔案（會固定用離屏平台、100% 縮放）")
        pixmap = grabbed.copy(area)
        scale = min(1.0, MAX_WIDTH / pixmap.width())
        if scale < 1.0:        # 先平滑縮到說明視窗的顯示寬度再畫標註：說明裡 1:1 顯示，小字不會有鋸齒，編號也不會跟著變小
            pixmap = pixmap.scaledToWidth(MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont(ui_font(11, bold=True)))
        # 第三個值指定圓點的位置：t/m/b（上、中、下）＋ l/r（左、右），預設左上 "tl"；會蓋到字時換一個。
        # 上下的圓點壓在框角上；"m" 的圓點整個放在框外（按鈕的字離左右邊很近，壓在框線上會蓋到字）
        for widget, number, *corner in marks:
            origin = widget.mapTo(top, QPoint(0, 0)) - area.topLeft()
            box = QRect(round(origin.x() * scale) - 5, round(origin.y() * scale) - 5,
                        round(widget.width() * scale) + 10, round(widget.height() * scale) + 10)
            box = box.intersected(pixmap.rect().adjusted(2, 2, -2, -2))
            painter.setPen(QPen(QColor(ACCENT), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(box, 9, 9)
            where = corner[0] if corner else "tl"
            y = box.bottom() if "b" in where else box.center().y() if "m" in where else box.top()
            outside = 17 if "m" in where else 0
            center = QPoint(box.right() + outside if "r" in where else box.left() - outside, y)
            center = QPoint(min(max(16, center.x()), pixmap.width() - 16), min(max(16, center.y()), pixmap.height() - 16))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawEllipse(center, 14, 14)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(QRect(center.x() - 14, center.y() - 14, 28, 28), Qt.AlignmentFlag.AlignCenter, number)
        painter.end()
        if not pixmap.save(str(OUT / f"{name}.png")):
            raise RuntimeError(f"無法寫入 {OUT / name}.png")
        saved.append(name)
        print(f"saved {name}.png  {pixmap.width()}x{pixmap.height()}")

    # ======================================================================== 第一次使用（還沒有任何設定）
    if wanted("setup-1-checklist", "setup-7-account"):
        fresh = work / "fresh"
        DataStore(fresh).ensure_ready(create=True)
        fresh_ctx = AppContext(LocalConfigStore(work / "fresh-local"), fresh, AuditLog(work / "fresh-local" / "audit.log"),
                               hpdcs_factory=lambda _c: FakeHpdcs(configured=False))
        fresh_window = open_window(fresh_ctx)
        fresh_window.go("query")
        checklist = fresh_window.pages["query"].checklist
        save("setup-1-checklist", fresh_window, [(checklist, "1")], window_rect(fresh_window))
        account = CredentialsDialog(fresh_window, fresh_ctx)          # 還沒設定過：帳號、密碼都是空的
        show_dialog(account)
        save("setup-7-account", account, [(account.account, "1", "tr"), (account.password, "2", "tr"),
                                          (account.save_test, "3")])
        account.reject()
        fresh_window.close()
        fresh_window.deleteLater()

    # ======================================================================== 示範資料
    store = make_demo_store(work)
    fake = FakeHpdcs()
    local = LocalConfigStore(work / "local")
    local.update(data_dir=str(store.root), last_assessor="林美華 護理師", recent_assessors=["林美華 護理師"],
                 hide_setup_checklist=True)
    ctx = AppContext(local, store.root, AuditLog(work / "local" / "audit.log"), hpdcs_factory=lambda _c: fake)
    window = open_window(ctx)
    query, referral, settings = window.pages["query"], window.pages["referral"], window.pages["settings"]

    # ------------------------------------------------------------------ 篩檢查詢 → 製作轉介衛教單
    window.go("query")
    result = IcopeResult([PlanResult("EFA_115", "can_assess", "今年可以繼續評估：O，可以繼續評估！"),
                          PlanResult("EFA_Pilot_115", "can_assess", "今年可以繼續評估：O，可以繼續評估！")])
    ctx.set_patient(DEMO_IDS["王小明"], "王小明")
    entry = HistoryEntry(datetime.now(), DEMO_IDS["王小明"], "王小明", result, plans=ctx.active_plans(),
                         birth_roc="0400315")          # 像讀過健保卡一樣：結果上方會有年齡核對那一行
    ctx.add_history(entry)
    query.id_input.setText(DEMO_IDS["王小明"])
    query.show_entry(entry)
    pump()
    if wanted("query-to-referral"):
        save("query-to-referral", window, [(query.card_button, "1", "tr"), (find_button(query, "製作轉介衛教單"), "2")],
             content_rect(window))

    # ------------------------------------------------------------------ 每天：勾項目 → 帶入 → 確認與列印 → 列印
    # 只勾兩個都有院所預設的項目：快速開始要示範最短的路；沒有預設的項目留給下面的「調整內容」
    window.go("referral")
    for domain_id in ("cognitive", "mobility"):
        referral.domain_step.cards[domain_id].setChecked(True)
    save("daily-1-tick-and-apply", window,
         [(window.nav_buttons["referral"], "1"), (referral.domain_step.cards["cognitive"], "2"),
          (referral.summary.defaults_button, "3", "mr")], window_rect(window))
    referral.summary.defaults_button.click()
    referral.toast.hide()
    save("daily-2-confirm-shortcut", window, [(referral.confirm_shortcut, "4")], content_rect(window))

    if wanted("daily-4-switch-elder"):
        ctx.set_patient(DEMO_IDS["林阿土"], "林阿土")            # 這份轉介單還是王小明的：會先問，不會默默混在一起
        pump()
        banner = referral.patient_banner
        save("daily-4-switch-elder", window,
             [(find_button(banner, "清除，開始 林阿土 的轉介單"), "1", "bl"), (find_button(banner, "保留勾選，只換成這位長者"), "2", "bl"),
              (find_button(banner, "繼續編輯 王小明 的"), "3", "br")], content_rect(window, bottom_of(referral.stepper, window, 8)))
        ctx.set_patient(DEMO_IDS["王小明"], "王小明")            # 同一位長者：提示自己收起來，轉介單內容不變
        pump()

    if wanted("daily-3-print"):
        referral.go_to(3)
        confirm = referral.confirm_step
        deadline = time.monotonic() + 40
        while (confirm._generating or not confirm.is_current) and time.monotonic() < deadline:
            pump(0.1)
        if confirm._generating or not confirm.is_current:
            raise RuntimeError("預覽沒有在 40 秒內產生，daily-3-print 沒有輸出")
        pump(0.8)
        confirm_module.print_pdf = lambda _parent, _path: True      # 不開系統列印視窗；其餘走真正的送印流程，畫面才會和使用者看到的一樣
        confirm.print_button.click()
        pump(0.5)
        save("daily-3-print", window, [(confirm.print_button, "5", "tr"), (find_button(confirm, "開始下一位長者"), "6", "br")],
             content_rect(window))

    # ------------------------------------------------------------------ 調整內容：沒有院所預設的項目自己選
    referral.go_to(0)
    referral.domain_step.cards["hearing"].setChecked(True)
    if wanted("adjust-resources"):
        referral.go_to(1)
        referral.resource_step.show_domain("hearing")
        pump(0.6)
        rows = referral.resource_step.rows
        save("adjust-resources", window,
             [(referral.resource_step.tabs[2], "1"), (rows[0], "2", "tr"), (referral.next_button, "3")], content_rect(window))

    if wanted("adjust-leaflets"):
        referral.go_to(2)
        pump(0.6)
        save("adjust-leaflets", window, [(referral.material_step.rows[1], "1"), (referral.next_button, "2")],
             content_rect(window))

    if wanted("leaflets-1-entry", "leaflets-2-dialog"):
        referral.go_to(0)
        top_part = content_rect(window, height=330)
        save("leaflets-1-entry", window, [(referral.leaflet_button, "1")], top_part)
        dialog = LeafletPrintDialog(window, ctx)
        show_dialog(dialog)
        for row in dialog.rows[:2]:
            row.checkbox.setChecked(True)
        save("leaflets-2-dialog", dialog, [(dialog.rows[0], "2"), (dialog.print_button, "3")])
        dialog.reject()

    # ------------------------------------------------------------------ 第一次設定（管理者）
    window.go("settings")
    clinic = settings.pages["clinic"]
    if wanted("setup-2-clinic-and-notes"):
        settings.select("clinic")
        window.resize(WINDOW[0], 1080)               # 拉高視窗：診所資料與下面的各項衛教重點拍在同一張，不必捲動
        pump()
        clinic.note_domain.setCurrentIndex(clinic.note_domain.findData("hearing"))
        clinic.note_text.setPlainText("和長輩說話時放慢速度、面對面。\n覺得聽不清楚請告訴家人，並安排聽力檢查。")   # 有變更：儲存鈕可按
        clinic.scroll_view.ensureWidgetVisible(clinic.note_count)
        pump()
        save("setup-2-clinic-and-notes", window,
             [(clinic.name, "1", "ml"), (clinic.note_domain, "2", "tr"), (clinic.note_text, "3", "tr"),
              (clinic.save_button, "4")], content_rect(window))
        clinic.load()                                # 復原：關閉視窗時才不會問「有尚未儲存的設定」
        window.resize(*WINDOW)
        pump()

    if wanted("setup-3-resources-default"):
        settings.select("resources")
        resources_tab = settings.pages["resources"]
        pump()
        selection = resources_tab.table.selectionModel()          # 選兩筆還不是院所預設的：按鈕才會是「設為院所預設」
        for row in (2, 3):
            selection.select(resources_tab.proxy.index(row, 1), selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
        pump()
        save("setup-3-resources-default", window,
             [(find_button(resources_tab, "匯入"), "1"), (resources_tab.table, "2"), (resources_tab.default_button, "3")],
             content_rect(window))

    if wanted("setup-4-leaflets"):
        settings.select("materials")
        materials_tab = settings.pages["materials"]
        pump()
        materials_tab.table.selectRow(0)
        pump()
        save("setup-4-leaflets", window,
             [(find_button(materials_tab, "加入 PDF…"), "1"), (materials_tab.table, "2"),
              (materials_tab.default_button, "3")], content_rect(window))

    if wanted("setup-5-import-pack", "setup-6-import-dialog"):
        settings.select("pack")
        pack_tab = settings.pages["pack"]
        pump()
        save("setup-5-import-pack", window, [(pack_tab.import_button, "1")], content_rect(window))
        # 用 make_clinic_pack 做一個小的虛構設定包（和院所自己做設定包的方式一樣），拿來拍匯入視窗。
        # 圖說與教學引用這裡的數量：5 筆資源（1 筆和現有的重複）、2 份單張 → 合併會新增 4 筆、2 份
        import json

        from make_clinic_pack import build_pack
        from ui_snapshots import make_material_pdf
        pack_pdfs = work / "pack-pdfs"
        pack_pdfs.mkdir()
        make_material_pdf(pack_pdfs / "長輩飲食建議.pdf", "長輩飲食建議", 1)
        make_material_pdf(pack_pdfs / "居家防跌.pdf", "居家防跌", 1)
        spec = {"resources": [
            {"name": "示範醫院 記憶門診", "type": "醫療院所", "domains": ["cognitive"], "phones": ["05-0000001"], "pinned": True},
            {"name": "示範醫院 眼科", "type": "醫療院所", "domains": ["vision"], "phones": ["05-0000001"], "pinned": True},
            {"name": "示範里 巷弄長照站", "type": "巷弄長照站", "domains": ["cognitive", "mobility", "social"],
             "phones": ["05-0000002"], "pinned": True},
            {"name": "示範輔具資源中心", "type": "輔具資源中心", "domains": ["mobility", "hearing"], "phones": ["05-0000003"]}],
            "materials": [{"file": "長輩飲食建議.pdf", "name": "長輩飲食建議", "domains": ["nutrition"]},
                          {"file": "居家防跌.pdf", "name": "居家防跌", "domains": ["mobility"]}]}
        (work / "demo-pack.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        demo_pack = work / "示範診所設定包.zip"
        build_pack(work / "demo-pack.json", pack_pdfs, demo_pack)
        importer = PackImportDialog(window, store, read_pack(demo_pack))
        show_dialog(importer)
        save("setup-6-import-dialog", importer, [(importer.merge, "2"), (importer.import_button, "3")])
        importer.reject()

    referral.confirm_step.release_document()        # 關閉時不要跳「轉介單還沒印出」
    window.close()
    shutil.rmtree(work, ignore_errors=True)
    missing = [name for name in chosen if name not in saved]
    if missing:                       # 某個區塊的守衛 wanted(…) 漏了名稱：選到的圖沒有產生，不能當成成功
        print(f"沒有產生：{'、'.join(missing)}。檢查產生這些圖的區塊，外層的 wanted(…) 是否列了它們的完整檔名。")
        return 1
    print(f"完成 {len(saved)} 張。請逐張打開檢查，並核對 help_dialog.py 與 docs/GUIDE.md 圖說裡的文字與數字。")
    return 0


if __name__ == "__main__":
    # 固定用離屏平台、100% 縮放：圖片不受這台電腦的螢幕縮放（125%／150%）影響，誰重跑都一樣
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    raise SystemExit(main())
