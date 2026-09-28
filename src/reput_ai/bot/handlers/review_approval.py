import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

router = Router(name="review_approval")
logger = logging.getLogger("reput_ai.bot")


@router.callback_query(F.data.startswith("review:approve:"))
async def approve_review_callback(callback: CallbackQuery) -> None:
    review_id = callback.data.split(":")[-1]
    logger.info(f"Review approved via Telegram: {review_id}")
    await callback.answer("✅ Ответ одобрен и отправлен в публикацию!")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("review:regenerate:"))
async def regenerate_review_callback(callback: CallbackQuery) -> None:
    review_id = callback.data.split(":")[-1]
    logger.info(f"Review regeneration requested via Telegram: {review_id}")
    await callback.answer("🔄 Запущена перегенерация ответа ИИ...")


@router.callback_query(F.data.startswith("review:edit:"))
async def edit_review_callback(callback: CallbackQuery) -> None:
    review_id = callback.data.split(":")[-1]
    logger.info(f"Review edit requested via Telegram: {review_id}")
    await callback.answer("✏️ Введите ваш вариант ответа сообщением:")
