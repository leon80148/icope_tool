"""pyscard 硬體層：讀卡機列舉與健保卡基本資料段讀取。

移植自參考專案的 card-agent/nhi_card.py。桌面程式直接在程序內呼叫，
不再經過本機 HTTP 服務；新增 cancel 回呼讓使用者可以中途取消等待插卡。

與展望 HIS 共用讀卡機的硬性要求：
  - 連卡一律 SCARD_SHARE_SHARED（共享模式，絕不獨占）
  - disposition 一律 SCARD_LEAVE_CARD——pyscard 2.3.1 的 disposition 參數在 connect() 層
    （disconnect() 不收參數），若讓卡被 reset/unpower，HIS 既有連線會收到
    SCARD_W_RESET_CARD 被打斷。絕不依賴 pyscard 預設值。
"""
import logging
import time

from smartcard.System import readers as pcsc_readers
from smartcard.scard import (
    SCARD_SHARE_SHARED, SCARD_LEAVE_CARD,
    SCardEstablishContext, SCardReleaseContext, SCardGetStatusChange,
    SCARD_SCOPE_USER, SCARD_STATE_UNAWARE, SCARD_STATE_PRESENT, SCARD_S_SUCCESS,
)
from smartcard.Exceptions import NoCardException, CardConnectionException

from icope_tool.services.card.nhi_parse import ParseError, parse_basic_response

log = logging.getLogger('icope_tool.card')

SELECT_NHI_AID = [0x00, 0xA4, 0x04, 0x00, 0x10,
                  0xD1, 0x58, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
                  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x11]
READ_BASIC = [0x00, 0xCA, 0x11, 0x00, 0x02, 0x00, 0x00]
GET_RESPONSE = [0x00, 0xC0, 0x00, 0x00]  # + Le

POLL_INTERVAL = 0.5
_SHARING_VIOLATION_HRESULTS = {0x8010000B, -0x7FEFFFF5}  # SCARD_E_SHARING_VIOLATION（無號/有號）


