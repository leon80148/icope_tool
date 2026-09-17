"""路徑慣例。

- data 資料夾（可共用）：預設 <程式所在資料夾>/data，可在設定改指到 NAS。
- 本機資料夾（不共用）：%LOCALAPPDATA%/IcopeTool，放 config.json 與稽核日誌。
- 暫存：系統暫存/IcopeTool，放產生的轉介單 PDF（含病患姓名，不留在共用資料夾）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from icope_tool import APP_NAME

PACKAGE_DIR = Path(__file__).resolve().parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """打包後為 exe 所在資料夾；開發時為 repo 根目錄。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return PACKAGE_DIR.parent


def resource_path(*parts: str) -> Path:
    return PACKAGE_DIR.joinpath("resources", *parts)


def default_data_dir() -> Path:
    return app_dir() / "data"


def default_local_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def temp_output_dir() -> Path:
    return Path(tempfile.gettempdir()) / APP_NAME
