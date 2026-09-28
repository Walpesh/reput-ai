import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from reput_ai.config import settings
from reput_ai.bot.handlers import create_review_approval_router, create_start_router

logger = logging.getLogger("reput_ai.bot")


def create_bot(token: str | None = None) -> Bot:
    bot_token = token or settings.TELEGRAM_BOT_TOKEN or "1234567890:DUMMY_TOKEN_FOR_TESTING_PURPOSES_ONLY"
    return Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    """Create a dispatcher with freshly built routers.

    Routers are rebuilt on every call because aiogram forbids attaching the same
    ``Router`` instance to more than one ``Dispatcher`` (important for tests and
    for hot-reloading webhook workers).
    """
    dp = Dispatcher()
    dp.include_router(create_start_router())
    dp.include_router(create_review_approval_router())
    return dp


async def start_polling(bot: Bot | None = None, dp: Dispatcher | None = None) -> None:
    """Start bot in polling mode for local development or native runner."""
    active_bot = bot or create_bot()
    active_dp = dp or create_dispatcher()
    logger.info("Starting Telegram Bot in Polling mode...")
    await active_dp.start_polling(active_bot)


async def setup_webhook(bot: Bot | None = None, webhook_url: str | None = None) -> None:
    """Register webhook URL on Telegram servers."""
    active_bot = bot or create_bot()
    url = webhook_url or settings.TELEGRAM_WEBHOOK_URL
    if not url:
        raise ValueError("TELEGRAM_WEBHOOK_URL is not configured")
    logger.info(f"Setting up Telegram webhook to {url}")
    await active_bot.set_webhook(url=url, drop_pending_updates=True)


async def remove_webhook(bot: Bot | None = None) -> None:
    """Remove webhook registration from Telegram servers."""
    active_bot = bot or create_bot()
    logger.info("Removing Telegram webhook...")
    await active_bot.delete_webhook()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_polling())

