"""圖文說明：程式內的使用說明（F1）與 docs/GUIDE.md。

圖片由 tools/guide_images.py 產生，放在 icope_tool/resources/guide/（會跟著程式一起打包）。
這裡檢查兩件事：「引用得到」——少一張圖、多一張沒人用的圖、說明對話框載不到圖；
以及「和畫面一致」——說明引用的按鈕與訊息文字，畫面上已經改掉或拿掉的話會在這裡失敗。
圖片裡的字看不看得清楚、編號有沒有蓋到字，測試看不出來：重新產生圖片後要逐張打開檢查。
"""
from __future__ import annotations

import re
from pathlib import Path

from icope_tool.paths import resource_path

ROOT = Path(__file__).resolve().parent.parent
GUIDE_DIR = resource_path("guide")
GUIDE_MD = ROOT / "docs" / "GUIDE.md"


def _help_images() -> list[str]:
    from icope_tool.ui.help_dialog import HELP_HTML
    return re.findall(r'<img[^>]+src="([^"]+)"', HELP_HTML)


def _doc_images() -> list[tuple[str, str]]:
    return re.findall(r"!\[([^\]]*)\]\(([^)\s]+)\)", GUIDE_MD.read_text(encoding="utf-8"))


def test_every_referenced_image_exists_and_no_image_is_orphaned():
    in_help = set(_help_images())
    in_docs = _doc_images()
    assert len(in_help) >= 10 and len(in_docs) >= 10                      # 真的是圖文並茂，不是只放一兩張
    for alt, link in in_docs:
        target = (GUIDE_MD.parent / link).resolve()
        assert target.is_file() and target.parent == GUIDE_DIR.resolve(), link   # 文件與程式共用同一份圖
        assert alt.strip(), f"{link} 沒有替代文字"
    on_disk = {p.name for p in GUIDE_DIR.glob("*.png")}
    # 兩份各自都要用到每一張：只看聯集的話，其中一份漏掉一張圖也不會被發現
    assert in_help == on_disk, sorted(in_help ^ on_disk)
    assert {Path(link).name for _alt, link in in_docs} == on_disk, "docs/GUIDE.md 與 resources/guide 的圖片不一致"


def test_every_figure_in_the_guide_has_a_numbered_caption():
    lines = [line.lstrip("> ").strip() for line in GUIDE_MD.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line]
    numbers = []
    for index, line in enumerate(lines):
        if line.startswith("!["):
            caption = re.match(r"\*圖 (\d+)：.+\*$", lines[index + 1])
            assert caption, f"圖片下一行要是「*圖 N：…*」的圖說：{line[:40]}"
            numbers.append(int(caption.group(1)))
    assert numbers == list(range(1, len(numbers) + 1))                    # 圖號連續：正文才能用「圖 N」指過去


def test_the_help_and_the_guide_number_the_same_figures_the_same_way():
    """兩份說明是各自的文字：其中一份調了圖的順序或放錯圖，同一張圖的圖號就會對不起來。"""
    from icope_tool.ui.help_dialog import HELP_HTML
    in_help = dict(re.findall(r'<img src="([^"]+)"[^>]*></p><p class="cap">圖 (\d+)：', HELP_HTML))
    lines = [line.lstrip("> ").strip() for line in GUIDE_MD.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line]
    in_guide = {}
    for index, line in enumerate(lines):
        image = re.match(r"!\[[^\]]*\]\(([^)\s]+)\)", line)
        if image:
            in_guide[Path(image.group(1)).name] = re.match(r"\*圖 (\d+)：", lines[index + 1]).group(1)
    assert len(in_help) == len(_help_images())
    assert in_help == in_guide


def test_help_images_carry_alt_text_and_captions():
    from icope_tool.ui.help_dialog import HELP_HTML
    tags = re.findall(r"<img[^>]+>", HELP_HTML)
    assert tags and all(re.search(r'alt="[^"]{6,}"', tag) for tag in tags)   # 螢幕報讀與載不到圖時的說明
    assert HELP_HTML.count('class="cap"') >= len(tags)                       # 每張圖都有圖說（看哪裡、按什麼）


def test_guide_images_are_readable_and_reasonably_small(qtbot):
    from PySide6.QtGui import QPixmap
    files = sorted(GUIDE_DIR.glob("*.png"))
    total = 0
    for path in files:
        pixmap = QPixmap(str(path))
        assert not pixmap.isNull(), path.name
        # 文字看得清楚、也不會大到拖慢說明視窗。對話框用原尺寸，最窄的是 540 的國健署帳號視窗
        assert 520 <= pixmap.width() <= 1280, (path.name, pixmap.width())
        assert path.stat().st_size < 300 * 1024, path.name
        total += path.stat().st_size
    assert total < 2 * 1024 * 1024                                           # 跟著安裝檔一起發佈：整包不到 2 MB


