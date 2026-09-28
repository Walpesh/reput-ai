from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="start")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(
        "👋 Здравствуйте! Это бот ReputationAI.\n\n"
        "Здесь вы сможете оперативно получать уведомления о новых отзывах клиентов "
        "и в один клик утверждать сгенерированные ИИ ответы.\n\n"
        f"Ваш Telegram ID: <code>{message.from_user.id}</code> (используйте его при привязке в панели)."
    )
