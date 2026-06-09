PLANETS_RU: dict[str, str] = {
    "Sun": "Солнце",
    "Moon": "Луна",
    "Mercury": "Меркурий",
    "Venus": "Венера",
    "Mars": "Марс",
    "Jupiter": "Юпитер",
    "Saturn": "Сатурн",
    "Uranus": "Уран",
    "Neptune": "Нептун",
    "Pluto": "Плутон",
    "Mean_Node": "Северный узел",
    "True_Node": "Северный узел",
    "Chiron": "Хирон",
    "Ascendant": "Асцендент",
    "Medium_Coeli": "МС",
}

SIGNS_RU: dict[str, str] = {
    "Ari": "Овен",
    "Tau": "Телец",
    "Gem": "Близнецы",
    "Can": "Рак",
    "Leo": "Лев",
    "Vir": "Дева",
    "Lib": "Весы",
    "Sco": "Скорпион",
    "Sag": "Стрелец",
    "Cap": "Козерог",
    "Aqu": "Водолей",
    "Pis": "Рыбы",
}

ASPECTS_RU: dict[str, str] = {
    "conjunction": "соединение",
    "sextile": "секстиль",
    "square": "квадрат",
    "trine": "тригон",
    "opposition": "оппозиция",
    "quincunx": "квинконс",
}


def planet_ru(name: str) -> str:
    return PLANETS_RU.get(name, name)


def sign_ru(code: str) -> str:
    return SIGNS_RU.get(code, code)


def aspect_ru(name: str) -> str:
    return ASPECTS_RU.get(name.lower(), name)


def fmt_degrees(deg: float) -> str:
    d = int(deg)
    m = int(round((deg - d) * 60))
    if m == 60:
        d += 1
        m = 0
    return f"{d}°{m:02d}'"
