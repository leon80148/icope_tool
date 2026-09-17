"""hpdcs client 行為測試：FakeSession / FakeOcr 注入，不連網、不載 onnx。

移植自參考專案並鎖定同樣的關鍵行為：
- 登入 POST 帶 sAcc/sPwd/sValidateCode；Result=='1' 成功，之後先 GET Default.aspx
- 驗證碼錯恰好重試 3 次 → CaptchaManualRequired（含圖 + token）
- 帳密錯只 POST 一次 + 停用自動登入（防鎖帳號）
- SessionExpired 重登一次後成功；連續失效即放棄
- 結果解析四種狀態、跨計畫彙整、當日快取
新增：多台電腦共用 cookie 檔時優先沿用較新的 cookie、計畫設定錯誤、login() 測試登入。
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pytest
import requests
from requests.cookies import RequestsCookieJar

import icope_tool.services.hpdcs.client as hc
from icope_tool.models import HpdcsPrefs
from icope_tool.services.hpdcs.client import (
    BusyError, CaptchaManualRequired, CredentialError, HpdcsClient, LayoutChanged,
    PlanConfigError, PlanResult,
)
from icope_tool.services.hpdcs.plans import parse_custom_plans, plan_codes, plan_label

FIX = Path(__file__).parent / "fixtures" / "hpdcs"


def _fix(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, text="", content=None, json_data=None, headers=None):
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeOcr:
    def __init__(self, code):
        self.code = code

    def classification(self, _img):
        return self.code


class FakeSession:
    """可編程的假 requests.Session。以 sValidateCode 是否等於 correct_captcha 決定登入結果。"""

    def __init__(self, correct_captcha="55555", password_ok=True, pid_result=None,
                 expired_on_check=0, home_on_check=0, home_on_post=0, set_cookie_on_login=False):
        self.headers = {}
        self.cookies = RequestsCookieJar()
        self.correct_captcha = correct_captcha
        self.password_ok = password_ok
        self.pid_result = pid_result or {}
        self.expired_on_check = expired_on_check
        self.home_on_check = home_on_check
        self.home_on_post = home_on_post
        self.set_cookie_on_login = set_cookie_on_login
        self.calls = []
        self._check_get_count = 0
        self._check_post_count = 0

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        m = method.lower()
        if url.endswith("/index.aspx") or url.endswith("/Default.aspx"):
            return FakeResponse(text=_fix("home_no_form.html"))
        if "/ValidateCode.aspx" in url:
            return FakeResponse(content=b"GIF89a-fake-bytes", headers={"content-type": "image/gif"})
        if url.endswith("/Login.ashx"):
            data = kwargs.get("data", {})
            if data.get("sValidateCode") != self.correct_captcha:
                return FakeResponse(json_data={"Result": "0", "Msg": "驗證碼錯誤，請重新輸入"})
            if not self.password_ok:
                return FakeResponse(json_data={"Result": "0", "Msg": "帳號或密碼錯誤"})
            if self.set_cookie_on_login:
                self.cookies.set("ASPSESS", "sess-xyz", domain="hpdcs.hpa.gov.tw", path="/")
            return FakeResponse(json_data={"Result": "1", "Msg": ""})
        if "EF2_CheckIDExist.aspx" in url:
            if m == "get":
                self._check_get_count += 1
                if self._check_get_count <= self.expired_on_check:
                    return FakeResponse(text=_fix("session_expired.html"))
                if self._check_get_count <= self.home_on_check:
                    return FakeResponse(text=_fix("home_no_form.html"))
                return FakeResponse(text=_fix("check_page.html"))
            self._check_post_count += 1
            if self._check_post_count <= self.home_on_post:
                return FakeResponse(text=_fix("home_no_form.html"))
            data = kwargs.get("data", {})
            pid = data.get(hc.TBPID_FIELD, "")
            plan = next((p for p in ("EFA_Pilot_115", "EFA_115", "EFA_114") if "/" + p + "/" in url), None)
            outcome = self.pid_result.get(pid, "notdone")
            if isinstance(outcome, dict):
                outcome = outcome.get(plan, "notdone")
            fixture = {
                "done": "check_done.html",
                "notdone": "check_notdone.html",
                "done_other": "check_done_other.html",
                "blocked": "check_blocked.html",
            }.get(outcome, "check_notdone.html")
            return FakeResponse(text=_fix(fixture))
        raise AssertionError("unexpected url: " + url)


def make_client(session, ocr_code="55555", plans=("EFA_115", "EFA_Pilot_115"),
                creds=("demo001", "pw"), clock=None, cookie_path=None, today_fn=dt.date.today):
    return HpdcsClient(
        credentials_provider=lambda: creds,
        plans_provider=lambda: tuple(plans),
        cookie_path=cookie_path,
        session_factory=lambda: session,
        ocr_factory=lambda: FakeOcr(ocr_code),
        clock=clock or time.monotonic,
        today_fn=today_fn,
        sleep=lambda _s: None,
    )


def login_posts(session):
    return [c for c in session.calls if c[1].endswith("/Login.ashx")]


# ---------------------------------------------------------------------------
# 登入
# ---------------------------------------------------------------------------
def test_login_success_posts_credentials_and_captcha():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="55555")
    result = client.query_icope("A123456789")
    posts = login_posts(session)
    assert len(posts) == 1
    data = posts[0][2]["data"]
    assert data["sAcc"] == "demo001" and data["sPwd"] == "pw" and data["sValidateCode"] == "55555"
    assert client.status()["logged_in"] is True
    assert result.done_this_year is False
    assert result.verdict == "can_assess"


def test_captcha_wrong_retries_three_times_then_manual():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111")
    with pytest.raises(CaptchaManualRequired) as ei:
        client.query_icope("A123456789")
    assert len(login_posts(session)) == hc.CAPTCHA_MAX_RETRY
    assert ei.value.token
    assert ei.value.image_bytes


def test_credential_error_posts_once_and_disables_autologin():
    session = FakeSession(correct_captcha="55555", password_ok=False)
    client = make_client(session, ocr_code="55555")
    with pytest.raises(CredentialError):
        client.query_icope("A123456789")
    assert len(login_posts(session)) == 1
    assert client.status()["auto_login_disabled"] is True
    session.calls.clear()
    with pytest.raises(CredentialError):
        client.query_icope("A123456789")
    assert not login_posts(session)


def test_invalidate_clears_disabled():
    session = FakeSession(correct_captcha="55555", password_ok=False)
    client = make_client(session, ocr_code="55555")
    with pytest.raises(CredentialError):
        client.query_icope("A123456789")
    client.invalidate()
    assert client.status()["auto_login_disabled"] is False


def test_missing_credentials_raises_credential_error():
    session = FakeSession()
    client = make_client(session, creds=None)
    with pytest.raises(CredentialError):
        client.query_icope("A123456789")


def test_login_visits_default_to_establish_context():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, plans=("EFA_115",))
    client.query_icope("A123456789")
    idx_login = next(i for i, c in enumerate(session.calls) if c[1].endswith("/Login.ashx"))
    idx_default = next(i for i, c in enumerate(session.calls)
                       if c[0] == "get" and c[1].endswith("/Default.aspx") and i > idx_login)
    idx_check = next(i for i, c in enumerate(session.calls) if "EF2_CheckIDExist" in c[1])
    assert idx_login < idx_default < idx_check


def test_password_never_exposed_in_status_or_errors():
    session = FakeSession(correct_captcha="55555", password_ok=False)
    client = make_client(session, creds=("demo001", "S3cret!pw"))
    with pytest.raises(CredentialError) as ei:
        client.query_icope("A123456789")
    assert "S3cret" not in str(ei.value)
    assert "S3cret" not in json.dumps(client.status(), ensure_ascii=False)
    assert client.status()["account_masked"] == "dem***"


def test_login_method_forces_fresh_login():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, plans=("EFA_115",))
    client.login()
    client.login()
    assert len(login_posts(session)) == 2
    assert client.status()["logged_in"] is True


# ---------------------------------------------------------------------------
# session 過期
# ---------------------------------------------------------------------------
def test_session_expired_relogins_once_then_succeeds():
    session = FakeSession(correct_captcha="55555", expired_on_check=1, pid_result={"A123456789": "done"})
    client = make_client(session, plans=("EFA_115",))
    result = client.query_icope("A123456789")
    assert result.done_this_year is True
    assert len(login_posts(session)) == 2


def test_session_expired_twice_gives_up():
    session = FakeSession(correct_captcha="55555", expired_on_check=99)
    client = make_client(session, plans=("EFA_115",))
    with pytest.raises(hc.SessionExpired):
        client.query_icope("A123456789")


def test_check_redirected_to_home_triggers_relogin():
    session = FakeSession(correct_captcha="55555", home_on_check=1, pid_result={"A123456789": "done"})
    client = make_client(session, plans=("EFA_115",))
    assert client.query_icope("A123456789").done_this_year is True
    assert len(login_posts(session)) == 2


def test_mid_query_session_loss_self_heals():
    session = FakeSession(correct_captcha="55555", home_on_post=1, pid_result={"A123456789": "done"})
    client = make_client(session, plans=("EFA_115",))
    assert client.query_icope("A123456789").done_this_year is True
    assert len(login_posts(session)) == 2


def test_connection_error_retries_once():
    session = FakeSession(correct_captcha="55555")
    original = session.request
    state = {"boomed": False}

    def flaky(method, url, **kwargs):
        if url.endswith("/index.aspx") and not state["boomed"]:
            state["boomed"] = True
            raise requests.ConnectionError("boom")
        return original(method, url, **kwargs)

    session.request = flaky
    client = make_client(session, plans=("EFA_115",))
    assert client.query_icope("A123456789") is not None


# ---------------------------------------------------------------------------
# 結果解析 + 彙整
# ---------------------------------------------------------------------------
def test_done_in_either_plan_means_done_this_year():
    session = FakeSession(correct_captcha="55555", pid_result={"A123456789": "done"})
    result = make_client(session).query_icope("A123456789")
    assert result.done_this_year is True
    assert result.verdict == "done"
    assert [p.plan for p in result.plans] == ["EFA_115", "EFA_Pilot_115"]


def test_notdone_when_no_plan_registered():
    result = make_client(FakeSession()).query_icope("A123456789")
    assert result.done_this_year is False
    assert result.can_assess_any is True


def test_unrecognized_result_raises_layout_changed():
    client = make_client(FakeSession())
    with pytest.raises(LayoutChanged):
        client._parse_result("EFA_115", '<span id="ContentPlaceHolder1_Lmsg1">奇怪的訊息</span>')


def test_parse_done_other_and_blocked_states():
    client = make_client(FakeSession())
    other = client._parse_result("EFA_Pilot_115", _fix("check_done_other.html"))
    assert other.status == "done_other"
    assert other.counts_as_done and not other.done and not other.can_assess
    blocked = client._parse_result("EFA_Pilot_115", _fix("check_blocked.html"))
    assert blocked.status == "blocked"
    assert not blocked.counts_as_done and not blocked.can_assess
    assert blocked.message == "年齡不符合本計畫收案條件"


def test_all_blocked_verdict():
    session = FakeSession(pid_result={"A123456789": "blocked"})
    result = make_client(session).query_icope("A123456789")
    assert result.verdict == "blocked"


def test_done_in_one_plan_other_plan_done_other_still_done():
    session = FakeSession(pid_result={"A123456789": {"EFA_115": "done", "EFA_Pilot_115": "done_other"}})
    result = make_client(session).query_icope("A123456789")
    assert result.done_this_year is True
    assert {p.plan: p.status for p in result.plans} == {"EFA_115": "done", "EFA_Pilot_115": "done_other"}


def test_empty_lmsg1_is_session_expired():
    client = make_client(FakeSession())
    with pytest.raises(hc.SessionExpired):
        client._parse_result(
            "EFA_115",
            '<span id="ContentPlaceHolder1_Lmsg1"></span><input id="ContentPlaceHolder1_TBPID">',
        )


def test_post_redirected_to_home_is_session_expired_not_layout():
    client = make_client(FakeSession())
    with pytest.raises(hc.SessionExpired):
        client._parse_result("EFA_115", _fix("home_no_form.html"))


def test_plan_result_message_strips_prefix():
    assert PlanResult("EFA_115", "done", raw="今年可以繼續評估：X，該身分證已被登錄！[ICOPE評估表]").message == "該身分證已被登錄"


# ---------------------------------------------------------------------------
# 快取
# ---------------------------------------------------------------------------
def test_cache_same_day_no_second_network_hit():
    session = FakeSession()
    client = make_client(session, plans=("EFA_115",))
    client.query_icope("A123456789")
    n = len(session.calls)
    again = client.query_icope("A123456789")
    assert again.cached is True
    assert len(session.calls) == n


def test_force_bypasses_cache():
    session = FakeSession()
    client = make_client(session, plans=("EFA_115",))
    client.query_icope("A123456789")
    n = len(session.calls)
    client.query_icope("A123456789", force=True)
    assert len(session.calls) > n


def test_cache_key_is_not_plaintext_id():
    client = make_client(FakeSession(), plans=("EFA_115",))
    client.query_icope("A123456789")
    assert all("A123456789" not in key[0] for key in client._cache)


def test_cache_expires_next_day():
    day = [dt.date(2026, 8, 29)]
    session = FakeSession()
    client = make_client(session, plans=("EFA_115",), today_fn=lambda: day[0])
    client.query_icope("A123456789")
    n = len(session.calls)
    day[0] = dt.date(2026, 8, 30)
    result = client.query_icope("A123456789")
    assert result.cached is False
    assert len(session.calls) > n


def test_cache_misses_when_plans_change():
    session = FakeSession()
    plans: list[tuple[str, ...]] = [("EFA_115",)]
    client = HpdcsClient(
        credentials_provider=lambda: ("a", "p"), plans_provider=lambda: plans[0],
        session_factory=lambda: session, ocr_factory=lambda: FakeOcr("55555"), sleep=lambda _s: None,
    )
    client.query_icope("A123456789")
    plans[0] = ("EFA_115", "EFA_Pilot_115")
    assert client.query_icope("A123456789").cached is False


def test_invalidate_clears_cache():
    session = FakeSession()
    client = make_client(session, plans=("EFA_115",))
    client.query_icope("A123456789")
    client.invalidate()
    assert client.query_icope("A123456789").cached is False


# ---------------------------------------------------------------------------
# lock / 計畫設定
# ---------------------------------------------------------------------------
def test_busy_when_lock_held(monkeypatch):
    client = make_client(FakeSession(), plans=("EFA_115",))
    monkeypatch.setattr(hc, "LOCK_TIMEOUT", 0.3)
    client._lock.acquire()
    try:
        with pytest.raises(BusyError):
            client.query_icope("A123456789")
    finally:
        client._lock.release()


def test_no_plans_raises_plan_config_error():
    session = FakeSession()
    client = make_client(session, plans=())
    with pytest.raises(PlanConfigError):
        client.query_icope("A123456789")
    assert session.calls == []


# ---------------------------------------------------------------------------
# 人工驗證碼
# ---------------------------------------------------------------------------
def test_manual_captcha_resume_success():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111")
    with pytest.raises(CaptchaManualRequired) as ei:
        client.query_icope("A123456789")
    result = client.query_icope("A123456789", captcha_token=ei.value.token, captcha_text="55555")
    assert result.done_this_year is False
    assert client.status()["logged_in"] is True


def test_manual_captcha_wrong_again_returns_new_captcha():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111")
    with pytest.raises(CaptchaManualRequired) as first:
        client.query_icope("A123456789")
    with pytest.raises(CaptchaManualRequired) as second:
        client.query_icope("A123456789", captcha_token=first.value.token, captcha_text="00000")
    assert second.value.token != first.value.token


def test_manual_captcha_bad_token_rejected():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111")
    with pytest.raises(CaptchaManualRequired):
        client.query_icope("A123456789")
    with pytest.raises(CredentialError):
        client.query_icope("A123456789", captcha_token="wrong-token", captcha_text="55555")


def test_manual_captcha_expired_token_rejected():
    now = [1000.0]
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111", clock=lambda: now[0])
    with pytest.raises(CaptchaManualRequired) as ei:
        client.query_icope("A123456789")
    now[0] += hc.PENDING_CAPTCHA_TTL + 1
    with pytest.raises(CredentialError):
        client.query_icope("A123456789", captcha_token=ei.value.token, captcha_text="55555")


# ---------------------------------------------------------------------------
# 計畫代碼
# ---------------------------------------------------------------------------
def test_plan_label_parsing():
    assert plan_label("EFA_115") == "115 年度正式計畫"
    assert plan_label("EFA_Pilot_115") == "115 年度試辦計畫"
    assert plan_label("weird") == "weird"


def test_plan_codes_follow_roc_year_and_prefs():
    d = dt.date(2026, 8, 29)
    assert plan_codes(HpdcsPrefs(), d) == ("EFA_115", "EFA_Pilot_115")
    assert plan_codes(HpdcsPrefs(pilot=False), d) == ("EFA_115",)
    assert plan_codes(HpdcsPrefs(official=False), d) == ("EFA_Pilot_115",)
    assert plan_codes(HpdcsPrefs(official=False, pilot=False), d) == ()
    assert plan_codes(HpdcsPrefs(), dt.date(2027, 1, 1)) == ("EFA_116", "EFA_Pilot_116")


def test_custom_plans_override():
    prefs = HpdcsPrefs(official=True, pilot=True, custom_plans=[" EFA_120 ", "", "EFA_Special_120"])
    assert plan_codes(prefs, dt.date(2026, 1, 1)) == ("EFA_120", "EFA_Special_120")


def test_parse_custom_plans_input():
    good, bad = parse_custom_plans("EFA_116, EFA_Pilot_116、壞/代碼")
    assert good == ["EFA_116", "EFA_Pilot_116"]
    assert bad == ["壞/代碼"]


# ---------------------------------------------------------------------------
# 持久化 cookie（單機重開 + 多機共用）
# ---------------------------------------------------------------------------
def test_persisted_cookie_skips_login(tmp_path):
    ck = tmp_path / "ck.json"
    s1 = FakeSession(set_cookie_on_login=True)
    make_client(s1, plans=("EFA_115",), cookie_path=ck).query_icope("A123456789")
    assert ck.exists()
    s2 = FakeSession(set_cookie_on_login=True)
    make_client(s2, plans=("EFA_115",), cookie_path=ck).query_icope("A123456789")
    assert not login_posts(s2)
    assert not any("ValidateCode" in c[1] for c in s2.calls)


def test_expired_persisted_cookie_falls_back_to_login(tmp_path):
    ck = tmp_path / "ck.json"
    s1 = FakeSession(set_cookie_on_login=True)
    make_client(s1, plans=("EFA_115",), cookie_path=ck).query_icope("A123456789")
    data = json.loads(ck.read_text())
    data["saved_at"] = time.time() - hc.SESSION_TIMEOUT - 10
    ck.write_text(json.dumps(data))
    s2 = FakeSession(set_cookie_on_login=True)
    make_client(s2, plans=("EFA_115",), cookie_path=ck).query_icope("A123456789")
    assert login_posts(s2)


def test_invalidate_deletes_cookie_file(tmp_path):
    ck = tmp_path / "ck.json"
    client = make_client(FakeSession(set_cookie_on_login=True), plans=("EFA_115",), cookie_path=ck)
    client.query_icope("A123456789")
    assert ck.exists()
    client.invalidate()
    assert not ck.exists()


class FakeServer:
    """模擬 hpdcs 伺服器：同一帳號同時只有一個有效 session（新登入會踢掉舊的）。"""

    def __init__(self):
        self.valid: str | None = None
        self.counter = 0
        self.login_count = 0


class ServerSession(FakeSession):
    def __init__(self, server: FakeServer):
        super().__init__()
        self.server = server

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/Login.ashx"):
            self.server.counter += 1
            self.server.login_count += 1
            self.server.valid = f"sess-{self.server.counter}"
            self.cookies.set("ASPSESS", self.server.valid, domain="hpdcs.hpa.gov.tw", path="/")
            return FakeResponse(json_data={"Result": "1", "Msg": ""})
        if "EF2_CheckIDExist.aspx" in url and method.lower() == "get":
            if self.cookies.get("ASPSESS") != self.server.valid:
                return FakeResponse(text=_fix("session_expired.html"))
            return FakeResponse(text=_fix("check_page.html"))
        if "EF2_CheckIDExist.aspx" in url:
            return FakeResponse(text=_fix("check_notdone.html"))
        return super().request(method, url, **kwargs)


def _machine(server, ck, wall):
    return HpdcsClient(
        credentials_provider=lambda: ("acc", "pw"), plans_provider=lambda: ("EFA_115",),
        cookie_path=ck, session_factory=lambda: ServerSession(server),
        ocr_factory=lambda: FakeOcr("55555"), wall_time=lambda: wall[0], sleep=lambda _s: None,
    )


def test_shared_cookie_prevents_cross_machine_login_ping_pong(tmp_path):
    server = FakeServer()
    ck = tmp_path / "auth" / "hpdcs_cookies.json"
    wall = [time.time()]
    machine_a = _machine(server, ck, wall)
    machine_b = _machine(server, ck, wall)

    machine_a.query_icope("A123456789")              # A 登入（第 1 次）
    wall[0] += 5
    machine_b.query_icope("B123456789")              # B 沿用 A 的 cookie，不登入
    assert server.login_count == 1

    machine_a.invalidate()                           # A 的 session 被清掉並重新登入 → 舊 session 失效
    wall[0] += 5
    machine_a.query_icope("C123456789", force=True)
    assert server.login_count == 2

    wall[0] += 5
    machine_b.query_icope("D123456789", force=True)  # B 發現失效 → 改用 A 較新的 cookie，不再登入
    assert server.login_count == 2


def test_stale_shared_cookie_then_fresh_login(tmp_path):
    server = FakeServer()
    ck = tmp_path / "ck.json"
    wall = [time.time()]
    machine = _machine(server, ck, wall)
    machine.query_icope("A123456789")
    server.valid = "kicked-by-someone-else"          # 伺服器端失效，檔案裡也沒有更新的 cookie
    wall[0] += 5
    machine.query_icope("B123456789", force=True)
    assert server.login_count == 2


def test_refresh_captcha_returns_new_token_and_old_one_stops_working():
    session = FakeSession(correct_captcha="55555")
    client = make_client(session, ocr_code="11111")
    with pytest.raises(CaptchaManualRequired) as first:
        client.query_icope("A123456789")
    fresh = client.refresh_captcha(first.value.token)
    assert fresh.token != first.value.token and fresh.image_bytes
    with pytest.raises(CredentialError):
        client.query_icope("A123456789", captcha_token=first.value.token, captcha_text="55555")
    assert client.query_icope("A123456789", captcha_token=fresh.token, captcha_text="55555").verdict == "can_assess"


def test_cancel_stops_before_next_request():
    import threading
    session = FakeSession()
    cancel = threading.Event()
    original = session.request

    def request(method, url, **kwargs):
        if "EF2_CheckIDExist" in url:
            cancel.set()          # 使用者在查詢途中按下取消
        return original(method, url, **kwargs)

    session.request = request
    client = make_client(session, plans=("EFA_115", "EFA_Pilot_115"))
    with pytest.raises(hc.QueryCancelled):
        client.query_icope("A123456789", cancel=cancel)
    checks = [c for c in session.calls if "EF2_CheckIDExist" in c[1]]
    assert len(checks) == 1       # 第一個請求送出後就停，不再查第二個計畫
    assert client._lock.acquire(blocking=False)
    client._lock.release()


def test_cancel_while_waiting_for_lock():
    import threading
    client = make_client(FakeSession(), plans=("EFA_115",))
    cancel = threading.Event()
    cancel.set()
    client._lock.acquire()
    try:
        with pytest.raises(hc.QueryCancelled):
            client.query_icope("A123456789", cancel=cancel)
    finally:
        client._lock.release()


def test_invalidate_and_clear_cache_do_not_wait_for_a_running_query():
    """程式碼審查 P2：查詢進行中（持有鎖）時，從畫面清除帳號或快取不能卡住，改在下一個操作開始時套用。"""
    import time
    session = FakeSession()
    client = make_client(session)
    client._session = object()
    client._logged_in = True
    client._cache[("A123456789", ("EFA_115",))] = (None, None)
    assert client._lock.acquire(timeout=1)            # 模擬另一個執行緒的查詢持有鎖
    started = time.monotonic()
    client.invalidate()
    client.clear_cache()
    assert time.monotonic() - started < 0.1
    assert client._invalidate_pending and client._session is not None
    client._lock.release()
    client._acquire()                                  # 下一個操作開始
    try:
        assert client._session is None and not client._logged_in and client._cache == {}
        assert not client._invalidate_pending and not client._clear_cache_pending
    finally:
        client._lock.release()


def test_real_ocr_engine_is_created_with_onnxruntime_telemetry_switched_off(monkeypatch):
    """README 說程式不回傳任何使用統計：onnxruntime 在 Windows 上預設會發出遙測事件，載入辨識引擎前先關掉。"""
    import sys
    import types

    import onnxruntime
    order = []
    monkeypatch.setattr(onnxruntime, "disable_telemetry_events", lambda: order.append("telemetry off"))
    fake = types.ModuleType("ddddocr")
    fake.DdddOcr = lambda show_ad=False: order.append("engine") or FakeOcr("55555")
    monkeypatch.setitem(sys.modules, "ddddocr", fake)
    client = HpdcsClient(credentials_provider=lambda: ("demo001", "pw"), plans_provider=lambda: ("EFA_115",))
    assert client._get_ocr().classification(b"") == "55555"
    assert order == ["telemetry off", "engine"]

