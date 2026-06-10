from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from markupsafe import Markup
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from astrobot.config import get_settings
from astrobot.db.models import (
    BirthProfile,
    Favorite,
    GeocodeCache,
    HoroscopeCache,
    LLMUsageLog,
    LunarEvent,
    QuestionLog,
    Response,
    User,
)
from astrobot.db.session import get_engine

_STATIC_DIR = Path(__file__).parent / "admin_static"
_STATIC_URL_PREFIX = "/admin-static"


# ---------- helpers ----------

def _fmt_dt(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value)


def _truncate(value, limit: int = 80) -> str:
    if value is None:
        return ""
    s = str(value).replace("\n", " ").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _badge(text: str, color: str) -> Markup:
    return Markup(
        f'<span style="background:{color};color:#fff;'
        f'padding:2px 8px;border-radius:10px;font-size:12px;'
        f'font-weight:600;white-space:nowrap;">{text}</span>'
    )


def _tier_badge(user: User) -> Markup:
    if user.premium_until and user.premium_until > datetime.now(UTC):
        until = user.premium_until.strftime("%d.%m.%Y")
        return _badge(f"💎 Premium до {until}", "#7c3aed")
    return _badge("🆓 Free", "#64748b")


def _money(value: float) -> str:
    if value is None:
        return "—"
    return f"${value:.4f}"


def _approx_cost(usage: LLMUsageLog) -> str:
    settings = get_settings()
    cost = (
        max(0, (usage.input_tokens or 0) - (usage.cached_tokens or 0))
        / 1_000_000
        * settings.llm_price_input_usd_per_m
        + (usage.cached_tokens or 0)
        / 1_000_000
        * settings.llm_price_cache_hit_usd_per_m
        + (usage.output_tokens or 0)
        / 1_000_000
        * settings.llm_price_output_usd_per_m
    )
    return _money(cost)


# ---------- auth ----------

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        settings = get_settings()
        username = form.get("username") or ""
        password = form.get("password") or ""
        ok = (
            secrets.compare_digest(str(username), settings.admin_user)
            and bool(settings.admin_password)
            and secrets.compare_digest(str(password), settings.admin_password)
        )
        if not ok:
            return False
        request.session.update({"authenticated": True})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return bool(request.session.get("authenticated"))


# ---------- views: 👥 Пользователи ----------

class UserAdmin(ModelView, model=User):
    name = "Юзер"
    name_plural = "Юзеры"
    category = "👥 Пользователи"
    icon = "fa-solid fa-user"

    column_list = [
        User.id,
        User.tg_user_id,
        "tier",
        User.display_name,
        User.gender,
        User.astro_terms_enabled,
        User.bonus_questions,
        User.referral_code,
        User.referred_by_user_id,
        User.push_horoscope_enabled,
        User.push_lunar_enabled,
        User.lang,
        User.created_at,
    ]
    column_labels = {
        User.tg_user_id: "Telegram ID",
        User.display_name: "Имя",
        User.gender: "Пол",
        User.astro_terms_enabled: "Термины",
        User.bonus_questions: "Бонусные ?",
        User.referral_code: "Реф-код",
        User.referred_by_user_id: "Пригласил",
        User.push_horoscope_enabled: "📅 Утро",
        User.push_lunar_enabled: "🌑 Луна",
        User.default_response: "По умолчанию",
        User.lang: "Язык",
        User.premium_until: "Премиум до",
        User.legal_agreed_at: "Согласие",
        User.created_at: "Создан",
        "tier": "Тариф",
    }
    column_formatters = {
        "tier": lambda m, _: _tier_badge(m),
        User.gender: lambda m, _: {"m": "♂ М", "f": "♀ Ж"}.get(m.gender or "", "—"),
        User.created_at: lambda m, _: _fmt_dt(m.created_at),
        User.premium_until: lambda m, _: _fmt_dt(m.premium_until),
        User.legal_agreed_at: lambda m, _: _fmt_dt(m.legal_agreed_at),
    }
    column_formatters_detail = column_formatters
    column_searchable_list = [User.tg_user_id, User.referral_code]
    column_sortable_list = [
        User.id,
        User.created_at,
        User.premium_until,
        User.bonus_questions,
    ]
    column_default_sort = [(User.created_at, True)]
    page_size = 50
    form_excluded_columns = ["profile", "questions", "usage", "responses", "favorites"]


