"""程式進入點。"""
from __future__ import annotations

import argparse
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from icope_tool import APP_DISPLAY_NAME, APP_NAME, __version__
from icope_tool.paths import default_data_dir, default_local_dir


def _write_error_log(local_dir: Path, text: str) -> None:
    try:
        folder = local_dir / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "error.log", "a", encoding="utf-8") as fh:
            fh.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} v{__version__}\n{text}")
    except OSError:
        pass


def install_translations(app) -> None:
    """讓 Qt 內建對話框（是／取消、檔案對話框按鈕）顯示繁體中文。"""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    locale = QLocale("zh_TW")
    if translator.load(locale, "qtbase", "_", folder):
        app.installTranslator(translator)
    QLocale.setDefault(locale)


def resolve_data_dir(local_store, requested: Path | None):
    """決定 data 資料夾；首次使用或無法存取時詢問。回傳 Path 或 None（使用者選擇結束）。"""
    from PySide6.QtWidgets import QDialog

    from icope_tool.store import DataFolderUnavailable, DataStore, StoreError
    from icope_tool.ui.dialogs.data_folder import DataFolderDialog

    config = local_store.load()
    path = requested or (Path(config.data_dir) if config.data_dir else None)
    create = requested is not None
    if path is None:
        dialog = DataFolderDialog(None, "first_run")
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
            return None
        path, create = dialog.chosen, True
    while True:
        try:
            DataStore(path).ensure_ready(create=create or path == default_data_dir())
            return path
        except (DataFolderUnavailable, StoreError, OSError):
            dialog = DataFolderDialog(None, "unavailable", path)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
                return None
            create = dialog.chosen != path
            path = dialog.chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_DISPLAY_NAME)
    parser.add_argument("--data-dir", type=Path, help="資料存放位置（預設：程式旁的 data，或上次使用的位置）")
    parser.add_argument("--local-dir", type=Path, help="本機設定與紀錄的位置（測試用）")
    parser.add_argument("--self-test", type=Path, metavar="輸出資料夾",
                        help="自我檢測：確認字型、讀卡元件、PDF、驗證碼辨識等是否正常，結果寫到指定資料夾")
    args = parser.parse_args(argv)
    if args.self_test:
        from icope_tool.selftest import run
        return run(args.self_test)

    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication, QMessageBox

    from icope_tool.audit import AuditLog
    from icope_tool.store import LocalConfigStore
    from icope_tool.ui.context import AppContext
    from icope_tool.ui.main_window import MainWindow
    from icope_tool.ui.theme import app_icon, apply_theme

    local_dir = args.local_dir or default_local_dir()
    local_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    install_translations(app)
    apply_theme(app)
    from icope_tool.ui.workers import keep_garbage_collection_on_main_thread
    gc_timer = keep_garbage_collection_on_main_thread(app)   # noqa: F841 - 計時器隨 app 存活

    def excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _write_error_log(local_dir, text)
        QMessageBox.critical(None, "發生未預期的錯誤",
                             f"{exc_type.__name__}：{exc}\n\n程式可以繼續使用；若一直發生，請把 "
                             f"{local_dir / 'logs' / 'error.log'} 提供給管理者。")

    sys.excepthook = excepthook

    lock = QLockFile(str(local_dir / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QMessageBox.information(None, APP_DISPLAY_NAME, "程式已經開著了，請從工作列切換到已開啟的視窗。")
        return 0

    local_store = LocalConfigStore(local_dir)
    data_dir = resolve_data_dir(local_store, args.data_dir)
    if data_dir is None:
        return 0
    try:
        local_store.update(data_dir=str(data_dir))
    except Exception:   # noqa: BLE001 - 無法寫入本機設定時仍可使用，只是下次要重新選資料夾
        from icope_tool.ui.messages import local_save_failed
        QMessageBox.warning(None, APP_DISPLAY_NAME, local_save_failed(local_store.path))

    ctx = AppContext(local_store, data_dir, AuditLog(local_dir / "logs" / "audit.log"))
    ctx.audit.record("app_start", version=__version__)
    window = MainWindow(ctx)
    screen = app.primaryScreen().availableGeometry()
    if screen.width() < 1400 or screen.height() < 860:
        window.showMaximized()
    else:
        window.resize(1360, 860)
        window.show()

    threading.Thread(target=ctx.hpdcs.warm_up_ocr, name="ocr-warmup", daemon=True).start()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
