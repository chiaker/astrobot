from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astrobot.config import get_settings
from astrobot.db.models import (
    Favorite,
    LLMUsageLog,
    QuestionLog,
    Response,
    User,
)
from astrobot.db.session import get_session

router = APIRouter(tags=["stats"])


def _cost_of(row: dict, settings) -> float:
    return (
        max(0, (row["input_tokens"] or 0) - (row["cached_tokens"] or 0))
        / 1_000_000
        * settings.llm_price_input_usd_per_m
        + (row["cached_tokens"] or 0)
        / 1_000_000
        * settings.llm_price_cache_hit_usd_per_m
        + (row["output_tokens"] or 0)
        / 1_000_000
        * settings.llm_price_output_usd_per_m
    )


def _money(value: float) -> str:
    if value >= 100:
        return f"${value:.0f}"
    if value >= 1:
        return f"${value:.2f}"
    return f"${value:.4f}"


def _is_premium(premium_until, now) -> bool:
    return premium_until is not None and premium_until > now


async def _gather(session: AsyncSession) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    day = now - timedelta(days=1)
    week = now - timedelta(days=7)
    month = now - timedelta(days=30)

    total_users = (await session.scalar(select(func.count(User.id)))) or 0
    premium_users = (
        await session.scalar(
            select(func.count(User.id)).where(
                User.premium_until.isnot(None), User.premium_until > now
            )
        )
    ) or 0
    free_users = total_users - premium_users

    new_users_7d = (
        await session.scalar(
            select(func.count(User.id)).where(User.created_at >= week)
        )
    ) or 0

    referred_users = (
        await session.scalar(
            select(func.count(User.id)).where(User.referred_by_user_id.isnot(None))
        )
    ) or 0

    # LLM usage with tier breakdown
    usage_stmt = (
        select(
            LLMUsageLog.user_id,
            LLMUsageLog.kind,
            LLMUsageLog.model,
            LLMUsageLog.input_tokens,
            LLMUsageLog.cached_tokens,
            LLMUsageLog.output_tokens,
            LLMUsageLog.created_at,
            User.premium_until,
            User.tg_user_id,
        )
        .join(User, User.id == LLMUsageLog.user_id)
        .where(LLMUsageLog.created_at >= month)
    )
    usage_rows = (await session.execute(usage_stmt)).mappings().all()

    windows = {"day": day, "week": week, "month": month}
    cost_by_window_tier: dict[str, dict[str, float]] = {
        w: {"free": 0.0, "premium": 0.0} for w in windows
    }
    count_by_window_tier: dict[str, dict[str, int]] = {
        w: {"free": 0, "premium": 0} for w in windows
    }
    kind_counts_7d: dict[str, int] = {}
    by_user_30d: dict[int, dict] = {}

    for row in usage_rows:
        is_prem = _is_premium(row["premium_until"], now)
        tier = "premium" if is_prem else "free"
        cost = _cost_of(dict(row), settings)
        for name, since in windows.items():
            if row["created_at"] >= since:
                cost_by_window_tier[name][tier] += cost
                count_by_window_tier[name][tier] += 1
        if row["created_at"] >= week:
            kind_counts_7d[row["kind"]] = kind_counts_7d.get(row["kind"], 0) + 1

        u = by_user_30d.setdefault(
            row["user_id"],
            {
                "user_id": row["user_id"],
                "tg_user_id": row["tg_user_id"],
                "tier": tier,
                "cost": 0.0,
                "calls": 0,
            },
        )
        u["cost"] += cost
        u["calls"] += 1

    top_users = sorted(by_user_30d.values(), key=lambda r: r["cost"], reverse=True)[:10]

    questions_7d = (
        await session.scalar(
            select(func.count(QuestionLog.id)).where(QuestionLog.created_at >= week)
        )
    ) or 0
    responses_7d = (
        await session.scalar(
            select(func.count(Response.id)).where(Response.created_at >= week)
        )
    ) or 0
    favorites_total = (await session.scalar(select(func.count(Favorite.id)))) or 0

    return {
        "now": now,
        "total_users": total_users,
        "premium_users": premium_users,
        "free_users": free_users,
        "new_users_7d": new_users_7d,
        "referred_users": referred_users,
        "cost_by_window_tier": cost_by_window_tier,
        "count_by_window_tier": count_by_window_tier,
        "kind_counts_7d": dict(
            sorted(kind_counts_7d.items(), key=lambda kv: -kv[1])
        ),
        "top_users": top_users,
        "questions_7d": questions_7d,
        "responses_7d": responses_7d,
        "favorites_total": favorites_total,
    }


