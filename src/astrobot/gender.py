"""Best-effort gender guess from a (Russian) first name.

Conservative by design: returns "m"/"f" only when reasonably confident, and
None when ambiguous so the caller can ask the user explicitly.
"""
from __future__ import annotations

import re
from typing import Literal

Gender = Literal["m", "f"]

# Unisex diminutives — never guess, always ask.
_AMBIGUOUS: frozenset[str] = frozenset(
    {"саша", "женя", "валя", "слава", "ника"}
)

# Male names that end in а/я (would otherwise be read as female by the heuristic).
_MALE_VOWEL_EXC: frozenset[str] = frozenset(
    {
        "никита", "илья", "кузьма", "фома", "лука", "данила", "гаврила",
        "савва", "фока", "ерёма", "ерема", "добрыня", "сила", "пантелея",
        "велимир", "юра", "вова", "дима", "лёша", "леша", "коля", "толя",
        "витя", "петя", "ваня", "гена", "сёма", "сема", "стёпа", "степа",
    }
)

# Names whose ending doesn't decide (soft sign, etc.) — list the common ones.
_MALE_EXPLICIT: frozenset[str] = frozenset(
    {"игорь", "лазарь", "елисей", "матвей", "андрей", "сергей", "алексей",
     "дмитрий", "юрий", "геннадий", "анатолий", "виталий", "валерий",
     "аркадий", "евгений", "григорий", "макар"}
)
_FEMALE_EXPLICIT: frozenset[str] = frozenset(
    {"любовь", "нинель", "адель", "рахиль", "эсфирь", "суламифь", "мэри"}
)

_MALE_CONSONANT_END = set("бвгджзйклмнпрстфхцчшщ")


def guess_gender(name: str | None) -> Gender | None:
    """Guess gender from a name. None when uncertain (caller should ask)."""
    if not name:
        return None
    first = name.strip().split()[0].lower().replace("ё", "е")
    first = re.sub(r"[^а-яa-z-]", "", first)
    if len(first) < 2:
        return None

    if first in _AMBIGUOUS:
        return None
    if first in _MALE_VOWEL_EXC or first in _MALE_EXPLICIT:
        return "m"
    if first in _FEMALE_EXPLICIT:
        return "f"

    last = first[-1]
    if last in ("а", "я"):
        return "f"
    if last in _MALE_CONSONANT_END:
        return "m"
    # ends in ь, о, и, у, ы, э, ю, е or a latin letter → too uncertain
    return None
