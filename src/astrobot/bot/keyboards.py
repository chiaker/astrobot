from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MENU_NATAL = "🌟 Натальная карта"
MENU_HOROSCOPE = "🔮 Гороскоп"
MENU_QUESTION = "💬 Спросить Астру"
MENU_PROFILE = "👤 Профиль"
MENU_PREMIUM = "💎 Премиум"
MENU_ABOUT = "ℹ️ Об Астре"
MENU_FAVORITES = "⭐ Избранное"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_NATAL), KeyboardButton(text=MENU_HOROSCOPE)],
            [KeyboardButton(text=MENU_QUESTION), KeyboardButton(text=MENU_FAVORITES)],
            [KeyboardButton(text=MENU_PREMIUM), KeyboardButton(text=MENU_PROFILE)],
            [KeyboardButton(text=MENU_ABOUT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выбери пункт меню",
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


def ask_again_with_save_kb(response_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{response_id}"),
                InlineKeyboardButton(text="💬 Спросить ещё", callback_data="ask_again"),
            ]
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
            ]
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
        ]
    )


def city_choice_kb(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options: list of (label, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in options
        ]
    )
