import asyncio
import logging

from reput_ai.bot.bot import start_polling
from reput_ai.core.logging import setup_logging


def main() -> None:
    setup_logging()
    logging.getLogger("reput_ai.bot").info("Launching ReputationAI Telegram bot runner")
    asyncio.run(start_polling())


if __name__ == "__main__":
    main()
