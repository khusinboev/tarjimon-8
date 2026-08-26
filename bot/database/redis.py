from redis.asyncio import Redis
from bot.config.settings import settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
        )
    return _client


# INCR+EXPIRE atomik oyna-hisoblagich (rate limiter) — ikkalasini bitta
# Lua skriptga birlashtiradi, chunki alohida `incr()` va shartli
# `expire()` chaqiruvi orasida uzilish (masalan tarmoq xatosi) bo'lsa,
# kalit TTL'siz qolib, o'sha foydalanuvchi/xona uchun oyna HECH QACHON
# tiklanmay, doimiy bloklangan holatga tushib qolishi mumkin edi.
# `EXPIRE key seconds NX` (Redis 7+) serverda yo'q (production — 6.0.16),
# shuning uchun Lua orqali: Redis bitta skriptni to'liq atomik bajaradi.
_RATE_INCR_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


async def atomic_rate_incr(redis: Redis, key: str, window_seconds: int) -> int:
    """`key`ni oshiradi, birinchi urinishda ATOMIK ravishda TTL qo'yadi.

    Chaqiruvchi natijani limit bilan solishtiradi. Redis xatosi bu yerda
    ushlanmaydi — fail-open siyosati chaqiruvchining o'zida (odatda
    `except Exception: return False/0`).
    """
    return await redis.eval(_RATE_INCR_SCRIPT, 1, key, window_seconds)
