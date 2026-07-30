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

from bot import locales
from bot.config.settings import settings
from bot.database.models import Event, Translation, TranslationSignal
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal, engine
from bot.services.events import EventService, EventType
from bot.services.quota import QuotaService
from bot.services.translation import TranslationError, TranslationService
from bot.services.tts import TtsError, TtsService
from bot.keyboards.user import direction_label, main_menu, translation_actions
from bot.utils.text import chunk, chunk_html_safe, content_hash, html_escape

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

    # Tarjima `<code>` ichida yuboriladi — escape shart, aks holda matndagi
    # `<` yoki `&` butun xabarni buzadi (Telegram HTML'ni rad etadi).
    check("html escape: teg", html_escape("a <b> c") == "a &lt;b&gt; c")
    check("html escape: ampersand", html_escape("Tom & Jerry") == "Tom &amp; Jerry")

    # Escape matnni uzaytiradi, shuning uchun bo'laklash escape'dan keyingi
    # uzunlikni hisoblashi kerak.
    nasty = "&" * 3000
    safe = chunk_html_safe(nasty, 1000)
    check(
        "escape'dan keyin ham limitga sig'adi",
        all(len(html_escape(p)) <= 1000 for p in safe),
        f"{len(safe)} bo'lak",
    )
    check("bo'laklash matnni yo'qotmaydi", "".join(safe) == nasty)


async def test_locales() -> None:
    print("\n[2] Interfeys tillari")
    check("uch til mavjud", locales.SUPPORTED == {"uz", "id", "en"}, str(sorted(locales.SUPPORTED)))

    # Qoida: uz → uz, id → id, qolgani → en.
    rules = [
        ("uz", "uz"), ("uz-UZ", "uz"), ("id", "id"), ("id-ID", "id"),
        ("en", "en"), ("en-US", "en"), ("ru", "en"), ("pt-br", "en"),
        (None, "en"), ("", "en"),
    ]
    ok = all(locales.resolve(tg) == want for tg, want in rules)
    check("til aniqlash qoidasi", ok)

    # Har bir lokalda barcha kalitlar bor va format ishlaydi. Yarim tarjima
    # qilingan holat ishlab turgan botda `AttributeError` bo'lib chiqardi.
    for code in sorted(locales.SUPPORTED):
        t = locales.get(code)
        try:
            t.WELCOME.format(name="X", source="A", target="B")
            t.CONTACT_TOO_LONG.format(length=10, limit=5)
            t.CONTACT_RATE_LIMITED.format(minutes=7)
            t.TOO_LONG.format(length=10, limit=5)
            t.QUOTA_EXCEEDED.format(limit=50)
            t.LANG_SWAPPED.format(source="A", target="B")
            check(f"{code}: barcha satrlar formatlanadi", True)
        except (AttributeError, KeyError, IndexError) as exc:
            check(f"{code}: barcha satrlar formatlanadi", False, repr(exc))

    # Menyu tugmalari to'plami tarjima handleri uchun muhim: har bir tildagi
    # tugma matni "tarjima qilinadigan matn" deb qabul qilinmasligi kerak.
    check("menyu tugmalari 9 ta (3 til × 3 tugma)", len(locales.MENU_BUTTONS) == 9)
    for code in locales.SUPPORTED:
        t = locales.get(code)
        in_set = all(
            btn in locales.MENU_BUTTONS
            for btn in (t.BTN_LANGUAGES, t.BTN_CONTACT, t.BTN_HELP)
        )
        check(f"{code}: tugmalari filtrga kiradi", in_set)


async def test_languages() -> None:
    print("\n[3] Tillar ma'lumotnomasi")
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
    print("\n[4] Foydalanuvchi va sozlamalar")
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

        # Telegram tili yo'q edi — standart `en` bo'lishi kerak, `uz` emas.
        check(
            "interfeys tili standart en",
            user.settings.interface_lang == "en",
            user.settings.interface_lang,
        )

        return user.id


async def test_interface_lang_resolution() -> None:
    """Yangi user Telegram tiliga qarab to'g'ri interfeys tilini oladi."""
    print("\n[5] Interfeys tilini aniqlash")
    from sqlalchemy import delete

    from bot.database.models import User, UserSettings

    cases = [("uz", "uz"), ("id-ID", "id"), ("ru", "en"), ("de", "en")]
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        for offset, (telegram_lang, expected) in enumerate(cases, start=10):
            tg_id = TEST_TELEGRAM_ID + offset
            user, _ = await repo.get_or_create(tg_id, telegram_lang=telegram_lang)
            await session.commit()
            check(
                f"telegram_lang={telegram_lang} → {expected}",
                user.settings.interface_lang == expected,
                user.settings.interface_lang,
            )
            await session.execute(
                delete(UserSettings).where(UserSettings.user_id == user.id)
            )
            await session.execute(delete(User).where(User.id == user.id))
            await session.commit()


