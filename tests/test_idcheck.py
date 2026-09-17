from icope_tool.idcheck import LETTER_CODES, check_person_id, checksum_ok, mask_person_id, normalize_person_id


def _with_valid_checksum(prefix9: str) -> str:
    """給前 9 碼，補出正確的第 10 碼。"""
    for last in "0123456789":
        if checksum_ok(prefix9 + last):
            return prefix9 + last
    raise AssertionError("no checksum digit")


def test_known_valid_national_id():
    assert check_person_id("A123456789").ok


def test_normalizes_fullwidth_lowercase_and_spaces():
    assert normalize_person_id(" ａ１２３ 456-789 ") == "A123456789"
    assert check_person_id("ａ１２３４５６７８９").ok


def test_checksum_mismatch_is_reported():
    result = check_person_id("A123456788")
    assert not result.ok and result.state == "checksum"


def test_every_letter_has_code_and_generated_ids_pass():
    assert len(LETTER_CODES) == 26
    for letter in LETTER_CODES:
        assert check_person_id(_with_valid_checksum(f"{letter}20000001")).ok


def test_new_resident_certificate_uses_same_checksum():
    value = _with_valid_checksum("A80000001")
    assert check_person_id(value).ok
    bad = value[:-1] + str((int(value[-1]) + 1) % 10)
    assert check_person_id(bad).state == "checksum"


def test_old_resident_certificate_format_accepted():
    assert check_person_id("AB12345678").ok


def test_typing_and_format_states():
    assert check_person_id("").state == "empty"
    typing = check_person_id("A12345")
    assert typing.state == "typing" and "4" in typing.message
    assert check_person_id("1234567890").state == "format"
    assert check_person_id("A1234567890").state == "format"


def test_mask():
    assert mask_person_id("A123456789") == "A12****789"
