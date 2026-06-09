import pytest

from astrobot.safety.crisis import is_crisis


@pytest.mark.parametrize(
    "text",
    [
        "хочу покончить с собой",
        "не хочу жить",
        "не хочу больше жить",
        "хочу убить себя",
        "не вижу смысла жить",
        "хочу повеситься",
        "повешусь",
        "наглотаюсь таблеток",
        "хочу резать себя",
        "лучше бы я умер",
        "лучше бы я не рождалась",
        "если бы я исчез",
        "я устала жить",
        "невыносимо жить",
        "хочу спрыгнуть с крыши",
        "вены вскрою",
        "Я подумываю о суициде",
        "self-harm",
    ],
)
def test_crisis_detected(text):
    assert is_crisis(text), f"missed crisis phrase: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "Какие у меня сильные стороны?",
        "Жизнь сложная, расскажи про карту",
        "Солнце в Рыбах что значит",
        "Хочу узнать про отношения",
        "Чувствую тревогу из-за работы",
        "Как мне найти призвание?",
        "Стоит ли мне уехать в другую страну",
        "",
    ],
)
def test_safe_phrases_not_flagged(text):
    assert not is_crisis(text), f"false positive on: {text!r}"
