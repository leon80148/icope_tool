"""tools/ 底下工具的測試（不需要 Qt）。"""
from __future__ import annotations

import ast
import errno
import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from icope_tool.domains import DOMAIN_IDS
from icope_tool.services import pack
from icope_tool.store import DataStore
from tests.test_store import make_pdf

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                  # dataclass 會從 sys.modules 找自己的模組
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path) -> tuple[Path, Path]:
    """一份院所自己整理的清單（JSON）＋一個放 PDF 的資料夾。"""
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    make_pdf(pdfs / "營養.pdf", pages=1, text="nutrition")
    make_pdf(pdfs / "綜合.pdf", pages=2, text="all")
    spec = {
        "_source": "底線開頭的鍵是給人看的註解，工具不讀",
        "resources": [
            {"name": "示範記憶門診", "type": "醫療院所", "domains": ["cognitive"], "phones": ["05-0000000"],
             "hours": "週一、三、五上午", "address": "示範市示範路 1 號", "pinned": True},
            {"name": "示範巷弄長照站", "type": "巷弄長照站", "domains": ["cognitive", "social"],
             "contact": "王小姐", "phones": ["05-1111111"], "pinned": True, "include_by_default": True},
        ],
        "materials": [
            {"file": "營養.pdf", "name": "高齡長輩飲食建議", "description": "三好一巧", "domains": ["nutrition"],
             "include_by_default": True},
            {"file": "綜合.pdf", "name": "各項目 QR code", "domains": ["cognitive", "nutrition"]},
        ],
    }
    path = tmp_path / "pack-source.json"
    path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path, pdfs


def test_clinic_pack_is_built_through_the_apps_own_export(source, tmp_path):
    tool = _load("make_clinic_pack")
    spec, pdfs = source
    out = tmp_path / "out" / "示範診所設定包.zip"
    summary = tool.build_pack(spec, pdfs, out)
    assert (summary.resources, summary.materials, summary.pages) == (3, 2, 3)      # 含程式預設的「本院門診追蹤」
    # 提醒「哪些項目沒有資源」只看院所自己列的：預設的門診追蹤適用全部項目，算進去就永遠不會提醒
    assert summary.uncovered == [d for d in DOMAIN_IDS if d not in ("cognitive", "social")]

    loaded = pack.read_pack(out)
    pack.validate_pack_materials(loaded, tmp_path / "scratch")
    by_name = {r.name: r for r in loaded.manifest.resources}
    assert set(by_name) == {"本院門診追蹤", "示範記憶門診", "示範巷弄長照站"}           # 取代模式也不會少了門診追蹤
    assert by_name["示範記憶門診"].hours == "週一、三、五上午" and by_name["示範記憶門診"].pinned
    assert by_name["示範巷弄長照站"].include_by_default and by_name["示範巷弄長照站"].contact == "王小姐"
    assert not by_name["示範記憶門診"].include_by_default
    leaflets = {m.name: m for m in loaded.manifest.materials}
    assert leaflets["高齡長輩飲食建議"].include_by_default and leaflets["高齡長輩飲食建議"].domains == ["nutrition"]
    assert leaflets["各項目 QR code"].pages == 2 and len(leaflets["各項目 QR code"].sha256) == 64
    with zipfile.ZipFile(out) as archive:                                          # 和程式匯出的格式完全一樣
        names = archive.namelist()
        assert "pack.json" in names and sum(n.startswith("materials/") for n in names) == 2
        assert not any("auth" in n or "settings" in n for n in names)


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_built_pack_imports_in_both_modes(source, tmp_path, mode):
    tool = _load("make_clinic_pack")
    spec, pdfs = source
    out = tmp_path / "pack.zip"
    tool.build_pack(spec, pdfs, out)
    clinic = DataStore(tmp_path / "clinic")
    clinic.ensure_ready(create=True)
    result = pack.apply_pack(clinic, pack.read_pack(out), mode)
    names = sorted(r.name for r in clinic.list_resources())
    assert names == ["本院門診追蹤", "示範巷弄長照站", "示範記憶門診"]                # 合併：門診追蹤視為重複，不會變兩筆
    assert result.materials_added == 2 and all(clinic.material_file(m).exists() for m in clinic.list_materials())


