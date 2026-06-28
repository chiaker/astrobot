from __future__ import annotations

import html
import secrets as _secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import desc, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.config import get_settings
from astrobot.db.models import (
    BirthProfile,
    Favorite,
    LLMUsageLog,
    Payment,
    QuestionLog,
    SupportTicket,
    User,
)
from astrobot.db.session import get_session
from astrobot.payments import service as payment_service
from astrobot.payments import yookassa
from astrobot.redis_client import get_redis

router = APIRouter(tags=["admin"])

PAGE_SIZE = 50

# Brute-force protection for the admin login.
_LOGIN_MAX_FAILS = 10
_LOGIN_WINDOW_SECONDS = 15 * 60


def _login_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _login_blocked(ip: str) -> bool:
    """True when this IP has exceeded the failed-login budget for the window."""
    try:
        redis = get_redis()
        n = await redis.get(f"admin:login:fail:{ip}")
        return n is not None and int(n) >= _LOGIN_MAX_FAILS
    except Exception:
        return False  # Redis down → don't lock admins out


async def _record_login_fail(ip: str) -> None:
    try:
        redis = get_redis()
        key = f"admin:login:fail:{ip}"
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, _LOGIN_WINDOW_SECONDS)
    except Exception:
        pass


async def _clear_login_fails(ip: str) -> None:
    try:
        await get_redis().delete(f"admin:login:fail:{ip}")
    except Exception:
        pass

# ─── cost / format helpers ────────────────────────────────────────────────────

def _cost_of(inp, cached, out, s) -> float:
    return (
        max(0, (inp or 0) - (cached or 0)) / 1_000_000 * s.llm_price_input_usd_per_m
        + (cached or 0) / 1_000_000 * s.llm_price_cache_hit_usd_per_m
        + (out or 0) / 1_000_000 * s.llm_price_output_usd_per_m
    )

def _money(v: float) -> str:
    if v >= 100:
        return f"${v:.0f}"
    if v >= 1:
        return f"${v:.2f}"
    return f"${v:.5f}"

def _is_prem(pu, now: datetime) -> bool:
    return pu is not None and pu > now

# Timestamps are stored in UTC; the admin panel displays them in Moscow time.
_MSK = ZoneInfo("Europe/Moscow")

def _msk(dt):
    """Convert a stored (UTC) datetime to Moscow time for display. Naive datetimes
    are assumed UTC. Returns None unchanged."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_MSK)

def _fmt_dt(dt) -> str:
    dt = _msk(dt)
    return dt.strftime("%d.%m.%Y %H:%M") if dt else "—"

def _fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"

def _esc(s) -> str:
    """HTML-escape any user-controlled value before embedding in templates."""
    return html.escape(str(s)) if s is not None else ""


def _trunc(s, n: int = 80) -> str:
    if not s:
        return ""
    s = str(s).replace("\n", " ").strip()
    s = s[:n-1] + "…" if len(s) > n else s
    return html.escape(s)

def _tier_badge(pu, now) -> str:
    if _is_prem(pu, now):
        return f'<span class="badge b-prem">💎 {_msk(pu).strftime("%d.%m.%y")}</span>'
    return '<span class="badge b-free">Free</span>'

def _kind_badge(k: str) -> str:
    cls = {"natal": "b-natal", "horoscope": "b-horo", "question": "b-q"}.get(k, "b-free")
    return f'<span class="badge {cls}">{k}</span>'

def _sparkline(vals: list[float], w: int = 120, h: int = 36, color: str = "#7c3aed") -> str:
    if not vals or max(vals) == 0:
        return f'<svg width="{w}" height="{h}"></svg>'
    mx = max(vals)
    n = len(vals)
    step = w / max(n - 1, 1)
    pts = " ".join(
        f"{i * step:.0f},{h - max(2, v / mx * (h - 4) + 2):.0f}"
        for i, v in enumerate(vals)
    )
    return (
        f'<svg width="{w}" height="{h}" style="overflow:visible;display:block">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linejoin="round" points="{pts}"/>'
        '</svg>'
    )

def _hbars(items: dict[str, int]) -> str:
    if not items:
        return '<p class="muted small">Нет данных.</p>'
    mx = max(items.values()) or 1
    colors = {"natal": "#8b5cf6", "horoscope": "#3b82f6", "question": "#10b981"}
    out = []
    for name, val in items.items():
        w = int(val / mx * 180)
        c = colors.get(name, "#94a3b8")
        out.append(
            f'<div class="bar-row">'
            f'<div class="bar-name">{name}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w}px;background:{c}"></div></div>'
            f'<div class="bar-val">{val}</div>'
            f'</div>'
        )
    return "".join(out)


# ─── CSS ─────────────────────────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#1e293b;font-size:14px}
a{color:#7c3aed;text-decoration:none}a:hover{color:#5b21b6;text-decoration:underline}

.topnav{background:#1e1b4b;display:flex;align-items:center;padding:0 20px;height:52px;gap:6px;position:sticky;top:0;z-index:100}
.topnav .brand{font-weight:700;font-size:16px;color:#a5b4fc;margin-right:20px;white-space:nowrap}
.topnav nav{display:flex;gap:2px;flex:1}
.topnav nav a{color:#94a3b8;padding:6px 11px;border-radius:6px;font-size:13px;font-weight:500;white-space:nowrap}
.topnav nav a:hover{color:#fff;background:rgba(255,255,255,.08);text-decoration:none}
.topnav nav a.active{color:#fff;background:rgba(255,255,255,.13)}
.topnav .out{color:#64748b;font-size:13px;padding:6px 11px;border-radius:6px}
.topnav .out:hover{color:#94a3b8;text-decoration:none;background:rgba(255,255,255,.06)}

.page{max-width:1360px;margin:0 auto;padding:24px 24px 60px}

h1.ph{font-size:22px;font-weight:700;margin-bottom:4px}
.ph-sub{color:#64748b;font-size:13px;margin-bottom:20px}

h2.sec{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#94a3b8;margin:28px 0 10px}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}
.kpi{background:#fff;border-radius:10px;padding:15px 17px;border:1px solid #e2e8f0}
.kpi-label{font-size:10px;color:#94a3b8;font-weight:700;text-transform:uppercase;letter-spacing:.8px}
.kpi-value{font-size:26px;font-weight:700;color:#1e293b;margin:5px 0 2px;line-height:1}
.kpi-sub{font-size:11px;color:#94a3b8}
.kpi-spark{margin-top:8px}
.kpi.accent .kpi-value{color:#7c3aed}
.kpi.green .kpi-value{color:#059669}

.card{background:#fff;border-radius:10px;border:1px solid #e2e8f0;padding:18px 20px;margin-bottom:14px}
.card-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.card-title{font-size:13px;font-weight:600;color:#475569}

.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
@media(max-width:900px){.g2,.g3{grid-template-columns:1fr}}

.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 12px;color:#64748b;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;border-bottom:2px solid #e2e8f0;white-space:nowrap;background:#fff}
td{padding:9px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
tr:last-child td{border-bottom:none}
tbody tr:hover td{background:#faf7ff}
.r{text-align:right}

.badge{display:inline-flex;align-items:center;padding:2px 7px;border-radius:8px;font-size:11px;font-weight:600;color:#fff;white-space:nowrap}
.b-prem{background:#7c3aed}
.b-free{background:#94a3b8}
.b-natal{background:#8b5cf6}
.b-horo{background:#3b82f6}
.b-q{background:#10b981}
.b-ok{background:#059669}
.b-warn{background:#d97706}

.search-row{display:flex;gap:8px;margin-bottom:16px;align-items:center}
.search-row input{flex:1;padding:8px 12px;border:1px solid #e2e8f0;border-radius:6px;font-size:13px;background:#fff}
.search-row input:focus{outline:none;border-color:#7c3aed;box-shadow:0 0 0 2px #ede9fe}

.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;border:none;text-decoration:none;line-height:1}
.btn:hover{text-decoration:none;opacity:.9}
.btn-p{background:#7c3aed;color:#fff}
.btn-g{background:#059669;color:#fff}
.btn-ghost{background:#f1f5f9;color:#475569}
.btn-ghost:hover{background:#e2e8f0}
.btn-danger{background:#dc2626;color:#fff}
.btn-sm{padding:4px 9px;font-size:12px}

.fg{display:flex;flex-direction:column;gap:4px;margin-bottom:14px}
.fg label{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
.fg input[type=text],.fg input[type=number],.fg input[type=datetime-local]{padding:8px 11px;border:1px solid #e2e8f0;border-radius:6px;font-size:14px;width:100%}
.fg input:focus{outline:none;border-color:#7c3aed;box-shadow:0 0 0 2px #ede9fe}
.fg .note{font-size:11px;color:#94a3b8}
.fa{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding-top:4px}

.alert{padding:10px 16px;border-radius:8px;font-size:13px;margin-bottom:14px}
.alert-ok{background:#dcfce7;color:#166534;border:1px solid #86efac}
.alert-err{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5}

code{font-family:monospace;font-size:12px;background:#f1f5f9;padding:1px 5px;border-radius:3px}
.muted{color:#94a3b8}
.small{font-size:12px}
.mono{font-family:monospace}

.dg{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:700px){.dg{grid-template-columns:1fr}}
.di .dl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#94a3b8;margin-bottom:2px}
.di .dv{font-size:14px;color:#1e293b;font-weight:500}

.bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.bar-name{font-size:12px;color:#475569;width:90px;flex-shrink:0;text-align:right}
.bar-track{flex:1;background:#f1f5f9;border-radius:3px;height:14px}
.bar-fill{height:14px;border-radius:3px;background:#7c3aed;min-width:2px}
.bar-val{font-size:12px;color:#64748b;width:42px}

.pager{display:flex;gap:6px;justify-content:center;padding-top:16px;flex-wrap:wrap}
.pager a,.pager span{padding:5px 10px;border-radius:5px;border:1px solid #e2e8f0;font-size:13px;color:#475569}
.pager a:hover{background:#f1f5f9;text-decoration:none}
.pager .cur{background:#7c3aed;color:#fff;border-color:#7c3aed}

.sep{height:1px;background:#e2e8f0;margin:20px 0}
"""

