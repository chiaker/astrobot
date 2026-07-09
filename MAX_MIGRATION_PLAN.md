# План переноса Astrobot в MAX

Перенос Telegram-бота (aiogram) в мессенджер MAX (библиотека **maxapi**) с сохранением
одной кодовой базы. Документ — рабочий чеклист миграции.

## Зафиксированные решения

| Вопрос | Решение | Следствие |
|---|---|---|
| Кодовая база | **Один репозиторий + слой платформы** | Бизнес-логика и хендлеры общие; отличается только слой отправки/приёма событий |
| База данных | **Отдельный Postgres под MAX** | Схема переносится почти как есть; ноль риска коллизий ID; аналитика раздельная |
| Админка | **Отдельный экземпляр sqladmin** | Второй `/admin` на своём поддомене, смотрит в свою БД |
| YooKassa | **Отдельный магазин** | Свои `shop_id`/`secret`; свои чеки и отчётность |

Итог: **один Docker-образ, два деплоя** с разным `.env`. Платформа выбирается переменной
`PLATFORM=telegram|max`.

---

## 1. Целевая архитектура

```
                 ┌─────────────────────────────────────────┐
                 │            ОБЩЕЕ ЯДРО (без изменений)     │
                 │  astrology/  llm/  tarot  lunar  gender   │
                 │  referral  limits  safety/  legal/  db/   │
                 │  payments/{yookassa,catalog,service}      │
                 └───────────────────┬───────────────────────┘
                                     │ вызывается из хендлеров
                 ┌───────────────────┴───────────────────────┐
                 │      ОБЩИЕ ХЕНДЛЕРЫ (bot/handlers/*)        │
                 │  логика диалогов, FSM, тексты, клавиатуры   │
                 │  работают через ИНТЕРФЕЙС платформы, а не   │
                 │  напрямую с aiogram/maxapi                  │
                 └───────┬───────────────────────────┬────────┘
                         │                           │
            ┌────────────┴─────────┐     ┌───────────┴──────────┐
            │  adapter: Telegram   │     │    adapter: MAX      │
            │  (aiogram)           │     │    (maxapi)          │
            └──────────────────────┘     └──────────────────────┘
```

Ключевая новая сущность — **слой платформы** (`bot/platform/`): тонкий интерфейс,
за которым прячутся различия aiogram ↔ maxapi. Хендлеры зовут интерфейс, а не SDK.

### Интерфейс платформы (черновик)

```python
# bot/platform/base.py
class PlatformMessage(Protocol):
    user_id: int          # внешний id (tg_user_id | max_user_id)
    chat_id: int
    text: str | None
    username: str | None

class PlatformContext(Protocol):
    """То, что хендлер получает вместо aiogram Message/CallbackQuery."""
    async def reply(self, text: str, kb: Keyboard | None = None) -> None: ...
    async def edit(self, text: str, kb: Keyboard | None = None) -> None: ...
    async def answer_callback(self) -> None: ...
    async def send_photo(self, path_or_id: str, caption: str | None = None) -> None: ...

class Keyboard:                      # нейтральное описание клавиатуры
    rows: list[list[Button]]
class Button:
    text: str
    payload: str | None              # callback
    url: str | None                  # link
```

Адаптеры:
- `bot/platform/telegram.py` — маппит в aiogram `InlineKeyboardMarkup`, `Message.answer`, HTML по умолчанию.
- `bot/platform/max.py` — маппит в `InlineKeyboardBuilder`, `event.message.answer(..., format=TextFormat.HTML)`, `InputMedia`.

> Форматирование: у maxapi `format=` задаётся **на каждую отправку** (в aiogram —
> глобально через `DefaultBotProperties`). Адаптер MAX подставляет `TextFormat.HTML`
> по умолчанию, чтобы хендлеры не знали об этом различии. Это уже проверено на прототипе.

---

## 2. Что переносится БЕЗ изменений (≈50% кода)

Эти модули не знают про мессенджер — общие как есть:

- `astrology/` — расчёт карт, транзиты, синастрия, рендеринг SVG→PNG
- `llm/` — генерация ответов
- `tarot.py`, `lunar.py`, `gender.py`, `referral.py`, `limits.py`
- `safety/`, `legal/`
- `db/models.py`, `db/session.py`, миграции (с одной правкой — см. §4)
- `payments/yookassa.py`, `payments/catalog.py`, `payments/service.py` (мелкие правки — см. §5)
- `metrics.py`, `logging_setup.py`, `redis_client.py`

