"""年度計畫代碼。

hpdcs 的查詢頁依計畫分開：/EardlyFunction_V2/EFA_{民國年}/…（正式）與 EFA_Pilot_{民國年}（試辦）。
預設依今天的民國年自動推導，每年免改設定；診所可只勾自己參與的計畫，或填自訂代碼覆寫。
命名規則只寫在這裡的 _TEMPLATES：畫面、工具、測試都從 PlanCode 推導，不得把年份寫死。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from icope_tool.models import HpdcsPrefs

_TEMPLATES = {"official": "EFA_{year}", "pilot": "EFA_Pilot_{year}"}
_KIND_NAMES = {"official": "正式", "pilot": "試辦"}
_PLAN_RE = re.compile(r"^EFA_(?:(Pilot)_)?(\d+)$")
PLAN_CODE_RE = re.compile(r"^[A-Za-z0-9_]{3,40}$")


def roc_year(today: date) -> int:
    return today.year - 1911


@dataclass(frozen=True)
class PlanCode:
    """一個計畫代碼：kind 是 official／pilot／other（不合命名規則的自訂代碼），year 是民國年。"""

    code: str
    kind: str
    year: int | None

    @classmethod
    def parse(cls, code: str) -> "PlanCode":
        match = _PLAN_RE.match(code)
        if not match:
            return cls(code, "other", None)
        return cls(code, "pilot" if match.group(1) else "official", int(match.group(2)))

    @classmethod
    def make(cls, kind: str, year: int) -> "PlanCode":
        return cls(_TEMPLATES[kind].format(year=year), kind, year)

    @property
    def kind_name(self) -> str:
        return _KIND_NAMES.get(self.kind, "")

    @property
    def label(self) -> str:
        """EFA_115 → 115 年度正式計畫；EFA_Pilot_115 → 115 年度試辦計畫；其他原樣。"""
        if self.year is None:
            return self.code
        return f"{self.year} 年度{self.kind_name}計畫"


def plan_codes(prefs: HpdcsPrefs, today: date) -> tuple[str, ...]:
    custom = [p.strip() for p in prefs.custom_plans if p and p.strip()]
    if custom:
        return tuple(dict.fromkeys(custom))            # 去重、保留順序（快取與過期判斷都比對整個 tuple）
    year = roc_year(today)
    return tuple(PlanCode.make(kind, year).code for kind in ("official", "pilot")
                 if getattr(prefs, kind))


def plan_label(code: str) -> str:
    return PlanCode.parse(code).label


def plans_summary(plans: tuple[str, ...]) -> str:
    """('EFA_115', 'EFA_Pilot_115') → 「 115 年度正式、試辦計畫」（開頭留一格接在「查詢」後面）；其他情況逐一列出。"""
    if not plans:
        return "計畫：尚未選擇"
    parsed = [PlanCode.parse(p) for p in plans]
    years = {p.year for p in parsed}
    if any(p.year is None for p in parsed) or len(years) != 1:
        return "計畫：" + "、".join(p.label for p in parsed)
    return f" {years.pop()} 年度{'、'.join(p.kind_name for p in parsed)}計畫"


def parse_custom_plans(text: str) -> tuple[list[str], list[str]]:
    """把輸入框文字（逗號、頓號、空白分隔）拆成代碼；回傳 (合法代碼, 不合法片段)。"""
    parts = [p for p in re.split(r"[,，、\s]+", text or "") if p]
    good = [p for p in parts if PLAN_CODE_RE.match(p)]
    bad = [p for p in parts if not PLAN_CODE_RE.match(p)]
    return good, bad
