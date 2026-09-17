"""讀卡模組測試（移植自參考專案 card-agent/tests；注入 fake，不碰硬體）。"""
from __future__ import annotations

import pytest

from icope_tool.services.card import nhi_card
from icope_tool.services.card.nhi_card import CardReadError, _final_error
from icope_tool.services.card.nhi_parse import BASIC_RESPONSE_LEN, ParseError, parse_basic_response

FIELDS = {"card_no": "000012345678", "name": "王小明", "pid": "A123456789",
          "birth_roc": "0340101", "sex": "M", "issue_date_roc": "1140101"}


def build_payload(card_no=b"000012345678", name="王小明", pid=b"A123456789",
                  birth=b"0340101", sex=b"M", issue=b"1140101", name_pad=b" "):
    name_bytes = name.encode("big5") if isinstance(name, str) else name
    data = (card_no.ljust(12, b" ") + name_bytes.ljust(20, name_pad) + pid.ljust(10, b" ")
            + birth.ljust(7, b" ") + sex + issue.ljust(7, b" "))
    assert len(data) == BASIC_RESPONSE_LEN
    return data


class FakeReader:
    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name


@pytest.fixture(autouse=True)
def fast_poll_and_clean(monkeypatch):
    monkeypatch.setattr(nhi_card, "POLL_INTERVAL", 0.01)
    nhi_card._reader_verdicts.clear()
    yield
    nhi_card._reader_verdicts.clear()


# ---- 解析 -------------------------------------------------------------------
def test_all_fields_parsed():
    assert parse_basic_response(build_payload()) == FIELDS


def test_name_trailing_nul_padding():
    assert parse_basic_response(build_payload(name="陳大文", name_pad=b"\x00"))["name"] == "陳大文"


def test_name_undecodable_big5_does_not_raise():
    fields = parse_basic_response(build_payload(name=b"\xff\xff" + "明".encode("big5")))
    assert "明" in fields["name"] and "�" in fields["name"]


def test_pid_lowercase_normalized():
    assert parse_basic_response(build_payload(pid=b"a123456789"))["pid"] == "A123456789"


def test_wrong_length_and_bad_pid_raise():
    with pytest.raises(ParseError):
        parse_basic_response(build_payload()[:-1])
    with pytest.raises(ParseError):
        parse_basic_response(build_payload(pid=b"0123456789"))
    with pytest.raises(ParseError):
        parse_basic_response("not-bytes")  # type: ignore[arg-type]


# ---- 錯誤優先序 ---------------------------------------------------------------
def test_error_priority():
    assert _final_error(saw_busy=True, saw_empty=True, saw_not_nhi=True).code == "CARD_IN_USE"
    assert _final_error(saw_busy=False, saw_empty=True, saw_not_nhi=True).code == "NO_CARD"
    assert _final_error(saw_busy=False, saw_empty=False, saw_not_nhi=True).code == "NOT_NHI_CARD"
    assert _final_error(saw_busy=False, saw_empty=False, saw_not_nhi=False).code == "NO_CARD"


# ---- 輪詢自癒 -----------------------------------------------------------------
def test_enumeration_failure_then_recovery(monkeypatch):
    calls = {"n": 0}

    def fake_enumerate(hint=""):
        calls["n"] += 1
        return [] if calls["n"] <= 2 else [FakeReader("R1")]

    monkeypatch.setattr(nhi_card, "_enumerate", fake_enumerate)
    monkeypatch.setattr(nhi_card, "_passive_states", lambda names: {"R1": b"\x3b\xaa"})
    monkeypatch.setattr(nhi_card, "_read_from_reader", lambda r: FIELDS)
    assert nhi_card.read_basic(wait_s=2) == FIELDS
    assert calls["n"] >= 3


def test_real_establish_context_exception_is_caught(monkeypatch):
    from smartcard.pcsc.PCSCExceptions import EstablishContextException

    calls = {"n": 0}

    def fake_pcsc_readers():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise EstablishContextException(0x8010001D)
        return [FakeReader("R1")]

    monkeypatch.setattr(nhi_card, "pcsc_readers", fake_pcsc_readers)
    monkeypatch.setattr(nhi_card, "_passive_states", lambda names: {"R1": b"\x3b\xaa"})
    monkeypatch.setattr(nhi_card, "_read_from_reader", lambda r: FIELDS)
    assert nhi_card.read_basic(wait_s=2) == FIELDS

    def always_raise():
        raise EstablishContextException(0x8010001D)

    monkeypatch.setattr(nhi_card, "pcsc_readers", always_raise)
    assert nhi_card.list_readers() == []


def test_no_reader_only_after_deadline(monkeypatch):
    calls = {"n": 0}

    def fake_enumerate(hint=""):
        calls["n"] += 1
        return []

    monkeypatch.setattr(nhi_card, "_enumerate", fake_enumerate)
    with pytest.raises(CardReadError) as ei:
        nhi_card.read_basic(wait_s=0.1)
    assert ei.value.code == "NO_READER"
    assert calls["n"] >= 2


def test_two_strike_then_cached_skip(monkeypatch):
    connects = {"n": 0}

    def fake_read(reader):
        connects["n"] += 1
        raise CardReadError("NOT_NHI_CARD", "x")

    monkeypatch.setattr(nhi_card, "_enumerate", lambda hint="": [FakeReader("R1")])
    monkeypatch.setattr(nhi_card, "_passive_states", lambda names: {"R1": b"\x3b\xbb"})
    monkeypatch.setattr(nhi_card, "_read_from_reader", fake_read)
    with pytest.raises(CardReadError) as ei:
        nhi_card.read_basic(wait_s=0.3)
    assert ei.value.code == "NOT_NHI_CARD"
    assert connects["n"] == 2
    assert nhi_card._reader_verdicts["R1"] == (b"\x3b\xbb", "not_nhi")


def test_not_nhi_with_empty_slot_reports_no_card(monkeypatch):
    def fake_read(reader):
        raise CardReadError("NOT_NHI_CARD", "x")

    monkeypatch.setattr(nhi_card, "_enumerate", lambda hint="": [FakeReader("R1"), FakeReader("R2")])
    monkeypatch.setattr(nhi_card, "_passive_states", lambda names: {"R1": b"\x3b\xcc", "R2": None})
    monkeypatch.setattr(nhi_card, "_read_from_reader", fake_read)
    with pytest.raises(CardReadError) as ei:
        nhi_card.read_basic(wait_s=0.2)
    assert ei.value.code == "NO_CARD"


def test_cancel_stops_waiting(monkeypatch):
    monkeypatch.setattr(nhi_card, "_enumerate", lambda hint="": [FakeReader("R1")])
    monkeypatch.setattr(nhi_card, "_passive_states", lambda names: {"R1": None})
    ticks = {"n": 0}

    def cancel():
        ticks["n"] += 1
        return ticks["n"] > 3

    with pytest.raises(CardReadError) as ei:
        nhi_card.read_basic(wait_s=30, cancel=cancel)
    assert ei.value.code == "CANCELLED"
