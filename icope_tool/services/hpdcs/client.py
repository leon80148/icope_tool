"""國健署 hpdcs.hpa.gov.tw ICOPE「個案身分證檢核」查詢 client。

移植自參考專案（一套內部使用的網頁版）的 hpdcs_client.py（協定見 docs/hpdcs_protocol.md），差異：
- 路徑、計畫、帳密一律由建構子注入，不讀環境變數、沒有模組層單例
  （data 資料夾是程式啟動後才決定的，且可能是多台電腦共用的資料夾）。
- ddddocr 懶載入：import 會連帶載入 onnxruntime 與 OpenCV（約數秒），不可拖慢程式啟動。
- session 失效時先改用共用資料夾裡「其他電腦較新的登入 cookie」，沒有才重新登入，
  避免同一帳號在多台電腦間互相踢下線。

安全原則（沿用）：
- 登入失敗分類 default-closed：只有 Login.ashx 的 Msg 明確含「驗證碼」才重試；
  其餘（含帳密錯）立即停止並停用自動登入，避免累計錯誤觸發帳號鎖定。
- 快取以身分證 sha256 前綴為 key，明文身分證不落在快取結構。
- 密碼只在登入 POST 當下使用，不出現在任何回應、狀態或例外訊息。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import secrets
import threading
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from icope_tool.services.hpdcs.credentials import mask_account
from icope_tool.services.hpdcs.plans import plan_label
from icope_tool.store import atomic_write_bytes

# =============================================================================
# 常數（hpdcs 改版只改此處；值由實地側錄定案）
# =============================================================================
BASE_URL = "https://hpdcs.hpa.gov.tw"
SITE_URL = BASE_URL + "/index.aspx"
INDEX_PATH = "/index.aspx"
LOGIN_API = "/Login.ashx"
DEFAULT_PATH = "/Default.aspx"      # 登入後須先訪問，建立 session context
CAPTCHA_PATH = "/ValidateCode.aspx"
CHECK_PATH_TMPL = "/EardlyFunction_V2/{plan}/EF2_CheckIDExist.aspx"   # "Eardly" 為官方拼字

TBPID_FIELD = "ctl00$ContentPlaceHolder1$TBPID"
TBPID_ID = "ContentPlaceHolder1_TBPID"
CHECK_BTN_FIELD = "ctl00$ContentPlaceHolder1$BtnCheck"
CHECK_BTN_VALUE = "檢查"
RESULT_SPAN_ID = "ContentPlaceHolder1_Lmsg1"

DONE_MARKER = "該身分證已被登錄"
DONE_OTHER_MARKER = "已被其它計畫登錄"
CAN_ASSESS_MARKER = "可以繼續評估"
CANNOT_MARKER = "無法繼續評估"
LOGIN_OK_RESULT = "1"
CAPTCHA_ERR_HINT = "驗證碼"

CAPTCHA_MAX_RETRY = 3
PENDING_CAPTCHA_TTL = 180          # 人工驗證碼半開登入存活秒數
SESSION_TIMEOUT = 30 * 60          # hpdcs session 逾時；持久化 cookie 逾此視為過期
TIMEOUT = (10, 30)                 # 每請求 (connect, read)
QUERY_DEADLINE = 90                # 單次查詢全程預算
LOCK_TIMEOUT = 30

_USER_AGENT = "Mozilla/5.0 (compatible; IcopeTool/1.0)"


# =============================================================================
# 例外
# =============================================================================
class HpdcsError(Exception):
    """hpdcs 整合的基底例外；訊息可直接顯示給使用者。"""


class CredentialError(HpdcsError):
    """帳密未設定或錯誤。錯誤時停用自動登入（防鎖帳號），重新儲存帳密才解除。"""


class CaptchaManualRequired(HpdcsError):
    """自動辨識連續失敗，需人工輸入驗證碼。攜帶驗證碼圖與續作 token。"""

    def __init__(self, image_bytes: bytes, content_type: str | None, token: str):
        super().__init__("驗證碼無法自動辨識，請手動輸入")
        self.image_bytes = image_bytes
        self.content_type = content_type or "image/gif"
        self.token = token


class SessionExpired(HpdcsError):
    """查詢時發現 session 已過期。"""


class NetworkError(HpdcsError):
    """連線或逾時。"""


class LayoutChanged(HpdcsError):
    """回應結構不符預期（hpdcs 可能改版）。default-closed：回報而非臆測。"""


class BusyError(HpdcsError):
    """另一個查詢正在進行。"""


class PlanConfigError(HpdcsError):
    """沒有任何要查詢的計畫。"""


class QueryCancelled(HpdcsError):
    """使用者取消查詢。"""


# =============================================================================
# 結果
# =============================================================================
class PlanResult:
    """單一計畫的檢核結果。status：done / done_other / can_assess / blocked。"""

    __slots__ = ("plan", "status", "raw")

    def __init__(self, plan: str, status: str, raw: str | None = None):
        self.plan = plan
        self.status = status
        self.raw = raw

    @property
    def label(self) -> str:
        return plan_label(self.plan)

    @property
    def done(self) -> bool:
        return self.status == "done"

    @property
    def can_assess(self) -> bool:
        return self.status == "can_assess"

    @property
    def counts_as_done(self) -> bool:
        return self.status in ("done", "done_other")

    @property
    def message(self) -> str:
        """hpdcs 原文的說明部分（去掉「今年…：X，」前綴與結尾驚嘆號）。"""
        text = (self.raw or "").replace("[ICOPE評估表]", "").strip()
        if "，" in text:
            text = text.split("，", 1)[1]
        return text.rstrip("！!").strip()


class IcopeResult:
    """跨計畫彙整。done_this_year = 任一計畫已登錄（含被其它計畫登錄）。"""

    __slots__ = ("done_this_year", "can_assess_any", "plans", "cached", "checked_at")

    def __init__(self, plans: list[PlanResult], cached: bool = False, checked_at: datetime | None = None):
        self.plans = plans
        self.done_this_year = any(p.counts_as_done for p in plans)
        self.can_assess_any = any(p.can_assess for p in plans)
        self.cached = cached
        self.checked_at = checked_at or datetime.now()

    @property
    def verdict(self) -> str:
        """done（今年已做過）/ can_assess（可以做）/ blocked（無法做，非已做）。"""
        if self.done_this_year:
            return "done"
        if self.can_assess_any:
            return "can_assess"
        return "blocked"


def pid_key(person_id: str) -> str:
    return hashlib.sha256(person_id.encode("utf-8")).hexdigest()[:16]


def ocr_available() -> bool:
    try:
        return importlib.util.find_spec("ddddocr") is not None
    except (ImportError, ValueError):
        return False


# =============================================================================
# Client
# =============================================================================
class HpdcsClient:
    """thread-safe；全域 lock 序列化所有登入與查詢。"""

    def __init__(
        self,
        credentials_provider: Callable[[], tuple[str, str] | None],
        plans_provider: Callable[[], tuple[str, ...]],
        cookie_path: Path | None = None,
        session_factory: Callable[[], requests.Session] = requests.Session,
        ocr_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        today_fn: Callable[[], date] = date.today,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._credentials_provider = credentials_provider
        self._plans_provider = plans_provider
        self._cookie_path = Path(cookie_path) if cookie_path else None
        self._session_factory = session_factory
        self._ocr_factory = ocr_factory
        self._clock = clock
        self._wall_time = wall_time
        self._today = today_fn
        self._sleep = sleep

        self._lock = threading.Lock()
        self._ocr_lock = threading.Lock()
        self._session: requests.Session | None = None
        self._logged_in = False
        self._auto_login_disabled = False
        self._last_login_at: str | None = None
        self._last_error: str | None = None
        self._ocr: Any = None
        self._pending_captcha: dict | None = None
        self._cache: dict[tuple[str, tuple[str, ...]], tuple[date, IcopeResult]] = {}
        self._cookie_stamp: float | None = None   # 目前 session 所用 cookie 檔的 saved_at
        self._cancel: threading.Event | None = None
        self._invalidate_pending = False           # 查詢進行中收到的清除要求，下一個操作開始時套用
        self._clear_cache_pending = False

    # ---- 對外 API -------------------------------------------------------
    def status(self) -> dict:
        creds = self._safe_credentials()
        try:
            plans = self._plans_provider()
        except Exception:
            plans = ()
        return {
            "configured": creds is not None,
            "account_masked": mask_account(creds[0]) if creds else "",
            "logged_in": self._logged_in,
            "auto_login_disabled": self._auto_login_disabled,
            "last_login_at": self._last_login_at,
            "last_error": self._last_error,
            "ocr_available": self._ocr_factory is not None or ocr_available(),
            "active_plans": [{"plan": p, "label": plan_label(p)} for p in plans],
        }

    def invalidate(self) -> None:
        """清除 session、停用旗標與快取（帳密或計畫變更時呼叫），同時刪除持久化 cookie。

        從畫面呼叫時不等待：有查詢正在進行（持有鎖）就先記下，下一個操作開始時套用，視窗不會卡住。
        """
        self._auto_login_disabled = False           # 讓畫面立刻反映「已重新儲存帳密」
        self._last_error = None
        if self._lock.acquire(blocking=False):
            try:
                self._apply_invalidate()
            finally:
                self._lock.release()
        else:
            self._invalidate_pending = True

    def clear_cache(self) -> None:
        if self._lock.acquire(blocking=False):
            try:
                self._cache.clear()
                self._clear_cache_pending = False
            finally:
                self._lock.release()
        else:
            self._clear_cache_pending = True

    def _apply_invalidate(self) -> None:
        self._session = None
        self._logged_in = False
        self._auto_login_disabled = False
        self._pending_captcha = None
        self._last_error = None
        self._cache.clear()
        self._cookie_stamp = None
        self._delete_cookie_file()
        self._invalidate_pending = self._clear_cache_pending = False

    def _apply_pending(self) -> None:
        if self._invalidate_pending:
            self._apply_invalidate()
        elif self._clear_cache_pending:
            self._cache.clear()
            self._clear_cache_pending = False

    def warm_up_ocr(self) -> bool:
        """背景預先載入 OCR 模型，讓第一次查詢不必等。回傳是否可用。"""
        try:
            self._get_ocr()
            return True
        except Exception:
            return False

    def login(self, captcha_token: str | None = None, captcha_text: str | None = None) -> None:
        """強制重新登入（設定頁「測試登入」用）。"""
        self._acquire()
        try:
            deadline = self._clock() + QUERY_DEADLINE
            if captcha_token:
                self._resume_manual_login(captcha_token, captcha_text)
                return
            if self._auto_login_disabled:
                raise CredentialError("先前登入失敗已停用自動登入，請重新儲存正確的帳號密碼")
            self._session = None
            self._logged_in = False
            self._login(deadline)
        finally:
            self._lock.release()

    def refresh_captcha(self, token: str) -> CaptchaManualRequired:
        """人工驗證碼看不清楚時換一張（沿用同一個半開登入 session）。"""
        self._acquire()
        try:
            pending = self._pending_captcha
            if not pending or pending["token"] != token or self._clock() > pending["expires_at"]:
                raise CredentialError("驗證碼已失效，請重新查詢")
            return self._make_manual_captcha(pending["session"], self._clock() + QUERY_DEADLINE)
        finally:
            self._lock.release()

    def query_icope(self, person_id: str, captcha_token: str | None = None,
                    captcha_text: str | None = None, force: bool = False,
                    cancel: threading.Event | None = None) -> IcopeResult:
        """查詢某身分證今年是否已做 ICOPE（逐一查詢生效計畫）。cancel 被設定時，在下一個請求前停止。"""
        plans = tuple(self._plans_provider())
        if not plans:
            raise PlanConfigError("尚未選擇要查詢的計畫，請到「設定 › 國健署帳號」勾選")
        self._acquire(cancel)
        self._cancel = cancel
        try:
            deadline = self._clock() + QUERY_DEADLINE
            key = (pid_key(person_id), plans)
            if not force and not captcha_token:
                cached = self._cache.get(key)
                if cached and cached[0] == self._today():
                    return IcopeResult(cached[1].plans, cached=True, checked_at=cached[1].checked_at)

            if captcha_token:
                self._resume_manual_login(captcha_token, captcha_text)
            else:
                self._ensure_login(deadline)

            results = []
            for plan in plans:
                self._check_deadline(deadline)
                try:
                    result = self._check_one_plan(plan, person_id, deadline)
                except SessionExpired:
                    self._logged_in = False
                    result = self._recover_and_retry(plan, person_id, deadline)
                results.append(result)

            outcome = IcopeResult(results)
            self._cache[key] = (self._today(), outcome)
            self._save_cookies(self._session)
            self._last_error = None
            return outcome
        finally:
            self._cancel = None
            self._lock.release()

    # ---- 登入 -----------------------------------------------------------
    def _acquire(self, cancel: threading.Event | None = None) -> None:
        deadline = self._clock() + LOCK_TIMEOUT
        while not self._lock.acquire(timeout=0.2):
            if cancel is not None and cancel.is_set():
                raise QueryCancelled("已取消查詢")
            if self._clock() > deadline:
                raise BusyError("另一筆查詢正在進行，請稍候再試")
        self._apply_pending()

    def _ensure_login(self, deadline: float) -> None:
        if self._auto_login_disabled:
            raise CredentialError("先前登入失敗已停用自動登入，請到設定重新儲存正確的帳號密碼")
        if self._session is not None and self._logged_in:
            return
        if self._session is None and self._safe_credentials():
            # 沿用持久化 cookie（程式重開或其他電腦剛登入過）；過期時查詢會偵測並自動重登
            if self._adopt_cookie_file(require_newer=False):
                return
        self._login(deadline)

    def _recover_and_retry(self, plan: str, person_id: str, deadline: float) -> PlanResult:
        """session 失效：先試共用資料夾裡較新的 cookie，不行再重新登入；各最多一次。"""
        if self._adopt_cookie_file(require_newer=True):
            try:
                return self._check_one_plan(plan, person_id, deadline)
            except SessionExpired:
                self._logged_in = False
        if self._auto_login_disabled:
            raise CredentialError("先前登入失敗已停用自動登入，請到設定重新儲存正確的帳號密碼")
        self._login(deadline)
        return self._check_one_plan(plan, person_id, deadline)

    def _login(self, deadline: float) -> None:
        creds = self._safe_credentials()
        if not creds:
            raise CredentialError("尚未設定國健署系統帳號密碼")
        account, password = creds

        session = self._new_session()
        self._http(session, "get", INDEX_PATH, deadline)

        for _ in range(CAPTCHA_MAX_RETRY):
            self._check_deadline(deadline)
            code = self._solve_captcha(session, deadline)
            if not code:
                continue
            result, msg = self._post_login(session, account, password, code, deadline)
            if result == LOGIN_OK_RESULT:
                self._finish_login(session, deadline)
                return
            if CAPTCHA_ERR_HINT in (msg or ""):
                continue
            raise self._credential_failure()

        raise self._make_manual_captcha(session, deadline)

    def _credential_failure(self) -> CredentialError:
        """帳密或帳號狀態錯誤：停用自動登入（default-closed，防鎖帳號）。"""
        self._auto_login_disabled = True
        self._logged_in = False
        self._last_error = "credential"
        return CredentialError("國健署系統登入失敗（帳號密碼錯誤或帳號狀態異常），已暫停自動登入以免帳號被鎖")

    def _finish_login(self, session: requests.Session, deadline: float) -> None:
        self._http(session, "get", DEFAULT_PATH, deadline)
        self._session = session
        self._logged_in = True
        self._last_login_at = datetime.now().isoformat(timespec="seconds")
        self._last_error = None
        self._pending_captcha = None
        self._save_cookies(session)

    def _resume_manual_login(self, token: str, captcha_text: str | None) -> None:
        pending = self._pending_captcha
        if not pending or pending["token"] != token:
            raise CredentialError("驗證碼已失效，請重新查詢")
        if self._clock() > pending["expires_at"]:
            self._pending_captcha = None
            raise CredentialError("驗證碼輸入逾時，請重新查詢")
        if not captcha_text or not captcha_text.strip():
            raise CredentialError("請輸入驗證碼")

        creds = self._safe_credentials()
        if not creds:
            raise CredentialError("尚未設定國健署系統帳號密碼")
        account, password = creds
        session = pending["session"]
        deadline = self._clock() + QUERY_DEADLINE

        result, msg = self._post_login(session, account, password, captcha_text.strip(), deadline)
        if result == LOGIN_OK_RESULT:
            self._finish_login(session, deadline)
            return
        if CAPTCHA_ERR_HINT in (msg or ""):
            raise self._make_manual_captcha(session, deadline)
        raise self._credential_failure()

    def _make_manual_captcha(self, session: requests.Session, deadline: float) -> CaptchaManualRequired:
        image, content_type = self._get_captcha(session, deadline)
        token = secrets.token_urlsafe(16)
        self._pending_captcha = {
            "token": token,
            "session": session,
            "expires_at": self._clock() + PENDING_CAPTCHA_TTL,
        }
        return CaptchaManualRequired(image, content_type, token)

    def _solve_captcha(self, session: requests.Session, deadline: float) -> str | None:
        """回傳自動辨識的 5 碼數字；辨識失敗或無 OCR 回 None。"""
        if self._ocr_factory is None and not ocr_available():
            return None
        image, _ = self._get_captcha(session, deadline)
        try:
            ocr = self._get_ocr()
            code = (ocr.classification(image) or "").strip()
        except Exception:
            return None
        if len(code) == 5 and code.isdigit():
            return code
        return None

    def _get_ocr(self):
        with self._ocr_lock:
            if self._ocr is None:
                if self._ocr_factory is not None:
                    self._ocr = self._ocr_factory()
                else:
                    import ddddocr   # 懶載入（連帶 onnxruntime / OpenCV）
                    import onnxruntime
                    onnxruntime.disable_telemetry_events()   # Windows 版預設會發出遙測事件；這個程式不回傳任何使用統計
                    self._ocr = ddddocr.DdddOcr(show_ad=False)
            return self._ocr

    def _get_captcha(self, session: requests.Session, deadline: float) -> tuple[bytes, str | None]:
        resp = self._http(session, "get", CAPTCHA_PATH, deadline,
                          params={"t": str(int(self._wall_time() * 1000))})
        return resp.content, resp.headers.get("content-type")

    def _post_login(self, session, account, password, code, deadline) -> tuple[str, str]:
        resp = self._http(session, "post", LOGIN_API, deadline,
                          data={"sAcc": account, "sPwd": password, "sValidateCode": code})
        try:
            payload = resp.json()
        except ValueError:
            raise LayoutChanged("國健署系統登入回應格式改變了，請改用網站查詢")
        result = str(payload.get("Result", payload.get("result", ""))).strip()
        msg = payload.get("Msg", payload.get("msg", "")) or ""
        return result, msg

    # ---- 查詢 -----------------------------------------------------------
    def _check_one_plan(self, plan: str, person_id: str, deadline: float) -> PlanResult:
        if self._session is None:
            raise SessionExpired("尚未登入")
        path = CHECK_PATH_TMPL.format(plan=plan)
        get_resp = self._http(self._session, "get", path, deadline)
        soup = BeautifulSoup(get_resp.text, "html.parser")
        if self._looks_like_login(soup) or soup.find(id=TBPID_ID) is None:
            raise SessionExpired("session 已過期或查詢頁被導回首頁")

        form = self._collect_hidden(soup)
        form[TBPID_FIELD] = person_id
        form[CHECK_BTN_FIELD] = CHECK_BTN_VALUE
        post_resp = self._http(self._session, "post", path, deadline, data=form)
        return self._parse_result(plan, post_resp.text)

    @staticmethod
    def _collect_hidden(soup: BeautifulSoup) -> dict[str, str]:
        data = {}
        for inp in soup.select("input[type=hidden]"):
            name = inp.get("name")
            if name:
                data[str(name)] = str(inp.get("value", ""))
        return data

    @staticmethod
    def _looks_like_login(soup: BeautifulSoup) -> bool:
        return bool(soup.find(id="username") or soup.find(id="IMGValidate"))

    def _parse_result(self, plan: str, html: str) -> PlanResult:
        soup = BeautifulSoup(html, "html.parser")
        if self._looks_like_login(soup):
            raise SessionExpired("session 已過期")
        span = soup.find(id=RESULT_SPAN_ID)
        if span is None:
            if soup.find(id=TBPID_ID) is None:
                raise SessionExpired("查詢後沒有結果欄位，疑似 session 中途失效")
            raise LayoutChanged("國健署查詢頁的格式改變了，請改用網站查詢")
        text = span.get_text(strip=True)
        if not text:
            raise SessionExpired("結果欄位為空，疑似 session 中途失效")
        if DONE_MARKER in text:
            return PlanResult(plan, "done", raw=text)
        if DONE_OTHER_MARKER in text:
            return PlanResult(plan, "done_other", raw=text)
        if CAN_ASSESS_MARKER in text:
            return PlanResult(plan, "can_assess", raw=text)
        if CANNOT_MARKER in text:
            return PlanResult(plan, "blocked", raw=text)
        raise LayoutChanged("國健署回覆了無法辨識的結果，請改用網站查詢")

    # ---- HTTP -----------------------------------------------------------
    def _new_session(self) -> requests.Session:
        session = self._session_factory()
        try:
            session.headers.update({"User-Agent": _USER_AGENT})
        except Exception:
            pass
        return session

    def _http(self, session, method: str, path: str, deadline: float, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        url = path if path.startswith("http") else BASE_URL + path
        last_exc: Exception | None = None
        for attempt in range(2):
            self._check_deadline(deadline)
            try:
                return session.request(method, url, **kwargs)
            except requests.ConnectionError as exc:
                last_exc = exc
                if attempt == 0:
                    self._sleep(1)
                    continue
            except requests.RequestException as exc:
                self._last_error = "network"
                raise NetworkError("連不上國健署系統，請確認這台電腦可以上網") from exc
        self._last_error = "network"
        raise NetworkError("連不上國健署系統，請確認這台電腦可以上網") from last_exc

    def _check_deadline(self, deadline: float) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise QueryCancelled("已取消查詢")
        if self._clock() > deadline:
            self._last_error = "timeout"
            raise NetworkError("國健署系統回應太慢，請稍後再試")

    def _safe_credentials(self) -> tuple[str, str] | None:
        try:
            return self._credentials_provider()
        except Exception:
            return None

    # ---- 持久化 cookie（共用資料夾 auth/hpdcs_cookies.json）------------------
    def _read_cookie_file(self) -> dict | None:
        if self._cookie_path is None:
            return None
        try:
            data = json.loads(self._cookie_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("cookies"):
            return None
        return data

    def _adopt_cookie_file(self, require_newer: bool) -> bool:
        data = self._read_cookie_file()
        if not data:
            return False
        saved_at = float(data.get("saved_at", 0))
        if self._wall_time() - saved_at > SESSION_TIMEOUT:
            return False
        if require_newer and self._cookie_stamp is not None and saved_at <= self._cookie_stamp:
            return False
        session = self._new_session()
        loaded = 0
        for cookie in data["cookies"]:
            try:
                session.cookies.set(cookie["name"], cookie["value"],
                                    domain=cookie.get("domain") or "", path=cookie.get("path") or "/")
                loaded += 1
            except Exception:
                continue
        if not loaded:
            return False
        self._session = session
        self._logged_in = True
        self._cookie_stamp = saved_at
        return True

    def _save_cookies(self, session: requests.Session | None) -> None:
        if session is None or self._cookie_path is None:
            return
        try:
            cookies = [{
                "name": c.name, "value": c.value, "domain": c.domain,
                "path": c.path or "/", "secure": bool(c.secure),
            } for c in session.cookies]
            if not cookies:
                return
            saved_at = self._wall_time()
            payload = json.dumps({"saved_at": saved_at, "cookies": cookies})
            atomic_write_bytes(self._cookie_path, payload.encode("utf-8"))
            self._cookie_stamp = saved_at
        except (OSError, ValueError):
            pass   # 最佳努力，失敗不影響查詢

    def _delete_cookie_file(self) -> None:
        if self._cookie_path is None:
            return
        try:
            self._cookie_path.unlink()
        except OSError:
            pass