# ─── layout ──────────────────────────────────────────────────────────────────

def _layout(title: str, body: str, active: str = "") -> str:
    nav = [
        ("📊 Сводка", "/admin/stats", "stats"),
        ("👥 Юзеры", "/admin/users", "users"),
        ("💳 Платежи", "/admin/payments", "payments"),
        ("🆘 Поддержка", "/admin/support", "support"),
        ("📋 Логи LLM", "/admin/logs", "logs"),
        ("🚫 Исключения", "/admin/exclusions", "exclusions"),
    ]
    nav_html = "".join(
        f'<a href="{url}" class="{"active" if k == active else ""}">{lbl}</a>'
        for lbl, url, k in nav
    )
    return (
        "<!doctype html><html lang='ru'><head>"
        "<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title} · Astra Admin</title>"
        "<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap' rel='stylesheet'>"
        f"<style>{_CSS}</style></head><body>"
        f"<div class='topnav'>"
        f"  <a href='/admin/stats' class='brand'>✨ Astra Admin</a>"
        f"  <nav>{nav_html}</nav>"
        f"  <a href='/admin/logout' class='out'>Выйти</a>"
        f"</div>"
        f"<div class='page'>{body}</div>"
        "</body></html>"
    )


# ─── stats page ──────────────────────────────────────────────────────────────

async def _gather_stats(session: AsyncSession) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)
    d30 = now - timedelta(days=30)

    # Users flagged as excluded (test/staff) are dropped from every metric below.
    excluded_ids = set(
        (
            await session.scalars(
                select(User.id).where(User.excluded_from_stats.is_(True))
            )
        ).all()
    )

    def _excl(user_id_col):
        """Filter expression dropping excluded users by their user_id column.
        No-op (always-true) when nothing is excluded."""
        return user_id_col.notin_(excluded_ids) if excluded_ids else true()

    total = (await session.scalar(select(func.count(User.id)).where(_excl(User.id)))) or 0
    premium = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.premium_until.isnot(None), User.premium_until > now, _excl(User.id)
            )
        )
    ) or 0
    new_24h = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= h24, _excl(User.id))
        )
    ) or 0
    new_7d = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= d7, _excl(User.id))
        )
    ) or 0
    referred = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.referred_by_user_id.isnot(None), _excl(User.id)
            )
        )
    ) or 0

    # LLM usage last 30 days
    rows = (
        await session.execute(
            select(
                LLMUsageLog.user_id,
                LLMUsageLog.kind,
                LLMUsageLog.input_tokens,
                LLMUsageLog.cached_tokens,
                LLMUsageLog.output_tokens,
                LLMUsageLog.created_at,
                User.premium_until,
                User.tg_user_id,
            )
            .join(User, User.id == LLMUsageLog.user_id)
            .where(LLMUsageLog.created_at >= d30, _excl(LLMUsageLog.user_id))
        )
    ).mappings().all()

    windows = {"24h": h24, "7d": d7, "30d": d30}
    cost_tier: dict[str, dict[str, float]] = {w: {"free": 0.0, "premium": 0.0} for w in windows}
    cnt_tier: dict[str, dict[str, int]] = {w: {"free": 0, "premium": 0} for w in windows}
    kind_7d: dict[str, int] = {}
    by_user: dict[int, dict] = {}
    daily_cost: dict[str, float] = {}

    for r in rows:
        prem = _is_prem(r["premium_until"], now)
        tier = "premium" if prem else "free"
        cost = _cost_of(r["input_tokens"], r["cached_tokens"], r["output_tokens"], settings)
        for w, since in windows.items():
            if r["created_at"] >= since:
                cost_tier[w][tier] += cost
                cnt_tier[w][tier] += 1
        if r["created_at"] >= d7:
            kind_7d[r["kind"]] = kind_7d.get(r["kind"], 0) + 1
        dk = r["created_at"].strftime("%d.%m")
        daily_cost[dk] = daily_cost.get(dk, 0.0) + cost
        u = by_user.setdefault(
            r["user_id"],
            {"uid": r["user_id"], "tg": r["tg_user_id"], "tier": tier, "cost": 0.0, "calls": 0},
        )
        u["cost"] += cost
        u["calls"] += 1

    top10 = sorted(by_user.values(), key=lambda x: x["cost"], reverse=True)[:10]

    # Daily new users last 14 days
    daily_users_rows = (
        await session.execute(
            select(
                func.date_trunc("day", User.created_at).label("d"),
                func.count(User.id).label("n"),
            )
            .where(User.created_at >= now - timedelta(days=14), _excl(User.id))
            .group_by("d")
            .order_by("d")
        )
    ).all()
    daily_users = {r.d.strftime("%d.%m"): r.n for r in daily_users_rows}

    questions_7d = (
        await session.scalar(
            select(func.count(QuestionLog.id)).where(
                QuestionLog.created_at >= d7, _excl(QuestionLog.user_id)
            )
        )
    ) or 0
    favorites_total = (
        await session.scalar(
            select(func.count(Favorite.id)).where(_excl(Favorite.user_id))
        )
    ) or 0

    days14 = [(now - timedelta(days=i)).strftime("%d.%m") for i in range(13, -1, -1)]

    # ── Money: revenue (succeeded payments, by paid_at) ──
    async def _revenue(since: datetime | None) -> float:
        q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "succeeded", _excl(Payment.user_id)
        )
        if since is not None:
            q = q.where(Payment.paid_at >= since)
        return float((await session.scalar(q)) or 0)

    revenue = {
        "24h": await _revenue(h24),
        "7d": await _revenue(d7),
        "30d": await _revenue(d30),
        "all": await _revenue(None),
    }
    rev_by_kind_rows = (
        await session.execute(
            select(
                Payment.kind,
                func.coalesce(func.sum(Payment.amount), 0),
                func.count(Payment.id),
            )
            .where(
                Payment.status == "succeeded",
                Payment.paid_at >= d30,
                _excl(Payment.user_id),
            )
            .group_by(Payment.kind)
        )
    ).all()
    rev_by_kind = {r[0]: {"amount": float(r[1] or 0), "count": r[2]} for r in rev_by_kind_rows}

    refunds_30d_amount = float(
        (
            await session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.status == "refunded",
                    Payment.refunded_at >= d30,
                    _excl(Payment.user_id),
                )
            )
        )
        or 0
    )
    refunds_30d_count = (
        await session.scalar(
            select(func.count(Payment.id)).where(
                Payment.status == "refunded",
                Payment.refunded_at >= d30,
                _excl(Payment.user_id),
            )
        )
    ) or 0

    paying_users = (
        await session.scalar(
            select(func.count(func.distinct(Payment.user_id))).where(
                Payment.status == "succeeded", _excl(Payment.user_id)
            )
        )
    ) or 0

    # ── Subscriptions / onboarding ──
    profiles = (
        await session.scalar(
            select(func.count(BirthProfile.user_id)).where(_excl(BirthProfile.user_id))
        )
    ) or 0
    expiring_7d = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.premium_until.isnot(None),
                User.premium_until > now,
                User.premium_until <= now + timedelta(days=7),
                _excl(User.id),
            )
        )
    ) or 0

    # ── Activity (distinct active users by LLM calls) ──
    async def _active(since: datetime) -> int:
        return (
            await session.scalar(
                select(func.count(func.distinct(LLMUsageLog.user_id))).where(
                    LLMUsageLog.created_at >= since, _excl(LLMUsageLog.user_id)
                )
            )
        ) or 0

    dau = await _active(h24)
    wau = await _active(d7)
    mau = await _active(d30)

    cost_30d = cost_tier["30d"]["free"] + cost_tier["30d"]["premium"]

    return {
        "now": now,
        "settings": settings,
        "total": total,
        "premium": premium,
        "free": total - premium,
        "new_24h": new_24h,
        "new_7d": new_7d,
        "referred": referred,
        "cost_tier": cost_tier,
        "cnt_tier": cnt_tier,
        "kind_7d": dict(sorted(kind_7d.items(), key=lambda kv: -kv[1])),
        "top10": top10,
        "daily_cost": daily_cost,
        "daily_users": daily_users,
        "days14": days14,
        "questions_7d": questions_7d,
        "favorites_total": favorites_total,
        "revenue": revenue,
        "rev_by_kind": rev_by_kind,
        "refunds_30d_amount": refunds_30d_amount,
        "refunds_30d_count": refunds_30d_count,
        "paying_users": paying_users,
        "profiles": profiles,
        "expiring_7d": expiring_7d,
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "cost_30d": cost_30d,
    }