_CSS = """
* { box-sizing: border-box; }
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f8fafc;
    color: #1e293b;
    margin: 0;
    padding: 24px;
    line-height: 1.5;
}
.wrap { max-width: 1200px; margin: 0 auto; }
.header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #e2e8f0;
}
.brand { font-size: 20px; font-weight: 700; color: #5b21b6; }
.nav a {
    color: #7c3aed; text-decoration: none; font-weight: 600;
    margin-left: 16px; font-size: 14px;
}
.nav a:hover { color: #4c1d95; }
h2 {
    margin: 32px 0 12px;
    font-size: 16px; font-weight: 700;
    color: #475569;
    text-transform: uppercase; letter-spacing: 0.5px;
}
.kpis {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
}
.kpi {
    background: white;
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    border: 1px solid #e2e8f0;
}
.kpi-label {
    font-size: 12px; color: #64748b;
    text-transform: uppercase; letter-spacing: 0.5px;
    font-weight: 600;
}
.kpi-value {
    font-size: 28px; font-weight: 700; color: #1e293b;
    margin-top: 4px;
}
.kpi-sub { font-size: 12px; color: #64748b; margin-top: 4px; }
.kpi.premium .kpi-value { color: #7c3aed; }
.kpi.free .kpi-value { color: #475569; }
.card {
    background: white;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    border: 1px solid #e2e8f0;
    margin-bottom: 16px;
}
.card h3 {
    margin: 0 0 14px;
    font-size: 15px; font-weight: 700;
}
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th {
    text-align: left; padding: 10px 12px;
    color: #64748b; font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px;
    border-bottom: 1px solid #e2e8f0;
}
td {
    padding: 10px 12px;
    border-bottom: 1px solid #f1f5f9;
}
tr:last-child td { border-bottom: none; }
tr:hover td { background: #faf7ff; }
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    color: white;
}
.badge-premium { background: #7c3aed; }
.badge-free { background: #94a3b8; }
.cost-strong { font-weight: 700; color: #5b21b6; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 700px) { .grid-2 { grid-template-columns: 1fr; } }
.muted { color: #94a3b8; }
.right { text-align: right; }
"""


