"""ICOPE 評估的年齡資格：65 歲以上（原住民 55 歲以上），以「年度」計算——當年度會滿就算，不看月日。

出生日期來自健保卡的基本資料段（民國 YYYMMDD）；身分證字號本身不含生日，手動輸入時無從核對。
"""
from __future__ import annotations

ELDER_AGE = 65
INDIGENOUS_AGE = 55


def age_in_year(birth_roc: str | None, year: int) -> int | None:
    """西元 year 這一年會滿幾歲；出生日期讀不出來（健保卡這一欄是寬鬆解析的）回傳 None。"""
    text = (birth_roc or "").strip()
    if len(text) != 7 or not (text.isascii() and text.isdigit()):
        return None
    birth_year = int(text[:3])
    age = (year - 1911) - birth_year
    if birth_year < 1 or age < 0:
        return None
    return age