def _render_stats(d: dict) -> str:
    ct = d["cost_tier"]
    cn = d["cnt_tier"]
    s = d["settings"]

    def total_cost(w):
        return ct[w]["free"] + ct[w]["premium"]

    def total_cnt(w):
        return cn[w]["free"] + cn[w]["premium"]

    # sparklines
    cost_vals = [d["daily_cost"].get(day, 0.0) for day in d["days14"]]
    user_vals = [d["daily_users"].get(day, 0) for day in d["days14"]]
    spark_cost = _sparkline(cost_vals, color="#7c3aed")
    spark_users = _sparkline([float(v) for v in user_vals], color="#059669")

    # top-10 table
    top_rows = "".join(
        f"<tr><td><a href='/admin/users/{u['uid']}'>{u['uid']}</a></td>"
        f"<td class='mono'>{u['tg']}</td>"
        f"<td>{'<span class=\"badge b-prem\">💎</span>' if u['tier']=='premium' else '<span class=\"badge b-free\">Free</span>'}</td>"
        f"<td class='r'>{u['calls']}</td>"
        f"<td class='r'><b>{_money(u['cost'])}</b></td></tr>"
        for u in d["top10"]
    ) or "<tr><td colspan='5' class='muted'>Нет данных.</td></tr>"

    pct_prem = f"{d['premium']/d['total']*100:.0f}%" if d["total"] else "0%"

    rev = d["revenue"]
    paying = d["paying_users"]
    conv = f"{paying / d['total'] * 100:.1f}%" if d["total"] else "0%"
    arppu = rev["all"] / paying if paying else 0.0
    onb_pct = f"{d['profiles'] / d['total'] * 100:.0f}%" if d["total"] else "0%"
    rk = d["rev_by_kind"]

    def _rk(k: str) -> str:
        return _money_rub(rk.get(k, {}).get("amount", 0))

    body = f"""
<h1 class='ph'>Сводка</h1>
<p class='ph-sub'>Обновлено: {_fmt_dt(d['now'])}</p>

<h2 class='sec'>Пользователи</h2>
<div class='kpis'>
  <div class='kpi'>
    <div class='kpi-label'>Всего</div>
    <div class='kpi-value'>{d['total']}</div>
    <div class='kpi-sub'>+{d['new_7d']} за 7д</div>
    <div class='kpi-spark'>{spark_users}</div>
  </div>
  <div class='kpi accent'>
    <div class='kpi-label'>Премиум</div>
    <div class='kpi-value'>{d['premium']}</div>
    <div class='kpi-sub'>{pct_prem} от базы</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Free</div>
    <div class='kpi-value'>{d['free']}</div>
    <div class='kpi-sub'>{100 - int(d['premium']/d['total']*100) if d['total'] else 100}% от базы</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Новых (24ч)</div>
    <div class='kpi-value green'>{d['new_24h']}</div>
    <div class='kpi-sub'>+{d['new_7d']} за 7д</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>По рефералам</div>
    <div class='kpi-value'>{d['referred']}</div>
    <div class='kpi-sub'>всего привлечено</div>
  </div>
</div>

<h2 class='sec'>Деньги</h2>
<div class='kpis'>
  <div class='kpi green'>
    <div class='kpi-label'>Выручка сутки</div>
    <div class='kpi-value'>{_money_rub(rev['24h'])}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Выручка 7 дней</div>
    <div class='kpi-value'>{_money_rub(rev['7d'])}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Выручка 30 дней</div>
    <div class='kpi-value'>{_money_rub(rev['30d'])}</div>
  </div>
  <div class='kpi accent'>
    <div class='kpi-label'>Выручка всего</div>
    <div class='kpi-value'>{_money_rub(rev['all'])}</div>
    <div class='kpi-sub'>ARPPU {_money_rub(arppu)}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Возвраты 30д</div>
    <div class='kpi-value'>{_money_rub(d['refunds_30d_amount'])}</div>
    <div class='kpi-sub'>{d['refunds_30d_count']} шт</div>
  </div>
</div>
<p class='small muted' style='margin-top:6px'>
  Выручка 30д по товарам: подписки <b>{_rk('subscription')}</b> ·
  пакеты вопросов <b>{_rk('question_pack')}</b> ·
  пересчёты натала <b>{_rk('natal_regen')}</b>
</p>

<h2 class='sec'>Подписки и конверсия</h2>
<div class='kpis'>
  <div class='kpi accent'>
    <div class='kpi-label'>Платящих всего</div>
    <div class='kpi-value'>{paying}</div>
    <div class='kpi-sub'>конверсия в платных: {conv}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Активный премиум</div>
    <div class='kpi-value'>{d['premium']}</div>
    <div class='kpi-sub'>истекают за 7д: {d['expiring_7d']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Прошли онбординг</div>
    <div class='kpi-value'>{d['profiles']}</div>
    <div class='kpi-sub'>{onb_pct} от всех</div>
  </div>
</div>

<h2 class='sec'>Активность</h2>
<div class='kpis'>
  <div class='kpi'>
    <div class='kpi-label'>DAU (сутки)</div>
    <div class='kpi-value'>{d['dau']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>WAU (7 дней)</div>
    <div class='kpi-value'>{d['wau']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>MAU (30 дней)</div>
    <div class='kpi-value'>{d['mau']}</div>
  </div>
</div>

<h2 class='sec'>Расходы LLM</h2>
<div class='kpis'>
  <div class='kpi'>
    <div class='kpi-label'>Сутки</div>
    <div class='kpi-value accent'>{_money(total_cost('24h'))}</div>
    <div class='kpi-sub'>free {_money(ct['24h']['free'])} · prem {_money(ct['24h']['premium'])}</div>
    <div class='kpi-spark'>{spark_cost}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>7 дней</div>
    <div class='kpi-value'>{_money(total_cost('7d'))}</div>
    <div class='kpi-sub'>free {_money(ct['7d']['free'])} · prem {_money(ct['7d']['premium'])}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>30 дней</div>
    <div class='kpi-value'>{_money(total_cost('30d'))}</div>
    <div class='kpi-sub'>free {_money(ct['30d']['free'])} · prem {_money(ct['30d']['premium'])}</div>
  </div>
</div>

<h2 class='sec'>Объём запросов</h2>
<div class='kpis'>
  <div class='kpi'>
    <div class='kpi-label'>LLM (24ч)</div>
    <div class='kpi-value'>{total_cnt('24h')}</div>
    <div class='kpi-sub'>free {cn['24h']['free']} · prem {cn['24h']['premium']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>LLM (7д)</div>
    <div class='kpi-value'>{total_cnt('7d')}</div>
    <div class='kpi-sub'>free {cn['7d']['free']} · prem {cn['7d']['premium']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Вопросы (7д)</div>
    <div class='kpi-value'>{d['questions_7d']}</div>
  </div>
  <div class='kpi'>
    <div class='kpi-label'>Избранное</div>
    <div class='kpi-value'>{d['favorites_total']}</div>
    <div class='kpi-sub'>всего сохранено</div>
  </div>
</div>

<div class='g2' style='margin-top:14px'>
  <div class='card'>
    <div class='card-head'><span class='card-title'>Топ-10 по расходам (30д)</span></div>
    <div class='tbl-wrap'>
      <table>
        <thead><tr><th>ID</th><th>TG ID</th><th>Тариф</th><th class='r'>Вызовов</th><th class='r'>Стоимость</th></tr></thead>
        <tbody>{top_rows}</tbody>
      </table>
    </div>
  </div>
  <div class='card'>
    <div class='card-head'><span class='card-title'>По типам запросов (7д)</span></div>
    {_hbars(d['kind_7d'])}
    <div class='sep'></div>
    <p class='small muted'>Цены: in <code>${s.llm_price_input_usd_per_m}/M</code> · out <code>${s.llm_price_output_usd_per_m}/M</code> · cache <code>${s.llm_price_cache_hit_usd_per_m}/M</code></p>
  </div>
</div>
"""
    return _layout("Сводка", body, active="stats")