async def test_quota(user_id: int) -> None:
    print("\n[6] Kunlik limit")
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
    print("\n[7] Tarjima (tarmoq talab qiladi)")
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
    print("\n[8] Tarjima yozuvi va signallar")
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

        rows = await repo.recent(user_id, limit=10)
        check("eksport uchun o'qiladi", len(rows) == 1)

        # Ovoz eshitish — sifat signali. Sevimlilar olib tashlangan, lekin
        # `tts_played` va `retranslated` signallari qoladi.
        await repo.add_signal(
            translation_id=translation.id, user_id=user_id, signal="tts_played"
        )
        await session.commit()
        signals = (
            await session.execute(
                select(func.count(TranslationSignal.id)).where(
                    TranslationSignal.translation_id == translation.id
                )
            )
        ).scalar_one()
        check("sifat signali yozildi", signals == 1)

        recent = await repo.find_recent_by_hash(
            user_id, content_hash(text, "auto", "en"), within_seconds=60
        )
        check("qayta tarjima aniqlanadi", recent is not None and recent.id == translation.id)


async def test_events(user_id: int) -> None:
    print("\n[9] Voqealar jurnali")
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
        check("voqealar yozildi", total == 2, f"{total} ta")  # keyingi tekshiruvlar qo'shimcha yozadi

        # `user_id` bo'yicha filtrlash shart: ishlab turgan bazada jonli
        # foydalanuvchilarning `translate.succeeded` voqealari ham bor va
        # ularsiz sinov begona qatorni tekshirib qolardi.
        row = (
            await session.execute(
                select(Event)
                .where(
                    Event.user_id == user_id,
                    Event.event_type == EventType.TRANSLATE_SUCCEEDED,
                )
                .limit(1)
            )
        ).scalar_one()
        check("JSONB payload saqlandi", row.payload.get("latency_ms") == 123, str(row.payload))

        # `source` — `events` jadvalidagi ustun nomi va ayni paytda juda tabiiy
        # payload kaliti. Ilgari u jimgina ustunga yozilib CHECK cheklovini
        # buzgan va almashtirish tugmasini ishdan chiqargan edi.
        await events.log(
            EventType.LANG_SWAPPED,
            user_id=user_id,
            source="uz",
            target="en",
            from_lang="uz",
        )
        await session.commit()

        swapped = (
            await session.execute(
                select(Event)
                .where(Event.user_id == user_id, Event.event_type == EventType.LANG_SWAPPED)
                .limit(1)
            )
        ).scalar_one()
        check(
            "`source` payloadga tushadi, ustunga emas",
            swapped.payload.get("source") == "uz" and swapped.source == "bot",
            f"source ustuni={swapped.source}, payload={swapped.payload}",
        )

        # Noto'g'ri `event_source` baland ovozda yiqilishi kerak — bazadagi
        # CheckViolation butun tranzaksiyani yiqitadi, ya'ni voqea yozish
        # asosiy oqimni buzadi.
        try:
            await events.log(EventType.MENU_OPENED, user_id=user_id, event_source="xato")
            check("noto'g'ri event_source rad etiladi", False, "xato chiqmadi")
        except ValueError:
            check("noto'g'ri event_source rad etiladi", True)


async def test_tts() -> None:
    print("\n[10] Ovoz (tarmoq talab qiladi)")
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


