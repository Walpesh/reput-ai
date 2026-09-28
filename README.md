# ReputationAI

Автоматизированная платформа мониторинга и ответа на отзывы с использованием ИИ (OpenRouter/DeepSeek/Gemini/Qwen), согласованиями в Telegram (aiogram 3.x) и воронкой перехвата негатива.

## Архитектура

- **Backend**: FastAPI + Pydantic v2 (Async, Modular Monolith / Headless First)
- **База данных**: PostgreSQL (SQLAlchemy 2.0 Async + Alembic)
- **Очереди и задачи**: Celery + Redis
- **ИИ-движок**: OpenRouter API (Primary: DeepSeek-Chat, Fallback: Gemini/Qwen) с защитой от Prompt Injection
- **Telegram Bot**: aiogram 3.x (Уведомления, 1-click кнопки `[Одобрить]`, `[Редактировать]`, `[Перегенерировать]`)
- **Биллинг**: Интеграция с ЮKassa / Т-Банк (триал 14 дней, подписка)
- **Деплой**: Нативный запуск на Ubuntu Server через systemd и Nginx (без Docker)

## Структура проекта

```text
reput-ai/
├── src/reput_ai/
│   ├── main.py             # Точка входа FastAPI
│   ├── config.py           # Конфигурация через pydantic-settings
│   ├── core/               # Безопасность и общие утилиты
│   ├── db/                 # Модели SQLAlchemy 2.0 и подключение к БД
│   ├── api/                # REST API эндпоинты (v1)
│   ├── schemas/            # Pydantic v2 схемы
│   ├── llm/                # Интеграция с OpenRouter, защита от инъекций, промпты
│   ├── bot/                # Telegram-бот на aiogram 3
│   ├── workers/            # Celery задачи (парсинг, генерация)
│   └── services/           # Сервисы бизнес-логики (биллинг и др.)
├── alembic/                # Миграции базы данных
├── deploy/                 # Конфигурации systemd и Nginx
└── tests/                  # Тесты (pytest)
```

## Установка и запуск

1. Склонируйте репозиторий и создайте виртуальное окружение:
```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
```

2. Установите зависимости:
```bash
pip install -e ".[dev]"
```

3. Настройте переменные окружения:
```bash
cp .env.example .env
# Отредактируйте .env значениями ваших ключей и URL
```

4. Запуск миграций:
```bash
alembic upgrade head
```

5. Запуск тестов:
```bash
pytest
```

## Этап 3: Воркеры, скрейпер и биллинг

### 3.1. Celery Worker System

- **Брокер/бэкенд**: Redis (`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`).
- **Приложение**: `src/reput_ai/workers/celery_app.py` (beat-расписание внутри).
- **Задачи**:
  - `workers/tasks/scraper.py` — периодический опрос Yandex Maps (и других платформ) каждые **15–30 минут** (`SCRAPER_POLL_INTERVAL_MINUTES`, по умолчанию 15);
  - `workers/tasks/ai_tasks.py` — асинхронная обработка LLM-задач (генерация ответов на новые отзывы);
  - `workers/tasks/billing_tasks.py` — проверка платежей, продление/приостановка подписок.
- **Запуск**:
```bash
celery -A reput_ai.workers.celery_app.celery_app worker --loglevel=info
celery -A reput_ai.workers.celery_app.celery_app beat --loglevel=info
```

### 3.2. Billing Engine (`src/reput_ai/services/billing.py`)

- Интеграция с **ЮKassa** и **Т-Банк** (Т-Пэй): создание платежей/подписок, рекуррентные списания.
- **Триал 14 дней** (`TRIAL_PERIOD_DAYS`), далее автоматическое продление по `BILLING_PERIOD_DAYS`.
- **Webhook-обработчик**: `POST /api/v1/subscriptions/webhook` — **обязательная валидация подписи**
  (HMAC-SHA256, `YOOKASSA_WEBHOOK_SECRET` / `TBANK_PASSWORD`); запросы без корректной подписи отклоняются (401/403).
- **Жизненный цикл статусов**: `TRIAL → ACTIVE → PAST_DUE → CANCELED`
  (модель `Subscription.Status` в `src/reput_ai/db/models/subscription.py`).
- **Автоприостановка** при неудачном платеже: `ACTIVE → PAST_DUE` с grace-period
  (`BILLING_GRACE_PERIOD_DAYS`), затем → `CANCELED`; API-эндпоинты воронки и скрейпинга блокируются для приостановленных подписок.

### 3.3. Перехват негатива (воронка)

`POST /api/v1/funnel/{token}` (+ страницы логина по QR/короткой ссылке, `PUBLIC_BASE_URL`, `FEEDBACK_FORM_PATH`):

