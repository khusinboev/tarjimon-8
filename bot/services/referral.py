"""Referal bonuslari — do'stni taklif qilish uchun mukofot.

Oqim:
  1. Yangi user `/start <referrer_telegram_id>` orqali kirsa, `start.py`
     `user.referred_by` ni yozadi. Bu yerda hali mukofot YO'Q — faqat
     bog'lanish qayd etiladi.
  2. Yangi user birinchi marta muvaffaqiyatli tarjima qilganda (`translate.py`)
     shu modul chaqiriladi va ikkala tomonga VIP kun beriladi.

Bonusni `/start` da emas, birinchi tarjimadan keyin berish ataylab: aks
holda havolani ochib-yopib (botdan haqiqatan foydalanmasdan) mukofot yig'ish
juda oson bo'lardi. `referral_bonus_granted` bayrog'i takroriy berishning
oldini oladi.
"""

from __future__ import annotations

import logging

from aiogram.types import Message

from bot import locales
from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.user_repository import UserRepository
from bot.services.events import EventService, EventType
from bot.utils.formatters import format_datetime

logger = logging.getLogger(__name__)


async def grant_referral_bonus(
    message: Message,
    session,
    events: EventService,
    user: User,
    session_id,
) -> None:
    """Yangi userning birinchi tarjimasidan keyin ikkala tomonga VIP kun beradi.

    Xavfsiz — istalgan xatoda jimgina qaytadi, chunki bu qo'shimcha bonus
    mantig'i va asosiy tarjima oqimini buzmasligi kerak.
    """
    if user.referred_by is None or user.settings is None:
        return
    if user.settings.referral_bonus_granted:
        return

    repo = UserRepository(session)

    # Bayroqni DARHOL, ATOMIK ravishda qo'yamiz (bonus berilishidan oldin):
    # agar foydalanuvchi deyarli bir vaqtda 2 ta xabar yuborsa (masalan
    # tarjima navbatda turganda yana bittasini yuborsa — bu ikkita ALOHIDA
    # DB sessiyasida ishlanadi), faqat BITTASI `True` natija oladi —
    # ikkinchisi shu yerda to'xtaydi, referal bonusi 2 marta berilmaydi.
    granted = await repo.set_referral_bonus_granted(user.id)
    if not granted:
        return
    user.settings.referral_bonus_granted = True

    referrer = await repo.get_by_id(user.referred_by)
    if referrer is None:
        # Referrer o'chirilgan yoki topilmadi — yangi userga baribir kichik
        # xush kelibsiz bonusini beramiz, referrerga hech narsa bermaymiz.
        new_until = await repo.extend_premium(
            user.id, settings.REFERRAL_WELCOME_DAYS, max_days=settings.PREMIUM_MAX_DAYS
        )
        await events.log(
            EventType.REFERRAL_BONUS_GRANTED,
            user_id=user.id,
            session_id=session_id,
            referrer_id=None,
            welcome_days=settings.REFERRAL_WELCOME_DAYS,
            referrer_days=0,
        )
        if new_until:
            t = locales.get(user.settings.interface_lang)
            try:
                await message.answer(
                    t.REFERRAL_WELCOME_NOTICE.format(
                        days=settings.REFERRAL_WELCOME_DAYS,
                        until=format_datetime(new_until),
                    )
                )
            except Exception:
                logger.info("Xush kelibsiz bonusi xabarini yuborib bo'lmadi", exc_info=True)
        return

    new_user_until = await repo.extend_premium(
        user.id, settings.REFERRAL_WELCOME_DAYS, max_days=settings.PREMIUM_MAX_DAYS
    )
    referrer_until = await repo.extend_premium(
        referrer.id, settings.REFERRAL_BONUS_DAYS, max_days=settings.PREMIUM_MAX_DAYS
    )

    await events.log(
        EventType.REFERRAL_BONUS_GRANTED,
        user_id=user.id,
        session_id=session_id,
        referrer_id=referrer.id,
        welcome_days=settings.REFERRAL_WELCOME_DAYS,
        referrer_days=settings.REFERRAL_BONUS_DAYS,
    )

    # Yangi userga — shu javob ichida, alohida xabar sifatida.
    if new_user_until:
        t = locales.get(user.settings.interface_lang)
        try:
            await message.answer(
                t.REFERRAL_WELCOME_NOTICE.format(
                    days=settings.REFERRAL_WELCOME_DAYS,
                    until=format_datetime(new_user_until),
                )
            )
        except Exception:
            logger.info("Xush kelibsiz bonusi xabarini yuborib bo'lmadi", exc_info=True)

    # Referrerga — u boshqa chatda, shuning uchun alohida yuboriladi va
    # bloklagan/o'chirgan bo'lsa xato botni to'xtatmasligi kerak.
    if referrer_until:
        referrer_locale = locales.get(
            referrer.settings.interface_lang if referrer.settings else None
        )
        try:
            await message.bot.send_message(
                referrer.telegram_id,
                referrer_locale.REFERRAL_BONUS_NOTICE.format(
                    days=settings.REFERRAL_BONUS_DAYS,
                    until=format_datetime(referrer_until),
                ),
            )
        except Exception:
            logger.info(
                "Referal bonusi haqida %s ga xabar berib bo'lmadi",
                referrer.telegram_id,
                exc_info=True,
            )
