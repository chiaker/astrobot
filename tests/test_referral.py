import re

from astrobot.referral import generate_code, parse_start_arg


def test_generate_code_format():
    code = generate_code()
    assert re.fullmatch(r"[A-Z0-9]{8}", code)


def test_generate_code_uniqueness_sample():
    codes = {generate_code() for _ in range(10_000)}
    # 32^8 = ~1.1T values; collisions in 10k samples are vanishingly rare
    assert len(codes) >= 9_995


def test_parse_start_arg_with_prefix():
    assert parse_start_arg("/start ref_ABCDEF12") == "ABCDEF12"


def test_parse_start_arg_lowercase_normalized():
    assert parse_start_arg("/start ref_abcdef12") == "ABCDEF12"


def test_parse_start_arg_no_prefix_pure_code():
    assert parse_start_arg("/start ABCDEF12") == "ABCDEF12"


def test_parse_start_arg_no_arg():
    assert parse_start_arg("/start") is None


def test_parse_start_arg_invalid_chars():
    assert parse_start_arg("/start ref_ABCDEFG!") is None


def test_parse_start_arg_wrong_length():
    assert parse_start_arg("/start ref_ABC") is None


def test_parse_start_arg_none_input():
    assert parse_start_arg(None) is None
