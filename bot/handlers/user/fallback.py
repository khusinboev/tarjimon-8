"""Noma'lum buyruqlar uchun oxirgi to'siq.

Muammo: `translate` routeri `/` bilan boshlangan matnni ataylab
o'tkazib yuboradi (buyruq tarjima qilinmasin), boshqa hech bir router
esa `/foo` kabi noma'lum buyruqni ushlamaydi — foydalanuvchi hech qanday
javob olmasdi va bot "o'lik" ko'rinardi.

Shu router `main.py`da ENG OXIRIDA (`translate`dan keyin) ulanadi. aiogram
xabarni routerlar bo'ylab ulanish tartibida o'tkazadi, ya'ni bu yerga
faqat HECH BIR boshqa handler ushlamagan xabar yetib keladi — mavjud
buyruqlar (`/start`, `/help`, admin `/admin`, `/report` va h.k.) bu
yerga hech qachon tushmaydi. Admin bo'lmagan odam `/admin` yozsa ham shu
yerga tushadi va oddiy "noma'lum buyruq" javobini oladi — panel borligi
oshkor qilinmaydi.

Faqat SHAXSIY chatda javob beriladi: guruhda boshqa botlarning
buyruqlariga (`/start@OtherBot`, `/ban`) javob berish spam bo'lardi.
"""

from __future__ import annotations

import re
from types import ModuleType

from aiogram import Router
from aiogram.types import Message

router = Router(name="fallback")

# Buyruq tokeni: `/` + bo'shliq, `/` va `@` bo'lmagan belgilar, ixtiyoriy
# `@BotName`, keyin bo'shliq yoki matn oxiri. `/usr/bin` kabi yo'l yoki
# yolg'iz `/` buyruq hisoblanmaydi.
_COMMAND_RE = re.compile(r"^/[^\s/@]+(?:@\S+)?(?:\s|$)")


def is_command_text(text: str | None) -> bool:
    """Matn buyruqqa o'xshaydimi (`/foo`, `/foo@Bot arg`)."""
    return bool(text) and _COMMAND_RE.match(text) is not None


def should_answer_unknown_command(text: str | None, chat_type: str | None) -> bool:
    """Sof mantiq (sinov uchun alohida): faqat shaxsiy chat + buyruq matni."""
    return chat_type == "private" and is_command_text(text)


# Filtr va sinov bitta funksiyadan foydalanadi — mantiq ikki joyda
# ajralib ketmasin. `chat.type` — `ChatType` (str enum), "private" bilan
# to'g'ridan-to'g'ri solishtiriladi.
@router.message(
    lambda message: should_answer_unknown_command(
        message.text, message.chat.type if message.chat else None
    )
)
async def unknown_command(message: Message, t: ModuleType) -> None:
    await message.answer(t.UNKNOWN_COMMAND)