class CardReadError(Exception):
    """讀卡失敗。code：NO_READER / NO_CARD / CARD_IN_USE / NOT_NHI_CARD / PARSE_ERROR / READ_ERROR / CANCELLED。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def list_readers() -> list[str]:
    """列出讀卡機名稱。PC/SC 不可用（SCardSvr 未就緒/停用等）視同無讀卡機。

    ⚠️ 必須攔截廣義 Exception：pyscard 的 EstablishContextException/ListReadersException
    繼承自 BaseSCardException（**不是** SmartcardException 子類）——開機初期智慧卡服務
    未就緒時就是拋這些；漏接會讓整次讀卡變成不明錯誤且無法自癒。
    """
    try:
        return [str(r) for r in pcsc_readers()]
    except Exception as e:
        log.info('讀卡機列舉失敗（服務未就緒？）: %s', e)
        return []


def _enumerate(reader_hint: str = ''):
    """列舉讀卡機物件（套用 hint 過濾）。失敗回空 list——呼叫端在等待視窗內重試。"""
    try:
        objs = list(pcsc_readers())
    except Exception as e:
        log.info('讀卡機列舉失敗（服務未就緒？）: %s', e)
        return []
    if reader_hint:
        matched = [r for r in objs if reader_hint.lower() in str(r).lower()]
        if matched:
            return matched
    return objs


def _is_sharing_violation(exc: Exception) -> bool:
    hresult = getattr(exc, 'hresult', None)
    if hresult in _SHARING_VIOLATION_HRESULTS:
        return True
    return 'sharing violation' in str(exc).lower() or '0x8010000b' in str(exc).lower()


def _passive_states(reader_names: list[str]) -> dict[str, bytes | None] | None:
    """以 SCardGetStatusChange 查各讀卡機目前狀態與 ATR。

    純查詢資源管理員快取，**完全不連卡、不碰卡片**——用來在連卡前先分辨
    「無卡」與「已知非健保卡（醫事人員卡/SAM 常駐）」。失敗回 None（呼叫端退回逐台嘗試）。
    """
    if not reader_names:
        return {}
    try:
        hresult, hcontext = SCardEstablishContext(SCARD_SCOPE_USER)
        if hresult != SCARD_S_SUCCESS:
            return None
        try:
            request = [(name, SCARD_STATE_UNAWARE) for name in reader_names]
            hresult, states = SCardGetStatusChange(hcontext, 0, request)
            if hresult != SCARD_S_SUCCESS:
                return None
            result: dict[str, bytes | None] = {}
            for name, eventstate, atr in states:
                present = bool(eventstate & SCARD_STATE_PRESENT)
                result[name] = bytes(atr) if (present and atr) else None
            return result
        finally:
            SCardReleaseContext(hcontext)
    except Exception:
        return None


# 跨呼叫的讀卡機判定快取：reader 名稱 → (ATR, 'not_nhi')。
# 醫事人員卡/SAM 常駐插槽（兩讀卡機診所配置）只在插入後被探測一次，之後不再碰觸，
# 避免每次讀卡都對 HIS 使用中的卡片送 SELECT。ATR 變了（換卡）就重新探測。
_reader_verdicts: dict[str, tuple[bytes, str]] = {}


def _final_error(saw_busy: bool, saw_empty: bool, saw_not_nhi: bool) -> CardReadError:
    """逾時後的錯誤分類（優先序）。

    有空的讀卡機在等卡時，回「請插入健保卡」比「不是健保卡」正確——
    兩讀卡機配置（健保卡機＋醫事人員卡機）下，醫事卡常駐永遠會觸發 saw_not_nhi，
    但使用者要的是把健保卡插進另一台空的讀卡機。
    """
    if saw_busy:
        return CardReadError('CARD_IN_USE',
                              '讀卡機正被其他程式（如 HIS）使用，請稍候幾秒再試')
    if saw_not_nhi and not saw_empty:
        return CardReadError('NOT_NHI_CARD',
                              '這張卡不是健保卡（請確認未插成自然人憑證或其他卡片）')
    return CardReadError('NO_CARD', '未偵測到健保卡，請插入卡片後重試')


def _read_from_reader(reader) -> dict:
    """對單一讀卡機讀一次。丟 NoCardException / CardConnectionException / CardReadError。

    ⚠️ 實卡實測（2026-08-29，114/12 發卡的新世代健保卡，Infineon SLJ52G 晶片）：
    新卡**不支援 SELECT**（一律回 6D00），但**直接送 GET DATA 就回 57 bytes + 9000**；
    舊世代卡則需先 SELECT AID。因此 SELECT 只做 best-effort（回應不看），
    「是否為健保卡」以 READ 是否成功為準（非健保卡直讀會失敗，如醫事卡回 6A86）。
    """
    conn = reader.createConnection()
    conn.connect(mode=SCARD_SHARE_SHARED, disposition=SCARD_LEAVE_CARD)
    try:
        try:
            atr_hex = bytes(conn.getATR()).hex().upper()
        except Exception:
            atr_hex = '?'
        for attempt in (1, 2):  # 同一連線內重試一次（冷卡第一次讀取可能不穩）
            conn.transmit(SELECT_NHI_AID)  # best-effort：舊卡需要、新卡回 6D00 無妨
            data, sw1, sw2 = conn.transmit(READ_BASIC)
            if sw1 == 0x61:  # T=0：資料待 GET RESPONSE 取回
                data, sw1, sw2 = conn.transmit(GET_RESPONSE + [sw2])
            if (sw1, sw2) == (0x90, 0x00):
                try:
                    return parse_basic_response(bytes(data))
                except ParseError as e:
                    raise CardReadError('PARSE_ERROR', f'卡片資料解讀失敗（{e}），請重插卡片再試')
            # 只記狀態碼與 ATR 供排障（無任何卡片個資）
            log.info('READ 基本資料失敗 attempt=%d SW=%02X%02X len=%d ATR=%s',
                     attempt, sw1, sw2, len(data), atr_hex)
            time.sleep(0.2)
        raise CardReadError('NOT_NHI_CARD',
                             '這張卡不是健保卡（請確認未插成自然人憑證或其他卡片）')
    finally:
        try:
            conn.disconnect()  # disposition 已於 connect 指定 LEAVE_CARD
        except Exception:
            pass


def read_basic(wait_s: float, reader_hint: str = '', cancel=None) -> dict:
    """輪詢直到讀到健保卡或逾時。

    自癒設計：
    - **開機競態**：SCardSvr 是 trigger-start 服務，開機初期可能未就緒/讀卡機尚未枚舉。
      列舉失敗或為空不立即放棄，於等待視窗內每輪重試（消除「必須先開 IC 控制軟體
      把服務帶起來」的順序依賴）。
    - **兩振出局判定**：同一張卡（同 ATR）READ 失敗要跨連線再試一輪才定讞非健保卡並
      快取跳過——保護冷開機後第一次讀取可能不穩的卡，也仍避免反覆碰觸常駐醫事卡/SAM
      （每次插入最多被探測兩輪）。
    """
    deadline = time.monotonic() + max(0.0, wait_s)
    session_skip: set[str] = set()  # 被動偵測不可用時的退路：本次呼叫內跳過已判定者
    saw_reader = False
    saw_not_nhi = False
    saw_busy = False
    saw_empty = False

    while True:
        if cancel is not None and cancel():
            raise CardReadError('CANCELLED', '已取消讀卡')
        reader_objs = _enumerate(reader_hint)  # 每輪重新列舉（開機競態/熱插拔自癒）
        if reader_objs:
            saw_reader = True
            names = [str(r) for r in reader_objs]
            states = _passive_states(names)  # None = 查不到狀態，退回逐台嘗試

            for reader in reader_objs:
                name = str(reader)
                atr = states.get(name) if states is not None else None

                if states is not None:
                    if atr is None:
                        saw_empty = True   # 空讀卡機：等待插卡的位置，完全不連卡
                        continue
                    cached = _reader_verdicts.get(name)
                    if cached and cached[0] == atr and cached[1] == 'not_nhi':
                        saw_not_nhi = True  # 已定讞非健保卡（醫事人員卡/SAM 常駐），不再碰觸
                        continue
                elif name in session_skip:
                    saw_not_nhi = True
                    continue

                try:
                    fields = _read_from_reader(reader)
                    if atr:
                        _reader_verdicts.pop(name, None)
                    return fields
                except NoCardException:
                    saw_empty = True
                    continue
                except CardReadError as e:
                    if e.code == 'NOT_NHI_CARD':
                        saw_not_nhi = True
                        if atr:
                            prev = _reader_verdicts.get(name)
                            if prev and prev[0] == atr and prev[1] == 'suspect':
                                _reader_verdicts[name] = (atr, 'not_nhi')  # 兩振出局，定讞
                            else:
                                _reader_verdicts[name] = (atr, 'suspect')  # 下一輪再給一次機會
                        else:
                            session_skip.add(name)
                        continue
                    raise
                except CardConnectionException as e:
                    if _is_sharing_violation(e):
                        saw_busy = True   # HIS 佔用中，留在輪詢裡重試
                        continue
                    log.warning('讀卡連線錯誤 reader=%s err=%s', name, e)
                    raise CardReadError('READ_ERROR', '讀卡過程發生錯誤，請重插卡片再試')

        if time.monotonic() >= deadline:
            if not saw_reader:
                raise CardReadError('NO_READER',
                                     '找不到讀卡機，請確認讀卡機已接上電腦')
            raise _final_error(saw_busy, saw_empty, saw_not_nhi)
        time.sleep(POLL_INTERVAL)
