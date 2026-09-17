"""背景工作：結果回主執行緒、Qt 物件只在主執行緒刪除、循環回收只在主執行緒做。"""
from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QObject

from icope_tool.ui.workers import keep_garbage_collection_on_main_thread, run_in_background


def test_garbage_collection_moves_to_the_main_thread(qtbot):
    holder = QObject()
    gc.enable()
    timer = keep_garbage_collection_on_main_thread(holder, interval_ms=10)
    assert not gc.isenabled()
    for _ in range(2000):
        cycle: list = []
        cycle.append(cycle)
    assert gc.get_count()[0] > 100
    qtbot.waitUntil(lambda: gc.get_count()[0] < 50, timeout=2000)   # 由主執行緒的計時器回收
    timer.stop()


def test_result_reaches_the_main_thread_and_relay_is_cleaned_up(qtbot):
    owner = QObject()
    seen = []
    run_in_background(owner, lambda: threading.get_ident(), seen.append)
    relay = owner.children()[0]
    assert relay.signals.parent() is relay                           # signals 掛在主執行緒的 relay 底下
    qtbot.waitUntil(lambda: bool(seen), timeout=2000)
    assert seen[0] != threading.get_ident()                          # 工作在背景執行緒跑
    qtbot.waitUntil(lambda: not owner.children(), timeout=2000)      # relay 與 signals 已刪除


def test_owner_closed_before_the_result_is_ignored_quietly(qtbot):
    owner = QObject()
    gate = threading.Event()
    seen = []
    run_in_background(owner, lambda: gate.wait(2) and "done", seen.append)
    owner.deleteLater()
    qtbot.wait(50)                                                   # owner、relay、signals 都已刪除
    gate.set()
    qtbot.wait(300)
    assert seen == []
