"""把例外轉成給櫃檯人員看的訊息：發生什麼事、可以怎麼做。"""
from __future__ import annotations

from dataclasses import dataclass

from icope_tool.eligibility import ELDER_AGE, INDIGENOUS_AGE, age_in_year
from icope_tool.services.hpdcs.client import (
    BusyError, CredentialError, LayoutChanged, NetworkError, PlanConfigError, SessionExpired,
)
from icope_tool.store import StoreError


@dataclass
class ErrorView:
    kind: str            # warning / danger
    title: str
    text: str
    action: str          # retry / settings_hpdcs / settings_local / open_site / none


def describe_query_error(exc: BaseException, credentials_configured: bool) -> ErrorView:
    if isinstance(exc, CredentialError):
        if not credentials_configured:
            return ErrorView("warning", "還沒設定國健署系統帳號",
                             "設定帳號密碼後，就能在這裡直接查詢。", "settings_hpdcs")
        if "停用" in str(exc) or "暫停" in str(exc) or "登入失敗" in str(exc):
            return ErrorView("danger", "國健署系統登入失敗，已暫停自動登入",
                             "帳號或密碼可能不正確，或帳號被停用。為避免帳號被鎖，程式已停止重試；"
                             "請到設定確認後重新儲存帳號密碼，就會自動恢復。", "settings_hpdcs")
        return ErrorView("warning", "驗證碼已失效", str(exc), "retry")
    if isinstance(exc, PlanConfigError):
        return ErrorView("warning", "還沒選擇要查詢的計畫", str(exc), "settings_hpdcs")
    if isinstance(exc, NetworkError):
        return ErrorView("danger", "連不上國健署系統",
                         f"{exc}。國健署網站偶爾會維護或回應很慢，可以稍後重試，或先改用網站查詢。", "retry")
    if isinstance(exc, BusyError):
        return ErrorView("warning", "上一筆查詢還在進行", "請等上一筆完成後再查詢。", "retry")
    if isinstance(exc, SessionExpired):
        return ErrorView("danger", "國健署系統連線不穩定",
                         "登入狀態一直失效，可能有其他人正用同一個帳號登入。請稍後重試。", "retry")
    if isinstance(exc, LayoutChanged):
        return ErrorView("danger", "國健署網站的畫面可能改版了",
                         f"{exc}。請先到國健署網站查詢，並通知系統管理者更新程式。", "open_site")
    if isinstance(exc, StoreError):
        return ErrorView("danger", "無法讀取設定資料", str(exc), "settings_local")
    return ErrorView("danger", "查詢時發生未預期的錯誤",
                     f"{type(exc).__name__}：{exc}\n可以重試一次；若持續發生，請改用國健署網站查詢並通知管理者。",
                     "retry")


def describe_age(birth_roc: str, year: int) -> tuple[str, str]:
    """查詢結果旁的年齡核對：回傳（標籤樣式, 文字）。只是提醒——國健署的判定與能按的按鈕都不因此改變。"""
    if not birth_roc:
        return "caption", f"未讀健保卡，無法核對年齡（評估對象：{ELDER_AGE} 歲以上，原住民 {INDIGENOUS_AGE} 歲以上）"
    age = age_in_year(birth_roc, year)
    if age is None:
        return "caption", "健保卡上的出生日期無法核對年齡"      # 雜訊或不合理的年份；不說「無法判讀」，免得一直重插卡
    born = f"民國 {int(birth_roc[:3])} 年出生，今年度滿 {age} 歲"
    if age >= ELDER_AGE:
        return "success", f"{born}（符合 {ELDER_AGE} 歲以上）"
    if age >= INDIGENOUS_AGE:
        return "warning", f"{born}，未滿 {ELDER_AGE} 歲；原住民 {INDIGENOUS_AGE} 歲以上可評估，請確認身分"
    return "warning", f"{born}，未滿 {ELDER_AGE} 歲（原住民為 {INDIGENOUS_AGE} 歲），不符合評估年齡"


def local_save_failed(path) -> str:
    return (f"無法寫入這台電腦的設定檔（{path}），這次的變更只在程式開著時有效，下次開啟會還原。"
            "請確認磁碟空間，或請管理者檢查使用者資料夾的權限。")


CARD_HINTS = {
    "NO_READER": "請確認讀卡機的 USB 線已接上。若這台電腦接了兩台讀卡機，可到「設定 › 本機設定」指定健保卡用的那台。",
    "NO_CARD": "請把健保卡晶片朝上、完整插入讀卡機後再按一次。",
    "CARD_IN_USE": "HIS 可能正在讀這張卡，等幾秒再按一次即可。",
    "NOT_NHI_CARD": "插入的可能是自然人憑證或醫事人員卡，請改插健保卡。",
    "PARSE_ERROR": "請把卡片拔出重新插入後再試；若仍失敗，請手動輸入身分證字號。",
    "READ_ERROR": "請把卡片拔出重新插入後再試；若仍失敗，請手動輸入身分證字號。",
}
