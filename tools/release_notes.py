"""發佈用：確認 tag 和程式版本一致，並從 CHANGELOG.md 取出這一版的說明。

用法：python tools/release_notes.py v1.1.0 [輸出檔]      （.github/workflows/release.yml 會呼叫）
      給了輸出檔就寫成 UTF-8 檔案——不要在 PowerShell 裡用 > 或管線接，那會用主控台編碼把中文轉壞。

tag 不是「v＋目前版本」、或 CHANGELOG.md 沒有這一版的段落，就以非零結束、不輸出任何東西：
寧可發佈失敗，也不要發出一個檔名、畫面上的版本和 tag 對不起來，或沒有說明的版本。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ReleaseError(Exception):
    pass


def notes_for(tag: str, changelog: str, version: str | None = None) -> str:
    if version is None:
        from icope_tool import __version__ as version
    if tag != f"v{version}":
        raise ReleaseError(f"tag「{tag}」和程式版本 {version} 不一致：先改 icope_tool/__init__.py 與 pyproject.toml，或改用 v{version}")
    section = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.MULTILINE | re.DOTALL)
    if section is None or not section.group(1).strip():
        raise ReleaseError(f"CHANGELOG.md 沒有 [{version}] 這一版的說明")
    return section.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        sys.stderr.buffer.write(__doc__.encode("utf-8"))
        return 2
    try:
        notes = notes_for(argv[0], (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        if len(argv) == 2:
            Path(argv[1]).write_text(notes, encoding="utf-8", newline="\n")
    except (ReleaseError, OSError) as exc:
        sys.stderr.buffer.write(f"不能發佈：{exc}\n".encode("utf-8"))      # Windows 主控台的預設編碼不是 UTF-8
        return 1
    if len(argv) == 1:
        sys.stdout.buffer.write(notes.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
