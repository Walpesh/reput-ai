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