@pytest.mark.parametrize("broken, message", [
    ({"resources": [{"name": "打錯項目", "domains": ["cognitve"]}], "materials": []}, "cognitve"),
    ({"resources": [], "materials": [{"file": "不存在.pdf", "name": "x", "domains": ["nutrition"]}]}, "不存在.pdf"),
    ({"resources": [{"name": "沒有適用項目"}], "materials": []}, "沒有適用項目"),
    ({"resources": [{"name": "多打了欄位", "domains": ["vision"], "phone": "05-1"}], "materials": []}, "phone"),
    # 最外層的鍵打錯：以前整份資源清單會被當成空的，照樣做出一個只有「本院門診追蹤」的設定包
    ({"resouces": [{"name": "鍵打錯", "domains": ["vision"]}], "materials": []}, "resouces"),
    ([{"name": "最外層不是物件", "domains": ["vision"]}], "最外層"),
    ({"resources": {"name": "不是清單", "domains": ["vision"]}}, "resources"),
    ({"resources": [], "materials": []}, "沒有任何"),
    ({"resources": [{"name": "項目寫成字串", "domains": "cognitive"}]}, "domains"),
    # JSON 的 "false" 是字串：bool("false") 是 True，單張會被默默設成院所預設
    ({"materials": [{"file": "營養.pdf", "name": "旗標寫成字串", "domains": ["nutrition"],
                     "include_by_default": "false"}]}, "include_by_default"),
    ({"resources": [{"name": "常用寫成字串", "domains": ["vision"], "pinned": "yes"}]}, "pinned"),
    ({"resources": [{"name": "電話寫成字串", "domains": ["vision"], "phones": "05-1"}]}, "電話寫成字串"),
    # 名稱＋地址相同就是同一筆：合併匯入會略過，取代匯入卻會留下兩筆
    ({"resources": [{"name": "同一個據點", "address": "示範路 1 號", "domains": ["vision"]},
                    {"name": "同一個據點", "address": "示範路 1 號", "domains": ["hearing"]}]}, "第 1 筆和第 2 筆"),
    ({"resources": [{"name": "本院門診追蹤", "domains": ["cognitive"]}]}, "程式預設"),
    # 從表格轉出來的空值：str(None) 是 "None"，以前會通過檢查，單張再被默默改用 PDF 的檔名
    ({"materials": [{"file": "營養.pdf", "name": None, "domains": ["nutrition"]}]}, "單張第 1 筆"),
    ({"materials": [{"file": "營養.pdf", "name": False, "domains": ["nutrition"]}]}, "單張第 1 筆"),
    ({"materials": [{"file": "營養.pdf", "name": 0, "domains": ["nutrition"]}]}, "單張第 1 筆"),
    ({"resources": [{"name": 12345, "domains": ["vision"]}]}, "資源第 1 筆"),
    ({"materials": [{"name": "沒有寫檔名", "domains": ["nutrition"]}]}, "file"),
])
def test_mistakes_in_the_source_are_reported_instead_of_silently_dropped(source, tmp_path, broken, message):
    tool = _load("make_clinic_pack")
    _spec, pdfs = source
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tool.PackSourceError) as error:
        tool.build_pack(path, pdfs, tmp_path / "never.zip")
    assert message in str(error.value) and not (tmp_path / "never.zip").exists()


@pytest.mark.parametrize("name", ["設定包.json", "設定包.pdf", "設定包"])
def test_output_must_be_a_zip(source, tmp_path, name):
    """程式用副檔名分辨 zip 設定包與 JSON 示範資料：zip 內容取了別的檔名，匯入時會被當成壞掉的檔案。"""
    tool = _load("make_clinic_pack")
    spec, pdfs = source
    with pytest.raises(tool.PackSourceError, match="zip"):
        tool.build_pack(spec, pdfs, tmp_path / name)
    assert not (tmp_path / name).exists()


def test_a_failed_write_keeps_the_previous_pack(source, tmp_path, monkeypatch):
    """重新產生時寫到一半失敗（磁碟滿、網路磁碟斷線）：上一次做好的設定包要原封不動，不能變成半個 zip。"""
    tool = _load("make_clinic_pack")
    spec, pdfs = source
    out = tmp_path / "設定包.zip"
    out.write_bytes(b"previous good pack")
    real_replace = os.replace

    def replace(src, dst):
        if Path(dst) == out:                       # 只讓「換上成品」這一步失敗；暫存資料夾裡的寫入照常
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(tool.PackSourceError, match="設定包.zip"):
        tool.build_pack(spec, pdfs, out)
    assert out.read_bytes() == b"previous good pack"
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []      # 沒有留下暫存檔


# ---------------------------------------------------------------------------- guide_images.py
def test_guide_image_list_matches_the_files_on_disk():
    tool = _load("guide_images")
    assert set(tool.IMAGES) == {p.stem for p in tool.OUT.glob("*.png")}
    assert len(tool.IMAGES) == len(set(tool.IMAGES))


def test_only_filter_is_checked_against_full_image_names(capsys):
    """--only 打錯或沒有對到任何圖片時要失敗並列出可用名稱（不必啟動 Qt），不能什麼都沒產生還回報成功。"""
    tool = _load("guide_images")
    assert tool.selected("daily-3-print") == ("daily-3-print",)
    assert tool.selected("") == tuple(tool.IMAGES)
    assert tool.main(["--only", "no-such-image"]) == 2
    assert "daily-3-print" in capsys.readouterr().out


def test_every_image_name_used_in_the_script_is_a_full_name():
    """區塊守衛寫成 wanted("daily-3") 這種片段時，--only daily-3-print 會整段跳過卻正常結束。

    這裡只看名稱；「選到的圖真的有產生」由工具自己在結束前核對（少一張就以非零結束）。
    """
    tool = _load("guide_images")
    tree = ast.parse((TOOLS / "guide_images.py").read_text(encoding="utf-8"))
    guarded, saved = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("wanted", "save"):
            names = [arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            if node.func.id == "wanted":
                guarded.update(names)               # 守衛的每一個參數都要看，不只第一個
            else:
                saved.update(names[:1])
    assert saved == set(tool.IMAGES)
    assert guarded <= set(tool.IMAGES), sorted(guarded - set(tool.IMAGES))
