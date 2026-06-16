import random

from astrobot.tarot import MAJOR_ARCANA, POSITIONS, cards_to_markdown, draw_three


def test_draw_three_distinct_cards():
    cards = draw_three(random.Random(42))
    assert len(cards) == 3
    names = [c.name for c in cards]
    assert len(set(names)) == 3  # no duplicates
    assert all(n in MAJOR_ARCANA for n in names)


def test_draw_three_positions():
    cards = draw_three(random.Random(1))
    assert [c.position for c in cards] == list(POSITIONS)
    assert all(isinstance(c.reversed, bool) for c in cards)


def test_cards_to_markdown_with_question():
    cards = draw_three(random.Random(7))
    md = cards_to_markdown(cards, "Что меня ждёт в любви?")
    assert "Вопрос: Что меня ждёт" in md
    for c in cards:
        assert c.name in md


def test_cards_to_markdown_no_question():
    cards = draw_three(random.Random(7))
    md = cards_to_markdown(cards, None)
    assert "не задан" in md