---

## 3. Что переписывается под слой платформы

Весь `bot/` + точки входа. ~24 файла, завязанных на aiogram.

| Файл | Что делаем |
|---|---|
| `bot/dispatcher.py` | Две сборки диспетчера: aiogram и maxapi. FSM: aiogram RedisStorage ↔ maxapi `RedisContext` |
| `bot/keyboards.py` | Строит нейтральный `Keyboard`; адаптер рендерит в SDK-разметку |
| `bot/handlers/*` | Меняем типы `Message/CallbackQuery` → `PlatformContext`; `call.data` → `ctx.payload`; `reply_markup=` → `kb=` |
| `bot/middlewares.py` | Dedupe / DbSession / User — переносятся, но регистрируются в каждом SDK по-своему |
| `bot/responses.py`, `bot/utils.py` | `edit_or_send` и хелперы → в адаптеры |
| `web/routes/telegram.py` | + новый `web/routes/max.py` — приём вебхука MAX (формат события и заголовок `Authorization` другие) |
| `web/app.py` | Выбор адаптера по `PLATFORM`; `set_my_commands` → эквивалент MAX (проверить поддержку) |
| `scheduler.py` | Пуши шлём через интерфейс платформы, не через `bot` напрямую |

---

## 4. База данных (отдельный Postgres)

Схема переносится почти как есть. Единственное семантическое изменение — идентификатор
пользователя.

- `users.tg_user_id` → в MAX это `max_user_id`. Так как **БД отдельная**, проще всего
  оставить имя нейтральным: переименовать в `external_user_id` (BigInteger, unique) в общей
  модели. Одна миграция-переименование, применяется к обеим базам.
- Всё остальное (профили, платежи, кэш, подписки) — идентично.
- Отдельная БД = **свой стек миграций Alembic не нужен**: те же `versions/`, просто
  `DATABASE_URL` указывает на другую базу. Прогон `alembic upgrade head` на новой БД.

Подводный камень: поля вроде `telegram_charge_id` в `Payment` (миграция 0020) — в MAX не
используются, остаются NULL. Не удаляем, чтобы не плодить расхождение схемы.

---

## 5. Платежи (отдельный магазин YooKassa)

Хорошая новость: у MAX нет своих платежей, но YooKassa как внешний эквайринг работает
на любой платформе, и код уже есть. Правки минимальны:

- **Новый магазин**: свои `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` в `.env` MAX-деплоя.
  Код `payments/yookassa.py` не меняется — берёт креды из конфига.
- **`return_url`**: сейчас `config.py:yookassa_return_url_effective` возвращает
  `https://t.me/{bot_username}`. Нужно сделать платформо-зависимым: для MAX — ссылка
  обратно в MAX-бот (deep link MAX; уточнить формат в их API) или явный `YOOKASSA_RETURN_URL`.
- **Ветка Telegram Stars — выпиливается** для MAX. В `bot/handlers/payment.py` весь код
  вокруг `LabeledPrice` / `PreCheckoutQuery` / `successful_payment` (25 совпадений) не
  переносится; остаётся только YooKassa-флоу: кнопка → `create_payment` → ссылка на оплату
  → вебхук `/payments/yookassa` → `service.reconcile_payment`.
- **Вебхук платежей** уже платформо-независим (`web/routes/payments.py`) — реконсилит
  реальный статус из YooKassa, не доверяя телу. Отдельный магазин = отдельный URL вебхука
  в кабинете YooKassa, указывающий на MAX-деплой.
- **Автопродление** (`RECURRING_ENABLED`) — как и в TG, включать только когда магазин
  YooKassa разрешит recurring; иначе месячный тариф продаётся как разовый.
- **Чеки/НДС** (`YOOKASSA_VAT_CODE`, `build_receipt`) — свои для нового юрлица/магазина.

---

## 6. Админка (отдельный экземпляр)

`web/admin.py` работает чисто поверх БД → переиспользуется почти без изменений.

- Разворачивается тем же процессом MAX-деплоя, смотрит в свою БД, свой поддомен.
- Правки косметические: подписи `"Telegram ID"` → `"MAX ID"` (или нейтральное «Внешний ID»).
- Свои `ADMIN_USER` / `ADMIN_PASSWORD` / `ADMIN_SECRET` в `.env` MAX.

---

## 7. Деплой

Топология зеркалит текущую, второй независимый стек.

