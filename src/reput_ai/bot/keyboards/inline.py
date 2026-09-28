from uuid import UUID
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_review_approval_keyboard(review_id: UUID) -> InlineKeyboardMarkup:
    """Generate 1-click inline button approval keyboard:

    [Approve] [Edit] [Regenerate]
    """
    review_str = str(review_id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"review:approve:{review_str}",
                ),
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"review:edit:{review_str}",
                ),
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=f"review:regenerate:{review_str}",
                ),
            ]
        ]
    )
    return keyboard
