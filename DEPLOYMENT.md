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

## Что развёрнуто на сервере

- `app` — бот в webhook-режиме на `/telegram/webhook/{secret}`
- `postgres`, `redis` — persistent данные на named volumes
- `migrate` — one-shot перед app
- `caddy` — HTTPS-фронт на 80/443
- `prometheus`, `grafana` — метрики (внутри Docker-сети, доступ через SSH-туннель)
- `backup` — ежедневный pg_dump в `/opt/astrobot/backups/`

## Доступ к админкам в проде

Без публичного домена для `:3000`/`:9090` — через SSH-туннель:

```powershell
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 root@<ip>
```

Потом `http://localhost:3000` (Grafana) и `:9090` (Prometheus) на своей машине.

Админка SQLAdmin доступна публично по `https://<домен>/admin` — авторизация через `ADMIN_USER`/`ADMIN_PASSWORD`.
