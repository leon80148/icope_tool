"""健保卡「基本資料段」解析（移植自參考專案的 card-agent）— 純函式模組（零 pyscard / 零硬體依賴，可獨立單元測試）。

回應 layout（57 bytes；✅ 2026-08-29 實卡實測定案——114/12 發卡的新世代卡逐欄與卡面核對相符）：

    [0:12]   卡號（12 碼 ASCII 數字）
    [12:32]  姓名（20 bytes，Big5 編碼，尾端補空白或 \\x00；異體字可能解碼失敗 → replacement）
    [32:42]  身分證號（10 碼 ASCII，1 英文 + 9 數字）
    [42:49]  出生日期（民國 YYYMMDD，7 碼 ASCII）
    [49]     性別（單一 byte；實測為 'M'/'F'。原樣 pass-through，不做映射）
    [50:57]  發卡日期（民國 YYYMMDD，7 碼 ASCII）

驗證策略：只對 pid（本功能唯一 load-bearing 欄位）嚴格驗證格式；
其餘欄位寬鬆處理（僅顯示用途，解不出不應讓整次讀卡失敗）。
"""
import re

BASIC_RESPONSE_LEN = 57
PID_RE = re.compile(r'^[A-Z][0-9]{9}$')


class ParseError(Exception):
    """卡片回應無法解析（長度不符 / 身分證欄位格式不符）。"""


def _ascii_field(data: bytes) -> str:
    """ASCII 欄位：去除尾端 \\x00 與空白後解碼（errors='replace' 防禦非 ASCII 雜訊）。"""
    return data.rstrip(b'\x00 ').decode('ascii', errors='replace').strip()


def _big5_name(data: bytes) -> str:
    """姓名欄位：去尾端補字後以 Big5 解碼；異體字解不出以 U+FFFD 取代（僅顯示用途）。"""
    return data.rstrip(b'\x00 ').decode('big5', errors='replace').strip()


def parse_basic_response(data: bytes) -> dict:
    """解析 57-byte 基本資料段回應。

    回傳 dict：card_no / name / pid / birth_roc / sex / issue_date_roc（全為 str）。
    長度不是 57 或 pid 格式不符 → raise ParseError。
    """
    if not isinstance(data, (bytes, bytearray)):
        raise ParseError('回應型別錯誤')
    data = bytes(data)
    if len(data) != BASIC_RESPONSE_LEN:
        raise ParseError(f'回應長度 {len(data)} != {BASIC_RESPONSE_LEN}')

    pid = _ascii_field(data[32:42]).upper()
    if not PID_RE.match(pid):
        raise ParseError('身分證欄位格式不符（非 1 英文 + 9 數字）')

    return {
        'card_no': _ascii_field(data[0:12]),
        'name': _big5_name(data[12:32]),
        'pid': pid,
        'birth_roc': _ascii_field(data[42:49]),
        'sex': _ascii_field(data[49:50]),
        'issue_date_roc': _ascii_field(data[50:57]),
    }
