"""年度計畫代碼。

hpdcs 的查詢頁依計畫分開：/EardlyFunction_V2/EFA_{民國年}/…（正式）與 EFA_Pilot_{民國年}（試辦）。
預設依今天的民國年自動推導，每年免改設定；診所可只勾自己參與的計畫，或填自訂代碼覆寫。
"""
from __future__ import annotations

import re
from datetime import date

from icope_tool.models import HpdcsPrefs

_PLAN_RE = re.compile(r"^EFA_(?:(Pilot)_)?(\d+)$")
PLAN_CODE_RE = re.compile(r"^[A-Za-z0-9_]{3,40}$")


def roc_year(today: date) -> int:
    return today.year - 1911


def plan_codes(prefs: HpdcsPrefs, today: date) -> tuple[str, ...]:
    custom = tuple(p.strip() for p in prefs.custom_plans if p and p.strip())
    if custom:
        return custom
    year = roc_year(today)
    codes = []
    if prefs.official:
        codes.append(f"EFA_{year}")
    if prefs.pilot:
        codes.append(f"EFA_Pilot_{year}")
    return tuple(codes)


def plan_label(code: str) -> str:
    """EFA_115 → 115 年度正式計畫；EFA_Pilot_115 → 115 年度試辦計畫；其他原樣。"""
    match = _PLAN_RE.match(code)
    if not match:
        return code
    pilot, year = match.group(1), match.group(2)
    return f"{year} 年度試辦計畫" if pilot else f"{year} 年度正式計畫"


def parse_custom_plans(text: str) -> tuple[list[str], list[str]]:
    """把輸入框文字（逗號、頓號、空白分隔）拆成代碼；回傳 (合法代碼, 不合法片段)。"""
    parts = [p for p in re.split(r"[,，、\s]+", text or "") if p]
    good = [p for p in parts if PLAN_CODE_RE.match(p)]
    bad = [p for p in parts if not PLAN_CODE_RE.match(p)]
    return good, bad