async def test_keyboards() -> None:
    """Tarjima ostidagi tugmalar. Foydalanuvchi so'ragan tuzilma shu."""
    print("\n[11] Klaviaturalar")
    async with AsyncSessionLocal() as session:
        langs = LanguageRepository(session)
        auto = await langs.by_code("auto")
        uz = await langs.by_code("uz")
        en = await langs.by_code("en")

    for code in sorted(locales.SUPPORTED):
        t = locales.get(code)

        menu = main_menu(t)
        labels = [b.text for row in menu.keyboard for b in row]
        check(f"{code}: menyuda 3 tugma", len(labels) == 3, ", ".join(labels))
        check(f"{code}: murojaat tugmasi bor", t.BTN_CONTACT in labels)
        check(
            f"{code}: tarix va sozlama tugmasi yo'q",
            not any(
                w in x
                for x in labels
                for w in ("Tarix", "Histor", "Riwayat", "Sozlama", "Setting", "Pengaturan")
            ),
        )

        markup = translation_actions(t, 42, has_tts=True, source=auto, target=uz)
        rows = markup.inline_keyboard
        check(f"{code}: tarjima ostida 2 qator", len(rows) == 2)

        top = [b.callback_data for b in rows[0]]
        check(f"{code}: ovoz va almashtirish", top == ["tr:tts:42", "tr:swap:42"], str(top))
        check(
            f"{code}: sevimli tugmasi yo'q",
            not any(cb and cb.startswith("tr:fav") for cb in top),
        )

        bottom = rows[1][0]
        check(f"{code}: yo'nalish tugmasi tillar menyusini ochadi",
              bottom.callback_data == "tr:langs:42")
        # Yo'nalish tugma matnida ko'rinib turadi — almashtirgandan keyin
        # yangilanadi va ekranda qoladi.
        check(
            f"{code}: yo'nalish tugmada ko'rinadi",
            "→" in bottom.text and t.AUTO_DETECT in bottom.text,
            bottom.text,
        )

        # Ovoz yo'q bo'lsa faqat almashtirish qoladi.
        no_tts = translation_actions(t, 7, has_tts=False, source=uz, target=en)
        check(
            f"{code}: ovozsiz ham almashtirish bor",
            [b.callback_data for b in no_tts.inline_keyboard[0]] == ["tr:swap:7"],
        )

    # Til nomlari `name_native` dan olinadi — interfeys tilidan qat'i nazar.
    t_en, t_uz = locales.get("en"), locales.get("uz")
    check(
        "til nomi interfeys tiliga bog'liq emas",
        direction_label(t_en, uz, en) == direction_label(t_uz, uz, en),
        direction_label(t_en, uz, en),
    )


async def test_support() -> None:
    """Adminga murojaat: matn tayyorlash va spam cheklovi."""
    print("\n[12] Adminga murojaat")
    from types import SimpleNamespace

    from bot.handlers.user.support import _admin_view, _rate_limited

    # Foydalanuvchi matnidagi HTML buzmasligi kerak: `<` bo'lsa Telegram
    # xabarni butunlay rad etadi va murojaat adminga yetmasdi.
    fake = SimpleNamespace(
        username="tester", first_name="A <b>Bold</b>", telegram_id=123, telegram_lang="uz"
    )
    view = _admin_view(fake, "narx < 100 & shart")
    check("murojaat matni escape qilinadi", "&lt; 100 &amp; shart" in view)
    check("ism ham escape qilinadi", "A &lt;b&gt;Bold&lt;/b&gt;" in view)
    check("telegram id ko'rinadi", "<code>123</code>" in view)
    check("username ko'rinadi", "@tester" in view)

    # Username yo'q bo'lsa yiqilmasligi kerak.
    anon = SimpleNamespace(
        username=None, first_name=None, telegram_id=9, telegram_lang=None
    )
    check("username/ism yo'q bo'lsa ham ishlaydi", "—" in _admin_view(anon, "salom"))

    # Redis yo'q — cheklov fail-open (tarjima oqimidagi bilan bir xil qaror).
    check("redis yo'q bo'lsa cheklov o'tkazadi", await _rate_limited(None, 1) == 0)

    # Redis bilan: limitdan keyin qolgan daqiqa qaytadi.
    try:
        from bot.database.redis import get_redis

        client = get_redis()
        await client.ping()
    except Exception:
        print("  ⏭  Redis yo'q — cheklov sinovi o'tkazib yuborildi")
        return

    uid = 999_111_222
    await client.delete(f"support:{uid}")
    allowed = [await _rate_limited(client, uid) for _ in range(settings.SUPPORT_RATE_LIMIT)]
    check(
        f"birinchi {settings.SUPPORT_RATE_LIMIT} murojaat o'tadi",
        all(x == 0 for x in allowed),
        str(allowed),
    )
    blocked = await _rate_limited(client, uid)
    check("keyingisi cheklanadi", blocked > 0, f"{blocked} daqiqa")
    await client.delete(f"support:{uid}")
    await client.aclose()


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
    await test_locales()
    await test_languages()
    user_id = await test_user_flow()
    await test_interface_lang_resolution()
    await test_quota(user_id)
    result = await test_translation()
    await test_translation_record(user_id, result)
    await test_events(user_id)
    await test_tts()
    await test_keyboards()
    await test_support()

    await cleanup()
    await engine.dispose()

    print("\n" + "=" * 60)
    print(f"Natija: {passed} o'tdi, {failed} yiqildi")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
