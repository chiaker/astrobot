from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.config import get_settings
from astrobot.db.models import BirthProfile, Broadcast, BroadcastVariant, User
from astrobot.db.session import get_session
from astrobot.limits import (
    BROADCAST_SEGMENT_LABELS,
    BROADCAST_SEGMENTS,
    PREMIUM_LIMITS,
)
from astrobot.web.routes.stats import _MSK, _esc, _layout

router = APIRouter(tags=["admin"])

MAX_BUTTONS = 3

# (value, label) for the button-type <select>. "none" = empty row (skipped).
_BTN_TYPES: list[tuple[str, str]] = [
    ("none", "— нет —"),
    ("url", "Ссылка (URL)"),
    ("ask", "Спросить Астру (вопрос)"),
    ("premium", "Купить премиум"),
    ("question_pack", "Докупить вопросы"),
    ("open_chat", "Открыть чат"),
    ("onboarding", "Пройти онбординг"),
]

_STATUS_LABELS = {
    "draft": ("Черновик", "#64748b"),
    "scheduled": ("Запланирована", "#7c3aed"),
    "sending": ("Отправляется", "#d97706"),
    "sent": ("Отправлена", "#059669"),
    "canceled": ("Отменена", "#dc2626"),
}

# Left-edge accent colour per segment card (matches the segment's vibe).
_SEG_COLORS = {
    "not_onboarded": "#64748b",
    "free_has_questions": "#10b981",
    "free_used_up": "#94a3b8",
    "premium_active": "#7c3aed",
    "premium_no_questions": "#d97706",
}

# Page-local styling for the constructor (the shared admin CSS has no textarea
# rules). The message textarea is compact at rest and expands on focus; the
# small script below also auto-grows it to fit the text while you're editing,
# then it collapses again on blur.
_BC_STYLE = """
.bc-seg{position:relative;overflow:hidden;transition:opacity .15s ease}
.bc-seg.off{opacity:.5}
.bc-seg::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--seg,#7c3aed)}
.bc-lbl{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.5px;margin:0 0 4px}
.bc-toggle{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:#334155;cursor:pointer;user-select:none}
.bc-text{width:100%;padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;
  font-family:inherit;line-height:1.55;resize:none;height:60px;background:#fff;color:#1e293b;
  caret-color:#7c3aed;
  transition:height .15s ease,box-shadow .15s ease,border-color .15s ease;overflow:hidden}
.bc-text:focus{outline:none;border-color:#c4b5fd;box-shadow:0 0 0 3px #ede9fe;min-height:180px}
.bc-in{width:100%;padding:8px 11px;border:1px solid #e2e8f0;border-radius:6px;font-size:14px;
  font-family:inherit;background:#fff;color:#1e293b;caret-color:#7c3aed}
.bc-sel{padding:8px 10px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px;background:#fff;width:100%}
.bc-in:focus,.bc-sel:focus{outline:none;border-color:#7c3aed;box-shadow:0 0 0 2px #ede9fe}
.bc-btns-head{font-size:11px;color:#94a3b8;margin:16px 0 2px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.bc-btnrow{display:grid;grid-template-columns:180px 1fr 1.6fr;gap:8px;margin-top:8px;align-items:center}
.bc-btnrow .bc-lbl{margin:0}
@media(max-width:760px){.bc-btnrow{grid-template-columns:1fr}.bc-btnrow .bc-lbl{display:none}}
.bc-hint{font-size:12px;color:#94a3b8;margin:8px 0 0}
.bc-stat{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#475569;margin:4px 0 0}
.bc-stat b{color:#1e293b}
.bc-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
"""

_BC_SCRIPT = """
(function(){
  function grow(t){t.style.height='auto';t.style.height=Math.min(t.scrollHeight+2,600)+'px';}
  document.querySelectorAll('textarea.bc-text').forEach(function(t){
    t.addEventListener('focus',function(){grow(t);});
    t.addEventListener('input',function(){grow(t);});
    t.addEventListener('blur',function(){t.style.height='';});
  });
  document.querySelectorAll('input.bc-enable').forEach(function(c){
    c.addEventListener('change',function(){
      var card=c.closest('.bc-seg');
      if(card){card.classList.toggle('off',!c.checked);}
    });
  });
})();
"""


def _auth_redirect(request: Request) -> RedirectResponse | None:
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    return None


