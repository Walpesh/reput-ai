import html
import logging
from uuid import UUID
from aiogram import Bot
from reput_ai.bot.keyboards.inline import get_review_approval_keyboard

logger = logging.getLogger("reput_ai.bot.service")


class TelegramBotService:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def notify_new_review(
        self,
        telegram_id: int,
        review_id: UUID,
        branch_name: str,
        platform_name: str,
        author_name: str,
        rating: int,
        review_text: str,
        generated_reply: str,
    ) -> bool:
        """Send notification about a new review requiring approval to the business owner via Telegram."""
        stars = "⭐" * max(1, min(5, rating))
        escaped_author = html.escape(author_name)
        escaped_branch = html.escape(branch_name)
        escaped_text = html.escape(review_text)
        escaped_reply = html.escape(generated_reply)

        text = (
            f"🔔 <b>Новый отзыв!</b>\n"
            f"📍 Филиал: <b>{escaped_branch}</b> ({platform_name})\n"
            f"👤 Автор: <b>{escaped_author}</b>\n"
            f"⭐ Оценка: {stars} ({rating}/5)\n\n"
            f"💬 <b>Текст отзыва:</b>\n"
            f"<i>«{escaped_text}»</i>\n\n"
            f"🤖 <b>Сгенерированный ответ ИИ:</b>\n"
            f"<i>«{escaped_reply}»</i>\n\n"
            f"Выберите действие с помощью кнопок ниже:"
        )

        keyboard = get_review_approval_keyboard(review_id)
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=keyboard,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send review notification to telegram user {telegram_id}: {e}")
            return False

    async def send_negative_feedback_alert(
        self,
        telegram_id: int,
        branch_name: str,
        rating: int,
        author_name: str,
        phone_or_contact: str | None,
        feedback_text: str,
    ) -> bool:
        """Direct channel for negative feedback (1-3 stars) from the QR/short link funnel."""
        escaped_branch = html.escape(branch_name)
        escaped_author = html.escape(author_name)
        escaped_contact = html.escape(phone_or_contact or "Не указан")
        escaped_text = html.escape(feedback_text)
        stars = "⚠️" * max(1, min(5, rating))

        text = (
            f"🚨 <b>ПЕРЕХВАЧЕН НЕГАТИВНЫЙ ОТЗЫВ!</b>\n\n"
            f"📍 Филиал: <b>{escaped_branch}</b>\n"
            f"⭐ Оценка клиента: {stars} ({rating}/5)\n"
            f"👤 Имя: <b>{escaped_author}</b>\n"
            f"📞 Контакт: <b>{escaped_contact}</b>\n\n"
            f"📝 <b>Содержание жалобы:</b>\n"
            f"<i>«{escaped_text}»</i>\n\n"
            f"❗️ Клиент не был допущен на публичные карты (Яндекс / 2GIS / Google / Авито). "
            f"Свяжитесь с клиентом для урегулирования ситуации!"
        )

        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send negative feedback alert to telegram user {telegram_id}: {e}")
            return False
