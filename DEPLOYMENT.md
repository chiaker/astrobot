# Деплой Astrobot

CI/CD: пуш в `main` → GitHub Actions собирает образ → `ghcr.io` → SSH-деплой на VPS.

## Один раз: настройка

### 1. GitHub repo

1. Создай private repo `astrobot` на GitHub.
2. Локально:
   ```powershell
   cd c:\p\projects\astrobot
   git init
   git add .
   git commit -m "initial"
   git branch -M main
   git remote add origin https://github.com/<user>/astrobot.git
   git push -u origin main
   ```
3. **Сделай GHCR-пакет публичным** после первого пуша
   (Settings → Packages → astrobot → Change visibility → Public).
   Иначе нужно логиниться `docker login ghcr.io` на сервере.

### 2. VPS (Ubuntu 22.04 / Debian 12)

На сервере под `root` или с `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/<user>/astrobot/main/scripts/bootstrap-server.sh \
  | bash -s -- https://github.com/<user>/astrobot.git
```

Скрипт:
- ставит Docker + git + ufw,
- клонирует репо в `/opt/astrobot`,
- открывает порты 22/80/443,
- копирует `.env.example` → `.env`.

После скрипта:
```bash
nano /opt/astrobot/.env   # вписать все секреты
cd /opt/astrobot
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Проверь: `curl https://<домен>/health` → `{db:ok, redis:ok}`.

### 3. GitHub Secrets

Repo → Settings → Secrets and variables → Actions → New repository secret. Создай:

| Secret | Значение |
|---|---|
| `SSH_HOST` | IP или хостнейм VPS |
| `SSH_USER` | `root` (или sudo-юзер) |
| `SSH_PRIVATE_KEY` | приватный SSH-ключ (`cat ~/.ssh/id_ed25519`) |
| `SSH_PORT` | (опционально) если порт SSH не 22 |

Публичный ключ ключа должен быть в `~/.ssh/authorized_keys` на VPS.

### 4. (Опционально) GitHub Environment `production`

Settings → Environments → New → `production`. Можно добавить required reviewers — деплой будет ждать одобрения.

## Каждый деплой

```powershell
git add .
git commit -m "..."
git push
```

Дальше GitHub Actions сам:
1. Соберёт образ `ghcr.io/<user>/astrobot:<sha>` + `:latest`.
2. Зайдёт по SSH в `/opt/astrobot`, обновит compose-файлы из git, скачает образ, пересоздаст контейнеры с новым `IMAGE_TAG`.
3. Удалит висящие старые образы (`docker image prune --filter "until=72h"`).

Прогресс — в Actions tab.

## Откат

В Actions запусти `deploy` вручную (workflow_dispatch) или ssh:

```bash
cd /opt/astrobot
export IMAGE_NAME=ghcr.io/<user>/astrobot
export IMAGE_TAG=<previous_short_sha>   # видно в Actions history
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Домены

Один домен `astra-ezoterika.ru`, A-записи всех имён → IP сервера:

| Имя | Что отдаёт | Через какие переменные |
|---|---|---|
| `@` | лендинг (статика из `landing/`) | `LANDING_DOMAIN` |
| `tg` | Telegram-бот: вебхук, `/admin`, `/health` | `BOT_DOMAIN`, `WEBHOOK_BASE_URL` в `.env` |
| `max` | MAX-бот: вебхук, `/admin`, `/health` | `MAX_DOMAIN`, `WEBHOOK_BASE_URL` в `.env.max` |
| `grafana` | Grafana (опционально) | `GRAFANA_DOMAIN` |

Caddy берёт имена из env — Caddyfile при смене домена не трогаем.

### Переезд на новый домен

1. Добавить A-записи (`@`, `tg`, `max`) → IP сервера, дождаться резолва:
   `nslookup tg.astra-ezoterika.ru 8.8.8.8`
2. В `/opt/astrobot/.env`:
   ```
   BOT_DOMAIN=tg.astra-ezoterika.ru
   MAX_DOMAIN=max.astra-ezoterika.ru
   LANDING_DOMAIN=astra-ezoterika.ru
   WEBHOOK_BASE_URL=https://tg.astra-ezoterika.ru
   ```
   В `/opt/astrobot/.env.max`: `WEBHOOK_BASE_URL=https://max.astra-ezoterika.ru`
3. Применить:
   ```bash
   cd /opt/astrobot
   export COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml"
   docker compose up -d caddy          # подхватит новые домены, выпустит сертификаты
   docker compose up -d app app-max    # перечитают WEBHOOK_BASE_URL
   docker compose exec -T app python -m astrobot.main --set-webhook
   ```
   MAX переподписывает вебхук сам при старте (старую подписку снимает, см.
   `web/app.py`), Telegram — явной командой выше.
4. Проверить: `curl https://tg.astra-ezoterika.ru/health`,
   `curl https://max.astra-ezoterika.ru/health` → `{db:ok, redis:ok}`.

## Акция «подписка на канал → +1 вопрос»

1. Сделать бота **админом** канала (иначе API не отдаёт участников и проверка
   подписки всегда возвращает «не подписан»).
2. В `/opt/astrobot/.env`:
   ```
   PROMO_CHANNEL_URL=https://t.me/ria_novosti_russiya
   PROMO_CHANNEL_ID=@ria_novosti_russiya
   ```
   В `/opt/astrobot/.env.max`:
   ```
   PROMO_CHANNEL_URL=https://max.ru/rossia_seichas
   PROMO_CHANNEL_ID=<числовой chat_id канала в MAX>
   ```
3. `docker compose up -d app app-max` — кнопка появится в меню и в пейволле.

Пустые переменные = акции нет, кнопка не показывается. Метрика выданных бонусов —
`astrobot_channel_bonus_claimed_total`.

## Лендинг (astra-ezoterika.ru)

Статика из `landing/`, раздаётся тем же Caddy — отдельного контейнера нет.
Каждый деплой делает `git reset --hard origin/main`, так что новый `index.html`
оказывается на диске сам; пересборка образа не нужна.

Разовая настройка:

1. DNS: A-запись `astra-ezoterika.ru` → IP сервера (без записи Caddy не получит
   сертификат).
2. В `/opt/astrobot/.env`: `LANDING_DOMAIN=astra-ezoterika.ru`
3. Пересоздать Caddy — у него появился новый volume-маунт, обычный
   `up -d --no-recreate` из деплоя его не подхватит:
   ```bash
   cd /opt/astrobot
   export COMPOSE_FILE="docker-compose.yml:docker-compose.prod.yml"
   docker compose up -d --force-recreate caddy
   ```
   Ингресс моргнёт на секунду, бот переживёт (Telegram/MAX ретраят вебхук).

Проверить: `curl -I https://astra-ezoterika.ru` → `200`.

## Что развёрнуто на сервере

- `app` — бот в webhook-режиме на `/telegram/webhook/{secret}`
- `postgres`, `redis` — persistent данные на named volumes
- `migrate` — one-shot перед app
- `caddy` — HTTPS-фронт на 80/443 + статика лендинга из `landing/`
- `prometheus`, `grafana` — метрики (внутри Docker-сети, доступ через SSH-туннель)
- `backup` — ежедневный pg_dump в `/opt/astrobot/backups/`

## Доступ к админкам в проде

Без публичного домена для `:3000`/`:9090` — через SSH-туннель:

```powershell
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 root@<ip>
```

Потом `http://localhost:3000` (Grafana) и `:9090` (Prometheus) на своей машине.

Админка SQLAdmin доступна публично по `https://<домен>/admin` — авторизация через `ADMIN_USER`/`ADMIN_PASSWORD`.
