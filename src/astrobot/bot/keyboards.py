from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MENU_NATAL = "🌟 Натальная карта"
MENU_HOROSCOPE = "🔮 Гороскоп"
MENU_QUESTION = "💬 Задать вопрос"
MENU_PROFILE = "👤 Профиль"
MENU_PREMIUM = "💎 Премиум"
MENU_ABOUT = "ℹ️ О боте"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_NATAL), KeyboardButton(text=MENU_HOROSCOPE)],
            [KeyboardButton(text=MENU_QUESTION), KeyboardButton(text=MENU_PROFILE)],
            [KeyboardButton(text=MENU_PREMIUM), KeyboardButton(text=MENU_ABOUT)],
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


def city_choice_kb(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """options: list of (label, callback_data)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=cb)] for label, cb in options
        ]
    )