# ─── users page ──────────────────────────────────────────────────────────────

async def _gather_users(
    session: AsyncSession, search: str, page: int
) -> tuple[list, int]:
    base = select(User).order_by(desc(User.created_at))
    cnt_q = select(func.count(User.id))
    if search:
        term = f"%{search}%"
        flt = or_(
            User.tg_user_id.cast(type_=None).like(term),  # cast via ilike workaround
            User.display_name.ilike(term),
            User.referral_code.ilike(term),
        )
        # simpler: use text search on tg_user_id string
        from sqlalchemy import String as SAStr
        from sqlalchemy import cast
        flt = or_(
            cast(User.tg_user_id, SAStr).like(term),
            User.display_name.ilike(term),
            User.referral_code.ilike(term),
        )
        base = base.where(flt)
        cnt_q = cnt_q.where(flt)

    total = (await session.scalar(cnt_q)) or 0
    users = (
        await session.execute(base.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))
    ).scalars().all()
    return list(users), total


def _render_users(users: list, total: int, page: int, search: str, now: datetime) -> str:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    def page_url(p: int) -> str:
        s = f"&search={_esc(search)}" if search else ""
        return f"/admin/users?page={p}{s}"

    pager_html = ""
    if total_pages > 1:
        pages = []
        for p in range(1, total_pages + 1):
            if p == page:
                pages.append(f"<span class='cur'>{p}</span>")
            elif abs(p - page) <= 2 or p in (1, total_pages):
                pages.append(f"<a href='{page_url(p)}'>{p}</a>")
            elif abs(p - page) == 3:
                pages.append("<span class='muted'>…</span>")
        pager_html = f"<div class='pager'>{''.join(pages)}</div>"

    rows = []
    for u in users:
        tier = _tier_badge(u.premium_until, now)
        name = _esc(u.display_name) if u.display_name else '<span class="muted">—</span>'
        gender_icon = {"m": "♂", "f": "♀"}.get(u.gender or "", "—")
        bonus_natal = (
            '<span class="badge b-warn">∞</span>' if (u.natal_regens_bonus or 0) == -1
            else str(u.natal_regens_bonus or 0)
        )
        rows.append(
            f"<tr>"
            f"<td><a href='/admin/users/{u.id}'>{u.id}</a></td>"
            f"<td class='mono small'>{u.tg_user_id}</td>"
            f"<td>{name}</td>"
            f"<td>{gender_icon}</td>"
            f"<td>{tier}</td>"
            f"<td class='r'>{u.bonus_questions or 0}</td>"
            f"<td class='r'>{bonus_natal}</td>"
            f"<td class='small muted'>{_msk(u.created_at).strftime('%d.%m.%Y') if u.created_at else '—'}</td>"
            f"<td><a href='/admin/users/{u.id}' class='btn btn-ghost btn-sm'>→</a></td>"
            f"</tr>"
        )

    table = f"""
<div class='tbl-wrap'>
<table>
<thead><tr>
  <th>ID</th><th>TG ID</th><th>Имя</th><th>Пол</th><th>Тариф</th>
  <th class='r'>Бонус?</th><th class='r'>Регенов Н.</th><th>Создан</th><th></th>
</tr></thead>
<tbody>{''.join(rows) or "<tr><td colspan='9' class='muted'>Нет пользователей.</td></tr>"}</tbody>
</table>
</div>
{pager_html}
"""
    body = f"""
<h1 class='ph'>Пользователи</h1>
<p class='ph-sub'>Всего: {total}</p>
<form method='get' action='/admin/users'>
  <div class='search-row'>
    <input name='search' placeholder='TG ID, имя или реф-код…' value='{_esc(search)}'>
    <button type='submit' class='btn btn-p'>Найти</button>
    <a href='/admin/users' class='btn btn-ghost'>Сброс</a>
  </div>
</form>
{table}
"""
    return _layout("Юзеры", body, active="users")


# ─── user detail ─────────────────────────────────────────────────────────────

async def _gather_user_detail(session: AsyncSession, user_id: int):
    user = await session.get(User, user_id)
    if user is None:
        return None, None, {}
    profile = await session.get(BirthProfile, user_id)

    # LLM usage last 30 days
    now = datetime.now(UTC)
    d30 = now - timedelta(days=30)
    settings = get_settings()
    llm_rows = (
        await session.execute(
            select(LLMUsageLog.kind, LLMUsageLog.input_tokens, LLMUsageLog.cached_tokens, LLMUsageLog.output_tokens)
            .where(LLMUsageLog.user_id == user_id, LLMUsageLog.created_at >= d30)
        )
    ).all()
    llm_by_kind: dict[str, dict] = {}
    for r in llm_rows:
        e = llm_by_kind.setdefault(r.kind, {"calls": 0, "cost": 0.0})
        e["calls"] += 1
        e["cost"] += _cost_of(r.input_tokens, r.cached_tokens, r.output_tokens, settings)

    fav_count = (
        await session.scalar(select(func.count(Favorite.id)).where(Favorite.user_id == user_id))
    ) or 0
    q_count = (
        await session.scalar(select(func.count(QuestionLog.id)).where(QuestionLog.user_id == user_id))
    ) or 0

    stats = {"llm": llm_by_kind, "favorites": fav_count, "questions": q_count}
    return user, profile, stats


