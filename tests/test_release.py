"""發佈前的一致性：版本號只有一個真相，這一版一定要有更新紀錄，tag 和版本不符就不發佈。"""
from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest

from icope_tool import __version__

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    name = "release_notes"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def test_version_is_the_same_everywhere():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == __version__
    assert project["license"] == "MIT" and (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_files_can_be_read_by_an_old_pip_on_any_locale(name):
    """python -m venv 附的舊版 pip 用系統編碼讀 requirements（GitHub 的 Windows runner 是 cp1252），
    中文註解會讓它直接解碼失敗、什麼都沒裝。pip 認 PEP 263 的編碼宣告：有非 ASCII 內容就要在前兩行宣告 utf-8。"""
    import re
    data = (ROOT / name).read_bytes()
    if data.isascii():
        return
    declared = [re.search(rb"coding[:=]\s*([-\w.]+)", line) for line in data.split(b"\n")[:2] if line.startswith(b"#")]
    assert any(match and match.group(1).lower() == b"utf-8" for match in declared), name
    data.decode("utf-8")


def test_the_current_version_has_release_notes():
    notes = _tool().notes_for(f"v{__version__}", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert "###" in notes and len(notes) > 200              # 真的有分類過的內容，不是空標題
    assert "## [" not in notes                              # 只有這一版，不會把下一段也帶進去


def test_notes_are_cut_at_the_next_version():
    changelog = "# 更新紀錄\n\n## [2.0.0] - 2027-01-01\n\n### 新增\n- 新的\n\n## [1.9.0] - 2026-12-01\n\n### 修正\n- 舊的\n"
    tool = _tool()
    assert tool.notes_for("v2.0.0", changelog, version="2.0.0").strip() == "### 新增\n- 新的"
    assert "舊的" in tool.notes_for("v1.9.0", changelog, version="1.9.0")


@pytest.mark.parametrize("tag", ["v9.9.9", "1.1.0", "v", ""])
def test_a_tag_that_does_not_match_the_version_is_refused(tag):
    """打錯 tag（或忘了改版本號）時，不能發佈一個檔名、畫面版本和 tag 對不起來的 zip。"""
    with pytest.raises(_tool().ReleaseError):
        _tool().notes_for(tag, "## [1.1.0] - 2026-09-18\n\n### 新增\n- x\n", version="1.1.0")


def test_a_version_without_notes_is_refused():
    with pytest.raises(_tool().ReleaseError):
        _tool().notes_for("v1.1.0", "## [1.0.0] - 2026-09-17\n\n### 新增\n- x\n", version="1.1.0")
