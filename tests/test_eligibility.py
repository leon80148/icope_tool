"""ICOPE 評估的年齡資格：用健保卡上的出生日期，以「年度」計算。"""
from __future__ import annotations

import pytest

from icope_tool.eligibility import ELDER_AGE, INDIGENOUS_AGE, age_in_year
from icope_tool.ui.messages import describe_age


def test_age_is_counted_by_calendar_year_not_by_birthday():
    assert age_in_year("0500101", 2026) == 65
    assert age_in_year("0501231", 2026) == 65          # 12/31 才生日：當年度滿 65 就算，不看月日
    assert age_in_year("0510101", 2026) == 64
    assert (ELDER_AGE, INDIGENOUS_AGE) == (65, 55)


@pytest.mark.parametrize("text", ["", "abc", "050123", "05012311", "05O1231", "0000101", "1160101", " ", None])
def test_a_birth_date_that_cannot_be_read_gives_no_age(text):
    """健保卡這一欄是寬鬆解析的，雜訊、空白、民國 0 年、比今年還晚的年份都不能讓查詢頁出錯或算出怪年齡。"""
    assert age_in_year(text, 2026) is None


@pytest.mark.parametrize("birth, role, parts", [
    ("0501231", "success", ["民國 50 年出生", "今年度滿 65 歲"]),
    ("0340101", "success", ["民國 34 年出生", "今年度滿 81 歲"]),
    ("0510101", "warning", ["今年度滿 64 歲", "未滿 65 歲", "原住民 55 歲以上可評估"]),
    ("0600101", "warning", ["今年度滿 55 歲", "原住民 55 歲以上可評估"]),
    ("0610101", "warning", ["今年度滿 54 歲", "未滿 65 歲"]),
    ("", "caption", ["未讀健保卡，無法核對年齡"]),
    # 雜訊和不合理的年份（比今年還晚）是同一句：不說「無法判讀」，那會讓人以為卡讀壞了而一直重插
    ("??", "caption", ["健保卡上的出生日期無法核對年齡"]),
    ("1300101", "caption", ["健保卡上的出生日期無法核對年齡"]),
])
def test_the_age_line_says_what_the_nurse_needs_to_decide(birth, role, parts):
    shown_role, text = describe_age(birth, 2026)
    assert shown_role == role
    for part in parts:
        assert part in text, text


def test_under_55_is_not_told_about_the_indigenous_rule():
    assert "可評估" not in describe_age("0610101", 2026)[1]        # 54 歲：原住民也還不行，不要讓人以為確認身分就可以
