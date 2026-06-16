"""Tarot deck (Major Arcana) and a random 3-card draw.

The LLM does the interpretation in Astra's voice; here we only pick cards and
hand the LLM a compact, unambiguous description of the spread.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Major Arcana, RU names. Order is the traditional 0..21.
MAJOR_ARCANA: tuple[str, ...] = (
    "Шут",
    "Маг",
    "Верховная Жрица",
    "Императрица",
    "Император",
    "Иерофант",
    "Влюблённые",
    "Колесница",
    "Сила",
    "Отшельник",
    "Колесо Фортуны",
    "Справедливость",
    "Повешенный",
    "Смерть",
    "Умеренность",
    "Дьявол",
    "Башня",
    "Звезда",
    "Луна",
    "Солнце",
    "Суд",
    "Мир",
)

POSITIONS: tuple[str, ...] = ("Прошлое", "Настоящее", "Будущее")

_REVERSED_CHANCE = 0.3


@dataclass(frozen=True)
class DrawnCard:
    position: str
    name: str
    reversed: bool


def draw_three(rng: random.Random | None = None) -> list[DrawnCard]:
    """Draw 3 distinct Major Arcana for past/present/future, each up/reversed."""
    r = rng or random
    names = r.sample(MAJOR_ARCANA, 3)
    return [
        DrawnCard(position=pos, name=name, reversed=r.random() < _REVERSED_CHANCE)
        for pos, name in zip(POSITIONS, names)
    ]


def cards_to_markdown(cards: list[DrawnCard], question: str | None) -> str:
    """Compact spread description fed to the LLM as cached context."""
    lines = ["# Расклад Таро (3 карты)"]
    q = (question or "").strip()
    lines.append(f"- Вопрос: {q}" if q else "- Вопрос: не задан (общий расклад)")
    lines.append("")
    lines.append("## Карты")
    for c in cards:
        orient = "перевёрнутая" if c.reversed else "прямая"
        lines.append(f"- {c.position}: {c.name} ({orient})")
    return "\n".join(lines)
