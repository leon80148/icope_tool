"""列印 PDF：以 QtPdf 逐頁轉成影像送印表機（最高 300 dpi，避免記憶體暴增）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QApplication, QDialog, QProgressDialog, QWidget

MAX_DPI = 300


class PrintError(Exception):
    pass


def print_pdf(parent: QWidget, path: Path) -> bool:
    """跳出列印對話框並列印；使用者取消回傳 False。"""
    document = QPdfDocument(parent)
    status = document.load(str(path))
    if status != QPdfDocument.Error.None_ or document.pageCount() == 0:
        raise PrintError("無法讀取要列印的 PDF")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setDocName(path.stem)
    printer.setFromTo(1, document.pageCount())
    dialog = QPrintDialog(printer, parent)
    dialog.setWindowTitle("列印轉介衛教單")
    dialog.setOption(QPrintDialog.PrintDialogOption.PrintPageRange, True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        document.close()
        return False

    first, last = 1, document.pageCount()
    if printer.printRange() == QPrinter.PrintRange.PageRange:
        first = max(1, printer.fromPage())
        last = min(document.pageCount(), printer.toPage() or document.pageCount())
    try:
        return send_to_printer(parent, document, printer, first, last)
    finally:
        document.close()


def send_to_printer(parent: QWidget | None, document: QPdfDocument, printer: QPrinter, first: int, last: int) -> bool:
    """逐頁送印。使用者取消回傳 False；任何一步失敗都丟 PrintError，絕不回報成功。"""
    painter = QPainter()
    if not painter.begin(printer):
        raise PrintError("無法開始列印，請確認印表機已開啟並連線")
    total = last - first + 1
    progress = QProgressDialog("正在準備列印…", "取消列印", 0, total, parent)
    progress.setWindowTitle("列印轉介衛教單")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(500)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    try:
        dpi = min(printer.resolution(), MAX_DPI)
        for index, page in enumerate(range(first - 1, last)):
            progress.setValue(index)
            progress.setLabelText(f"正在送出第 {index + 1} / {total} 頁…")
            QApplication.processEvents()
            if progress.wasCanceled():
                printer.abort()
                return False
            if index and not printer.newPage():
                printer.abort()
                raise PrintError(f"第 {index + 1} 頁送不出去，印表機可能離線、缺紙或佇列發生錯誤")
            point_size = document.pagePointSize(page)
            width = max(1, int(point_size.width() / 72 * dpi))
            height = max(1, int(point_size.height() / 72 * dpi))
            image = document.render(page, QSize(width, height))
            target = painter.viewport()
            scale = min(target.width() / image.width(), target.height() / image.height())
            w, h = int(image.width() * scale), int(image.height() * scale)
            rect = QRect(target.x() + (target.width() - w) // 2, target.y() + (target.height() - h) // 2, w, h)
            painter.drawImage(rect, image)
    finally:
        ended = painter.end() if painter.isActive() else True
        progress.reset()
        progress.deleteLater()
    if not ended or printer.printerState() == QPrinter.PrinterState.Error:
        raise PrintError("送印沒有完成，印表機可能離線或佇列發生錯誤")
    return True