def _render_user_detail(user, profile, stats: dict, now: datetime, msg: str = "", err: str = "") -> str:
    prem_val = (
        _msk(user.premium_until).strftime("%Y-%m-%dT%H:%M") if user.premium_until else ""
    )
    alert = ""
    if msg:
        alert = f"<div class='alert alert-ok'>✓ {_esc(msg)}</div>"
    if err:
        alert = f"<div class='alert alert-err'>✗ {_esc(err)}</div>"

    gender_str = {"m": "♂ Мужской", "f": "♀ Женский"}.get(user.gender or "", "—")
    natal_bonus = (
        "∞ безлимит (тест)" if (user.natal_regens_bonus or 0) == -1
        else str(user.natal_regens_bonus or 0)
    )

    llm_rows = "".join(
        f"<tr><td>{_kind_badge(k)}</td><td class='r'>{v['calls']}</td><td class='r'>{_money(v['cost'])}</td></tr>"
        for k, v in stats["llm"].items()
    ) or "<tr><td colspan='3' class='muted small'>Нет активности за 30д.</td></tr>"

    profile_section = "<p class='muted small'>Нет профиля рождения.</p>"
    if profile:
        time_str = (
            "не указано"
            if profile.time_unknown
            else (profile.birth_time.strftime("%H:%M") if profile.birth_time else "—")
        )
        profile_section = f"""
<div class='dg'>
  <div class='di'><div class='dl'>Дата рождения</div><div class='dv'>{_fmt_date(profile.birth_date)}</div></div>
  <div class='di'><div class='dl'>Время</div><div class='dv'>{time_str}</div></div>
  <div class='di'><div class='dl'>Город</div><div class='dv'>{_esc(profile.city_name) or '—'}</div></div>
  <div class='di'><div class='dl'>Часовой пояс</div><div class='dv'><code>{_esc(profile.tz) or '—'}</code></div></div>
  <div class='di'><div class='dl'>Обновлён</div><div class='dv small muted'>{_fmt_dt(profile.updated_at)}</div></div>
</div>
"""

    body = f"""
<p style='margin-bottom:12px'><a href='/admin/users'>← Все юзеры</a></p>
{alert}
<div class='g2'>
  <div class='card'>
    <div class='card-head'><span class='card-title'>Пользователь #{user.id}</span>
      {'<span class="badge b-prem">💎 Премиум</span>' if _is_prem(user.premium_until, now) else '<span class="badge b-free">Free</span>'}
    </div>
    <div class='dg'>
      <div class='di'><div class='dl'>TG ID</div><div class='dv mono'>{user.tg_user_id}</div></div>
      <div class='di'><div class='dl'>Имя</div><div class='dv'>{_esc(user.display_name) or '—'}</div></div>
      <div class='di'><div class='dl'>Пол</div><div class='dv'>{gender_str}</div></div>
      <div class='di'><div class='dl'>Астротермины</div><div class='dv'>{'Вкл' if user.astro_terms_enabled else 'Выкл'}</div></div>
      <div class='di'><div class='dl'>Реф-код</div><div class='dv mono'>{user.referral_code or '—'}</div></div>
      <div class='di'><div class='dl'>Премиум до</div><div class='dv'>{_fmt_dt(user.premium_until)}</div></div>
      <div class='di'><div class='dl'>Бонус вопросов</div><div class='dv'>{user.bonus_questions or 0}</div></div>
      <div class='di'><div class='dl'>Регенов натала</div><div class='dv'>{natal_bonus}</div></div>
      <div class='di'><div class='dl'>Согласие</div><div class='dv small muted'>{_fmt_dt(user.legal_agreed_at)}</div></div>
      <div class='di'><div class='dl'>Создан</div><div class='dv small muted'>{_fmt_dt(user.created_at)}</div></div>
      <div class='di'><div class='dl'>В статистике</div><div class='dv'>{'<span class="badge b-warn">🚫 Исключён</span>' if user.excluded_from_stats else 'Учитывается'}</div></div>
    </div>
  </div>
  <div class='card'>
    <div class='card-head'><span class='card-title'>Активность (30д)</span></div>
    <div class='tbl-wrap'>
      <table>
        <thead><tr><th>Тип</th><th class='r'>Вызовов</th><th class='r'>Расход</th></tr></thead>
        <tbody>{llm_rows}</tbody>
      </table>
    </div>
    <div class='sep'></div>
    <div class='dg' style='margin-top:8px'>
      <div class='di'><div class='dl'>Вопросов всего</div><div class='dv'>{stats['questions']}</div></div>
      <div class='di'><div class='dl'>Избранных</div><div class='dv'>{stats['favorites']}</div></div>
    </div>
  </div>
</div>

<div class='card' style='margin-top:0'>
  <div class='card-head'><span class='card-title'>Профиль рождения</span></div>
  {profile_section}
</div>

<div class='card'>
  <div class='card-head'><span class='card-title'>Редактировать</span></div>
  <form method='post' action='/admin/users/{user.id}'>
    <div class='g3'>
      <div class='fg'>
        <label>Премиум до (дата/время МСК)</label>
        <input type='datetime-local' name='premium_until' value='{prem_val}'>
        <div class='note'>Оставь пустым — сбросит премиум. Или используй быстрые кнопки ниже.</div>
      </div>
      <div class='fg'>
        <label>Бонус вопросов</label>
        <input type='number' name='bonus_questions' value='{user.bonus_questions or 0}' min='0'>
      </div>
      <div class='fg'>
        <label>Регенов натала (−1 = безлимит)</label>
        <input type='number' name='natal_regens_bonus' value='{user.natal_regens_bonus or 0}' min='-1'>
        <div class='note'>−1 даёт бесконечные генерации для теста.</div>
      </div>
    </div>
    <div class='fa'>
      <button type='submit' class='btn btn-p'>Сохранить</button>
      <button type='submit' name='quick' value='prem30' class='btn btn-g'>+30 дней премиум</button>
      <button type='submit' name='quick' value='prem365' class='btn btn-g'>+365 дней</button>
      <button type='submit' name='quick' value='add_questions' class='btn btn-g'>+10 вопросов</button>
      <button type='submit' name='quick' value='add_natal' class='btn btn-g'>+1 натал</button>
      <button type='submit' name='quick' value='reset_prem' class='btn btn-danger btn-sm'>Сбросить премиум</button>
      <button type='submit' name='quick' value='unlimited_natal' class='btn btn-ghost btn-sm'>∞ Natal (тест)</button>
      {"<button type='submit' name='quick' value='toggle_excluded' class='btn btn-g btn-sm'>↩ Вернуть в статистику</button>" if user.excluded_from_stats else "<button type='submit' name='quick' value='toggle_excluded' class='btn btn-ghost btn-sm'>🚫 Исключить из статистики</button>"}
      <button type='submit' name='quick' value='reset_all' class='btn btn-danger' onclick="return confirm('Полный сброс аккаунта — премиум, бонусы и лимиты. Продолжить?')">⚠ Полный сброс</button>
    </div>
  </form>
</div>
"""
    return _layout(f"Юзер #{user.id}", body, active="users")


# ─── logs page ───────────────────────────────────────────────────────────────

async def _gather_logs(session: AsyncSession, kind: str) -> list:
    q = (
        select(
            LLMUsageLog.id,
            LLMUsageLog.user_id,
            LLMUsageLog.kind,
            LLMUsageLog.model,
            LLMUsageLog.input_tokens,
            LLMUsageLog.cached_tokens,
            LLMUsageLog.output_tokens,
            LLMUsageLog.created_at,
            User.tg_user_id,
            User.premium_until,
        )
        .join(User, User.id == LLMUsageLog.user_id)
        .order_by(desc(LLMUsageLog.created_at))
        .limit(200)
    )
    if kind:
        q = q.where(LLMUsageLog.kind == kind)
    return (await session.execute(q)).mappings().all()


