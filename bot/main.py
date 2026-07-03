import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramNetworkError

from bot.config.settings import settings
from bot.database.session import init_db
from bot.handlers.user import start, common, subscription
from bot.handlers.admin import panel
from bot.middlewares.analytics import AnalyticsMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main():
    """Main function to start the bot"""

    # Initialize database
    logger.info("Initializing database...")
    await init_db()

    # Initialize bot and dispatcher
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()

    # Middlewares
    dp.message.middleware(AnalyticsMiddleware())

    # Register handlers
    dp.include_router(start.router)
    dp.include_router(subscription.router)
    dp.include_router(panel.router)
    dp.include_router(common.router)

    # Start bot
    logger.info("Bot started successfully!")
    try:
        while True:
            try:
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                break
            except TelegramNetworkError as exc:
                logger.warning("Telegram network error: %s. Reconnecting in 5s...", exc)
                await asyncio.sleep(5)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped!")
