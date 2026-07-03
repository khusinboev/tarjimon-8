from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from bot.services.user_service import UserService
from bot.database.session import AsyncSessionLocal

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command"""
    async with AsyncSessionLocal() as session:
        user_service = UserService(session)
        await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
        )

    welcome_text = (
        f"👋 Assalomu alaykum, {message.from_user.first_name}!\n\n"
        f"Botimizga xush kelibsiz!"
    )
    await message.answer(welcome_text)