```
TG-деплой  (уже есть)          MAX-деплой (новый)
├─ app  (webhook TG)           ├─ app  (webhook MAX)  ← тот же образ, PLATFORM=max
├─ postgres (TG)               ├─ postgres (MAX)
├─ redis                       ├─ redis
├─ caddy → BOT_DOMAIN          ├─ caddy → MAX_BOT_DOMAIN
└─ grafana                     └─ grafana (опц.)
```

- **Один образ, два compose-стека** (или один compose с профилями). MAX-стек:
  `command: python -m astrobot.main --mode=webhook`, `PLATFORM=max`, свой `.env`.
- **Домен**: свой `MAX_BOT_DOMAIN` (напр. `max.astrobot.<домен>`), Caddy — тот же паттерн
  dynamic upstream + zero-downtime docker-rollout, healthcheck `/health`.
- **Вебхук MAX**: `POST /subscriptions` в MAX API (аналог `set_webhook`). Токен в заголовке
  `Authorization`. Свой `WEBHOOK_SECRET`.
- **Сертификат вебхука (MAX → наш сервер): Caddy + Let's Encrypt работает, Минцифры НЕ нужен.**
  Проверено по докам и практике сообщества: требуется любой доверенный УЦ («в том числе»
  Минцифры), LE явно рекомендуется в гайдах по MAX. Пункты «домен = CN/SAN» и «полная цепочка»
  Caddy закрывает сам. Жёсткое требование с 25.05.2026: только HTTPS, без self-signed.
- **⚠️ Сертификат в ОБРАТНУЮ сторону (наш бот → API MAX): Минцифры НУЖЕН.** MAX перевёл API на
  `platform-api2.max.ru` с сертификатом Минцифры. Исходящие вызовы бота к API MAX упадут с
  ошибкой проверки TLS, если в доверенном хранилище контейнера нет корневого+промежуточного
  сертификата Минцифры. Решение — добавить Russian Trusted Root CA в CA-бандл в `Dockerfile`
  (`update-ca-certificates`). Это НЕ про Caddy, а про trust store самого app-контейнера.
- **Long polling — только для дева.** Прод строго на вебхуке (как сейчас в TG).

---

## 8. Планировщик и пуши

`scheduler.py` (APScheduler в процессе) переносится, но отправку пушей (утренний гороскоп,
лунные события, premium-reminder, follow-up через 48ч) заворачиваем в интерфейс платформы.
Логика расписаний и запросов к БД — без изменений.

---

## 9. Мелочи и подводные камни

- **Форматирование**: `format=TextFormat.HTML` на каждую отправку — прячем в адаптер MAX. ✅ проверено.
- **Меню команд**: `set_my_commands` (синие команды /start, /menu) — проверить, есть ли аналог в MAX API; если нет, убрать безболезненно.
- **Welcome / follow-up анимации**: сейчас `WELCOME_ANIMATION` / `FOLLOWUP_ANIMATION` могут быть Telegram `file_id`. **file_id не переносится между платформами** — для MAX использовать URL или предзагрузку через `POST /uploads` (maxapi: `bot.upload_media` / `InputMediaBuffer`).
- **Рефералы через deep link**: реф-код сейчас приходит в payload у `/start`. У MAX есть deep-linking (пример `15_deep_linking_bot.py`) — проверить формат стартового payload и смапить в `referral`.
- **Событие первого контакта**: в maxapi это `bot_started` (не `/start`) — онбординг вешаем и на него.
- **Лимиты MAX**: 30 RPS, размеры вложений (фото до 50 МБ и т.п.) — для рассылок (`broadcast`) учесть троттлинг под лимит MAX.
- **OPS-алерты**: `alerts.notify_ops` шлёт в чат по `OPS_CHAT_ID` — через адаптер; свой чат/ID для MAX.
- **`metrics` / Grafana / `/health`** — платформо-независимы, переносятся как есть.
- **Тесты** (`tests/`) — ядро тестируется как есть; для слоя платформы добавить адаптерные тесты (пример `13_manual_events_bot.py` у maxapi — ручная подача событий, удобно для тестов).
- **Дедупликация апдейтов** (`UpdateDedupeMiddleware`) — переносим; ключ дедупа зависит от id события MAX (другой формат).

---

## 9a. Дополнительно выявленное (грабли — учесть заранее)

