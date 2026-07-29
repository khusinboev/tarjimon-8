#!/usr/bin/env python3
"""Dud sinovi: asosiy oqim uchidan-uchiga ishlayaptimi.

Telegram'siz ishlaydi — repository, servis va baza qatlamlarini tekshiradi.
Ishga tushirish: python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from bot.database.models import Event, Translation, TranslationSignal
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal, engine
from bot.services.events import EventService, EventType
from bot.services.quota import QuotaService
from bot.services.translation import TranslationError, TranslationService
from bot.services.tts import TtsError, TtsService
from bot.utils.text import chunk, content_hash

TEST_TELEGRAM_ID = 999_000_001

passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {label}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))


async def test_text_utils() -> None:
    print("\n[1] Matn yordamchilari")
    h1 = content_hash("Salom  Dunyo", "auto", "en")
    h2 = content_hash("salom dunyo", "auto", "en")
    check("normallashtirish keshni birlashtiradi", h1 == h2)

    h3 = content_hash("salom dunyo", "auto", "ru")
    check("til juftligi hashga kiradi", h1 != h3)

    parts = chunk("Bir gap. " * 200, 500)
    check("uzun matn bo'linadi", len(parts) > 1, f"{len(parts)} bo'lak")
    check("har bir bo'lak limitdan kichik", all(len(p) <= 500 for p in parts))


async def test_languages() -> None:
    print("\n[2] Tillar ma'lumotnomasi")
    async with AsyncSessionLocal() as session:
        repo = LanguageRepository(session)
        LanguageRepository.invalidate_cache()

        langs = await repo.all_active()
        check("tillar yuklandi", len(langs) == 21, f"{len(langs)} ta")

        source_opts = await repo.selectable(include_auto=True)
        target_opts = await repo.selectable(include_auto=False)
        check("auto faqat manba ro'yxatida", len(source_opts) == len(target_opts) + 1)

        voice = await repo.tts_voice("uz")
        check("o'zbek ovozi bor", voice == "uz-UZ-MadinaNeural", str(voice))

        no_voice = await repo.tts_voice("ky")
        check("qirg'iz tilida ovoz yo'q", no_voice is None)


async def test_user_flow() -> None:
    print("\n[3] Foydalanuvchi va sozlamalar")
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)

        user, is_new = await repo.get_or_create(
            TEST_TELEGRAM_ID, username="sinov", first_name="Sinov"
        )
        await session.commit()
        check("yangi user yaratildi", is_new)
        check("sozlamalar avtomatik yaratildi", user.settings is not None)
        check(
            "standart yo'nalish auto→uz",
            user.settings.source_lang == "auto" and user.settings.target_lang == "uz",
        )

        same, is_new2 = await repo.get_or_create(
            TEST_TELEGRAM_ID, username="sinov", first_name="Sinov"
        )
        await session.commit()
        check("takroriy chaqiruv yangi user yaratmaydi", not is_new2 and same.id == user.id)

        return user.id


async def test_quota(user_id: int) -> None:
    print("\n[4] Kunlik limit")
    async with AsyncSessionLocal() as session:
        quota = QuotaService(session, redis=None)

        status = await quota.check_translation(user_id)
        check("boshlang'ich holat ruxsat", status.allowed and status.used == 0)

        for _ in range(3):
            await quota.consume_translation(user_id, chars=10)
        await session.commit()

        status = await quota.check_translation(user_id)
        check("hisob oshdi", status.used == 3, f"used={status.used}")

        status = await quota.check_translation(user_id, limit_override=3)
        check("limitga yetganda taqiqlanadi", not status.allowed)

        status = await quota.check_translation(user_id, limit_override=0)
        check("0 = cheksiz", status.allowed)


async def test_translation() -> None:
    print("\n[5] Tarjima (tarmoq talab qiladi)")
    service = TranslationService(redis=None)
    try:
        result = await service.translate("Salom dunyo", "auto", "en")
        check("tarjima qaytdi", bool(result.text), repr(result.text))
        check("kechikish o'lchandi", result.latency_ms >= 0, f"{result.latency_ms}ms")
        check("provayder yozildi", result.provider == "deep_translator")
    except TranslationError as exc:
        check("tarjima", False, f"{exc.code}: {exc}")
        return None

    # Xato yo'llari — bularning hammasi ishlab turgan botda tekshirilmagan.
    for label, args, expected in [
        ("bo'sh matn", ("", "auto", "en"), "empty_input"),
        ("bir xil til", ("test", "en", "en"), "same_language"),
        ("juda uzun", ("x" * 99_999, "auto", "en"), "too_long"),
    ]:
        try:
            await service.translate(*args)
            check(f"xato ushlanadi: {label}", False, "xato chiqmadi")
        except TranslationError as exc:
            check(f"xato ushlanadi: {label}", exc.code == expected, exc.code)

    return result


async def test_translation_record(user_id: int, result) -> None:
    print("\n[6] Tarjima yozuvi va signallar")
    if result is None:
        print("  ⏭  tarjima ishlamagani uchun o'tkazib yuborildi")
        return

    async with AsyncSessionLocal() as session:
        repo = TranslationRepository(session)
        text = "Salom dunyo"

        translation = await repo.create(
            user_id=user_id,
            chat_id=1,
            chat_type="private",
            input_kind="text",
            source_lang_requested="auto",
            source_lang_detected=result.source_lang_detected,
            target_lang="en",
            source_text=text,
            target_text=result.text,
            source_hash=content_hash(text, "auto", "en"),
            source_chars=len(text),
            target_chars=len(result.text),
            provider=result.provider,
            status="success",
            latency_ms=result.latency_ms,
        )
        await session.commit()
        check("tarjima saqlandi", translation.id is not None, f"id={translation.id}")

        history = await repo.history(user_id, limit=10)
        check("tarixda ko'rinadi", len(history) == 1)

        check("dastlab sevimli emas", not await repo.is_favorite(user_id, translation.id))

        await repo.add_signal(
            translation_id=translation.id, user_id=user_id, signal="favorited"
        )
        await session.commit()
        check("sevimliga qo'shildi", await repo.is_favorite(user_id, translation.id))

        favorites = await repo.favorites(user_id)
        check("sevimlilar ro'yxatida", len(favorites) == 1)

        await repo.add_signal(
            translation_id=translation.id, user_id=user_id, signal="unfavorited"
        )
        await session.commit()
        check("sevimlidan olindi", not await repo.is_favorite(user_id, translation.id))
        check("sevimlilar ro'yxati bo'shadi", len(await repo.favorites(user_id)) == 0)

        recent = await repo.find_recent_by_hash(
            user_id, content_hash(text, "auto", "en"), within_seconds=60
        )
        check("qayta tarjima aniqlanadi", recent is not None and recent.id == translation.id)


async def test_events(user_id: int) -> None:
    print("\n[7] Voqealar jurnali")
    async with AsyncSessionLocal() as session:
        events = EventService(session)
        await events.log(
            EventType.TRANSLATE_SUCCEEDED,
            user_id=user_id,
            chat_id=1,
            latency_ms=123,
            provider="deep_translator",
        )
        await events.log(EventType.MENU_OPENED, user_id=user_id, menu="settings")
        await session.commit()

        total = (
            await session.execute(
                select(func.count(Event.id)).where(Event.user_id == user_id)
            )
        ).scalar_one()
        check("voqealar yozildi", total == 2, f"{total} ta")

        row = (
            await session.execute(
                select(Event)
                .where(Event.event_type == EventType.TRANSLATE_SUCCEEDED)
                .limit(1)
            )
        ).scalar_one()
        check("JSONB payload saqlandi", row.payload.get("latency_ms") == 123, str(row.payload))


async def test_tts() -> None:
    print("\n[8] Ovoz (tarmoq talab qiladi)")
    service = TtsService(redis=None)
    try:
        result = await service.synthesize("Salom dunyo", "uz-UZ-MadinaNeural")
        check("audio yaratildi", len(result.audio) > 1000, f"{len(result.audio)} bayt")
        # MPEG ADTS freym sinxronizatsiyasi: 11 ta birlik bit (0xFF + yuqori 3 bit),
        # yoki ID3 tegi bilan boshlanishi mumkin.
        head = result.audio[:3]
        is_mp3 = head.startswith(b"ID3") or (
            head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
        )
        check("mp3 formati", is_mp3, head.hex())
    except TtsError as exc:
        check("TTS", False, f"{exc.code}: {exc}")

    try:
        await service.synthesize("x" * 5000, "uz-UZ-MadinaNeural")
        check("uzun matn rad etiladi", False)
    except TtsError as exc:
        check("uzun matn rad etiladi", exc.code == "too_long")


async def cleanup() -> None:
    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete

        from bot.database.models import DailyUsage, User

        user = (
            await session.execute(
                select(User).where(User.telegram_id == TEST_TELEGRAM_ID)
            )
        ).scalar_one_or_none()
        if user:
            await session.execute(
                delete(TranslationSignal).where(TranslationSignal.user_id == user.id)
            )
            await session.execute(delete(Event).where(Event.user_id == user.id))
            await session.execute(delete(Translation).where(Translation.user_id == user.id))
            await session.execute(delete(DailyUsage).where(DailyUsage.user_id == user.id))
            await session.delete(user)
            await session.commit()


async def main() -> None:
    print("=" * 60)
    print("Tarjimon-8 dud sinovi")
    print("=" * 60)

    await cleanup()

    await test_text_utils()
    await test_languages()
    user_id = await test_user_flow()
    await test_quota(user_id)
    result = await test_translation()
    await test_translation_record(user_id, result)
    await test_events(user_id)
    await test_tts()

    await cleanup()
    await engine.dispose()

    print("\n" + "=" * 60)
    print(f"Natija: {passed} o'tdi, {failed} yiqildi")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