def _render_logs(logs: list, kind: str, now: datetime) -> str:
    settings = get_settings()
    total_cost = sum(
        _cost_of(r["input_tokens"], r["cached_tokens"], r["output_tokens"], settings)
        for r in logs
    )

    rows = "".join(
        f"<tr>"
        f"<td class='small muted'>{r['id']}</td>"
        f"<td><a href='/admin/users/{r['user_id']}'>{r['user_id']}</a></td>"
        f"<td class='mono small'>{r['tg_user_id']}</td>"
        f"<td>{_kind_badge(r['kind'])}</td>"
        f"<td class='small muted'>{_trunc(r['model'], 30)}</td>"
        f"<td class='r small'>{r['input_tokens'] or 0}</td>"
        f"<td class='r small'>{r['cached_tokens'] or 0}</td>"
        f"<td class='r small'>{r['output_tokens'] or 0}</td>"
        f"<td class='r'><b>{_money(_cost_of(r['input_tokens'], r['cached_tokens'], r['output_tokens'], settings))}</b></td>"
        f"<td class='small muted'>{_msk(r['created_at']).strftime('%d.%m %H:%M') if r['created_at'] else '—'}</td>"
        f"</tr>"
        for r in logs
    ) or "<tr><td colspan='10' class='muted'>Нет данных.</td></tr>"

    filter_html = " ".join(
        f"<a href='/admin/logs?kind={k}' class='btn btn-sm {'btn-p' if kind == k else 'btn-ghost'}'>{k}</a>"
        for k in ("natal", "horoscope", "question")
    )

    body = f"""
<h1 class='ph'>Логи LLM</h1>
<p class='ph-sub'>Последние 200 записей · Сумма: <b>{_money(total_cost)}</b></p>
<div style='display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap'>
  {filter_html}
  <a href='/admin/logs' class='btn btn-sm btn-ghost'>Все типы</a>
</div>
<div class='card'>
  <div class='tbl-wrap'>
    <table>
      <thead><tr>
        <th>#</th><th>ID</th><th>TG ID</th><th>Тип</th><th>Модель</th>
        <th class='r'>in</th><th class='r'>cache</th><th class='r'>out</th>
        <th class='r'>≈$</th><th>Время</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""
    return _layout("Логи LLM", body, active="logs")


# ─── payments page ───────────────────────────────────────────────────────────

def _money_rub(v) -> str:
    return f"{float(v or 0):.0f} ₽"


def _money_cur(v, currency: str | None) -> str:
    """Amount with the right unit: ⭐ for Telegram Stars (XTR), ₽ otherwise."""
    if currency == "XTR":
        return f"{float(v or 0):.0f} ⭐"
    return f"{float(v or 0):.0f} ₽"


def _pay_method_badge(currency: str | None) -> str:
    if currency == "XTR":
        return '<span class="badge b-natal">⭐ Stars</span>'
    return '<span class="badge b-free">💳 Карта</span>'


_CANCEL_REASON_LABEL = {
    "user": "юзер отменил",
    "create_error": "ошибка создания",
    "yookassa": "отменён в YooKassa",
    "timeout": "не оплачен (таймаут)",
    "orphan": "сбой при создании",
}


def _pay_status_badge(status: str, cancel_reason: str | None = None) -> str:
    m = {
        "succeeded": ("b-ok", "✅ Оплачен"),
        "pending": ("b-warn", "⏳ Ожидает"),
        "canceled": ("b-free", "✖ Отменён"),
        "refunded": ("b-natal", "↩️ Возврат"),
    }
    cls, lbl = m.get(status, ("b-free", status or "—"))
    badge = f'<span class="badge {cls}">{lbl}</span>'
    if status == "canceled" and cancel_reason:
        reason = _CANCEL_REASON_LABEL.get(cancel_reason, cancel_reason)
        badge += f'<div class="small muted">{reason}</div>'
    return badge


async def _gather_payments(
    session: AsyncSession, status: str, page: int
) -> tuple[list, int, float, int]:
    base = (
        select(
            Payment.id,
            Payment.user_id,
            Payment.item_code,
            Payment.kind,
            Payment.amount,
            Payment.currency,
            Payment.status,
            Payment.cancel_reason,
            Payment.email,
            Payment.created_at,
            Payment.paid_at,
            User.tg_user_id,
        )
        .join(User, User.id == Payment.user_id)
        .order_by(desc(Payment.created_at))
    )
    cnt_q = select(func.count(Payment.id))
    if status:
        base = base.where(Payment.status == status)
        cnt_q = cnt_q.where(Payment.status == status)

    total = (await session.scalar(cnt_q)) or 0
    rows = (
        await session.execute(base.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE))
    ).mappings().all()

    # All-time revenue (succeeded only) + count
    revenue = (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "succeeded"
            )
        )
    ) or 0
    paid_count = (
        await session.scalar(
            select(func.count(Payment.id)).where(Payment.status == "succeeded")
        )
    ) or 0
    return list(rows), total, float(revenue), paid_count


def _render_payments(
    rows: list,
    total: int,
    revenue: float,
    paid_count: int,
    page: int,
    status: str,
    msg: str = "",
    err: str = "",
) -> str:
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    def page_url(p: int) -> str:
        s = f"&status={_esc(status)}" if status else ""
        return f"/admin/payments?page={p}{s}"

    pager_html = ""
    if total_pages > 1:
        pages = []
        for p in range(1, total_pages + 1):
            if p == page:
                pages.append(f"<span class='cur'>{p}</span>")
            elif abs(p - page) <= 2 or p in (1, total_pages):
                pages.append(f"<a href='{page_url(p)}'>{p}</a>")
            elif abs(p - page) == 3:
                pages.append("<span class='muted'>…</span>")
        pager_html = f"<div class='pager'>{''.join(pages)}</div>"

    def _action(r) -> str:
        if r["status"] != "succeeded":
            return ""
        amt = _money_cur(r["amount"], r["currency"])
        uid = r["user_id"]
        normal = (
            f"<form method='post' action='/admin/payments/{r['id']}/refund' "
            f"onsubmit=\"return confirm('Вернуть платёж {amt} юзеру {uid}? Начисления откатятся.')\" style='margin:0'>"
            f"<button type='submit' class='btn btn-danger btn-sm'>Вернуть</button>"
            f"</form>"
        )
        force = (
            f"<form method='post' action='/admin/payments/{r['id']}/refund' "
            f"onsubmit=\"return confirm('ПРИНУДИТЕЛЬНЫЙ возврат {amt} юзеру {uid} в обход лимитов?')\" style='margin:0'>"
            f"<input type='hidden' name='force' value='1'>"
            f"<button type='submit' class='btn btn-ghost btn-sm' title='В обход окна 14 дней и порога расхода'>Принуд.</button>"
            f"</form>"
        )
        return f"<div style='display:flex;gap:5px'>{normal}{force}</div>"

    body_rows = "".join(
        f"<tr>"
        f"<td class='small muted'>{r['id']}</td>"
        f"<td><a href='/admin/users/{r['user_id']}'>{r['user_id']}</a></td>"
        f"<td class='mono small'>{r['tg_user_id']}</td>"
        f"<td>{_esc(r['item_code'])} {_pay_method_badge(r['currency'])}</td>"
        f"<td class='r'><b>{_money_cur(r['amount'], r['currency'])}</b></td>"
        f"<td>{_pay_status_badge(r['status'], r['cancel_reason'])}</td>"
        f"<td class='small muted'>{_trunc(r['email'], 28)}</td>"
        f"<td class='small muted'>{_msk(r['created_at']).strftime('%d.%m %H:%M') if r['created_at'] else '—'}</td>"
        f"<td class='small muted'>{_msk(r['paid_at']).strftime('%d.%m %H:%M') if r['paid_at'] else '—'}</td>"
        f"<td>{_action(r)}</td>"
        f"</tr>"
        for r in rows
    ) or "<tr><td colspan='10' class='muted'>Нет платежей.</td></tr>"

    filter_html = " ".join(
        f"<a href='/admin/payments?status={k}' class='btn btn-sm {'btn-p' if status == k else 'btn-ghost'}'>{lbl}</a>"
        for k, lbl in (
            ("succeeded", "Оплаченные"),
            ("pending", "Ожидают"),
            ("canceled", "Отменённые"),
            ("refunded", "Возвраты"),
        )
    )

    alert = ""
    if msg:
        alert = f"<div class='alert alert-ok'>✓ {_esc(msg)}</div>"
    elif err:
        alert = f"<div class='alert alert-err'>✗ {_esc(err)}</div>"

    body = f"""
<h1 class='ph'>Платежи</h1>
<p class='ph-sub'>Выручка (оплачено): <b>{_money_rub(revenue)}</b> · успешных платежей: <b>{paid_count}</b></p>
{alert}
<div style='display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap'>
  {filter_html}
  <a href='/admin/payments' class='btn btn-sm btn-ghost'>Все</a>
</div>
<div class='card'>
  <div class='tbl-wrap'>
    <table>
      <thead><tr>
        <th>#</th><th>Юзер</th><th>TG ID</th><th>Товар</th>
        <th class='r'>Сумма</th><th>Статус</th><th>Email</th><th>Создан</th><th>Оплачен</th><th></th>
      </tr></thead>
      <tbody>{body_rows}</tbody>
    </table>
  </div>
