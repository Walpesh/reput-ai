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
