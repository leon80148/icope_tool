"""需要數秒以上的工作（批次加入 PDF、複製資料夾）：背景執行，前景顯示進度與取消。"""
from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QProgressDialog, QWidget

from icope_tool.ui.workers import run_in_background

Report = Callable[[int, str], None]


class _Reporter(QObject):
    progressed = Signal(int, str)


def run_with_progress(parent: QWidget, title: str, text: str, maximum: int,
                      work: Callable[[Report, threading.Event], object],
                      on_success: Callable[[object], None], on_error: Callable[[BaseException], None],
                      cancellable: bool = True) -> None:
    """work(report, cancel) 在背景執行：呼叫 report(完成數, 說明) 更新進度，並在安全的地方檢查 cancel。

    maximum 為 0 時顯示不確定進度（忙碌動畫）。取消只會設定 cancel，工作要自己在下一個安全點停下。
    """
    dialog = QProgressDialog(text, "取消", 0, maximum, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(400)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(420)
    cancel = threading.Event()
    if cancellable:
        dialog.canceled.connect(cancel.set)
        dialog.canceled.connect(lambda: dialog.setLabelText("正在取消，會在目前這個檔案處理完後停止…"))
    else:
        dialog.setCancelButton(None)   # type: ignore[arg-type]  # Qt 接受 nullptr：不顯示取消鈕
    reporter = _Reporter(dialog)
    reporter.progressed.connect(lambda value, message: (dialog.setValue(value), dialog.setLabelText(message)))
    dialog.setValue(0)

    def finished() -> None:
        dialog.reset()
        dialog.hide()
        dialog.deleteLater()

    run_in_background(parent, lambda: work(reporter.progressed.emit, cancel), on_success, on_error, finished)
