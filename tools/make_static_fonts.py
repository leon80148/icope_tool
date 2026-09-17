"""把 Google Fonts 的 Noto Sans TC 可變字型轉成 PDF 用的靜態 Regular / Bold。

fpdf2 每次實例化可變字型要 20 秒以上，所以打包前先轉好放進 icope_tool/services/pdf/fonts/。
用法：python tools/make_static_fonts.py <NotoSansTC[wght].ttf>
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

OUT = Path(__file__).resolve().parents[1] / "icope_tool" / "services" / "pdf" / "fonts"


def main(source: str) -> int:
    for weight, name in ((400, "Regular"), (700, "Bold")):
        font = TTFont(source)
        static = instantiateVariableFont(font, {"wght": weight}, updateFontNames=True)
        target = OUT / f"NotoSansTC-{name}.ttf"
        static.save(target)
        print("saved", target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
