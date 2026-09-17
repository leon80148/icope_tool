"""長者功能評估（ICOPE）的 8 個評估項目。

國健署表單固定這 8 項；轉介資源與衛教單張都以 domain id 標記「適用項目」。
color 皆選與白字對比 ≥ 4.5:1 的色碼（PDF 色塊標籤與 UI 標籤共用）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    id: str
    name: str            # 短名：UI 標籤、PDF 色塊
    title: str           # 完整名稱
    description: str     # 這一項在評估什麼
    icon: str            # resources/icons/<icon>.svg
    color: str
    excel_code: str | None = None   # 社區資源盤點表「類別」欄的次類別字母（A–F）


DOMAINS: tuple[Domain, ...] = (
    Domain("cognitive", "認知", "認知功能", "記憶力、定向力、判斷力", "brain", "#0E7490", "A"),
    Domain("mobility", "行動", "行動能力", "步態、平衡、下肢肌力", "footprints", "#1D4ED8", "B"),
    Domain("nutrition", "營養", "營養狀態", "體重減輕、食慾下降", "apple", "#15803D", "C"),
    Domain("vision", "視力", "視力", "看遠、看近、眼睛疾病", "eye", "#6D28D9", "D"),
    Domain("hearing", "聽力", "聽力", "聽不清楚、溝通困難", "ear", "#B45309", "E"),
    Domain("depression", "憂鬱", "憂鬱情緒", "情緒低落、失去興趣", "cloud-rain", "#BE185D", "F"),
    Domain("medication", "用藥", "用藥安全", "多重用藥、服藥順從性", "pill", "#B91C1C"),
    Domain("social", "社會", "社會照護與支持", "獨居、缺乏照顧或支持", "users", "#475569"),
)

DOMAIN_IDS: tuple[str, ...] = tuple(d.id for d in DOMAINS)
DOMAIN_BY_ID: dict[str, Domain] = {d.id: d for d in DOMAINS}
DOMAIN_BY_EXCEL_CODE: dict[str, Domain] = {d.excel_code: d for d in DOMAINS if d.excel_code}
DOMAIN_BY_NAME: dict[str, Domain] = {
    **{d.name: d for d in DOMAINS},
    **{d.title: d for d in DOMAINS},
}


def sort_domain_ids(ids) -> list[str]:
    """去重並依表單順序排序。未知 id 丟棄。"""
    wanted = set(ids)
    return [d for d in DOMAIN_IDS if d in wanted]


def domain_names(ids, sep: str = "、") -> str:
    return sep.join(DOMAIN_BY_ID[i].name for i in sort_domain_ids(ids))
