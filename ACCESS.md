# Astrobot — куда ходить

## Бот
- Telegram: `@astrology_bbot`
- Режим: polling (dev) / webhook (prod)

## HTTP-эндпойнты (порт 8000)
- `GET /health` — проверка БД + Redis
- `GET /health/live` — лёгкий liveness без зависимостей
- `GET /metrics` — Prometheus-метрики
- `POST /telegram/webhook/{secret}` — TG webhook (только prod)
- `POST /payments/{provider}` — заглушка, `501`
- `/admin/*` — админка (SQLAdmin)

## Сервисы

| Что | URL | Логин |
|---|---|---|
| Админка | http://localhost:8000/admin | `ADMIN_USER` / `ADMIN_PASSWORD` |
| Prometheus | http://localhost:9090 | — |
| Grafana | http://localhost:3000 | `admin` / `GRAFANA_PASSWORD` |
| Postgres | localhost:5432 | `astrobot` / `astrobot` (db: `astrobot`) |
| Redis | localhost:6379 | — |

## Где что лежит
- Бэкапы Postgres: `./backups/` (ежедневно, ротация 7д/4н/3м)
- Логи: `docker compose logs -f app`
- Конфиг: `.env` (шаблон в `.env.example`)
- Миграции: `src/astrobot/db/migrations/versions/`

## Команды
- `make up` — поднять стек
- `make down` — остановить
- `make logs` — лог app
- `make migrate` — alembic upgrade head
- `make test` — pytest
- `make shell` — shell в app-контейнере
- `make build-prod` — собрать prod-образ с git-SHA тегом

## Деплой на prod
```
IMAGE_TAG=$(git rev-parse --short HEAD) \
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
Нужен `BOT_DOMAIN`, `WEBHOOK_BASE_URL`, `WEBHOOK_SECRET` в `.env`. Caddy сам получит HTTPS от Let's Encrypt.
