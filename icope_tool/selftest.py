"""自我檢測（IcopeTool.exe --self-test <輸出資料夾>）。

在打包後的環境逐項確認：字型、繁中對話框按鈕、讀卡元件、PDF 產生與預覽、驗證碼辨識、主畫面。
結果寫成 report.json 與截圖，給安裝與排除問題時使用；不連網、不需要國健署帳號。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}

    def check(name: str, fn):
        started = time.perf_counter()
        try:
            detail = fn()
            report[name] = {"ok": True, "detail": detail}
        except Exception as exc:   # noqa: BLE001 - 自我檢測要記下所有失敗
            report[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc(limit=6)}
        report[name]["ms"] = round((time.perf_counter() - started) * 1000)

    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication, QMessageBox

    from icope_tool import __version__
    from icope_tool.app import install_translations
    from icope_tool.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv[:1])
    install_translations(app)
    apply_theme(app)
    from icope_tool.ui.workers import keep_garbage_collection_on_main_thread
    gc_timer = keep_garbage_collection_on_main_thread(app)   # noqa: F841 - 與正式程式相同
    report["version"] = {"ok": True, "detail": __version__, "frozen": bool(getattr(sys, "frozen", False))}

    check("font_noto_sans_tc", lambda: "Noto Sans TC" in QFontDatabase.families() or _raise("找不到 Noto Sans TC"))

    def translations():
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        texts = [b.text() for b in box.buttons()]
        box.deleteLater()
        if not any("取消" in t for t in texts):
            _raise(f"對話框按鈕不是中文：{texts}")
        return texts

    check("qt_translations_zh_tw", translations)

    def card_reader():
        from icope_tool.services.card import nhi_card
        return {"readers": nhi_card.list_readers()}

    check("card_reader_pyscard", card_reader)

    work = Path(tempfile.mkdtemp(prefix="icope-selftest-"))

    def pdf():
        from icope_tool.models import ClinicInfo, Resource
        from icope_tool.services.pdf.referral_sheet import ReferralJob, ReferralSection, build_referral_pdf
        job = ReferralJob(clinic=ClinicInfo(name="自我檢測診所", phone="05-0000000"),
                          sections=[ReferralSection("cognitive", [Resource(name="記憶門診", phones=["05-1234567"])])],
                          patient_name="測試長者")
        result = build_referral_pdf(job, {}, work / "selftest.pdf")
        from PySide6.QtPdf import QPdfDocument
        document = QPdfDocument()
        document.load(str(result.path))
        image = document.render(0, QSize(620, 877))
        image.save(str(out_dir / "pdf_page1.png"))
        pages = document.pageCount()
        document.close()
        if pages < 1 or image.isNull():
            _raise("QtPdf 無法轉出預覽")
        return {"pages": pages}

    check("pdf_build_and_preview", pdf)

    def ocr():
        from PIL import Image, ImageDraw
        from icope_tool.services.hpdcs.client import ocr_available
        if not ocr_available():
            _raise("找不到 ddddocr")
        import ddddocr
        engine = ddddocr.DdddOcr(show_ad=False)
        image = Image.new("RGB", (100, 34), "white")
        ImageDraw.Draw(image).text((12, 10), "40721", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="GIF")
        return {"sample_result": engine.classification(buffer.getvalue())}

    check("captcha_ocr", ocr)

    def main_window():
        from icope_tool.audit import AuditLog
        from icope_tool.store import DataStore, LocalConfigStore
        from icope_tool.ui.context import AppContext
        from icope_tool.ui.main_window import MainWindow
        store = DataStore(work / "data")
        store.ensure_ready(create=True)
        ctx = AppContext(LocalConfigStore(work / "local"), store.root, AuditLog(work / "local" / "audit.log"))
        window = MainWindow(ctx)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.resize(1366, 768)
        window.show()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            app.processEvents()
        for key in ("query", "referral", "settings"):
            window.go(key)
            app.processEvents()
            window.grab().save(str(out_dir / f"window_{key}.png"))
        window.close()
        return {"pages": 3}

    check("main_window", main_window)

    def referral_background_pdf():
        """實際走一次轉介第 4 步：背景執行緒產生 PDF、結果回主執行緒顯示預覽（死結修正的回歸檢查）。"""
        import gc

        from icope_tool.audit import AuditLog
        from icope_tool.models import Resource
        from icope_tool.store import DataStore, LocalConfigStore
        from icope_tool.ui.context import AppContext
        from icope_tool.ui.main_window import MainWindow
        if gc.isenabled():
            _raise("Python 自動循環回收沒有關閉，背景工作可能卡死")
        store = DataStore(work / "data-referral")
        store.ensure_ready(create=True)
        store.add_resource(Resource(name="記憶門診", domains=["cognitive"], phones=["05-1234567"]))
        ctx = AppContext(LocalConfigStore(work / "local-referral"), store.root,
                         AuditLog(work / "local-referral" / "audit.log"))
        window = MainWindow(ctx)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        window.resize(1366, 768)
        window.show()
        window.go("referral")
        page = window.pages["referral"]
        page.domain_step.cards["cognitive"].setChecked(True)
        started = time.monotonic()
        page.go_to(3)
        confirm = page.confirm_step
        while (confirm._pdf_path is None or confirm._generating) and time.monotonic() - started < 90:
            app.processEvents()
            time.sleep(0.02)
        if confirm._pdf_path is None:
            _raise("背景產生 PDF 逾時（90 秒）")
        seconds = round(time.monotonic() - started, 1)
        app.processEvents()
        window.grab().save(str(out_dir / "window_referral_preview.png"))
        confirm.release_document()          # 關閉視窗時不要跳「轉介單還沒印出」
        window.close()
        return {"seconds": seconds}

    check("referral_background_pdf", referral_background_pdf)

    def help_images():
        """使用說明（F1）的圖片是資料檔：打包時漏掉的話，說明會只剩文字。"""
        import re

        from PySide6.QtGui import QImage

        from icope_tool.paths import resource_path
        from icope_tool.ui.help_dialog import HELP_HTML
        names = sorted(set(re.findall(r'<img[^>]+src="([^"]+)"', HELP_HTML)))
        missing = [name for name in names if QImage(str(resource_path("guide", name))).isNull()]
        if missing or not names:
            _raise(f"使用說明缺少圖片：{missing or '說明裡沒有任何圖片'}")
        return {"images": len(names)}

    check("help_images", help_images)

    ok = all(item.get("ok") for item in report.values())
    (out_dir / "report.json").write_text(json.dumps({"ok": ok, "checks": report}, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return 0 if ok else 1


def _raise(message: str):
    raise RuntimeError(message)
