from astrobot.gender import guess_gender


def test_female_by_ending():
    for n in ("Анна", "Мария", "Ольга", "Татьяна", "Екатерина", "Дарья"):
        assert guess_gender(n) == "f", n


def test_male_by_ending():
    for n in ("Иван", "Пётр", "Сергей", "Дмитрий", "Александр", "Максим"):
        assert guess_gender(n) == "m", n


def test_male_vowel_exceptions():
    for n in ("Никита", "Илья", "Кузьма", "Фома", "Лука", "Данила"):
        assert guess_gender(n) == "m", n


def test_explicit_overrides():
    assert guess_gender("Игорь") == "m"
    assert guess_gender("Любовь") == "f"


def test_ambiguous_returns_none():
    for n in ("Саша", "Женя", "Валя", "Слава"):
        assert guess_gender(n) is None, n


def test_full_name_uses_first_token():
    assert guess_gender("Иванов Иван Иванович") == "m"
    assert guess_gender("Петрова Анна") == "f"


def test_empty_and_garbage():
    assert guess_gender("") is None
    assert guess_gender(None) is None
    assert guess_gender("123") is None
