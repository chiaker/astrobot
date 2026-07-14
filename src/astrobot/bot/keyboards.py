from __future__ import annotations

from astrobot.bot.platform import Button, Keyboard
from astrobot.db.models import User

MENU_NATAL = "🌟 Натальная карта"
MENU_HOROSCOPE = "🔮 Гороскоп"
MENU_QUESTION = "💬 Спросить Астру"
MENU_PROFILE = "👤 Профиль"
MENU_PREMIUM = "💎 Премиум"
MENU_ABOUT = "ℹ️ Об Астре"
MENU_FAVORITES = "⭐ Избранное"
MENU_SETTINGS = "⚙️ Настройки"

# Returns to the main menu by EDITING the current message into it (navigation).
MENU_BACK_BTN = Button(text="🔙 Меню", payload="menu:open")
# Returns to the menu as a NEW message, keeping the current one (under results,
# so a generated reading isn't replaced by the menu).
MENU_BACK_NEW_BTN = Button(text="🔙 Меню", payload="menu:new")


# Starts onboarding via a button so users never have to type /start — essential
# on MAX, where there's no easy "/start" tap. Payload reuses the onboarding-start
# handler (on_broadcast_onboarding), which is registered on both platforms.
ONBOARDING_START_BTN = Button(text="✨ Пройти знакомство", payload="bcast:onb")


def onboarding_start_kb() -> Keyboard:
    return Keyboard.from_rows([[ONBOARDING_START_BTN]])


def menu_back_row() -> list[Button]:
    return [MENU_BACK_BTN]


def with_back(rows: list[list[Button]]) -> Keyboard:
    """Append a '🔙 Меню' row to the given keyboard rows."""
    return Keyboard.from_rows([*rows, [MENU_BACK_BTN]])


def promo_row(user: User) -> list[Button]:
    """No-op kept for callers. Premium is intentionally offered ONLY from the
    main menu and at genuine paywalls (when the free quota / subscription runs
    out) — not sprinkled under every result. ALWAYS EMPTY — callers skip an
    empty row (both SDKs reject empty keyboard rows)."""
    return []


def main_menu_inline() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text=MENU_HOROSCOPE, payload="menu:horoscope"),
                Button(text=MENU_NATAL, payload="menu:natal"),
            ],
            [
                Button(text=MENU_QUESTION, payload="menu:question"),
                Button(text="🃏 Таро", payload="menu:tarot"),
            ],
            [
                Button(text="💞 Совместимость", payload="menu:compatibility"),
                Button(text=MENU_FAVORITES, payload="menu:favorites"),
            ],
            [
                Button(text=MENU_PREMIUM, payload="menu:premium"),
                Button(text=MENU_PROFILE, payload="menu:profile"),
            ],
            [
                Button(text="🤝 Пригласить друга", payload="referral:show"),
                Button(text=MENU_ABOUT, payload="menu:about"),
            ],
        ]
    )


def time_unknown_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [Button(text="Не знаю точного времени", payload="time:unknown")],
        ]
    )


def confirm_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text="✅ Сохранить", payload="onb:save"),
                Button(text="↩️ Заново", payload="onb:restart"),
            ]
        ]
    )


def cancel_kb() -> Keyboard:
    return Keyboard.from_rows([[Button(text="Отмена", payload="cancel")]])


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
CHAT_EXIT_BTN = Button(text="🚪 Выйти из чата", payload="chat:exit")


def chat_answer_kb(response_id: int, show_premium: bool = False) -> Keyboard:
    rows: list[list[Button]] = [
        [Button(text="⭐ Сохранить", payload=f"fav:save:{response_id}")],
    ]
    if show_premium:
        rows.append([Button(text="💎 Открыть Премиум", payload="menu:premium")])
    rows.append([CHAT_EXIT_BTN])
    return Keyboard.from_rows(rows)


_OWN_QUESTION_BTN = Button(text="✏️ Задать свой вопрос", payload="chat:own_question")


def topics_kb() -> Keyboard:
    rows = [
        [Button(text=title, payload=f"topic:{key}")]
        for key, (title, _) in QUESTION_TOPICS.items()
    ]
    rows.append([_OWN_QUESTION_BTN])
    rows.append([CHAT_EXIT_BTN])
    return Keyboard.from_rows(rows)


def topic_questions_kb(key: str) -> Keyboard:
    questions = QUESTION_TOPICS[key][1]
    rows = [
        [Button(text=label, payload=f"q:{key}:{idx}")]
        for idx, (label, _) in enumerate(questions)
    ]
    rows.append([_OWN_QUESTION_BTN])
    rows.append([Button(text="⬅️ К темам", payload="show_topics")])
    rows.append([CHAT_EXIT_BTN])
    return Keyboard.from_rows(rows)


def horoscope_period_kb(user: User | None = None) -> Keyboard:
    rows: list[list[Button]] = [
        [
            Button(text="Сегодня", payload="horo:today"),
            Button(text="Неделя", payload="horo:week"),
            Button(text="Месяц", payload="horo:month"),
        ]
    ]
    if user is not None:
        if user.push_horoscope_enabled:
            hour = f"{user.push_hour}:00" if user.push_hour is not None else "9:00"
            city = f" · {user.push_city_name}" if user.push_city_name else ""
            push_label = f"🌅 Утренний гороскоп: вкл · {hour}{city}"
        else:
            push_label = "🌅 Утренний гороскоп: выкл"
        rows.append([Button(text=push_label, payload="settings:push_horoscope")])
    rows.append([MENU_BACK_BTN])
    return Keyboard.from_rows(rows)