- **HTML-паритет — ✅ ПРОВЕРЕНО, риск мал.** MAX поддерживает `b/strong, i/em, s/del, u/ins,
  code/pre, a, mark, h1-6, blockquote`. Единственный тег из вашего белого списка
  `bot/formatting.py:_TELEGRAM_TAGS = {b,i,u,s,code,pre,a,tg-spoiler}`, которого нет в MAX —
  `tg-spoiler` (и он только пропускается, не инжектится). Работа = вариант `md_to_max_html()`
  с тем же кодом и белым списком БЕЗ `tg-spoiler`. Санитизация уже централизована в одном
  файле — менять точечно. Алиасы `<strong>/<em>` MAX тоже понимает.
- **Дедлайн `platform-api2.max.ru`.** Переезд API MAX на Минцифры-сертификат имел срок
  (~19.07.2026) — на момент планирования (09.07.2026) ещё не наступил, но Минцифры-CA в
  образе (§7) закладываем сразу, чтобы не ловить отвал после даты.
- **Deep-link рефералов — ✅ ПРОВЕРЕНО.** maxapi даёт `create_start_link(username, payload,
  encode=True)` + `decode_payload(event.payload)` в событии `BotStarted`. Реф-код → payload,
  читается на старте. Формат ссылки берёт на себя библиотека.
- **Нулевой перенос юзеров.** MAX-бот стартует с пустой БД (следствие отдельной базы):
  премиум/рефкоды/история не переносятся. Заложить в коммуникацию с юзерами.
- **Бэкапы второй БД.** Новой MAX-базе нужен свой backup-джоб (у TG он в `backups/`+`scripts/`).
- **SSL-контекст maxapi — ✅ ПРОВЕРЕНО.** maxapi на aiohttp (`>=3.13,<4`), не httpx. aiohttp
  читает системное хранилище → блок `update-ca-certificates` в Dockerfile достаточен для
  вызовов к MAX, certifi-нюанса нет. Зависимости совпадают с вашим стеком (pydantic v2,
  fastapi, uvicorn), плюс `backoff`, `magic_filter` (=`F`).

### Dockerfile: Russian Trusted Root CA (Минцифры)

Вставить после apt-блока (curl и ca-certificates уже стоят):

```dockerfile
RUN curl -fsSL https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
        -o /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
 && curl -fsSL https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
        -o /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt \
 && update-ca-certificates
```

Нюанс: `update-ca-certificates` обновляет системное хранилище (aiohttp/maxapi его читают).
`httpx` (YooKassa-клиент) берёт `certifi` — но Минцифры ему не нужен (у YooKassa обычный
публичный сертификат). Если где-то всё же нужен системный бандл для httpx — добавить
`ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt`.


## 10. Поэтапный план работ

**Этап 0 — Подготовка (0.5 дн)**
- [ ] Аккаунт MAX-бота через @MasterBot, токен, тестовый чат.
- [ ] `pip install maxapi`, прогнать прототип (`prototype_max/`) на живом токене.

**Этап 1 — Слой платформы (3–5 дн)** ← ядро миграции
- [x] `bot/platform/base.py` — интерфейс (`PlatformContext`, `Keyboard`, `Button`, `Media`, `StateStore`, `PlatformBot`).
- [x] `bot/platform/telegram.py` — адаптер поверх aiogram + устойчивость из `responses.py`. Импорт/конвертация проверены рантаймом, ruff чистый.
- [x] `bot/platform/max.py` — адаптер maxapi (структура из проверенных примеров; спорные имена полей событий помечены `TODO(max): verify` — сверить на Этапе 5).
- [x] `bot/platform/__init__.py` — ленивый `load_adapter('telegram'|'max')`, чтобы деплой тянул только свой SDK.
- [x] Перевести `keyboards.py` на нейтральный `Keyboard`. Заодно неизбежно: все локальные
  клавиатуры хендлеров (tarot/compat/payment/broadcast/favorites/profile/support/about/question/
  natal/horoscope) → нейтральные, все прямые send-сайты обёрнуты в `to_markup` (мост), общий
  `responses.py` конвертит на границе aiogram, `scheduler.py` (followup/broadcast) обёрнут.
  Проверено: ruff чистый, 28 конструкций клавиатур проходят через адаптер, grep-инвариант —
  ноль нейтральных клавиатур утекает в aiogram. **Нужен рантайм-смоук на тестовом боте перед
  деплоем** (окружение планирования не запускает бота: нет БД/Redis/токена и части deps).

