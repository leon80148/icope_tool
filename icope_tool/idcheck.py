"""身分證字號／居留證號格式與檢查碼驗證。

- 國民身分證：1 英文字母 + [12] + 8 碼數字，含檢查碼
- 新式居留證（2021 起）：1 英文字母 + [89] + 8 碼數字，檢查碼算法同身分證
- 舊式居留證：2 英文字母 + 8 碼數字，只驗格式（避免誤擋）
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LETTER_CODES = {
    "A": 10, "B": 11, "C": 12, "D": 13, "E": 14, "F": 15, "G": 16, "H": 17,
    "I": 34, "J": 18, "K": 19, "L": 20, "M": 21, "N": 22, "O": 35, "P": 23,
    "Q": 24, "R": 25, "S": 26, "T": 27, "U": 28, "V": 29, "W": 32, "X": 30,
    "Y": 31, "Z": 33,
}
_WEIGHTS = (1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1)

_NATIONAL = re.compile(r"^[A-Z][12]\d{8}$")
_RESIDENT_NEW = re.compile(r"^[A-Z][89]\d{8}$")
_RESIDENT_OLD = re.compile(r"^[A-Z][A-D]\d{8}$")
_TYPING = re.compile(r"^[A-Z]?[A-Z0-9]?\d{0,8}$")


@dataclass(frozen=True)
class IdCheck:
    ok: bool
    value: str          # 正規化後
    state: str          # empty / typing / format / checksum / ok
    message: str = ""


def normalize_person_id(text: str) -> str:
    """全形轉半形、去空白與連字號、轉大寫。"""
    value = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"[\s\-]", "", value).upper()


def checksum_ok(value: str) -> bool:
    code = LETTER_CODES[value[0]]
    digits = [code // 10, code % 10, *(int(c) for c in value[1:])]
    return sum(d * w for d, w in zip(digits, _WEIGHTS)) % 10 == 0


def mask_person_id(value: str) -> str:
    """A123456789 → A12****789（畫面上的查詢紀錄用）。"""
    if len(value) != 10:
        return value
    return f"{value[:3]}****{value[7:]}"


def check_person_id(text: str) -> IdCheck:
    value = normalize_person_id(text)
    if not value:
        return IdCheck(False, value, "empty")
    if _NATIONAL.match(value) or _RESIDENT_NEW.match(value):
        if checksum_ok(value):
            return IdCheck(True, value, "ok")
        return IdCheck(False, value, "checksum", "檢查碼不符，請核對是否有打錯字")
    if _RESIDENT_OLD.match(value):
        return IdCheck(True, value, "ok")
    if len(value) < 10 and _TYPING.match(value):
        return IdCheck(False, value, "typing", f"還差 {10 - len(value)} 碼")
    return IdCheck(False, value, "format", "格式應為 1 個英文字母加 9 碼數字，例如 A123456789")
