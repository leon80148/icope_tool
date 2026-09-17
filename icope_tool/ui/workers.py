"""把耗時工作（查詢國健署、讀卡、產生 PDF）丟到背景執行緒，結果回主執行緒處理。

回呼透過「住在主執行緒、以呼叫端 widget 為 parent」的 relay 物件接收：
- 跨執行緒的 signal 會自動排隊，UI 只在主執行緒更新
- 呼叫端 widget 被關閉時 relay 一起銷毀，不會對已刪除的元件回呼

Qt 物件一律在主執行緒刪除：背景執行緒若因 Python 回收而刪除 Qt 物件，解構時會拿著 Qt 的
訊號連線鎖去等 GIL，而主執行緒拿著 GIL 在建立元件、等同一把鎖，整個程式就凍結。因此：
- signals 物件掛在 relay 底下、工作函式由 relay 持有到結束，背景執行緒放掉時都不是最後一個參照
- 自動循環回收關閉，改由主執行緒定時回收（keep_garbage_collection_on_main_thread）
"""
from __future__ import annotations

import gc
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot


class _Signals(QObject):
    succeeded = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], object], signals: _Signals):
        super().__init__()
        self._fn = fn
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self._fn()
        except BaseException as exc:   # noqa: BLE001 - 所有錯誤都要回報給 UI
            signal_name, value = "failed", exc
        else:
            signal_name, value = "succeeded", result
        try:
            getattr(self._signals, signal_name).emit(value)
        except RuntimeError:
            pass        # 呼叫端已關閉，relay 與 signals 已刪除：沒有人需要這個結果


class _Relay(QObject):
    def __init__(self, owner: QObject, fn: Callable[[], object], on_success, on_error, on_finished):
        super().__init__(owner)
        self.fn = fn                          # 主執行緒持有到回呼結束
        self._on_success = on_success
        self._on_error = on_error
        self._on_finished = on_finished
        self.signals = _Signals(self)         # 掛在 relay 底下：隨 relay 在主執行緒刪除
        self.signals.succeeded.connect(self._success)
        self.signals.failed.connect(self._failure)

    # 先執行 on_finished（這個工作結束了），再交給成功／失敗處理：
    # 處理函式若再啟動下一個背景工作並設定忙碌狀態，不會被這個工作的 on_finished 蓋掉。
    @Slot(object)
    def _success(self, value):
        try:
            self._finished()
            if self._on_success:
                self._on_success(value)
        finally:
            self.fn = None
            self.deleteLater()

    @Slot(object)
    def _failure(self, exc):
        try:
            self._finished()
            if self._on_error:
                self._on_error(exc)
            else:
                raise exc
        finally:
            self.fn = None
            self.deleteLater()

    def _finished(self):
        if self._on_finished:
            self._on_finished()


def run_in_background(owner: QObject, fn: Callable[[], object], on_success: Callable | None = None,
                      on_error: Callable | None = None, on_finished: Callable | None = None) -> None:
    relay = _Relay(owner, fn, on_success, on_error, on_finished)
    QThreadPool.globalInstance().start(_Task(fn, relay.signals))


def keep_garbage_collection_on_main_thread(parent: QObject, interval_ms: int = 2000) -> QTimer:
    """關閉 Python 自動循環回收，改由主執行緒分代回收：年輕代每 2 秒、中代每 20 秒、全部每 2 分鐘。"""
    gc.disable()
    timer = QTimer(parent)
    timer.setInterval(interval_ms)
    ticks = {"n": 0}

    def collect() -> None:
        ticks["n"] += 1
        gc.collect(2 if ticks["n"] % 60 == 0 else 1 if ticks["n"] % 10 == 0 else 0)

    timer.timeout.connect(collect)
    timer.start()
    return timer
