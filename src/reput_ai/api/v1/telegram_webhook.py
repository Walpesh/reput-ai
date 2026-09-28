import logging

from aiogram.types import Update
from fastapi import APIRouter, Request, status

from reput_ai.bot.bot import create_bot, create_dispatcher

router = APIRouter(prefix="/telegram", tags=["telegram"])
logger = logging.getLogger("reput_ai.api.telegram")

_bot = None
_dispatcher = None


def get_webhook_bot_and_dispatcher():
    """Return (and lazily create) the process-wide bot and dispatcher for webhook mode."""
    global _bot, _dispatcher
    if _bot is None:
        _bot = create_bot()
    if _dispatcher is None:
        _dispatcher = create_dispatcher()
    return _bot, _dispatcher


def reset_webhook_runtime() -> None:
    """Drop the cached bot/dispatcher so tests and reloads can inject fresh instances."""
    global _bot, _dispatcher
    _bot = None
    _dispatcher = None


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(request: Request) -> dict[str, str]:
    """FastAPI webhook endpoint for aiogram 3.x updates."""
    bot, dp = get_webhook_bot_and_dispatcher()
    raw_update = await request.json()
    logger.debug("Received Telegram webhook update_id=%s", raw_update.get("update_id"))
    update = Update.model_validate(raw_update, context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return {"status": "ok"}
