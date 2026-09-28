from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from reput_ai.config import settings
from reput_ai.bot.handlers import start_router, approval_router


def create_bot() -> Bot:
    return Bot(
        token=settings.TELEGRAM_BOT_TOKEN or "1234567890:DUMMY_TOKEN_FOR_TESTING_PURPOSES_ONLY",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(approval_router)
    return dp