def _render(data: dict) -> str:
    settings = get_settings()
    cw = data["cost_by_window_tier"]
    cn = data["count_by_window_tier"]

    def total(w):
        return cw[w]["free"] + cw[w]["premium"]

    rows_html = []
    for u in data["top_users"]:
        tier_badge = (
            '<span class="badge badge-premium">Premium</span>'
            if u["tier"] == "premium"
            else '<span class="badge badge-free">Free</span>'
        )
        rows_html.append(
            f"<tr><td>{u['user_id']}</td><td>{u['tg_user_id']}</td>"
            f"<td>{tier_badge}</td><td>{u['calls']}</td>"
            f"<td class='right cost-strong'>{_money(u['cost'])}</td></tr>"
        )
    top_users_table = "".join(rows_html) or (
        "<tr><td colspan='5' class='muted'>Пока никто не делал запросов.</td></tr>"
    )

    kind_rows = "".join(
        f"<tr><td>{k}</td><td class='right'>{v}</td></tr>"
        for k, v in data["kind_counts_7d"].items()
    ) or "<tr><td colspan='2' class='muted'>Тишина.</td></tr>"

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Astra · Сводка</title>
<link rel="icon" href="/admin-static/logo.svg">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="brand">✨ Astra · Сводка</div>
    <div class="nav">
      <a href="/admin/stats">Сводка</a>
      <a href="/admin/">Таблицы</a>
      <a href="/admin/logout">Logout</a>
    </div>
  </div>

  <h2>Юзеры</h2>
  <div class="kpis">
    <div class="kpi"><div class="kpi-label">Всего</div>
      <div class="kpi-value">{data['total_users']}</div>
      <div class="kpi-sub">+{data['new_users_7d']} за 7 дней</div></div>
    <div class="kpi premium"><div class="kpi-label">Премиум</div>
      <div class="kpi-value">{data['premium_users']}</div>
      <div class="kpi-sub">из {data['total_users']}</div></div>
    <div class="kpi free"><div class="kpi-label">Free</div>
      <div class="kpi-value">{data['free_users']}</div>
      <div class="kpi-sub">{(data['free_users']/data['total_users']*100):.0f}% если есть</div></div>
    <div class="kpi"><div class="kpi-label">По рефералам</div>
      <div class="kpi-value">{data['referred_users']}</div>
      <div class="kpi-sub">всего привлечено</div></div>
  </div>

  <h2>Расходы на LLM по тарифу</h2>
  <div class="kpis">
    <div class="kpi"><div class="kpi-label">Сегодня</div>
      <div class="kpi-value">{_money(total('day'))}</div>
      <div class="kpi-sub">free {_money(cw['day']['free'])} · prem {_money(cw['day']['premium'])}</div></div>
    <div class="kpi"><div class="kpi-label">7 дней</div>
      <div class="kpi-value">{_money(total('week'))}</div>
      <div class="kpi-sub">free {_money(cw['week']['free'])} · prem {_money(cw['week']['premium'])}</div></div>
    <div class="kpi"><div class="kpi-label">30 дней</div>
      <div class="kpi-value">{_money(total('month'))}</div>
      <div class="kpi-sub">free {_money(cw['month']['free'])} · prem {_money(cw['month']['premium'])}</div></div>
  </div>

  <h2>Объём запросов (7д)</h2>
  <div class="kpis">
    <div class="kpi free"><div class="kpi-label">Free</div>
      <div class="kpi-value">{cn['week']['free']}</div></div>
    <div class="kpi premium"><div class="kpi-label">Premium</div>
      <div class="kpi-value">{cn['week']['premium']}</div></div>
    <div class="kpi"><div class="kpi-label">Вопросы</div>
      <div class="kpi-value">{data['questions_7d']}</div></div>
    <div class="kpi"><div class="kpi-label">Ответы Астры</div>
      <div class="kpi-value">{data['responses_7d']}</div></div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h3>Топ-10 юзеров по расходам (30д)</h3>
      <table>
        <thead><tr><th>ID</th><th>TG ID</th><th>Тариф</th><th>Вызовов</th><th class="right">Стоимость</th></tr></thead>
        <tbody>{top_users_table}</tbody>
      </table>
    </div>

    <div class="card">
      <h3>Запросы по типу (7д)</h3>
      <table>
        <thead><tr><th>Тип</th><th class="right">Кол-во</th></tr></thead>
        <tbody>{kind_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h3>Прочее</h3>
    <p style="margin: 0; color: #64748b; font-size: 14px;">
      ⭐ Избранного: <b>{data['favorites_total']}</b> &nbsp;·&nbsp;
      Цены LLM: in <code>${settings.llm_price_input_usd_per_m}</code>,
      out <code>${settings.llm_price_output_usd_per_m}</code>,
      cache hit <code>${settings.llm_price_cache_hit_usd_per_m}</code> за 1M
    </p>
  </div>

</div>
</body>
</html>"""


@router.get("/admin/stats", response_class=HTMLResponse)
async def stats_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/admin/login", status_code=303)
    data = await _gather(session)
    return HTMLResponse(_render(data))