class BirthProfileAdmin(ModelView, model=BirthProfile):
    name = "Профиль"
    name_plural = "Профили"
    category = "👥 Пользователи"
    icon = "fa-solid fa-id-card"

    column_list = [
        BirthProfile.user_id,
        BirthProfile.birth_date,
        BirthProfile.birth_time,
        BirthProfile.time_unknown,
        BirthProfile.city_name,
        BirthProfile.tz,
        BirthProfile.updated_at,
    ]
    column_labels = {
        BirthProfile.user_id: "Юзер ID",
        BirthProfile.birth_date: "Дата",
        BirthProfile.birth_time: "Время",
        BirthProfile.time_unknown: "Без времени",
        BirthProfile.city_name: "Город",
        BirthProfile.tz: "TZ",
        BirthProfile.updated_at: "Обновлён",
    }
    column_formatters = {
        BirthProfile.birth_date: lambda m, _: (
            m.birth_date.strftime("%d.%m.%Y") if m.birth_date else ""
        ),
        BirthProfile.birth_time: lambda m, _: (
            m.birth_time.strftime("%H:%M") if m.birth_time and not m.time_unknown else "—"
        ),
        BirthProfile.updated_at: lambda m, _: _fmt_dt(m.updated_at),
    }
    column_sortable_list = [BirthProfile.updated_at, BirthProfile.birth_date]
    column_default_sort = [(BirthProfile.updated_at, True)]
    column_searchable_list = [BirthProfile.city_name]
    page_size = 50


# ---------- views: 💬 Контент ----------

class QuestionLogAdmin(ModelView, model=QuestionLog):
    name = "Вопрос"
    name_plural = "Вопросы"
    category = "💬 Контент"
    icon = "fa-solid fa-circle-question"

    column_list = [
        QuestionLog.id,
        QuestionLog.user_id,
        QuestionLog.question,
        QuestionLog.answer,
        QuestionLog.created_at,
    ]
    column_labels = {
        QuestionLog.user_id: "Юзер",
        QuestionLog.question: "Вопрос",
        QuestionLog.answer: "Ответ",
        QuestionLog.created_at: "Когда",
    }
    column_formatters = {
        QuestionLog.question: lambda m, _: _truncate(m.question, 100),
        QuestionLog.answer: lambda m, _: _truncate(m.answer, 100),
        QuestionLog.created_at: lambda m, _: _fmt_dt(m.created_at),
    }
    column_default_sort = [(QuestionLog.created_at, True)]
    column_sortable_list = [QuestionLog.created_at]
    column_searchable_list = [QuestionLog.question]
    can_create = False
    can_edit = False
    page_size = 50


class ResponseAdmin(ModelView, model=Response):
    name = "Ответ Астры"
    name_plural = "Ответы Астры"
    category = "💬 Контент"
    icon = "fa-solid fa-comment-dots"

    column_list = [
        Response.id,
        Response.user_id,
        Response.kind,
        Response.brief,
        Response.created_at,
    ]
    column_labels = {
        Response.user_id: "Юзер",
        Response.kind: "Тип",
        Response.brief: "Краткая",
        Response.created_at: "Когда",
    }
    column_formatters = {
        Response.brief: lambda m, _: _truncate(m.brief, 100),
        Response.created_at: lambda m, _: _fmt_dt(m.created_at),
    }
    column_default_sort = [(Response.created_at, True)]
    column_sortable_list = [Response.created_at]
    can_create = False
    can_edit = False
    page_size = 50


class FavoriteAdmin(ModelView, model=Favorite):
    name = "Избранное"
    name_plural = "Избранное"
    category = "💬 Контент"
    icon = "fa-solid fa-star"

    column_list = [
        Favorite.id,
        Favorite.user_id,
        Favorite.kind,
        Favorite.label,
        Favorite.brief,
        Favorite.created_at,
    ]
    column_labels = {
        Favorite.user_id: "Юзер",
        Favorite.kind: "Тип",
        Favorite.label: "Метка",
        Favorite.brief: "Превью",
        Favorite.created_at: "Сохранено",
    }
    column_formatters = {
        Favorite.brief: lambda m, _: _truncate(m.brief, 100),
        Favorite.created_at: lambda m, _: _fmt_dt(m.created_at),
    }
    column_default_sort = [(Favorite.created_at, True)]
    column_sortable_list = [Favorite.created_at]
    page_size = 50


# ---------- views: 💰 Финансы ----------

class LLMUsageLogAdmin(ModelView, model=LLMUsageLog):
    name = "LLM-расход"
    name_plural = "LLM-расходы"
    category = "💰 Финансы"
    icon = "fa-solid fa-coins"

    column_list = [
        LLMUsageLog.id,
        LLMUsageLog.user_id,
        LLMUsageLog.kind,
        LLMUsageLog.model,
        LLMUsageLog.input_tokens,
        LLMUsageLog.cached_tokens,
        LLMUsageLog.output_tokens,
        "cost",
        LLMUsageLog.created_at,
    ]
    column_labels = {
        LLMUsageLog.user_id: "Юзер",
        LLMUsageLog.kind: "Тип",
        LLMUsageLog.model: "Модель",
        LLMUsageLog.input_tokens: "in",
        LLMUsageLog.cached_tokens: "cache",
        LLMUsageLog.output_tokens: "out",
        LLMUsageLog.created_at: "Когда",
        "cost": "≈$",
    }
    column_formatters = {
        "cost": lambda m, _: _approx_cost(m),
        LLMUsageLog.created_at: lambda m, _: _fmt_dt(m.created_at),
    }
    column_default_sort = [(LLMUsageLog.created_at, True)]
    column_sortable_list = [
        LLMUsageLog.created_at,
        LLMUsageLog.input_tokens,
        LLMUsageLog.output_tokens,
    ]
    can_create = False
    can_edit = False
    can_delete = False
    page_size = 100


