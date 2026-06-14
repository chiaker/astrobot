from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from astrobot.db.models import User
from astrobot.limits import is_premium

MENU_NATAL = "🌟 Натальная карта"
MENU_HOROSCOPE = "🔮 Гороскоп"
MENU_QUESTION = "💬 Спросить Астру"
MENU_PROFILE = "👤 Профиль"
MENU_PREMIUM = "💎 Премиум"
MENU_ABOUT = "ℹ️ Об Астре"
MENU_FAVORITES = "⭐ Избранное"
MENU_SETTINGS = "⚙️ Настройки"

# Button that returns to the main menu (edits the current message into it).
MENU_BACK_BTN = InlineKeyboardButton(text="🔙 Меню", callback_data="menu:open")


def menu_back_row() -> list[InlineKeyboardButton]:
    return [MENU_BACK_BTN]


def with_back(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Append a '🔙 Меню' row to the given keyboard rows."""
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [MENU_BACK_BTN]])


def promo_row(user: User) -> list[InlineKeyboardButton]:
    """Soft upsell shown under results: premium for free users, referral for all."""
    if is_premium(user):
        return [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral:show")]
    return [
        InlineKeyboardButton(text="💎 Премиум", callback_data="menu:premium"),
        InlineKeyboardButton(text="🤝 Друг = +2 вопроса", callback_data="referral:show"),
    ]


def main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=MENU_HOROSCOPE, callback_data="menu:horoscope"),
                InlineKeyboardButton(text=MENU_NATAL, callback_data="menu:natal"),
            ],
            [
                InlineKeyboardButton(text=MENU_QUESTION, callback_data="menu:question"),
                InlineKeyboardButton(text=MENU_FAVORITES, callback_data="menu:favorites"),
            ],
            [
                InlineKeyboardButton(text=MENU_PREMIUM, callback_data="menu:premium"),
                InlineKeyboardButton(text=MENU_PROFILE, callback_data="menu:profile"),
            ],
            [
                InlineKeyboardButton(text=MENU_SETTINGS, callback_data="menu:settings"),
                InlineKeyboardButton(text=MENU_ABOUT, callback_data="menu:about"),
            ],
            [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral:show")],
        ]
    )


def time_unknown_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Не знаю точного времени", callback_data="time:unknown")],
        ]
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Сохранить", callback_data="onb:save"),
                InlineKeyboardButton(text="↩️ Заново", callback_data="onb:restart"),
            ]
        ]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel")]]
    )


SUGGESTED_QUESTIONS: dict[str, str] = {
    "purpose": "В чём моё главное призвание по карте?",
    "strengths": "Какие у меня сильные стороны и где я могу проявиться лучше всего?",
    "growth": "Что мне сейчас стоит развивать в себе?",
    "love": "Какие люди мне подходят в отношениях и что я ищу в партнёре?",
    "work": "Какая работа ближе всего к моей натуре?",
    "year": "На что мне обратить внимание в этом году?",
}


def ask_again_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Спросить ещё", callback_data="ask_again")]
        ]
    )


def ask_again_with_save_kb(response_id: int, user: User) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{response_id}"),
                InlineKeyboardButton(text="💬 Спросить ещё", callback_data="ask_again"),
            ],
            promo_row(user),
            [MENU_BACK_BTN],
        ]
    )


def question_entry_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤔 Не знаю что спросить", callback_data="show_topics")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")],
        ]
    )


def suggested_questions_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Призвание", callback_data="ask:purpose"),
                InlineKeyboardButton(text="💪 Сильные стороны", callback_data="ask:strengths"),
            ],
            [
                InlineKeyboardButton(text="🌱 Точки роста", callback_data="ask:growth"),
                InlineKeyboardButton(text="❤️ Отношения", callback_data="ask:love"),
            ],
            [
                InlineKeyboardButton(text="💼 Работа", callback_data="ask:work"),
                InlineKeyboardButton(text="🔮 Этот год", callback_data="ask:year"),
            ],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")],
        ]
    )


def horoscope_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="horo:today"),
                InlineKeyboardButton(text="Неделя", callback_data="horo:week"),
                InlineKeyboardButton(text="Месяц", callback_data="horo:month"),
            ],
            [MENU_BACK_BTN],
        ]
    )


def horoscope_regen_kb(period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересчитать заново", callback_data=f"horo:regen:{period}")]
        ]
    )


def name_skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Пропустить", callback_data="onb:name:skip")]]
    )


def gender_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="onb:gender:m"),
                InlineKeyboardButton(text="Женский", callback_data="onb:gender:f"),
            ],
            [InlineKeyboardButton(text="Не указывать", callback_data="onb:gender:skip")],
        ]
    )


def astro_terms_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Да, с терминами", callback_data="onb:terms:yes"),
                InlineKeyboardButton(text="💬 Без терминов", callback_data="onb:terms:no"),
            ]
        ]
    )


def final_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно", callback_data="onb:final:ok"),
                InlineKeyboardButton(text="↩️ Начать заново", callback_data="onb:final:restart"),
            ]
        ]
    )


def reset_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Да, сбросить", callback_data="profile:reset:confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


def natal_paywall_kb() -> InlineKeyboardMarkup:
    from astrobot.limits import NATAL_REGEN_PRICE_RUB

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Купить пересчёт — {NATAL_REGEN_PRICE_RUB} ₽",
                    callback_data="pay:natal_regen",
                )
            ],
            [InlineKeyboardButton(text="💎 Открыть Премиум", callback_data="premium:show")],
            [MENU_BACK_BTN],
        ]
    )


def push_hour_kb() -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(text=f"{h}:00", callback_data=f"push:hour:{h}") for h in range(6, 10)]
    row2 = [InlineKeyboardButton(text=f"{h}:00", callback_data=f"push:hour:{h}") for h in range(10, 14)]
    return InlineKeyboardMarkup(
        inline_keyboard=[row1, row2, [InlineKeyboardButton(text="Отмена", callback_data="push:cancel")]]
    )


def city_choice_kb(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options: list of (label, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in options
        ]
    )