- клиент переходит по QR/короткой ссылке → оценивает заведение;
- **4–5★** — публикуется на публичных площадках (сгенерированный публичный ответ/ссылка);
- **1–3★** — уходит напрямую менеджменту в **Telegram** (уведомление ботом, согласование ответа), наружу не публикуется.

### 3.4. E2E headless-тесты

```bash
pytest tests/test_e2e_headless.py
```

Покрывают системный сценарий без frontend-UI: регистрация → ветка/платформа → сбор отзывов
воркером → LLM-обработка → воронка перехвата → биллинг (триал, webhook с валидацией подписи,
смена статусов, автоприостановка).

## Этап 4: Web Dashboard и деплой (systemd + Nginx/SSL)

### 4.1. Web Dashboard (Streamlit)

Лёгкий дашборд на **Streamlit** (`src/reput_ai/dashboard/`), подключённый к существующему
backend API через REST (`DASHBOARD_API_BASE_URL`), без прямого доступа к БД (Headless First):

- `client.py` — централизованный синхронный httpx-клиент API: register/login/JWT (auth),
  CRUD филиалов (`GET/POST /branches/`, `PATCH /branches/{id}`), список отзывов и переходы
  статусов (`PATCH /reviews/{id}/status`), подписка, короткие ссылки воронки;
- `options.py` — списки опций UI, полученные напрямую из backend-enum
  (`PlatformType`, `ToneOfVoice`, `ReviewStatus`, `SubscriptionStatus`) — единственный
  источник значений, ничего не дублируется и не выдумывается;
- `metrics.py` — чистые KPI-агрегации (средний рейтинг, негатив 1–3★, распределение оценок);
- `app.py` — UI со страницами (навигация в боковой панели):
  - **Вход/Регистрация** — существующие `POST /auth/login`, `POST /auth/register` (токен
    хранится только в `st.session_state`);
  - **Обзор** — KPI-карточки, фильтры (филиал/рейтинг/статус), график оценок, сводка
    подписки, короткая ссылка воронки, таблица отзывов;
  - **Филиалы** — список, создание (name, platform_type, platform_url, tone_of_voice,
    is_active) и настройка (PATCH backend принимает только name/tone_of_voice/is_active —
    платформа и URL отображаются только для чтения);
  - **Отзывы** — список с фильтрами, детали (author_name, rating, text, generated_reply,
    final_reply, status, created_at) и существующие переходы статусов
    (NEW/PENDING_APPROVAL/APPROVED/REJECTED/PUBLISHED);
  - **Подписка** — status, trial_ends_at, paid_until, payment_provider_id;
  - явная обработка ошибок API: 401 (сессия), 422 (валидация), 5xx (сервер),
    недоступность сети (retry).

Запуск локально:
```bash
streamlit run src/reput_ai/dashboard/app.py --server.port 8501
```

### 4.2. Production systemd (Ubuntu Server, без Docker)

Юниты в `deploy/systemd/` (см. `deploy/README.md`):

| Юнит | Сервис |
|------|--------|
| `fastapi.service` | uvicorn, `127.0.0.1:8000` |
| `celery.service` | celery worker + beat (опрос площадок, LLM, биллинг) |
| `bot.service` | Telegram-бот (aiogram 3.x) |
| `dashboard.service` | Streamlit-дашборд, `127.0.0.1:8501` |

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fastapi celery bot dashboard
```

### 4.3. Nginx + SSL (Certbot)

`deploy/nginx/reput-ai.conf` — reverse proxy:

- `/api/*`, `/docs`, `/redoc`, `/openapi.json` → FastAPI `:8000`;
- `/` (всё остальное) → Streamlit `:8501` (с WebSocket-заголовками);
- HTTP→HTTPS редирект, ACME webroot, TLS 1.2/1.3.

Полная инструкция по выпуску и автопродлению SSL — `deploy/nginx/README.md`
(`certbot --nginx -d your-domain.com`, `certbot renew --dry-run`).

### 4.4. Тесты (Headless First)

```bash
pytest tests/test_dashboard.py tests/test_deploy.py
```

- `tests/test_dashboard.py` — API-клиент дашборда (MockTransport): auth/филиалы/отзывы/
  подписка/воронка, KPI/фильтры, соответствия опций UI backend-enum,
  headless-запуск Streamlit (`/_stcore/health` = `ok`) без браузера;
- `tests/test_dashboard_ui.py` — UI-workflow тесты через `streamlit.testing.v1.AppTest`:
  вход/регистрация/выход, создание и настройка филиалов, детали отзывов и все 5
  переходов статусов, отрисовка полей подписки, обработка ошибок 401/422/5xx/сети
  (API мокается **только внутри тестов**);
- `tests/test_deploy.py` — наличие и корректность `fastapi.service`/`celery.service`/`bot.service`,
  маршрутизация и SSL в Nginx, документация Certbot.

