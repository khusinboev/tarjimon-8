from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.config.settings import settings
from bot.database.redis import get_redis
from bot.database.session import init_db
from bot.handlers.admin import panel
from bot.handlers.user import common, languages, start, subscription
from bot.handlers.user import support, translate, tts
from bot.middlewares.context import ContextMiddleware
from bot.middlewares.subscription import SubscriptionMiddleware

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def build_storage(redis):
    """FSM holatlari uchun Redis. Yo'q bo'lsa — xotira (restart'da yo'qoladi)."""
    if redis is None:
        logger.warning("Redis mavjud emas — FSM xotirada saqlanadi")
        return MemoryStorage()
    return RedisStorage.from_url(settings.REDIS_FSM_URL)


async def connect_redis():
    try:
        client = get_redis()
        await client.ping()
        logger.info("Redis ulandi")
        return client
    except Exception as exc:
        # Redis kesh va limit uchun. Usiz bot ishlaydi, faqat sekinroq.
        logger.warning("Redis ulanmadi (%s) — kesh va rate-limit o'chirilgan", exc)
        return None


async def main() -> None:
    logger.info("Ma'lumotlar bazasi tekshirilmoqda...")
    await init_db()

    redis = await connect_redis()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=await build_storage(redis))

    # Kontekst har bir update uchun bir marta: session, user, events, session_id.
    dp.update.outer_middleware(ContextMiddleware(redis))

    # Obuna tekshiruvi kontekstdan keyin — u `data["user"]` ga tayanadi.
    subscription_guard = SubscriptionMiddleware()
    dp.message.middleware(subscription_guard)
    dp.callback_query.middleware(subscription_guard)

    # Tartib muhim:
    #  - menyu routerlari (`languages`, `common`) `support` dan oldin: murojaat
    #    yozish holatida menyu tugmasi bosilsa u tugma sifatida ishlashi kerak,
    #    murojaat matni sifatida emas
    #  - `translate` eng oxirida: uning `F.text` filtri juda keng
    dp.include_router(start.router)
    dp.include_router(subscription.router)
    dp.include_router(languages.router)
    dp.include_router(common.router)
    dp.include_router(support.router)
    dp.include_router(tts.router)
    dp.include_router(panel.router)
    dp.include_router(translate.router)

    me = await bot.get_me()
    logger.info("Bot ishga tushdi: @%s (id=%s)", me.username, me.id)

    try:
        while True:
            try:
                await dp.start_polling(
                    bot, allowed_updates=dp.resolve_used_update_types()
                )
                break
            except TelegramNetworkError as exc:
                logger.warning("Tarmoq xatosi: %s. 5 soniyadan keyin qayta ulanish...", exc)
                await asyncio.sleep(5)
    finally:
        await bot.session.close()
        if redis is not None:
            await redis.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