**Этап 2 — Хендлеры на интерфейс (в процессе)**
- [x] Инфраструктура: `ContextMiddleware` инжектит `ctx: PlatformContext` в message/callback
  (аддитивно — не сломало не-мигрированные хендлеры), подключено в `dispatcher.py`.
  `PlatformContext.edit` получил `disable_preview`.
- [x] `about.py`, `legal.py`, `menu.py` — на `ctx` (эталонные шаблоны трёх форм): ноль
  aiogram-типов, ноль `to_markup`, `ctx.edit`/`ctx.reply`/`ctx.answer_callback`. Проверено
  ruff + сигнатурами. `response_toggle.py` — пустой роутер.
- [x] Паттерн для ЦЕНТРАЛЬНЫХ хелперов: `send_main_menu` оставлен как тонкий aiogram-мост
  для не-мигрированных вызывающих, а мигрированные зовут `render_main_menu` + `ctx` напрямую.
  Так миграция остаётся инкрементальной (не рушит дерево).
- [ ] Остальные хендлеры (onboarding, question, payment, natal, horoscope, compatibility,
  tarot, profile, support, favorites, broadcast, fallback). Почти все FSM-heavy и завязаны на
  `edit_or_send`/`save_and_send_response`/`need_profile`/`_answer_question`. FSM (142) →
  `StateStore`. **Делать против запущенного бота**: в окружении планирования эти модули даже
  не импортируются (нет `openai`/`geopy`/`kerykeion`), т.е. здесь их нельзя даже структурно
  проверить — блайнд-правки платёжного/онбординг-флоу рискованны.

**Этап 2 — Хендлеры на интерфейс (3–5 дн)**
- [ ] По одному переводим `handlers/*` с `Message/CallbackQuery` на `PlatformContext`.
- [ ] TG-бот при этом продолжает работать (регресс-проверка на каждом хендлере).

**Этап 3 — БД и конфиг (1–2 дн)**
- [ ] Миграция `tg_user_id` → `external_user_id`.
- [ ] `PLATFORM` в конфиге; выбор адаптера в `web/app.py` и `main.py`.
- [ ] Поднять отдельный Postgres MAX, `alembic upgrade head`.

**Этап 4 — Платежи (2–3 дн)**
- [ ] Новый магазин YooKassa, креды в `.env` MAX.
- [ ] `return_url` платформо-зависимый.
- [ ] Выпилить Stars-ветку из платёжного хендлера для MAX.
- [ ] Прогнать тестовый платёж и вебхук end-to-end.

**Этап 5 — Деплой (2–3 дн)**
- [ ] MAX-стек в compose, свой домен, Caddy, вебхук `POST /subscriptions`.
- [ ] Проверить сертификат (LE vs Минцифры).
- [ ] Healthcheck, docker-rollout, OPS-алерты, Grafana.

**Этап 6 — Мелочи и приёмка (2–3 дн)**
- [ ] Анимации (URL/upload вместо file_id), меню команд, рефералы, broadcast-троттлинг.
- [ ] Регресс TG + приёмка MAX по всем фичам.

---

## 11. Оценка сроков

| Сценарий | Срок (1 опытный разработчик) |
|---|---|
| С слоем платформы (выбранный путь) | **≈3–4 недели** |
| *(для сравнения)* грязный форк без абстракции | ≈2 недели, но двойная поддержка навсегда |

Большая часть времени — Этапы 1–2 (слой платформы + перевод хендлеров). Это разовая
инвестиция: после неё третью платформу или новые фичи добавлять дёшево.

---

## 12. Риски

- **Зрелость maxapi** (202⭐, релиз 2026) — возможны недокументированные баги. Митигация:
  прототип на каждую нетривиальную фичу до полного переноса; запасной вариант — `maxo`.
- ~~**Сертификат вебхука MAX** — может потребовать Минцифры.~~ **РАЗРЕШЕНО**: для вебхука Caddy+Let's Encrypt достаточно. Но Минцифры нужен в CA-бандле контейнера для ИСХОДЯЩИХ вызовов к `platform-api2.max.ru` (см. §7) — заложить в Dockerfile на Этапе 5.
- **YooKassa return_url в MAX** — уточнить формат deep-link возврата в бот; до выяснения использовать явный `YOOKASSA_RETURN_URL`.
- **Меню команд / нативные возможности MAX** — часть TG-фич может не иметь прямого аналога; выявляется на Этапе 6, деградируем мягко.
```