def horoscope_regen_kb(period: str) -> Keyboard:
    return Keyboard.from_rows(
        [[Button(text="🔄 Пересчитать заново", payload=f"horo:regen:{period}")]]
    )


def natal_cta_kb() -> Keyboard:
    """Call-to-action shown once after the first (onboarding) natal chart."""
    return Keyboard.from_rows(
        [
            [Button(text="💬 Вопросы", payload="menu:question")],
            [MENU_BACK_BTN],
        ]
    )


def followup_cta_kb() -> Keyboard:
    """Buttons under the 48h-after-registration follow-up message."""
    return Keyboard.from_rows(
        [
            [Button(text="💬 Вопросы", payload="menu:question")],
            [MENU_BACK_BTN],
        ]
    )


# Maps a broadcast button "type" to a fixed bot callback. These are all
# broadcast-specific (bcast:*) callbacks that OPEN the target flow as a NEW
# message — so the broadcast itself is never edited away when the user taps a
# button. The "url" and "ask" types are handled separately (per-button data).
_BROADCAST_CALLBACKS = {
    # "premium" opens the month subscription purchase directly (method picker),
    # mirroring "question_pack" which opens the 10-question pack purchase.
    "premium": "bcast:buy:month",
    "question_pack": "bcast:buy:question_pack",
    "open_chat": "bcast:chat",
    "onboarding": "bcast:onb",
}

# URL buttons must carry a real scheme — otherwise Telegram rejects the whole
# message with BUTTON_URL_INVALID, killing the entire broadcast send.
_URL_SCHEMES = ("http", "https", "tg")


def build_broadcast_kb(variant) -> Keyboard | None:
    """Build the inline keyboard for a BroadcastVariant from its JSON button list.
    Each button is a dict {type, label, value}. Unknown/empty/invalid entries are
    skipped. Returns None when there are no valid buttons (empty markup is
    rejected). A trailing '🔙 Меню' opens the menu as a NEW message so the broadcast
    stays visible."""
    rows: list[list[Button]] = []
    for idx, btn in enumerate(variant.buttons or []):
        if not isinstance(btn, dict):
            continue
        btype = (btn.get("type") or "").strip()
        label = (btn.get("label") or "").strip()
        value = (btn.get("value") or "").strip()
        if not label:
            continue
        if btype == "url":
            if value and value.split("://", 1)[0].lower() in _URL_SCHEMES:
                rows.append([Button(text=label, url=value)])
        elif btype == "ask":
            if value:
                rows.append([Button(text=label, payload=f"bcast:ask:{variant.id}:{idx}")])
        elif btype in _BROADCAST_CALLBACKS:
            rows.append([Button(text=label, payload=_BROADCAST_CALLBACKS[btype])])
    if not rows:
        return None
    rows.append([MENU_BACK_NEW_BTN])
    return Keyboard.from_rows(rows)


def premium_or_back_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [Button(text="💎 Открыть Премиум", payload="menu:premium")],
            [MENU_BACK_BTN],
        ]
    )


def tarot_entry_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [Button(text="🃏 Тянуть без вопроса", payload="tarot:draw")],
            [MENU_BACK_BTN],
        ]
    )


def compat_time_unknown_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [Button(text="Не знаю времени", payload="compat:time:unknown")],
            [MENU_BACK_BTN],
        ]
    )


def name_skip_kb() -> Keyboard:
    return Keyboard.from_rows([[Button(text="Пропустить", payload="onb:name:skip")]])


def gender_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text="Мужской", payload="onb:gender:m"),
                Button(text="Женский", payload="onb:gender:f"),
            ],
            [Button(text="Не указывать", payload="onb:gender:skip")],
        ]
    )


def astro_terms_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text="✨ Да, с терминами", payload="onb:terms:yes"),
                Button(text="💬 Без терминов", payload="onb:terms:no"),
            ]
        ]
    )


def final_confirm_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text="✅ Всё верно", payload="onb:final:ok"),
                Button(text="↩️ Начать заново", payload="onb:final:restart"),
            ]
        ]
    )


def reset_confirm_kb() -> Keyboard:
    return Keyboard.from_rows(
        [
            [
                Button(text="🗑 Да, сбросить", payload="profile:reset:confirm"),
                Button(text="Отмена", payload="cancel"),
            ]
        ]
    )


def natal_paywall_kb() -> Keyboard:
    from astrobot.limits import NATAL_REGEN_PRICE_RUB

    return Keyboard.from_rows(
        [
            [
                Button(
                    text=f"💳 Купить пересчёт — {NATAL_REGEN_PRICE_RUB} ₽",
                    payload="buy:natal_regen",
                )
            ],
            [Button(text="💎 Открыть Премиум", payload="premium:show")],
            [MENU_BACK_BTN],
        ]
    )


def push_hour_kb() -> Keyboard:
    row1 = [Button(text=f"{h}:00", payload=f"push:hour:{h}") for h in range(6, 10)]
    row2 = [Button(text=f"{h}:00", payload=f"push:hour:{h}") for h in range(10, 14)]
    return Keyboard.from_rows(
        [row1, row2, [Button(text="Отмена", payload="push:cancel")]]
    )


def city_choice_kb(options: list[tuple[str, str]]) -> Keyboard:
    """options: list of (label, callback_data)."""
    return Keyboard.from_rows(
        [[Button(text=label, payload=cb)] for label, cb in options]
    )
