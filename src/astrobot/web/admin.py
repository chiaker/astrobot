from __future__ import annotations

import secrets

from fastapi import FastAPI
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from astrobot.config import get_settings
from astrobot.db.models import (
    BirthProfile,
    GeocodeCache,
    LLMUsageLog,
    QuestionLog,
    Response,
    User,
)
from astrobot.db.session import get_engine


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


class UserAdmin(ModelView, model=User):
    name = "Юзер"
    name_plural = "Юзеры"
    icon = "fa-solid fa-user"
    column_list = [
        User.id,
        User.tg_user_id,
        User.lang,
        User.default_response,
        User.created_at,
    ]
    column_searchable_list = [User.tg_user_id]
    column_sortable_list = [User.id, User.created_at]
    column_default_sort = [(User.created_at, True)]


class BirthProfileAdmin(ModelView, model=BirthProfile):
    name = "Профиль"
    name_plural = "Профили"
    icon = "fa-solid fa-id-card"
    column_list = [
        BirthProfile.user_id,
        BirthProfile.birth_date,
        BirthProfile.time_unknown,
        BirthProfile.city_name,
        BirthProfile.tz,
        BirthProfile.updated_at,
    ]
    column_sortable_list = [BirthProfile.updated_at]


class QuestionLogAdmin(ModelView, model=QuestionLog):
    name = "Вопрос"
    name_plural = "Вопросы"
    icon = "fa-solid fa-circle-question"
    column_list = [
        QuestionLog.id,
        QuestionLog.user_id,
        QuestionLog.question,
        QuestionLog.created_at,
    ]
    column_default_sort = [(QuestionLog.created_at, True)]
    column_sortable_list = [QuestionLog.created_at]
    can_create = False
    can_edit = False


class ResponseAdmin(ModelView, model=Response):
    name = "Ответ"
    name_plural = "Ответы"
    icon = "fa-solid fa-comment-dots"
    column_list = [
        Response.id,
        Response.user_id,
        Response.kind,
        Response.created_at,
    ]
    column_default_sort = [(Response.created_at, True)]
    can_create = False
    can_edit = False


class LLMUsageLogAdmin(ModelView, model=LLMUsageLog):
    name = "LLM-расход"
    name_plural = "LLM-расходы"
    icon = "fa-solid fa-coins"
    column_list = [
        LLMUsageLog.id,
        LLMUsageLog.user_id,
        LLMUsageLog.kind,
        LLMUsageLog.model,
        LLMUsageLog.input_tokens,
        LLMUsageLog.cached_tokens,
        LLMUsageLog.output_tokens,
        LLMUsageLog.created_at,
    ]
    column_default_sort = [(LLMUsageLog.created_at, True)]
    can_create = False
    can_edit = False
    can_delete = False


class GeocodeCacheAdmin(ModelView, model=GeocodeCache):
    name = "Геокеш"
    name_plural = "Геокеш"
    icon = "fa-solid fa-map-location-dot"
    column_list = [
        GeocodeCache.id,
        GeocodeCache.query,
        GeocodeCache.display_name,
        GeocodeCache.tz,
        GeocodeCache.fetched_at,
    ]
    column_default_sort = [(GeocodeCache.fetched_at, True)]


def setup_admin(app: FastAPI) -> None:
    settings = get_settings()
    if not (settings.admin_password and settings.admin_secret):
        return

    admin = Admin(
        app,
        engine=get_engine(),
        authentication_backend=AdminAuth(secret_key=settings.admin_secret),
        title="Astrobot Admin",
        base_url="/admin",
    )
    admin.add_view(UserAdmin)
    admin.add_view(BirthProfileAdmin)
    admin.add_view(QuestionLogAdmin)
    admin.add_view(ResponseAdmin)
    admin.add_view(LLMUsageLogAdmin)
    admin.add_view(GeocodeCacheAdmin)
