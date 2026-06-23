from __future__ import annotations

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

# Returns to the main menu by EDITING the current message into it (navigation).
MENU_BACK_BTN = InlineKeyboardButton(text="🔙 Меню", callback_data="menu:open")
# Returns to the menu as a NEW message, keeping the current one (under results,
# so a generated reading isn't replaced by the menu).
MENU_BACK_NEW_BTN = InlineKeyboardButton(text="🔙 Меню", callback_data="menu:new")


def menu_back_row() -> list[InlineKeyboardButton]:
    return [MENU_BACK_BTN]


def with_back(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Append a '🔙 Меню' row to the given keyboard rows."""
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [MENU_BACK_BTN]])


def promo_row(user: User) -> list[InlineKeyboardButton]:
    """Soft upsell shown under results: 💎 Премиум for free users, nothing for
    premium. Referral lives only in the main menu now. MAY BE EMPTY — callers
    must skip an empty row (Telegram rejects empty keyboard rows)."""
    if is_premium(user):
        return []
    return [InlineKeyboardButton(text="💎 Премиум", callback_data="menu:premium")]


def main_menu_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=MENU_HOROSCOPE, callback_data="menu:horoscope"),
                InlineKeyboardButton(text=MENU_NATAL, callback_data="menu:natal"),
            ],
            [
                InlineKeyboardButton(text=MENU_QUESTION, callback_data="menu:question"),
                InlineKeyboardButton(text="🃏 Таро", callback_data="menu:tarot"),
            ],
            [
                InlineKeyboardButton(text="💞 Совместимость", callback_data="menu:compatibility"),
                InlineKeyboardButton(text=MENU_FAVORITES, callback_data="menu:favorites"),
            ],
            [
                InlineKeyboardButton(text=MENU_PREMIUM, callback_data="menu:premium"),
                InlineKeyboardButton(text=MENU_PROFILE, callback_data="menu:profile"),
            ],
            [
                InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="referral:show"),
                InlineKeyboardButton(text=MENU_ABOUT, callback_data="menu:about"),
            ],
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


# Two-level prepared questions: theme key → (title, [(button_label, full_question)]).
# button_label is shown in the inline keyboard (kept short for display).
# full_question is sent to the LLM and echoed in the chat.
QUESTION_TOPICS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "popular": (
        "🔥 Популярные",
        [
            ("Сколько судьбоносных партнёров?", "Сколько судьбоносных романтических партнёров уготовано мне в этой жизни?"),
            ("Когда я вступлю в отношения?", "Когда я вступлю в отношения?"),
            ("Финансы и карьера — на что упор?", "В чём скрыты мои финансы, карьера? На что сделать упор?"),
            ("Будут ли у меня дети?", "Будут ли у меня дети и с кем?"),
            ("В чём моё предназначение?", "В чём смысл лично моего рождения, какое моё предназначение?"),
            ("Где встретить партнёра по судьбе?", "Где я могу встретить партнёра по судьбе?"),
            ("Какую профессию выбрать?", "Какую профессию выбрать, чтобы быть реализованным?"),
            ("Кем я был в прошлой жизни?", "Хочу посмотреть, кем я вероятно был в прошлой жизни?"),
            ("Что отработать в этом воплощении?", "Что я должен отработать в этом воплощении?"),
        ],
    ),
    "love": (
        "❤️ Отношения, брак, семья",
        [
            ("Сколько судьбоносных партнёров?", "Сколько судьбоносных романтических партнёров уготовано мне в этой жизни?"),
            ("Будут ли у меня дети?", "Будут ли у меня дети и с кем?"),
            ("Какими будут отношения с детьми?", "Какие отношения у меня будут с детьми?"),
            ("Черты моего судьбоносного партнёра?", "Какие характеристики у моего судьбоносного мужа / жены?"),
            ("Почему повторяются болезненные отношения?", "Почему в моей жизни повторяются болезненные отношения?"),
            ("Где встретить партнёра по судьбе?", "Где я могу встретить партнёра по судьбе?"),
            ("Моя планета партнёра Даракарака?", "Какая у меня планета партнёра Даракарака?"),
            ("Как усилить Венеру?", "Как мне усилить планету любви Венеру?"),
            ("Когда я вступлю в отношения?", "Когда я вступлю в отношения?"),
        ],
    ),
    "money": (
        "💼 Финансы и карьера",
        [
            ("Финансы и карьера — на что упор?", "В чём скрыты мои финансы, карьера? На что сделать упор?"),
            ("Деньги будут лёгкими или тяжёлыми?", "Мои деньги будут лёгкими или тяжёлыми?"),
            ("Буду ли я богатым?", "Буду ли я богатым?"),
            ("Что мешает зарабатывать больше?", "Что мешает мне зарабатывать больше?"),
        ],
    ),
    "self": (
        "🧬 Личность",
        [
            ("Общие черты моей личности?", "Какие общие характеристики меня как личности в этой жизни?"),
            ("Мои сильные и слабые стороны", "Хочу узнать свои слабые и сильные стороны"),
            ("Какая планета влияет сильнее?", "Какая планета влияет на меня сильнее всего и что это значит?"),
            ("Какие у меня страхи по карте?", "Какие у меня страхи по карте?"),
        ],
    ),
    "health": (
        "🩺 Здоровье и тело",
        [
            ("Что карта говорит о здоровье?", "Давай посмотрим, что карта говорит о моём здоровье?"),
            ("Силы и слабости здоровья и тела", "Силы и слабости моего здоровья и тела"),
            ("Что делать для поддержания здоровья?", "Что именно мне нужно делать для поддержания здоровья?"),
        ],
    ),
    "purpose": (
        "✨ Предназначение и таланты",
        [
            ("В чём моё предназначение?", "В чём смысл лично моего рождения, какое моё предназначение?"),
            ("Моя социальная реализация и статус", "Хочу посмотреть свою социальную реализацию и статус"),
            ("Мои таланты, как их раскрыть?", "Какие у меня таланты, как их раскрыть?"),
            ("Какую профессию выбрать?", "Какую профессию выбрать, чтобы быть реализованным?"),
        ],
    ),
    "move": (
        "✈️ Эмиграция и путешествия",
        [
            ("Есть показатели эмиграции в карте?", "Есть ли у меня показатели эмиграции в карте?"),
            ("Когда вероятны переезды?", "В какие периоды вероятны переезды или смена места жительства?"),
            ("В какой стране лучше жить?", "В какой стране или регионе мне благоприятнее жить?"),
        ],
    ),
}


# Leaves the chat-with-Astra mode (clears FSM and shows the menu).
CHAT_EXIT_BTN = InlineKeyboardButton(text="🚪 Выйти из чата", callback_data="chat:exit")


def chat_answer_kb(response_id: int, show_premium: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="⭐ Сохранить", callback_data=f"fav:save:{response_id}")],
    ]
    if show_premium:
        rows.append([InlineKeyboardButton(text="💎 Открыть Премиум", callback_data="menu:premium")])
    rows.append([CHAT_EXIT_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_OWN_QUESTION_BTN = InlineKeyboardButton(text="✏️ Задать свой вопрос", callback_data="chat:own_question")


def topics_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"topic:{key}")]
        for key, (title, _) in QUESTION_TOPICS.items()
    ]
    rows.append([_OWN_QUESTION_BTN])
    rows.append([CHAT_EXIT_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_questions_kb(key: str) -> InlineKeyboardMarkup:
    questions = QUESTION_TOPICS[key][1]
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"q:{key}:{idx}")]
        for idx, (label, _) in enumerate(questions)
    ]
    rows.append([_OWN_QUESTION_BTN])
    rows.append([InlineKeyboardButton(text="⬅️ К темам", callback_data="show_topics")])
    rows.append([CHAT_EXIT_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def horoscope_period_kb(user: User | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="Сегодня", callback_data="horo:today"),
            InlineKeyboardButton(text="Неделя", callback_data="horo:week"),
            InlineKeyboardButton(text="Месяц", callback_data="horo:month"),
        ]
    ]
    if user is not None:
        if user.push_horoscope_enabled:
            hour = f"{user.push_hour}:00" if user.push_hour is not None else "9:00"
            city = f" · {user.push_city_name}" if user.push_city_name else ""
            push_label = f"🌅 Утренний гороскоп: вкл · {hour}{city}"
        else:
            push_label = "🌅 Утренний гороскоп: выкл"
        rows.append([InlineKeyboardButton(text=push_label, callback_data="settings:push_horoscope")])
    rows.append([MENU_BACK_BTN])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def horoscope_regen_kb(period: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересчитать заново", callback_data=f"horo:regen:{period}")]
        ]
    )


def natal_cta_kb() -> InlineKeyboardMarkup:
    """Call-to-action shown once after the first (onboarding) natal chart."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Вопросы", callback_data="menu:question")],
            [InlineKeyboardButton(text="💎 Тарифы", callback_data="menu:premium")],
            [MENU_BACK_BTN],
        ]
    )


def premium_or_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💎 Открыть Премиум", callback_data="menu:premium")],
            [MENU_BACK_BTN],
        ]
    )


def tarot_entry_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🃏 Тянуть без вопроса", callback_data="tarot:draw")],
            [MENU_BACK_BTN],
        ]
    )


def compat_time_unknown_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Не знаю времени", callback_data="compat:time:unknown")],
            [MENU_BACK_BTN],
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
