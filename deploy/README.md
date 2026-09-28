# Деплой — Ubuntu Server (нативно, без Docker)

Все юниты systemd лежат в `deploy/systemd/`:

| Файл | Сервис | Порт |
|------|--------|------|
| `fastapi.service` | Backend FastAPI (uvicorn) | `127.0.0.1:8000` |
| `celery.service` | Celery worker + beat (парсинг, LLM, биллинг) | — |
| `bot.service` | Telegram-бот (aiogram 3.x) | — |
| `dashboard.service` | Streamlit-дашборд (дополнительно) | `127.0.0.1:8501` |

## 1. Подготовка окружения

```bash
sudo useradd --system --home /opt/reput-ai --shell /usr/sbin/nologin www-data || true
sudo mkdir -p /opt/reput-ai
sudo git clone https://github.com/Walpesh/reput-ai.git /opt/reput-ai
cd /opt/reput-ai

python3 -m venv .venv
sudo .venv/bin/pip install -e ".[dev]"

sudo cp .env.example .env
sudo nano .env            # SECRET_KEY, DATABASE_URL, токены, домен
sudo chown -R www-data:www-data /opt/reput-ai
```

Требуются системные `postgresql`, `redis-server` и `nginx` (см. `deploy/nginx/README.md`).

## 2. Миграции

```bash
cd /opt/reput-ai && sudo -u www-data .venv/bin/alembic upgrade head
```

## 3. Установка и запуск сервисов

```bash
sudo cp deploy/systemd/fastapi.service   /etc/systemd/system/
sudo cp deploy/systemd/celery.service    /etc/systemd/system/
sudo cp deploy/systemd/bot.service       /etc/systemd/system/
sudo cp deploy/systemd/dashboard.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now fastapi celery bot dashboard

systemctl status fastapi celery bot dashboard
journalctl -u fastapi -u celery -u bot -u dashboard -f
```

## 4. Порядок запуска (Headless First)

1. PostgreSQL + Redis запущены;
2. `alembic upgrade head`;
3. `fastapi` → проверить `curl http://127.0.0.1:8000/health`;
4. `celery` → проверить журнал beat-расписания;
5. `bot` → проверить подключение к Telegram API;
6. только затем `dashboard` и Nginx (см. `deploy/nginx/README.md`).