</div>
{pager_html}
"""
    return _layout("Платежи", body, active="payments")


# ─── support page ──────────────────────────────────────────────────────────────

async def _gather_support(session: AsyncSession, status: str) -> list:
    q = (
        select(
            SupportTicket.id,
            SupportTicket.user_id,
            SupportTicket.kind,
            SupportTicket.message,
            SupportTicket.status,
            SupportTicket.answer,
            SupportTicket.payment_id,
            SupportTicket.created_at,
            SupportTicket.answered_at,
            User.tg_user_id,
            User.display_name,
        )
        .join(User, User.id == SupportTicket.user_id)
        .order_by(desc(SupportTicket.status == "open"), desc(SupportTicket.created_at))
        .limit(100)
    )
    if status:
        q = q.where(SupportTicket.status == status)
    return (await session.execute(q)).mappings().all()


def _ticket_status_badge(status: str) -> str:
    m = {"open": ("b-warn", "🕓 Открыт"), "answered": ("b-ok", "✅ Отвечен")}
    cls, lbl = m.get(status, ("b-free", status or "—"))
    return f'<span class="badge {cls}">{lbl}</span>'


def _render_support(rows: list, status: str, msg: str = "", err: str = "") -> str:
    open_count = sum(1 for r in rows if r["status"] == "open")

    filter_html = " ".join(
        f"<a href='/admin/support?status={k}' class='btn btn-sm {'btn-p' if status == k else 'btn-ghost'}'>{lbl}</a>"
        for k, lbl in (("open", "Открытые"), ("answered", "Отвеченные"))
    )

    cards = []
    for r in rows:
        kind = "↩️ Возврат" if r["kind"] == "refund" else "💬 Вопрос"
        name = _esc(r["display_name"]) if r["display_name"] else "—"
        created = _msk(r["created_at"]).strftime("%d.%m.%Y %H:%M") if r["created_at"] else "—"
        pay_link = (
            f" · <a href='/admin/payments'>платёж #{r['payment_id']}</a>"
            if r["payment_id"]
            else ""
        )
        answer_block = ""
        if r["answer"]:
            ad = _msk(r["answered_at"]).strftime("%d.%m.%Y %H:%M") if r["answered_at"] else ""
            answer_block = (
                f"<div class='sep'></div><div class='small'><b>Ответ</b> "
                f"<span class='muted'>{ad}</span><br>{_esc(r['answer'])}</div>"
            )
        if r["status"] == "open":
            answer_block = (
                f"<form method='post' action='/admin/support/{r['id']}/reply' style='margin-top:10px'>"
                f"<textarea name='answer' rows='2' required "
                f"style='width:100%;padding:8px 11px;border:1px solid #e2e8f0;border-radius:6px;font:inherit'"
                f" placeholder='Ответ пользователю…'></textarea>"
                f"<button type='submit' class='btn btn-p btn-sm' style='margin-top:6px'>Ответить</button>"
                f"</form>"
            )
        cards.append(
            f"<div class='card'>"
            f"<div class='card-head'><span class='card-title'>#{r['id']} · {kind} · "
            f"<a href='/admin/users/{r['user_id']}'>{name}</a> "
            f"<span class='mono small muted'>{r['tg_user_id']}</span>{pay_link}</span>"
            f"{_ticket_status_badge(r['status'])}</div>"
            f"<div class='small muted'>{created}</div>"
            f"<div style='margin-top:6px'>{_esc(r['message'])}</div>"
            f"{answer_block}"
            f"</div>"
        )
    body_cards = "".join(cards) or "<p class='muted'>Обращений нет.</p>"

    alert = ""
    if msg:
        alert = f"<div class='alert alert-ok'>✓ {_esc(msg)}</div>"
    elif err:
        alert = f"<div class='alert alert-err'>✗ {_esc(err)}</div>"

    body = f"""
<h1 class='ph'>Поддержка</h1>
<p class='ph-sub'>Открытых обращений: <b>{open_count}</b></p>
{alert}
<div style='display:flex;gap:8px;margin-bottom:16px;align-items:center;flex-wrap:wrap'>
  {filter_html}
  <a href='/admin/support' class='btn btn-sm btn-ghost'>Все</a>
</div>
{body_cards}
"""
    return _layout("Поддержка", body, active="support")


# ─── exclusions page ─────────────────────────────────────────────────────────

async def _gather_exclusions(session: AsyncSession) -> list:
    return list(
        (
            await session.execute(
                select(User)
                .where(User.excluded_from_stats.is_(True))
                .order_by(User.tg_user_id)
            )
        ).scalars().all()
    )


def _render_exclusions(users: list, msg: str = "", err: str = "") -> str:
    alert = ""
    if msg:
        alert = f"<div class='alert alert-ok'>✓ {_esc(msg)}</div>"
    elif err:
        alert = f"<div class='alert alert-err'>✗ {_esc(err)}</div>"

    current_ids = "\n".join(str(u.tg_user_id) for u in users)
    rows = "".join(
        f"<tr>"
        f"<td><a href='/admin/users/{u.id}'>{u.id}</a></td>"
        f"<td class='mono small'>{u.tg_user_id}</td>"
        f"<td>{_esc(u.display_name) or '<span class=\"muted\">—</span>'}</td>"
        f"</tr>"
        for u in users
    ) or "<tr><td colspan='3' class='muted'>Никто не исключён.</td></tr>"

    body = f"""
<h1 class='ph'>Исключения из статистики</h1>
<p class='ph-sub'>Эти пользователи (тест- и служебные аккаунты) не учитываются в Сводке — ни в метриках, ни в деньгах, ни в активности.</p>
{alert}
<div class='card'>
  <form method='post' action='/admin/exclusions'>
    <div class='fg'>
      <label>Telegram ID — по одному в строке (или через запятую/пробел)</label>
      <textarea name='tg_ids' rows='8'
        style='width:100%;padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;font:inherit;font-family:monospace'
        placeholder='123456789&#10;987654321'>{_esc(current_ids)}</textarea>
      <div class='note'>Это полный список. Сохранение перезапишет исключения целиком — пустое поле снимет все. TG ID, которых нет в базе, будут пропущены.</div>
    </div>
    <button type='submit' class='btn btn-p'>Сохранить</button>
  </form>
</div>
<div class='card'>
  <div class='card-head'><span class='card-title'>Сейчас исключены ({len(users)})</span></div>
  <div class='tbl-wrap'>
    <table>
      <thead><tr><th>ID</th><th>TG ID</th><th>Имя</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</div>