def _status_badge(status: str) -> str:
    label, color = _STATUS_LABELS.get(status, (status, "#64748b"))
    return f"<span class='badge' style='background:{color}'>{_esc(label)}</span>"


def _fmt_msk(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_MSK).strftime("%Y-%m-%d %H:%M")


# ─── recipient counts per segment (a sanity check before launching) ───────────

async def _segment_counts(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(UTC)
    onboarded = select(BirthProfile.user_id)
    is_onboarded = User.id.in_(onboarded)
    is_premium = and_(User.premium_until.isnot(None), User.premium_until > now)
    # "has questions" (SQL approximation of limits._has_questions; the premium
    # monthly-rollover nuance is ignored — it only shifts a few users).
    free_or_bonus = or_(User.free_questions_balance > 0, User.bonus_questions > 0)
    has_q_premium = or_(free_or_bonus, User.premium_questions_used < (PREMIUM_LIMITS.question_per_month or 0))

    conds = {
        "not_onboarded": ~is_onboarded,
        "free_has_questions": and_(is_onboarded, ~is_premium, free_or_bonus),
        "free_used_up": and_(is_onboarded, ~is_premium, ~free_or_bonus),
        "premium_active": and_(is_onboarded, is_premium, has_q_premium),
        "premium_no_questions": and_(is_onboarded, is_premium, ~has_q_premium),
    }
    out: dict[str, int] = {}
    for seg, cond in conds.items():
        out[seg] = (await session.scalar(select(func.count(User.id)).where(cond))) or 0
    return out


# ─── rendering ────────────────────────────────────────────────────────────────

def _render_list(rows: list[Broadcast]) -> str:
    body = [
        "<div class='card'>"
        "<div class='card-head'><span class='card-title'>Новая рассылка</span></div>"
        "<form method='post' action='/admin/broadcasts' style='display:flex;gap:8px'>"
        "<input type='text' name='name' placeholder='Название кампании' required style='flex:1'>"
        "<button type='submit' class='btn btn-p'>Создать</button>"
        "</form></div>"
    ]
    if not rows:
        body.append("<div class='card'><p>Пока нет рассылок.</p></div>")
    else:
        trs = []
        for b in rows:
            trs.append(
                "<tr>"
                f"<td>#{b.id}</td>"
                f"<td>{_esc(b.name)}</td>"
                f"<td>{_status_badge(b.status)}</td>"
                f"<td>{_fmt_msk(b.scheduled_at)}</td>"
                f"<td>{b.sent_count} / {b.failed_count}</td>"
                f"<td><a href='/admin/broadcasts/{b.id}' class='btn btn-ghost btn-sm'>→</a></td>"
                "</tr>"
            )
        body.append(
            "<div class='card'>"
            "<table><thead><tr>"
            "<th>ID</th><th>Название</th><th>Статус</th><th>Время (МСК)</th>"
            "<th>Отправлено / ошибок</th><th></th>"
            "</tr></thead><tbody>" + "".join(trs) + "</tbody></table></div>"
        )
    return _layout("Рассылки", "".join(body), active="broadcasts")


def _btn_type_select(name: str, current: str) -> str:
    opts = "".join(
        f"<option value='{v}'{' selected' if v == current else ''}>{_esc(lbl)}</option>"
        for v, lbl in _BTN_TYPES
    )
    return f"<select class='bc-sel' name='{name}'>{opts}</select>"


def _render_segment_card(seg: str, variant: BroadcastVariant | None, count: int) -> str:
    enabled = variant.enabled if variant else False
    text = variant.text if variant else ""
    animation = variant.animation if variant else ""
    buttons = list(variant.buttons) if (variant and variant.buttons) else []
    color = _SEG_COLORS.get(seg, "#7c3aed")
    off = "" if enabled else " off"

    btn_rows = [
        "<div class='bc-btnrow'>"
        "<div class='bc-lbl'>Тип</div>"
        "<div class='bc-lbl'>Текст кнопки</div>"
        "<div class='bc-lbl'>URL / текст вопроса</div>"
        "</div>"
    ]
    for i in range(MAX_BUTTONS):
        b = buttons[i] if i < len(buttons) and isinstance(buttons[i], dict) else {}
        btype = b.get("type", "none") or "none"
        label = b.get("label", "")
        value = b.get("value", "")
        btn_rows.append(
            "<div class='bc-btnrow'>"
            f"{_btn_type_select(f'seg_{seg}_btn{i}_type', btype)}"
            f"<input class='bc-in' type='text' name='seg_{seg}_btn{i}_label' "
            f"placeholder='Текст кнопки' value='{_esc(label)}'>"
            f"<input class='bc-in' type='text' name='seg_{seg}_btn{i}_value' "
            f"placeholder='URL или текст вопроса' value='{_esc(value)}'>"
            "</div>"
        )

    return (
        f"<div class='card bc-seg{off}' style='--seg:{color}'>"
        "<div class='card-head'>"
        f"<span class='card-title'>{_esc(BROADCAST_SEGMENT_LABELS[seg])}</span>"
        f"<span class='badge' style='background:#475569'>≈ {count} чел.</span>"
        "</div>"
        f"<label class='bc-toggle'>"
        f"<input type='checkbox' class='bc-enable' name='seg_{seg}_enabled'"
        f"{' checked' if enabled else ''}> Включить этот сегмент</label>"
        "<div class='bc-lbl' style='margin-top:14px'>Текст сообщения</div>"
        f"<textarea class='bc-text' name='seg_{seg}_text' "
        f"placeholder='Текст сообщения…'>{_esc(text)}</textarea>"
        "<div class='bc-lbl' style='margin-top:14px'>Анимация (file_id или URL гифки)</div>"
        f"<input class='bc-in' type='text' name='seg_{seg}_animation' "
        f"placeholder='необязательно' value='{_esc(animation)}'>"
        "<div class='bc-btns-head'>Кнопки (до 3)</div>"
        + "".join(btn_rows)
        + "</div>"
    )


def _render_editor(
    b: Broadcast,
    variants: dict[str, BroadcastVariant],
    counts: dict[str, int],
    default_tg: int | None,
    msg: str,
    err: str,
) -> str:
    parts: list[str] = [f"<style>{_BC_STYLE}</style>"]
    if msg:
        parts.append(f"<div class='alert alert-ok'>{_esc(msg)}</div>")
    if err:
        parts.append(f"<div class='alert alert-err'>{_esc(err)}</div>")

    parts.append(
        "<div class='card'><div class='card-head'>"
        f"<span class='card-title'>Рассылка #{b.id} — {_esc(b.name)}</span>"
        f"{_status_badge(b.status)}</div>"
        "<div class='bc-stat'>"
        f"<span>🕒 Запланировано (МСК): <b>{_fmt_msk(b.scheduled_at)}</b></span>"
        f"<span>✅ Отправлено: <b>{b.sent_count}</b></span>"
        f"<span>⚠️ Ошибок: <b>{b.failed_count}</b></span>"
        "</div>"
        "<p class='bc-hint'>Каждый пользователь получает вариант ровно одного сегмента. "
        "Кнопка «Спросить Астру» сразу задаёт вопрос (тратит 1 вопрос) — ставь её только сегментам с вопросами."
        "</p></div>"
    )

    editable = b.status in ("draft", "scheduled", "canceled")

    # Variant editor (one big save form)
    seg_cards = "".join(
        _render_segment_card(seg, variants.get(seg), counts.get(seg, 0))
        for seg in BROADCAST_SEGMENTS
    )
    if editable:
        parts.append(
            f"<form method='post' action='/admin/broadcasts/{b.id}'>"
            + seg_cards
            + "<div class='card'><button type='submit' class='btn btn-p'>"
            "💾 Сохранить все сегменты</button></div></form>"
        )
    else:
        parts.append(seg_cards)

    # Test send
    seg_opts = "".join(
        f"<option value='{seg}'>{_esc(BROADCAST_SEGMENT_LABELS[seg])}</option>"
        for seg in BROADCAST_SEGMENTS
    )
    parts.append(
        "<div class='card'><div class='card-head'>"
        "<span class='card-title'>Тест-отправка себе</span></div>"
        f"<form method='post' action='/admin/broadcasts/{b.id}/test' class='bc-actions'>"
        f"<select class='bc-sel' name='segment' style='width:auto'>{seg_opts}</select>"
        f"<input class='bc-in' type='number' name='tg_id' placeholder='Telegram ID' "
        f"style='width:auto' value='{default_tg if default_tg else ''}' required>"
        "<button type='submit' class='btn btn-ghost'>📨 Отправить тест</button>"
        "</form>"
        "<p class='bc-hint'>Отправит вариант выбранного сегмента в указанный чат — проверь вид и кнопки перед запуском.</p>"
        "</div>"
    )

    # Launch / schedule
    if editable:
        sched_val = ""
        if b.scheduled_at is not None:
            sched_val = (
                (b.scheduled_at if b.scheduled_at.tzinfo else b.scheduled_at.replace(tzinfo=UTC))
                .astimezone(_MSK)
                .strftime("%Y-%m-%dT%H:%M")
            )
        confirm = "return confirm('Запустить рассылку по выбранным сегментам?')"
        launch = [
            "<div class='card'><div class='card-head'>"
            "<span class='card-title'>Запуск</span></div>"
            f"<form method='post' action='/admin/broadcasts/{b.id}/schedule' "
            f"class='bc-actions' onsubmit=\"{confirm}\">"
            "<span class='bc-lbl' style='margin:0'>Время МСК</span>"
            f"<input class='bc-in' type='datetime-local' name='when' style='width:auto' "
            f"value='{sched_val}' required>"
            "<button type='submit' class='btn btn-p'>🕒 Запланировать</button>"
            "</form>"
            f"<form method='post' action='/admin/broadcasts/{b.id}/send-now' "
            f"style='margin-top:10px' onsubmit=\"{confirm}\">"
            "<button type='submit' class='btn btn-g'>🚀 Отправить сейчас</button>"
            "</form>"
        ]
        if b.status == "scheduled":
            launch.append(
                f"<form method='post' action='/admin/broadcasts/{b.id}/cancel' style='margin-top:10px'>"
                "<button type='submit' class='btn btn-danger'>✖ Отменить запуск</button></form>"
            )
        launch.append("</div>")
        parts.append("".join(launch))
    elif b.status == "sending":
        parts.append("<div class='card'><p>Рассылка отправляется…</p></div>")

    parts.append(
        "<p style='margin-top:12px'><a href='/admin/broadcasts' class='btn btn-ghost'>← К списку</a></p>"
    )
    parts.append(f"<script>{_BC_SCRIPT}</script>")
    return _layout(f"Рассылка #{b.id}", "".join(parts), active="broadcasts")


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _load_variants(session: AsyncSession, broadcast_id: int) -> dict[str, BroadcastVariant]:
    rows = await session.scalars(
        select(BroadcastVariant).where(BroadcastVariant.broadcast_id == broadcast_id)
    )
    return {v.segment: v for v in rows}


# ─── routes ───────────────────────────────────────────────────────────────────

@router.get("/admin/broadcasts", response_class=HTMLResponse)
async def broadcasts_list(request: Request, session: AsyncSession = Depends(get_session)):
    if (r := _auth_redirect(request)) is not None:
        return r
    rows = list(await session.scalars(select(Broadcast).order_by(Broadcast.id.desc())))
    return HTMLResponse(_render_list(rows))


@router.post("/admin/broadcasts")
async def broadcast_create(
    request: Request,
    name: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    name = name.strip() or "Без названия"
    b = Broadcast(name=name, status="draft")
    session.add(b)
    await session.commit()
    return RedirectResponse(url=f"/admin/broadcasts/{b.id}", status_code=303)


@router.get("/admin/broadcasts/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_editor(
    request: Request,
    broadcast_id: int,
    msg: str = "",
    err: str = "",
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        return HTMLResponse(_layout("404", "<p>Рассылка не найдена.</p>"), status_code=404)
    variants = await _load_variants(session, broadcast_id)
    counts = await _segment_counts(session)
    default_tg = get_settings().ops_chat_id
    return HTMLResponse(_render_editor(b, variants, counts, default_tg, msg, err))


@router.post("/admin/broadcasts/{broadcast_id}")
async def broadcast_save(
    request: Request,
    broadcast_id: int,
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        return RedirectResponse(url="/admin/broadcasts", status_code=303)
    if b.status not in ("draft", "scheduled", "canceled"):
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?err=Нельзя редактировать в этом статусе.",
            status_code=303,
        )

    form = await request.form()
    variants = await _load_variants(session, broadcast_id)

    for seg in BROADCAST_SEGMENTS:
        enabled = form.get(f"seg_{seg}_enabled") == "on"
        text = (form.get(f"seg_{seg}_text") or "").strip()
        animation = (form.get(f"seg_{seg}_animation") or "").strip()
        buttons: list[dict] = []
        for i in range(MAX_BUTTONS):
            btype = (form.get(f"seg_{seg}_btn{i}_type") or "none").strip()
            label = (form.get(f"seg_{seg}_btn{i}_label") or "").strip()
            value = (form.get(f"seg_{seg}_btn{i}_value") or "").strip()
            if btype != "none" and label:
                buttons.append({"type": btype, "label": label, "value": value})

        variant = variants.get(seg)
        if variant is None:
            variant = BroadcastVariant(broadcast_id=broadcast_id, segment=seg)
            session.add(variant)
        variant.enabled = enabled
        variant.text = text
        variant.animation = animation
        variant.buttons = buttons

    await session.commit()
    return RedirectResponse(
        url=f"/admin/broadcasts/{broadcast_id}?msg=Сохранено.", status_code=303
    )


@router.post("/admin/broadcasts/{broadcast_id}/test")
async def broadcast_test(
    request: Request,
    broadcast_id: int,
    segment: str = Form(...),
    tg_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    variants = await _load_variants(session, broadcast_id)
    variant = variants.get(segment)
    if variant is None or not (variant.text or variant.animation):
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?err=У этого сегмента нет наполнения.",
            status_code=303,
        )
    # Imported lazily to avoid a heavy import at module load.
    from astrobot.scheduler import _send_broadcast_variant

    bot = request.app.state.bot
    try:
        await _send_broadcast_variant(bot, tg_id, variant)
        out = f"msg=Тест отправлен в чат {tg_id}."
    except Exception as e:  # noqa: BLE001 — surface any send error to the admin
        out = f"err=Не удалось отправить: {e}"
    return RedirectResponse(url=f"/admin/broadcasts/{broadcast_id}?{out}", status_code=303)


def _arm_for_send(b: Broadcast) -> None:
    """Reset progress so a (re)launch starts fresh."""
    b.cursor_user_id = 0
    b.sent_count = 0
    b.failed_count = 0
    b.sent_at = None


@router.post("/admin/broadcasts/{broadcast_id}/schedule")
async def broadcast_schedule(
    request: Request,
    broadcast_id: int,
    when: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        return RedirectResponse(url="/admin/broadcasts", status_code=303)
    if b.status not in ("draft", "scheduled", "canceled"):
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?err=Нельзя запланировать в этом статусе.",
            status_code=303,
        )
    try:
        dt = datetime.fromisoformat(when.strip())
    except ValueError:
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?err=Некорректное время.", status_code=303
        )
    # datetime-local has no tz → interpret as Moscow time, store as UTC.
    dt_utc = (dt.replace(tzinfo=_MSK) if dt.tzinfo is None else dt).astimezone(UTC)
    _arm_for_send(b)
    b.scheduled_at = dt_utc
    b.status = "scheduled"
    await session.commit()
    return RedirectResponse(
        url=f"/admin/broadcasts/{broadcast_id}?msg=Запланировано на {_fmt_msk(dt_utc)} МСК.",
        status_code=303,
    )


@router.post("/admin/broadcasts/{broadcast_id}/send-now")
async def broadcast_send_now(
    request: Request,
    broadcast_id: int,
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        return RedirectResponse(url="/admin/broadcasts", status_code=303)
    if b.status not in ("draft", "scheduled", "canceled"):
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?err=Нельзя отправить в этом статусе.",
            status_code=303,
        )
    _arm_for_send(b)
    b.scheduled_at = datetime.now(UTC)
    b.status = "scheduled"  # the dispatch cron picks it up within a minute
    await session.commit()
    return RedirectResponse(
        url=f"/admin/broadcasts/{broadcast_id}?msg=Отправка начнётся в течение минуты.",
        status_code=303,
    )


@router.post("/admin/broadcasts/{broadcast_id}/cancel")
async def broadcast_cancel(
    request: Request,
    broadcast_id: int,
    session: AsyncSession = Depends(get_session),
):
    if (r := _auth_redirect(request)) is not None:
        return r
    b = await session.get(Broadcast, broadcast_id)
    if b is None:
        return RedirectResponse(url="/admin/broadcasts", status_code=303)
    if b.status in ("draft", "scheduled"):
        b.status = "canceled"
        b.scheduled_at = None
        await session.commit()
        return RedirectResponse(
            url=f"/admin/broadcasts/{broadcast_id}?msg=Запуск отменён.", status_code=303
        )
    return RedirectResponse(
        url=f"/admin/broadcasts/{broadcast_id}?err=Уже нельзя отменить.", status_code=303
    )
