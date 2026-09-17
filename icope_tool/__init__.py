"""ICOPE 小幫手（IcopeTool）。"""

__version__ = "1.1.0"                    # 和 pyproject.toml、CHANGELOG.md 一起改（tests/test_release.py 會檢查）

APP_NAME = "IcopeTool"                   # 內部識別碼：exe 檔名、%LOCALAPPDATA% 底下的資料夾。改了的話舊安裝會找不到設定
APP_DISPLAY_NAME = "ICOPE 小幫手"        # 畫面、歡迎視窗、PDF 的 Creator 都用這個；只在這裡定義
