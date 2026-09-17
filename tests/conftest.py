import gc
import os

import pytest

# pytest-qt 在沒有螢幕的 CI 上也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _collect_garbage_on_main_thread():
    """與正式程式一致：循環回收只在主執行緒做。背景執行緒回收 Qt 物件會和主執行緒互等而卡住。"""
    gc.disable()
    yield
    gc.collect()