"""
    return _layout("Исключения", body, active="exclusions")


# ─── login page ──────────────────────────────────────────────────────────────

_LOGIN_PAGE = """<!doctype html><html lang='ru'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Astra Admin · Вход</title>
<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap' rel='stylesheet'>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Inter,-apple-system,sans-serif;background:#1e1b4b;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.box{{background:#fff;border-radius:14px;padding:36px 40px;width:100%;max-width:360px;box-shadow:0 20px 60px rgba(0,0,0,.35)}}
.logo{{font-size:34px;text-align:center;margin-bottom:8px}}
.title{{font-size:20px;font-weight:700;text-align:center;color:#1e293b;margin-bottom:4px}}
.sub{{font-size:13px;color:#94a3b8;text-align:center;margin-bottom:26px}}
.fg{{margin-bottom:15px}}
label{{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:5px}}
input{{width:100%;padding:10px 12px;border:1px solid #e2e8f0;border-radius:8px;font-size:14px;outline:none;font-family:inherit}}
input:focus{{border-color:#7c3aed;box-shadow:0 0 0 3px #ede9fe}}
.btn{{width:100%;padding:11px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;margin-top:8px;font-family:inherit}}
.btn:hover{{background:#6d28d9}}
.err{{background:#fee2e2;color:#991b1b;border-radius:8px;padding:10px 14px;font-size:13px;margin-bottom:16px;border:1px solid #fca5a5}}
</style></head><body>
<div class='box'>
  <div class='logo'>✨</div>
  <div class='title'>Astra Admin</div>
  <div class='sub'>Панель управления</div>
  {err}
  <form method='post' action='/admin/login'>
    <div class='fg'><label>Логин</label><input name='username' type='text' autocomplete='username' autofocus></div>
    <div class='fg'><label>Пароль</label><input name='password' type='password' autocomplete='current-password'></div>
    <button class='btn' type='submit'>Войти →</button>
  </form>
</div>
</body></html>"""


# ─── routes ──────────────────────────────────────────────────────────────────

@router.get("/admin/login", include_in_schema=False)
async def login_page(request: Request, error: str = ""):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/admin/stats", status_code=302)
    err_html = "<div class='err'>Неверный логин или пароль.</div>" if error else ""
    return HTMLResponse(_LOGIN_PAGE.format(err=err_html))


@router.post("/admin/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    ip = _login_client_ip(request)
    if await _login_blocked(ip):
        return RedirectResponse(url="/admin/login?error=1", status_code=303)

    settings = get_settings()
    ok = (
        bool(settings.admin_password)
        and _secrets.compare_digest(username.strip(), settings.admin_user)
        and _secrets.compare_digest(password, settings.admin_password)
    )
    if ok:
        await _clear_login_fails(ip)
        request.session["authenticated"] = True
        return RedirectResponse(url="/admin/stats", status_code=303)
    await _record_login_fail(ip)
    return RedirectResponse(url="/admin/login?error=1", status_code=303)


@router.get("/admin/logout", include_in_schema=False)
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


@router.get("/admin/", include_in_schema=False)
async def admin_root(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    return RedirectResponse(url="/admin/stats", status_code=302)


@router.get("/admin/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    data = await _gather_stats(session)
    return HTMLResponse(_render_stats(data))


@router.get("/admin/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    search: str = "",
    page: int = 1,
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    page = max(1, page)
    users, total = await _gather_users(session, search.strip(), page)
    now = datetime.now(UTC)
    return HTMLResponse(_render_users(users, total, page, search, now))


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    user_id: int,
    msg: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    user, profile, stats = await _gather_user_detail(session, user_id)
    if user is None:
        return HTMLResponse(_layout("404", "<p>Пользователь не найден.</p>"), status_code=404)
    now = datetime.now(UTC)
    return HTMLResponse(_render_user_detail(user, profile, stats, now, msg=msg))


@router.post("/admin/users/{user_id}", response_class=HTMLResponse)
async def user_edit(
    request: Request,
    user_id: int,
    quick: str = Form(default=""),
    premium_until: str = Form(default=""),
    bonus_questions: int = Form(default=0),
    natal_regens_bonus: int = Form(default=0),
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)

    user = await session.get(User, user_id)
    if user is None:
        return RedirectResponse(url="/admin/users", status_code=303)

    now = datetime.now(UTC)
    msg = ""

    if quick == "prem30":
        base = user.premium_until if user.premium_until and user.premium_until > now else now
        user.premium_until = base + timedelta(days=30)
        msg = "Добавлено 30 дней премиум."
    elif quick == "prem365":
        base = user.premium_until if user.premium_until and user.premium_until > now else now
        user.premium_until = base + timedelta(days=365)
        msg = "Добавлено 365 дней премиум."
    elif quick == "reset_prem":
        user.premium_until = None
        msg = "Премиум сброшен."
    elif quick == "unlimited_natal":
        user.natal_regens_bonus = -1
        msg = "Натал: безлимит (−1)."
    elif quick == "add_questions":
        user.bonus_questions = (user.bonus_questions or 0) + 10
        msg = "Добавлено 10 вопросов."
    elif quick == "add_natal":
        user.natal_regens_bonus = (user.natal_regens_bonus or 0) + 1
        msg = "Добавлен 1 пересчёт натала."
    elif quick == "toggle_excluded":
        user.excluded_from_stats = not user.excluded_from_stats
        msg = "Исключён из статистики." if user.excluded_from_stats else "Возвращён в статистику."
    elif quick == "reset_all":
        user.premium_until = None
        user.bonus_questions = 0
        user.natal_regens_bonus = 0
        user.questions_reset_at = now
        user.free_questions_balance = 2
        user.premium_questions_used = 0
        msg = "Аккаунт полностью сброшен."
    else:
        # Manual form submission
        if premium_until.strip():
            try:
                dt = datetime.fromisoformat(premium_until.strip())
                # The datetime-local input is Moscow time → store as UTC.
                user.premium_until = (
                    dt.replace(tzinfo=_MSK).astimezone(UTC)
                    if dt.tzinfo is None
                    else dt.astimezone(UTC)
                )
            except ValueError:
                pass
        else:
            user.premium_until = None
        user.bonus_questions = max(0, bonus_questions)
        user.natal_regens_bonus = natal_regens_bonus  # allow -1
        msg = "Сохранено."

    await session.commit()
    return RedirectResponse(
        url=f"/admin/users/{user_id}?msg={msg}", status_code=303
    )


@router.get("/admin/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    kind: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    now = datetime.now(UTC)
    logs = await _gather_logs(session, kind.strip())
    return HTMLResponse(_render_logs(logs, kind, now))


@router.get("/admin/payments", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    status: str = "",
    page: int = 1,
    msg: str = "",
    err: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    page = max(1, page)
    rows, total, revenue, paid_count = await _gather_payments(session, status.strip(), page)
    return HTMLResponse(
        _render_payments(rows, total, revenue, paid_count, page, status.strip(), msg=msg, err=err)
    )


@router.post("/admin/payments/{payment_id}/refund", response_class=HTMLResponse)
async def payment_refund(
    request: Request,
    payment_id: int,
    force: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)

    payment = await session.get(Payment, payment_id)
    if payment is None:
        return RedirectResponse(url="/admin/payments?err=Платёж не найден", status_code=303)
    if payment.status != "succeeded":
        return RedirectResponse(
            url="/admin/payments?err=Вернуть можно только оплаченный платёж", status_code=303
        )

    # Policy gate (skipped when admin forces)
    if not force:
        allowed, reason = await payment_service.refund_eligibility(session, payment)
        if not allowed:
            return RedirectResponse(
                url=f"/admin/payments?err=Возврат не положен: {reason}. Можно «Принудительно».",
                status_code=303,
            )

    bot = getattr(request.app.state, "bot", None)

    if payment.provider == "telegram_stars":
        if not payment.telegram_charge_id:
            return RedirectResponse(
                url="/admin/payments?err=Нет id платежа Telegram Stars", status_code=303
            )
        user = await session.get(User, payment.user_id)
        if user is None or bot is None:
            return RedirectResponse(
                url="/admin/payments?err=Не удалось вернуть звёзды (нет бота/юзера)",
                status_code=303,
            )
        try:
            await bot.refund_star_payment(user.tg_user_id, payment.telegram_charge_id)
        except Exception:
            return RedirectResponse(
                url="/admin/payments?err=Telegram отклонил возврат звёзд", status_code=303
            )
    else:
        if not payment.yookassa_payment_id:
            return RedirectResponse(
                url="/admin/payments?err=Нет id платежа в YooKassa", status_code=303
            )
        try:
            await yookassa.create_refund(payment.yookassa_payment_id, float(payment.amount))
        except Exception:
            return RedirectResponse(
                url="/admin/payments?err=YooKassa отклонила возврат", status_code=303
            )

    await payment_service.refund_payment(session, payment, bot)
    suffix = " (принудительно)" if force else ""
    return RedirectResponse(url=f"/admin/payments?msg=Возврат выполнен{suffix}", status_code=303)


@router.get("/admin/support", response_class=HTMLResponse)
async def support_page(
    request: Request,
    status: str = "",
    msg: str = "",
    err: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    rows = await _gather_support(session, status.strip())
    return HTMLResponse(_render_support(rows, status.strip(), msg=msg, err=err))


@router.post("/admin/support/{ticket_id}/reply", response_class=HTMLResponse)
async def support_reply(
    request: Request,
    ticket_id: int,
    answer: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)

    ticket = await session.get(SupportTicket, ticket_id)
    if ticket is None:
        return RedirectResponse(url="/admin/support?err=Обращение не найдено", status_code=303)
    ans = answer.strip()
    if not ans:
        return RedirectResponse(url="/admin/support?err=Пустой ответ", status_code=303)

    ticket.answer = ans[:4000]
    ticket.status = "answered"
    ticket.answered_at = datetime.now(UTC)
    await session.commit()

    user = await session.get(User, ticket.user_id)
    bot = getattr(request.app.state, "bot", None)
    if user is not None and bot is not None:
        excerpt = html.escape(ticket.message[:200])
        try:
            await bot.send_message(
                user.tg_user_id,
                "✅ Ваше обращение рассмотрено:\n\n"
                f"<i>{excerpt}</i>\n\n"
                f"<b>Ответ:</b> {html.escape(ans)}",
            )
        except Exception:
            pass
    return RedirectResponse(url="/admin/support?msg=Ответ отправлен", status_code=303)


@router.get("/admin/exclusions", response_class=HTMLResponse)
async def exclusions_page(
    request: Request,
    msg: str = "",
    err: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    users = await _gather_exclusions(session)
    return HTMLResponse(_render_exclusions(users, msg=msg, err=err))


@router.post("/admin/exclusions", response_class=HTMLResponse)
async def exclusions_save(
    request: Request,
    tg_ids: str = Form(default=""),
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)

    wanted = {int(t) for t in tg_ids.replace(",", " ").split() if t.strip().isdigit()}

    # The textarea is the full list → clear all flags, then set the listed ones.
    await session.execute(
        update(User)
        .where(User.excluded_from_stats.is_(True))
        .values(excluded_from_stats=False)
    )
    matched = 0
    if wanted:
        result = await session.execute(
            update(User)
            .where(User.tg_user_id.in_(wanted))
            .values(excluded_from_stats=True)
        )
        matched = result.rowcount or 0
    await session.commit()

    msg = f"Сохранено. Исключено: {matched}."
    not_found = len(wanted) - matched
    if not_found > 0:
        msg += f" Не найдено в базе: {not_found}."
    return RedirectResponse(url=f"/admin/exclusions?msg={msg}", status_code=303)
