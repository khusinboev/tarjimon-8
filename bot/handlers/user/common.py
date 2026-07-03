from aiogram import Router, F
from aiogram.types import Message
from bot.database.session import AsyncSessionLocal
from bot.services.subscription_service import SubscriptionService
from bot.keyboards.inline import get_subscription_keyboard

router = Router()


@router.message(F.text)
async def handle_text_message(message: Message):
    """Handle any text message from users"""
    async with AsyncSessionLocal() as session:
        service = SubscriptionService(session, message.bot)
        not_subscribed = await service.check_user_subscriptions(message.from_user.id)

    if not_subscribed:
        await message.answer(
            "❗️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'lishingiz kerak:",
            reply_markup=get_subscription_keyboard(not_subscribed),
        )
        return

    await message.answer(
        "📩 Xabaringiz qabul qilindi!\n"
        "Tez orada javob beramiz."
    )
