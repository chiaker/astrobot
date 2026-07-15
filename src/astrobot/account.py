"""Два сброса — с разной глубиной и разными правами.

- reset_profile() — кнопка пользователя в боте. Только данные рождения, имя и
  настройки знакомства. Оплаченное, избранное, история и квоты остаются.
- reset_account() — «⚠ Полный сброс» в админке. До состояния нового
  пользователя, вместе с квотами и премиумом.

Разделение принципиальное: пользовательский сброс НЕ должен возвращать лимиты,
иначе цикл «потратил 2 бесплатных вопроса → сбросился → снова 2» даёт
бесконечный бесплатный доступ. Возврат квот — только через админку.
"""
from __future__ import annotations

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.db.models import (
    BirthProfile,
    Favorite,
    HoroscopeCache,
    LLMUsageLog,
    QuestionLog,
    Response,
    Subscription,
    SupportTicket,
    User,
)

log = structlog.get_logger(__name__)

# Payment намеренно НЕ трогаем: это бухгалтерия и сверка платежей, а не состояние
# пользователя. Лимиты натала/гороскопа считаются по LLMUsageLog — без его чистки
# сброс не вернёт квоты.
_WIPE_MODELS = (
    BirthProfile,
    HoroscopeCache,
    Favorite,
    Response,
    QuestionLog,
    LLMUsageLog,
    SupportTicket,
    Subscription,
)


async def reset_profile(session: AsyncSession, user: User) -> None:
    """Сбросить ТОЛЬКО профиль: данные рождения, имя, пол, режим терминов.

    Пользователь после этого проходит знакомство заново. Премиум, подписка,
    избранное, история вопросов, бонусы и LLMUsageLog (по нему считаются лимиты
    натала и гороскопа) НЕ трогаются — это кнопка «изменить данные», а не сброс
    аккаунта. Коммитит сам; при ошибке БД пробрасывает исключение.
    """
    uid = user.id
    log.info("profile_reset_start", user_id=uid)

    # HoroscopeCache считается от карты рождения: после смены даты старые тексты
    # относятся к чужой карте. Ровно та же инвалидация, что в тумблерах пола и
    # астротерминов (handlers/profile.py). Кэш натала лежит в самом BirthProfile.
    for model in (BirthProfile, HoroscopeCache):
        await session.execute(
            delete(model).where(model.user_id == uid),
            execution_options={"synchronize_session": False},
        )

    user.display_name = None
    user.gender = None
    user.astro_terms_enabled = True
    await session.commit()
    log.info("profile_reset_done", user_id=uid)


async def reset_account(session: AsyncSession, user: User) -> None:
    """Стереть все данные пользователя и вернуть колонки к дефолтам нового.

    Идентичность (tg_user_id, username, referral_code, created_at) и админский
    флаг excluded_from_stats сохраняются. Коммитит сам; при ошибке БД пробрасывает
    исключение — откат и сообщение пользователю на вызывающей стороне.
    """
    uid = user.id
    log.info("account_reset_start", user_id=uid)

    # synchronize_session=False: часть таблиц — relationship'ы User, и штатная
    # синхронизация сессии полезет в загруженные коллекции (ленивая загрузка под
    # async → greenlet error). Сразу после — commit, так что identity-map не нужен.
    for model in _WIPE_MODELS:
        await session.execute(
            delete(model).where(model.user_id == uid),
            execution_options={"synchronize_session": False},
        )

    user.default_response = "brief"
    user.premium_until = None
    user.premium_reminded_until = None
    user.referred_by_user_id = None
    user.bonus_questions = 0
    user.free_questions_balance = 2
    user.premium_questions_used = 0
    user.questions_reset_at = None
    user.push_horoscope_enabled = False
    user.push_lunar_enabled = False
    user.last_horoscope_push_at = None
    user.followup_sent_at = None
    user.legal_agreed_at = None
    user.display_name = None
    user.gender = None
    user.email = None
    user.astro_terms_enabled = True
    user.natal_regens_bonus = 0
    user.push_tz = None
    user.push_hour = None
    user.push_city_name = None
    await session.commit()
    log.info("account_reset_done", user_id=uid)
