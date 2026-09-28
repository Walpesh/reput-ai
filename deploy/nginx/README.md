# Nginx + SSL (Certbot) — ReputationAI

Reverse proxy для Ubuntu Server без Docker: FastAPI (`127.0.0.1:8000`) и
Streamlit-дашборд (`127.0.0.1:8501`).

## 1. Установка Nginx

```bash
sudo apt update
sudo apt install -y nginx
```

## 2. Установка конфигурации

1. В `reput-ai.conf` замените `your-domain.com` на ваш реальный домен
   (в двух местах: `server_name` и пути `ssl_certificate`).
2. Установите конфиг:

```bash
sudo cp deploy/nginx/reput-ai.conf /etc/nginx/sites-available/reput-ai.conf
sudo ln -s /etc/nginx/sites-available/reput-ai.conf /etc/nginx/sites-enabled/reput-ai.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

## 3. Получение SSL-сертификата (Certbot)

### Вариант A — плагин nginx (рекомендуется)

```bash
sudo apt install -y certbot python3-certbot-nginx

# Первичный выпуск сертификата + автоматическое обновление конфига Nginx:
sudo certbot --nginx -d your-domain.com

# Проверка авто-продления:
sudo certbot renew --dry-run
```

### Вариант B — webroot (если сертификат выпускается до настройки HTTPS-блока)

```bash
sudo mkdir -p /var/www/certbot
sudo apt install -y certbot
sudo certbot certonly --webroot -w /var/www/certbot \
  -d your-domain.com --agree-tos -m your-email@example.com --non-interactive
```

После выпуска сертификата `nginx -t && systemctl reload nginx`.

## 4. Автопродление

Пакет `certbot` устанавливает systemd-таймер `certbot.timer`, который запускает
`certbot renew` дважды в сутки. Проверить:

```bash
systemctl status certbot.timer
sudo certbot renew --dry-run
```

## 5. Проверка маршрутизации

```bash
# API (FastAPI):
curl -fsS https://your-domain.com/health
# Дашборд (Streamlit):
curl -fsSI https://your-domain.com/ | head -n 1   # 301 -> https или 200
```

| Путь | Бэкенд |
|------|--------|
| `/api/*`, `/docs`, `/redoc`, `/openapi.json` | FastAPI `127.0.0.1:8000` |
| всё остальное (`/`) | Streamlit `127.0.0.1:8501` |
