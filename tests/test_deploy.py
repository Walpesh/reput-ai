"""Stage 4 tests: production systemd units + Nginx reverse proxy configuration."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = PROJECT_ROOT / "deploy" / "systemd"
NGINX_CONF = PROJECT_ROOT / "deploy" / "nginx" / "reput-ai.conf"
NGINX_README = PROJECT_ROOT / "deploy" / "nginx" / "README.md"
DEPLOY_README = PROJECT_ROOT / "deploy" / "README.md"


# ---------------------------------------------------------------------------
# systemd units required by the RFP: fastapi.service, celery.service, bot.service
# ---------------------------------------------------------------------------
REQUIRED_UNITS = ("fastapi.service", "celery.service", "bot.service")


def test_required_systemd_units_exist() -> None:
    for unit in REQUIRED_UNITS:
        assert (SYSTEMD_DIR / unit).is_file(), f"missing systemd unit: {unit}"


def _read(unit: str) -> str:
    return (SYSTEMD_DIR / unit).read_text(encoding="utf-8")


def test_units_have_required_sections() -> None:
    for unit in REQUIRED_UNITS:
        content = _read(unit)
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content
        assert "WantedBy=multi-user.target" in content


def test_units_are_native_no_docker() -> None:
    """Deployment must be native on Ubuntu Server — no containers."""
    for unit in REQUIRED_UNITS + ("dashboard.service",):
        content = _read(unit)
        assert "docker" not in content.lower()
        assert "podman" not in content.lower()
        assert "ExecStart=" in content


def test_units_restart_always_and_env_file() -> None:
    for unit in REQUIRED_UNITS + ("dashboard.service",):
        content = _read(unit)
        assert "Restart=always" in content
        assert "EnvironmentFile=/opt/reput-ai/.env" in content
        assert "WorkingDirectory=/opt/reput-ai" in content


def test_fastapi_unit_runs_uvicorn() -> None:
    content = _read("fastapi.service")
    assert "ExecStart=" in content
    exec_start = next(l for l in content.splitlines() if l.startswith("ExecStart="))
    assert "uvicorn" in exec_start
    assert "reput_ai.main:app" in exec_start
    assert "--host 127.0.0.1" in exec_start
    assert "--port 8000" in exec_start


def test_celery_unit_runs_celery_worker_with_beat() -> None:
    content = _read("celery.service")
    exec_start = next(l for l in content.splitlines() if l.startswith("ExecStart="))
    assert "celery" in exec_start
    assert "worker" in exec_start
    assert "-B" in exec_start  # embedded beat scheduler
    assert "reput_ai.workers.celery_app" in exec_start


def test_bot_unit_runs_python_module() -> None:
    content = _read("bot.service")
    exec_start = next(l for l in content.splitlines() if l.startswith("ExecStart="))
    assert "python" in exec_start
    assert "reput_ai.bot" in exec_start


def test_units_wait_for_dependencies() -> None:
    assert "postgresql.service" in _read("fastapi.service")
    assert "redis-server.service" in _read("fastapi.service")
    assert "fastapi.service" in _read("celery.service")
    assert "fastapi.service" in _read("bot.service")


# ---------------------------------------------------------------------------
# Nginx reverse proxy + SSL
# ---------------------------------------------------------------------------
def test_nginx_conf_exists() -> None:
    assert NGINX_CONF.is_file()


def test_nginx_routes_api_to_fastapi() -> None:
    conf = NGINX_CONF.read_text(encoding="utf-8")
    assert "location /api/" in conf
    assert "proxy_pass http://127.0.0.1:8000" in conf
    # Docs endpoints are served by FastAPI directly.
    assert "location = /docs" in conf
    assert "location = /openapi.json" in conf


def test_nginx_routes_dashboard_with_websockets() -> None:
    conf = NGINX_CONF.read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:8501" in conf
    assert 'proxy_set_header Upgrade $http_upgrade' in conf
    assert 'proxy_set_header Connection "upgrade"' in conf
    assert "proxy_read_timeout" in conf


def test_nginx_ssl_and_https_redirect() -> None:
    conf = NGINX_CONF.read_text(encoding="utf-8")
    assert "listen 443 ssl" in conf
    assert "ssl_certificate " in conf
    assert "ssl_certificate_key" in conf
    assert "return 301 https://" in conf
    assert "acme-challenge" in conf  # Certbot webroot


def test_certbot_instructions_documented() -> None:
    readme = NGINX_README.read_text(encoding="utf-8")
    assert "certbot" in readme.lower()
    assert "certbot --nginx" in readme
    assert "renew" in readme  # auto-renewal instructions
    assert "letsencrypt" in readme.lower() or "ssl" in readme.lower()


def test_deploy_readme_documents_units() -> None:
    readme = DEPLOY_README.read_text(encoding="utf-8")
    for unit in REQUIRED_UNITS:
        assert unit in readme
    assert "systemctl daemon-reload" in readme
    assert "enable --now" in readme