# ---------- views: ⚙️ Система ----------

class GeocodeCacheAdmin(ModelView, model=GeocodeCache):
    name = "Геокеш"
    name_plural = "Геокеш"
    category = "⚙️ Система"
    icon = "fa-solid fa-map-location-dot"

    column_list = [
        GeocodeCache.id,
        GeocodeCache.query,
        GeocodeCache.display_name,
        GeocodeCache.tz,
        GeocodeCache.fetched_at,
    ]
    column_labels = {
        GeocodeCache.query: "Запрос",
        GeocodeCache.display_name: "Найдено",
        GeocodeCache.tz: "TZ",
        GeocodeCache.fetched_at: "Когда",
    }
    column_formatters = {
        GeocodeCache.fetched_at: lambda m, _: _fmt_dt(m.fetched_at),
    }
    column_default_sort = [(GeocodeCache.fetched_at, True)]
    column_searchable_list = [GeocodeCache.query]
    page_size = 50


class HoroscopeCacheAdmin(ModelView, model=HoroscopeCache):
    name = "Кеш гороскопов"
    name_plural = "Кеш гороскопов"
    category = "⚙️ Система"
    icon = "fa-solid fa-clock-rotate-left"

    column_list = [
        HoroscopeCache.id,
        HoroscopeCache.user_id,
        HoroscopeCache.period,
        HoroscopeCache.computed_for,
        HoroscopeCache.brief,
        HoroscopeCache.created_at,
    ]
    column_labels = {
        HoroscopeCache.user_id: "Юзер",
        HoroscopeCache.period: "Период",
        HoroscopeCache.computed_for: "На дату",
        HoroscopeCache.brief: "Превью",
        HoroscopeCache.created_at: "Сгенерён",
    }
    column_formatters = {
        HoroscopeCache.brief: lambda m, _: _truncate(m.brief, 80),
        HoroscopeCache.created_at: lambda m, _: _fmt_dt(m.created_at),
        HoroscopeCache.computed_for: lambda m, _: (
            m.computed_for.strftime("%d.%m.%Y") if m.computed_for else ""
        ),
    }
    column_default_sort = [(HoroscopeCache.created_at, True)]
    page_size = 50


class LunarEventAdmin(ModelView, model=LunarEvent):
    name = "Лунное событие"
    name_plural = "Лунные события"
    category = "⚙️ Система"
    icon = "fa-solid fa-moon"

    column_list = [
        LunarEvent.id,
        LunarEvent.event_date,
        LunarEvent.kind,
        LunarEvent.notified,
    ]
    column_labels = {
        LunarEvent.event_date: "Дата",
        LunarEvent.kind: "Фаза",
        LunarEvent.notified: "Отправлено",
    }
    column_formatters = {
        LunarEvent.event_date: lambda m, _: (
            m.event_date.strftime("%d.%m.%Y") if m.event_date else ""
        ),
        LunarEvent.kind: lambda m, _: ("🌑 Новолуние" if m.kind == "new" else "🌕 Полнолуние"),
    }
    column_default_sort = [(LunarEvent.event_date, False)]
    page_size = 50


# ---------- setup ----------

def setup_admin(app: FastAPI) -> None:
    settings = get_settings()
    if not (settings.admin_password and settings.admin_secret):
        return

    if _STATIC_DIR.exists():
        app.mount(
            _STATIC_URL_PREFIX,
            StaticFiles(directory=str(_STATIC_DIR)),
            name="admin_skin",
        )

    admin = Admin(
        app,
        engine=get_engine(),
        authentication_backend=AdminAuth(secret_key=settings.admin_secret),
        title="Astra · Админка",
        logo_url=f"{_STATIC_URL_PREFIX}/logo.svg",
        favicon_url=f"{_STATIC_URL_PREFIX}/logo.svg",
        base_url="/admin",
    )
    admin.add_view(UserAdmin)
    admin.add_view(BirthProfileAdmin)
    admin.add_view(QuestionLogAdmin)
    admin.add_view(ResponseAdmin)
    admin.add_view(FavoriteAdmin)
    admin.add_view(LLMUsageLogAdmin)
    admin.add_view(GeocodeCacheAdmin)
    admin.add_view(HoroscopeCacheAdmin)
    admin.add_view(LunarEventAdmin)
