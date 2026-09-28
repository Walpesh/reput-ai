import logging
import uuid
from collections.abc import Awaitable, Callable

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from reput_ai.db.session import async_session_maker
from reput_ai.db.models.review import Review, ReviewStatus

router = Router(name="review_approval")
logger = logging.getLogger("reput_ai.bot")


class ReviewEditState(StatesGroup):
    waiting_for_custom_reply = State()


RegenerateFn = Callable[[str, str, str], Awaitable[None]]


async def default_regenerate(review_id: str, review_text: str, tone_of_voice: str) -> None:
    """Queue a Celery regeneration task (imported lazily so the bot loads without a broker)."""
    from reput_ai.workers.tasks.ai_tasks import generate_review_reply_task

    generate_review_reply_task.delay(review_id, review_text, tone_of_voice)


regenerate_review: RegenerateFn = default_regenerate


async def approve_review(review_id: str) -> bool:
    """Mark the review as approved and freeze the currently generated reply."""
    review_uuid = uuid.UUID(review_id)
    async with async_session_maker() as session:
        result = await session.execute(select(Review).where(Review.id == review_uuid))
        review = result.scalar_one_or_none()
        if review is None:
            return False
        review.status = ReviewStatus.APPROVED
        if not review.final_reply and review.generated_reply:
            review.final_reply = review.generated_reply
        await session.commit()
        return True


async def request_regeneration(review_id: str) -> bool:
    """Queue a fresh AI generation for the review using the branch tone of voice."""
    review_uuid = uuid.UUID(review_id)
    async with async_session_maker() as session:
        result = await session.execute(
            select(Review).options(selectinload(Review.branch)).where(Review.id == review_uuid)
        )
        review = result.scalar_one_or_none()
        if review is None or review.branch is None:
            return False
        await regenerate_review(str(review.id), review.text, review.branch.tone_of_voice.value)
        return True


async def save_custom_reply(review_id: str, custom_text: str) -> bool:
    """Store the operator-authored reply and approve it."""
    review_uuid = uuid.UUID(review_id)
    async with async_session_maker() as session:
        result = await session.execute(select(Review).where(Review.id == review_uuid))
        review = result.scalar_one_or_none()
        if review is None:
            return False
        review.final_reply = custom_text
        review.status = ReviewStatus.APPROVED
        await session.commit()
        return True


async def approve_review_callback(callback: CallbackQuery) -> None:
    review_id_str = (callback.data or "").split(":")[-1]
    logger.info("Review approved via Telegram callback: %s", review_id_str)

    try:
        await approve_review(review_id_str)
    except Exception as exc:
        logger.warning("Could not update review status in DB (offline/mock): %s", exc)

    await callback.answer("✅ Ответ одобрен и отправлен в публикацию!")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


async def regenerate_review_callback(callback: CallbackQuery) -> None:
    review_id_str = (callback.data or "").split(":")[-1]
    logger.info("Review regeneration requested via Telegram callback: %s", review_id_str)

    try:
        await request_regeneration(review_id_str)
    except Exception as exc:
        logger.warning("Could not trigger regeneration task (offline/mock): %s", exc)

    await callback.answer("🔄 Запущена перегенерация ответа ИИ...")


async def edit_review_callback(callback: CallbackQuery, state: FSMContext) -> None:
    review_id_str = (callback.data or "").split(":")[-1]
    logger.info("Review edit requested via Telegram callback: %s", review_id_str)

    await state.set_state(ReviewEditState.waiting_for_custom_reply)
    await state.update_data(review_id=review_id_str)

    await callback.answer()
    if callback.message:
        await callback.message.reply(
            "✏️ <b>Редактирование ответа:</b>\n"
            "Пожалуйста, пришлите сообщение с вашим собственным текстом ответа."
        )


async def process_custom_reply(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    review_id_str = data.get("review_id")
    custom_text = message.text

    if not custom_text or not review_id_str:
        await message.reply("Текст ответа не может быть пустым.")
        return

    logger.info("Received manual reply for review %s", review_id_str)

    try:
        await save_custom_reply(review_id_str, custom_text)
    except Exception as exc:
        logger.warning("Could not update review with custom reply in DB: %s", exc)

    await state.clear()
    await message.reply(
        f"✅ <b>Ваш ответ сохранен и одобрен к публикации:</b>\n<i>«{custom_text}»</i>"
    )


def create_router() -> Router:
    """Build a fresh review-approval router.

    aiogram forbids attaching the same ``Router`` instance to more than one
    ``Dispatcher``, so handlers are registered anew for every dispatcher.
    """
    router = Router(name="review_approval")
    router.callback_query.register(approve_review_callback, F.data.startswith("review:approve:"))
    router.callback_query.register(
        regenerate_review_callback, F.data.startswith("review:regenerate:")
    )
    router.callback_query.register(edit_review_callback, F.data.startswith("review:edit:"))
    router.message.register(process_custom_reply, ReviewEditState.waiting_for_custom_reply)
    return router


router = create_router()