def test_help_dialog_really_loads_its_images(qtbot):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QImage, QPixmap, QTextDocument
    from PySide6.QtWidgets import QTextBrowser

    from icope_tool.ui.help_dialog import HelpDialog
    dialog = HelpDialog(None)
    qtbot.addWidget(dialog)
    browser = dialog.findChild(QTextBrowser)
    for name in set(_help_images()):
        loaded = browser.loadResource(int(QTextDocument.ResourceType.ImageResource.value), QUrl(name))
        image = loaded if isinstance(loaded, (QImage, QPixmap)) else QImage.fromData(loaded)   # 檔案資源回傳原始位元組
        assert not image.isNull() and image.width() >= 520, name


def test_figures_in_the_help_leave_no_blank_gap_below_them(qtbot):
    """內文的行高 150% 如果也套到放圖片的那一行，每張圖下面會多出半張圖高的空白（一頁只看得到一張圖和一片白）。"""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QTextBrowser

    from icope_tool.ui.help_dialog import HelpDialog
    dialog = HelpDialog(None)
    qtbot.addWidget(dialog)
    document = dialog.findChild(QTextBrowser).document()
    document.setTextWidth(900)
    layout = document.documentLayout()
    checked = 0
    block = document.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            char_format = fragments.fragment().charFormat()
            if char_format.isImageFormat():
                name = char_format.toImageFormat().name()
                bottom = layout.blockBoundingRect(block).y() + QImage(str(GUIDE_DIR / name)).height()
                caption = block.next()
                assert caption.text().startswith("圖 "), name                    # 圖片的下一段就是它的圖說，中間沒有別的段落
                gap = layout.blockBoundingRect(caption).y() - bottom            # 區塊自己的高度不含多出來的行高
                assert 0 <= gap <= 40, (name, gap)
                checked += 1
            fragments += 1
        block = block.next()
    assert checked == len(_help_images())


def test_guide_covers_the_tasks_staff_actually_do():
    text = GUIDE_MD.read_text(encoding="utf-8")
    for heading in ("## 這是什麼", "## 快速開始", "## 常見任務", "## 第一次設定", "## 卡住了怎麼辦", "## 下一步"):
        assert heading in text, heading
    for phrase in ("帶入院所預設", "確認與列印", "只印衛教單張", "設為院所預設", "各項衛教重點", "選擇設定包"):
        assert phrase in text, phrase


def test_every_task_in_the_guide_says_what_success_looks_like():
    """照著做的人要知道「這一步成功了沒」：每個任務段落都要寫出會看到什麼。"""
    text = GUIDE_MD.read_text(encoding="utf-8")
    sections = re.split(r"^(#{2,3} .+)$", text, flags=re.MULTILINE)
    tasks = {title.lstrip("# ").strip(): body for title, body in zip(sections[1::2], sections[2::2])}
    for title in ("快速開始：印出第一份轉介衛教單", "只印衛教單張", "查詢長者今年能不能做 ICOPE", "調整這份轉介單的內容",
                  "診所與轉介單、各項衛教重點", "轉介資源與院所預設", "衛教單張", "匯入設定包", "國健署帳號"):
        assert "你會看到" in tasks[title], title


def test_wording_quoted_in_the_help_and_guide_still_exists_in_the_ui():
    """說明引用的按鈕與訊息要和畫面一字不差。畫面改了字，這裡會失敗：兩份說明（必要時連圖片）要一起改。

    攔得到的是「字被改掉或拿掉」。字還在、但出現的時機或按下去的行為變了，這裡看不出來——
    那要靠改畫面的人重產圖片時，照 CLAUDE.md 的清單把兩份說明讀過一遍。
    """
    from icope_tool.ui.dialogs.leaflet_print import CHANGED_TEXT
    from icope_tool.ui.help_dialog import HELP_HTML
    from icope_tool.ui.query_page import VERDICT_VIEW
    ui_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "icope_tool" / "ui").rglob("*.py")
                          if path.name != "help_dialog.py")
    quoted = [view[2] for view in VERDICT_VIEW.values()] + [
        CHANGED_TEXT, "帶入院所預設", "確認與列印", "已送出列印", "開始下一位長者", "保留勾選，只換成這位長者",
        "只印衛教單張…", "下一步：選轉介資源", "下一步：選衛教單張", "下一步：確認與列印", "從資源庫加入…",
        "沒有新增內容，原本就選好了", "目前沒有院所預設，可自行選擇", "設為院所預設", "選擇設定包…", "合併（建議）",
        "設定帳號密碼", "儲存並測試登入", "登入成功", "已儲存診所資訊與衛教重點", "無法產生 PDF",
        "列印失敗，這份轉介單還沒印出", "已暫停自動登入", "無法存取資料存放位置", "重新指定 PDF…", "檔案遺失",
        "未讀健保卡，無法核對年齡"]
    guide = GUIDE_MD.read_text(encoding="utf-8")
    for text in quoted:
        assert text in ui_source, f"畫面上已經沒有「{text}」：請更新這份清單、help_dialog.py 與 docs/GUIDE.md"
        assert text in guide, f"docs/GUIDE.md 沒有照畫面寫「{text}」"
        assert text in HELP_HTML, f"help_dialog.py 沒有照畫面寫「{text}」"
