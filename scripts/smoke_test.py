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
from bot.services.events import EventService, EventType, utcnow
from bot.services.quota import QuotaService, resolve_limit_override
from bot.services.referral import grant_referral_bonus
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
    h2 = content_hash("Salom Dunyo", "auto", "en")
    check("bo'shliqlar normallashtirilib keshni birlashtiradi", h1 == h2)

    # Registr ENDI keshni birlashtirmaydi — avvalgi xato "US" (mamlakat)
    # va "us" (olmosh) kabi turli matnlarni bitta keshga tushirib, turli
    # foydalanuvchilarga bir-birining noto'g'ri tarjimasini qaytarardi.
    h_case = content_hash("salom dunyo", "auto", "en")
    check("registr keshni ENDI birlashtirmaydi (tuzatilgan xato)", h1 != h_case)

    h3 = content_hash("Salom Dunyo", "auto", "ru")
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
            t.HELP.format(limit=50, admin="someone")
            t.CONTACT_TOO_LONG.format(length=10, limit=5)
            t.TOO_LONG.format(length=10, limit=5)
            t.QUOTA_EXCEEDED.format(limit=50)
            t.LANG_SWAPPED.format(source="A", target="B")
            check(f"{code}: barcha satrlar formatlanadi", True)
        except (AttributeError, KeyError, IndexError) as exc:
            check(f"{code}: barcha satrlar formatlanadi", False, repr(exc))

    # Menyu tugmalari to'plami tarjima handleri uchun muhim: har bir tildagi
    # tugma matni "tarjima qilinadigan matn" deb qabul qilinmasligi kerak.
    check("menyu tugmalari 12 ta (3 til × 4 tugma)", len(locales.MENU_BUTTONS) == 12)
    for code in locales.SUPPORTED:
        t = locales.get(code)
        in_set = all(
            btn in locales.MENU_BUTTONS
            for btn in (t.BTN_LANGUAGES, t.BTN_DONATE, t.BTN_CONTACT, t.BTN_HELP)
        )
        check(f"{code}: tugmalari filtrga kiradi", in_set)


async def test_languages() -> None:
    print("\n[3] Tillar ma'lumotnomasi")
    async with AsyncSessionLocal() as session:
        repo = LanguageRepository(session)
        LanguageRepository.invalidate_cache()

        langs = await repo.all_active()
        codes = {lang.code for lang in langs}
        # Aniq songa bog'lamaymiz — til qo'shilganda sinov behuda yiqilardi.
        # Muhimi: asosiylari joyida va `auto` bor.
        check("tillar yuklandi", len(langs) >= 23, f"{len(langs)} ta")
        missing = {"auto", "uz", "ru", "en", "tr", "ar", "am", "om", "id"} - codes
        check("asosiy tillar joyida", not missing, f"yo'q: {missing}" if missing else "")

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

        row = await quota.usage.get(user_id)
        check("boshlang'ich holat bo'sh", row is None)

        for _ in range(3):
            status = await quota.try_reserve_translation(user_id, chars=10)
            check("band qilish muvaffaqiyatli", status.allowed, f"used={status.used}")
        await session.commit()

        row = await quota.usage.get(user_id)
        check(
            "hisob oshdi", row is not None and row.translations_count == 3,
            f"used={row.translations_count if row else None}",
        )

        status = await quota.try_reserve_translation(user_id, limit_override=3)
        check(
            "limitga yetganda taqiqlanadi", not status.allowed,
            f"used={status.used}, limit={status.limit}",
        )

        # `release_translation` haqiqatan ortga qaytaradimi (TOCTOU tuzatishi
        # bilan qo'shilgan yangi metod — muvaffaqiyatsiz urinish kvotani
        # sarflamasligi kerak).
        await quota.release_translation(user_id, chars=10)
        await session.commit()
        row = await quota.usage.get(user_id)
        check("release hisobni kamaytiradi", row.translations_count == 2, f"used={row.translations_count}")

        # Regressiya: `limit = limit_override or DEFAULT` Python'da `0` ni
        # yolg'on qiymat deb hisoblab, har doim DEFAULT'ga (50) tushib
        # qolgan edi. Usage hali DEFAULT'dan past bo'lgani uchun natija
        # tasodifan "allowed=True" chiqib, xatoni sinov ushlamagan edi.
        # Shu sababli bu yerda usage'ni ataylab DEFAULT'dan oshiramiz —
        # faqat shunda "0 = cheksiz" haqiqatan tekshiriladi.
        await quota.usage.increment(user_id, translations=settings.DAILY_TRANSLATION_LIMIT)
        await session.commit()
        status = await quota.try_reserve_translation(user_id, limit_override=0)
        check(
            "0 = cheksiz (usage DEFAULT'dan yuqori bo'lsa ham)",
            status.allowed,
            f"used={status.used}, limit={status.limit}",
        )

        # `None` — override yo'q, standart limit ishlaydi.
        status = await quota.try_reserve_translation(user_id, limit_override=None)
        check(
            "None = standart limit (cheksiz emas)",
            not status.allowed,
            f"used={status.used}, limit={status.limit}",
        )


async def test_premium_and_referral() -> None:
    """VIP (homiylik/referal) ustunlik tartibi va referal bonusi."""
    print("\n[6c] VIP va referal")
    from datetime import timedelta

    from sqlalchemy import delete

    from bot.database.models import Event, User, UserSettings

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 90, first_name="Vip")
        await session.commit()
        user_id = user.id

        # ── resolve_limit_override: ustunlik tartibi ──
        # Bu yerda faqat Python obyekti ustida ishlaymiz (bazaga yozmasdan) —
        # `resolve_limit_override` sof funksiya, `user.settings` dagi
        # qiymatlarni o'qiydi, xolos.

        # 1. Hech narsa yo'q -> None (chaqiruvchi standart limitni qo'llaydi).
        check(
            "override yo'q -> None",
            resolve_limit_override(user, kind="translation", vip_value=0) is None,
        )

        # 2. Faol VIP -> 0 (cheksiz).
        user.settings.premium_until = utcnow() + timedelta(days=1)
        check(
            "faol VIP -> cheksiz",
            resolve_limit_override(user, kind="translation", vip_value=0) == 0,
        )

        # 3. Tugagan VIP -> yana None (standart limitga qaytadi).
        user.settings.premium_until = utcnow() - timedelta(days=1)
        check(
            "tugagan VIP -> None",
            resolve_limit_override(user, kind="translation", vip_value=0) is None,
        )

        # 4. Qo'lda qo'yilgan override VIP'dan USTUN turadi (aniq admin qarori).
        user.settings.premium_until = utcnow() + timedelta(days=1)
        user.settings.daily_limit_override = 7
        check(
            "qo'lda override VIP'dan ustun",
            resolve_limit_override(user, kind="translation", vip_value=0) == 7,
        )
        # TTS uchun alohida ustun ishlatiladi, tarjima override'iga bog'liq emas.
        check(
            "TTS override alohida (hali yo'q -> VIP ishlaydi)",
            resolve_limit_override(user, kind="tts", vip_value=0) == 0,
        )
        user.settings.daily_limit_override = None

        # 5. Admin roli hammasidan ustun.
        user.role = "admin"
        user.settings.premium_until = None
        check(
            "admin roli -> har doim cheksiz",
            resolve_limit_override(user, kind="translation", vip_value=0) == 0,
        )
        user.role = "user"
        user.settings.premium_until = None
        await session.rollback()  # sof Python o'zgarishlar — bazaga yozilmaydi

        # ── extend_premium: stacking va max_days chegarasi ──
        first = await repo.extend_premium(user_id, 5, max_days=365)
        check("birinchi uzaytirish muvaffaqiyatli", first is not None)

        # Stacking: ikkinchi chaqiruv MAVJUD muddatga qo'shiladi, hozirgi
        # vaqtga emas — ikkinchi homiylik birinchisini "yeb qo'ymasligi" kerak.
        second = await repo.extend_premium(user_id, 5, max_days=365)
        gap_days = (second - first).days
        check(
            "stacking: ikkinchi muddat birinchisi ustiga qo'shiladi",
            4 <= gap_days <= 6,
            f"farq={gap_days} kun (birinchi={first}, ikkinchi={second})",
        )

        # max_days chegarasi: juda katta so'rov kesiladi.
        capped = await repo.extend_premium(user_id, 10_000, max_days=30)
        cap_days = (capped - utcnow()).days
        check(
            "max_days chegarasi ishlaydi",
            cap_days <= 30,
            f"{cap_days} kun (kutilgan <= 30)",
        )

        # days <= 0 -> hech narsa qilinmaydi.
        noop = await repo.extend_premium(user_id, 0, max_days=365)
        check("days=0 -> None, o'zgarish yo'q", noop is None)

        await session.commit()

    # ── Referal bonusi: end-to-end ──
    from types import SimpleNamespace

    class _FakeBot:
        async def send_message(self, *args, **kwargs) -> None:
            return None

    class _FakeMessage:
        def __init__(self) -> None:
            self.bot = _FakeBot()

        async def answer(self, *args, **kwargs) -> None:
            return None

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        referrer, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 91, first_name="Referrer")
        newbie, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 92, first_name="Newbie")
        await session.commit()

        # `newbie` shu sessiyaga bog'langan — atributni to'g'ridan-to'g'ri
        # o'zgartirish yetarli, `start.py` dagi bilan bir xil naqsh.
        newbie.referred_by = referrer.id
        await session.commit()

        events = EventService(session)
        fake_message = _FakeMessage()

        await grant_referral_bonus(fake_message, session, events, newbie, None)
        await session.commit()

        refreshed_newbie = await repo.get_by_id(newbie.id)
        refreshed_referrer = await repo.get_by_id(referrer.id)
        check(
            "yangi user VIP oldi",
            refreshed_newbie.settings.premium_until is not None
            and refreshed_newbie.settings.premium_until > utcnow(),
        )
        check(
            "referrer VIP oldi",
            refreshed_referrer.settings.premium_until is not None
            and refreshed_referrer.settings.premium_until > utcnow(),
        )
        check(
            "bayroq qo'yildi",
            refreshed_newbie.settings.referral_bonus_granted is True,
        )

        # Ikkinchi chaqiruv — bonus IKKINCHI marta berilmasligi kerak.
        referrer_until_before = refreshed_referrer.settings.premium_until
        await grant_referral_bonus(fake_message, session, events, refreshed_newbie, None)
        await session.commit()
        refreshed_referrer_again = await repo.get_by_id(referrer.id)
        check(
            "takroriy chaqiruv bonus bermaydi",
            refreshed_referrer_again.settings.premium_until == referrer_until_before,
        )

        await session.execute(
            delete(Event).where(Event.user_id.in_([newbie.id, referrer.id]))
        )
        await session.execute(
            delete(UserSettings).where(UserSettings.user_id.in_([newbie.id, referrer.id]))
        )
        await session.execute(delete(User).where(User.id.in_([newbie.id, referrer.id])))
        await session.commit()

    # Birinchi test blokidagi userni ham tozalaymiz.
    async with AsyncSessionLocal() as session:
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_image_translation() -> None:
    """Rasm tarjimasi: alohida kvota, admin-sozlanadigan umumiy limitlar, OCR provayder tanlovi."""
    print("\n[6d] Rasm tarjimasi va umumiy limitlar")
    from datetime import timedelta

    from sqlalchemy import delete

    from bot.database.models import DailyUsage, SystemSettings, User, UserSettings
    from bot.services import ocr as ocr_module
    from bot.services import system_config

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 95, first_name="ImgUser")
        await session.commit()
        user_id = user.id

        # ── resolve_limit_override: rasm uchun VIP CHEKSIZ emas, faqat
        # kengaytirilgan (`vip_value`) — OCR pulga tushishi mumkin.
        check(
            "override yo'q -> None",
            resolve_limit_override(user, kind="image", vip_value=15) is None,
        )
        user.settings.premium_until = utcnow() + timedelta(days=1)
        check(
            "faol VIP -> vip_value (cheksiz emas, kengaytirilgan)",
            resolve_limit_override(user, kind="image", vip_value=15) == 15,
        )
        user.settings.image_limit_override = 2
        check(
            "qo'lda override VIP'dan ustun",
            resolve_limit_override(user, kind="image", vip_value=15) == 2,
        )
        user.role = "admin"
        check(
            "admin -> cheksiz (0)",
            resolve_limit_override(user, kind="image", vip_value=15) == 0,
        )
        user.role = "user"
        user.settings.image_limit_override = None
        user.settings.premium_until = None
        await session.rollback()  # sof Python o'zgarishlar — bazaga yozilmaydi

    async with AsyncSessionLocal() as session:
        quota = QuotaService(session, redis=None)

        row = await quota.usage.get(user_id)
        check("boshlang'ich holat bo'sh", row is None)

        for _ in range(3):
            status = await quota.try_reserve_image(user_id)
            check("rasm band qilish muvaffaqiyatli", status.allowed)
        await session.commit()

        status = await quota.try_reserve_image(user_id, limit_override=3)
        check("rasm limitiga yetganda taqiqlanadi", not status.allowed)

        # Rasm sarfi matn/ovoz hisoblagichiga umuman tegmasligi kerak —
        # bular butunlay alohida o'q (`images_count` vs `translations_count`).
        row = await quota.usage.get(user_id)
        check(
            "rasm sarfi matn hisobiga ta'sir qilmaydi",
            row is not None and row.translations_count == 0,
            f"matn used={row.translations_count if row else None}",
        )

        await session.execute(delete(DailyUsage).where(DailyUsage.user_id == user_id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()

    # ── system_config: admin-sozlanadigan umumiy limitlar ──
    system_config.invalidate_cache()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(SystemSettings).where(SystemSettings.id == 1))
        await session.commit()

        limits = await system_config.get_effective_limits(session)
        check(
            "sozlanmagan holatda .env standarti",
            limits.translation == settings.DAILY_TRANSLATION_LIMIT
            and limits.image_free == settings.DAILY_IMAGE_LIMIT_FREE,
        )

        await system_config.set_limit(session, "daily_image_limit_free", 0)
        await session.commit()
        limits = await system_config.get_effective_limits(session)
        check(
            "0 = hammaga cheksiz (falsy-zero xatosisiz)",
            limits.image_free == 0,
            f"image_free={limits.image_free}",
        )

        await system_config.set_limit(session, "daily_image_limit_free", None)
        await session.commit()
        limits = await system_config.get_effective_limits(session)
        check(
            "None = .env standartiga qaytadi",
            limits.image_free == settings.DAILY_IMAGE_LIMIT_FREE,
        )

        await session.execute(delete(SystemSettings).where(SystemSettings.id == 1))
        await session.commit()
    system_config.invalidate_cache()

    # ── ocr.py: ko'p kalitli navbat, tanlov va fallback (tarmoqsiz —
    # chaqiruvlar almashtiriladi, `_ocrspace_key_usage` ham soxtalashtiriladi,
    # chunki haqiqiy oylik hisob uchun `translations` jadvaliga qator
    # kerak bo'lardi) ──
    original_ocrspace_keys_raw = settings.OCRSPACE_API_KEYS_RAW
    original_ocrspace_key = settings.OCRSPACE_API_KEY
    original_vision_key = settings.GOOGLE_VISION_API_KEY
    original_key_usage = ocr_module._ocrspace_key_usage
    original_call_ocrspace = ocr_module._call_ocrspace
    original_call_vision = ocr_module._call_google_vision
    key_usage: dict[int, int] = {}

    async def _fake_key_usage(_session) -> dict:
        return dict(key_usage)

    async def _fake_ocrspace(_image_bytes: bytes, api_key: str) -> str:
        return f"ocrspace matni ({api_key})"

    async def _fake_vision(_image_bytes: bytes) -> str:
        return "vision matni"

    async def _fake_ocrspace_first_key_fails(_image_bytes: bytes, api_key: str) -> str:
        if api_key == "keyA":
            raise ocr_module.OcrError("provider_error", "test xatosi")
        return f"ocrspace matni ({api_key})"

    ocr_module._ocrspace_key_usage = _fake_key_usage
    ocr_module._call_ocrspace = _fake_ocrspace
    ocr_module._call_google_vision = _fake_vision

    try:
        async with AsyncSessionLocal() as session:
            # Ikkalasi ham sozlanmagan -> not_configured.
            settings.OCRSPACE_API_KEYS_RAW = ""
            settings.OCRSPACE_API_KEY = ""
            settings.GOOGLE_VISION_API_KEY = ""
            try:
                await ocr_module.extract_text(session, b"x")
                check("ikkalasi ham yo'q -> xato", False, "OcrError kutilgan edi")
            except ocr_module.OcrError as exc:
                check("ikkalasi ham yo'q -> not_configured", exc.code == "not_configured")

            # Uch ta OCR.Space kaliti sozlangan, ikkalasi ham hali
            # ishlatilmagan -> birinchisi (indeks 0) tanlanadi.
            settings.OCRSPACE_API_KEYS_RAW = "keyA,keyB,keyC"
            settings.GOOGLE_VISION_API_KEY = ""
            key_usage.clear()
            result = await ocr_module.extract_text(session, b"x")
            check(
                "hech biri ishlatilmagan -> 0-indeks tanlanadi",
                result.provider == "ocrspace" and result.key_index == 0,
                f"key_index={result.key_index}",
            )

            # 0-kalit ko'proq ishlatilgan -> kam ishlatilgan (1-indeks) tanlanadi.
            key_usage[0] = 100
            key_usage[1] = 5
            key_usage[2] = 5
            result = await ocr_module.extract_text(session, b"x")
            check(
                "kam ishlatilgan kalit ustunlik qiladi",
                result.key_index == 1,
                f"key_index={result.key_index}",
            )

            # 0 va 1-kalit bepul hajmidan oshgan -> faqat 2-kalit qoladi.
            key_usage[0] = settings.OCRSPACE_FREE_MONTHLY
            key_usage[1] = settings.OCRSPACE_FREE_MONTHLY
            key_usage[2] = 10
            result = await ocr_module.extract_text(session, b"x")
            check(
                "bepul hajmi tuganganlar chetlanadi",
                result.key_index == 2,
                f"key_index={result.key_index}",
            )

            # Barcha OCR.Space kalitlari tugagan, Vision sozlanmagan ->
            # quota_exhausted (not_configured emas — farqi bor).
            key_usage[0] = key_usage[1] = key_usage[2] = settings.OCRSPACE_FREE_MONTHLY
            try:
                await ocr_module.extract_text(session, b"x")
                check("hammasi tugagan -> xato", False, "OcrError kutilgan edi")
            except ocr_module.OcrError as exc:
                check(
                    "hammasi tugagan, Vision yo'q -> quota_exhausted",
                    exc.code == "quota_exhausted",
                    exc.code,
                )

            # Endi Vision ham sozlangan -> OCR.Space tugagach avtomatik
            # Vision'ga o'tadi.
            settings.GOOGLE_VISION_API_KEY = "test-key"
            result = await ocr_module.extract_text(session, b"x")
            check(
                "OCR.Space tugasa Vision'ga o'tadi",
                result.provider == "google_vision" and result.text == "vision matni",
                result.provider,
            )

            # Tanlangan (0-indeks, "keyA") kalit xato bersa — boshqa TAYYOR
            # OCR.Space kalitiga o'tiladi, darhol Vision'ga sakramaydi.
            key_usage.clear()
            ocr_module._call_ocrspace = _fake_ocrspace_first_key_fails
            result = await ocr_module.extract_text(session, b"x")
            check(
                "birinchi kalit yiqilsa ikkinchi OCR.Space kalitiga o'tadi",
                result.provider == "ocrspace" and result.key_index == 1,
                f"provider={result.provider}, key_index={result.key_index}",
            )
    finally:
        settings.OCRSPACE_API_KEYS_RAW = original_ocrspace_keys_raw
        settings.OCRSPACE_API_KEY = original_ocrspace_key
        settings.GOOGLE_VISION_API_KEY = original_vision_key
        ocr_module._ocrspace_key_usage = original_key_usage
        ocr_module._call_ocrspace = original_call_ocrspace
        ocr_module._call_google_vision = original_call_vision


async def test_admin_users() -> None:
    """Admin: qidirish, limit, ban/unban va audit yozuvi."""
    print("\n[6b] Admin foydalanuvchi boshqaruvi")
    from sqlalchemy import delete

    from bot.database.models import AdminAction, User, UserSettings
    from bot.services.admin_service import AdminService

    target_tg = TEST_TELEGRAM_ID + 77
    admin_tg = TEST_TELEGRAM_ID + 78

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        target, _ = await repo.get_or_create(
            target_tg, username="smoke_target", first_name="Target"
        )
        admin, _ = await repo.get_or_create(
            admin_tg, username="smoke_admin", first_name="Admin"
        )
        await session.commit()
        target_id, admin_id = target.id, admin.id

        service = AdminService(session)
        by_tg = await service.find_users(str(target_tg))
        check("telegram_id bilan topiladi", len(by_tg) == 1 and by_tg[0].id == target_id)

        by_name = await service.find_users("@smoke_target")
        check("username bilan topiladi", len(by_name) == 1 and by_name[0].id == target_id)

        # Ichki id: avval telegram_id deb qarab ko'riladi. To'qnash bo'lmasa
        # ichki id yo'li ishlashi kerak.
        if await repo.get_by_telegram_id(target_id) is None:
            by_db = await service.find_users(str(target_id))
            check(
                "ichki id bilan topiladi",
                len(by_db) == 1 and by_db[0].id == target_id,
            )
        else:
            print("  ⏭  ichki id telegram_id bilan to'qnashdi — o'tkazib yuborildi")

        ok, msg = await service.set_user_limit(admin_id, target_id, 12)
        check("limit o'rnatiladi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "limit saqlanadi",
            refreshed.settings.daily_limit_override == 12,
            str(refreshed.settings.daily_limit_override),
        )

        ok, msg = await service.set_user_limit(admin_id, target_id, 0)
        check("0 = cheksiz saqlanadi", ok and refreshed is not None, msg)
        refreshed = await repo.get_by_id(target_id)
        check("cheksiz qiymat 0", refreshed.settings.daily_limit_override == 0)

        # Faqat DB qiymatini emas, haqiqiy quota tekshiruvini ham tasdiqlaymiz —
        # aynan shu yo'l orqali `translate.py` limitni qo'llaydi. Usage'ni
        # ataylab DEFAULT'dan oshiramiz, aks holda "allowed=True" tasodifan
        # ham chiqishi mumkin edi.
        quota = QuotaService(session, redis=None)
        await quota.usage.increment(target_id, translations=settings.DAILY_TRANSLATION_LIMIT)
        await session.commit()
        status = await quota.try_reserve_translation(
            target_id, limit_override=refreshed.settings.daily_limit_override
        )
        check(
            "admin panelidan qo'yilgan 0-limit haqiqatan cheksiz",
            status.allowed,
            f"used={status.used}, limit={status.limit}",
        )

        ok, msg = await service.clear_user_limit(admin_id, target_id)
        check("limit tozalanadi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "override NULL",
            refreshed.settings.daily_limit_override is None,
        )

        ok, msg = await service.set_user_tts_limit(admin_id, target_id, 7)
        check("ovoz limiti o'rnatiladi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "ovoz limiti saqlanadi",
            refreshed.settings.tts_limit_override == 7,
            str(refreshed.settings.tts_limit_override),
        )

        ok, msg = await service.clear_user_tts_limit(admin_id, target_id)
        check("ovoz limiti tozalanadi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "ovoz limiti override NULL",
            refreshed.settings.tts_limit_override is None,
        )

        ok, msg = await service.set_user_image_limit(admin_id, target_id, 5)
        check("rasm limiti o'rnatiladi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "rasm limiti saqlanadi",
            refreshed.settings.image_limit_override == 5,
            str(refreshed.settings.image_limit_override),
        )

        ok, msg = await service.clear_user_image_limit(admin_id, target_id)
        check("rasm limiti tozalanadi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check(
            "rasm limiti override NULL",
            refreshed.settings.image_limit_override is None,
        )

        ok, msg = await service.ban_user(admin_id, target_id)
        check("ban qilinadi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check("status banned", refreshed.status == "banned")

        # Qayta ban — rad.
        ok, _ = await service.ban_user(admin_id, target_id)
        check("qayta ban rad etiladi", not ok)

        ok, msg = await service.unban_user(admin_id, target_id)
        check("unban qilinadi", ok, msg)
        refreshed = await repo.get_by_id(target_id)
        check("status active", refreshed.status == "active")

        # Adminni ban qilib bo'lmasligi kerak.
        await repo.set_role(admin_id, "admin")
        await session.commit()
        ok, _ = await service.ban_user(admin_id, admin_id)
        check("admin ban qilinmaydi", not ok)

        card = await service.format_user_card(await repo.get_by_id(target_id))
        check("kartochkada telegram id bor", str(target_tg) in card)
        check("kartochkada holat bor", "Holat:" in card)
        check("kartochkada VIP qatori bor", "VIP:" in card)
        check("kartochkada ovoz limiti bor", "Ovoz limiti:" in card)
        check("kartochkada rasm limiti bor", "Rasm limiti:" in card)
        check("kartochkada taklif soni bor", "Taklif qilganlari:" in card)

        actions = (
            await session.execute(
                select(func.count(AdminAction.id)).where(
                    AdminAction.admin_id == admin_id
                )
            )
        ).scalar_one()
        check("audit yozuvlari bor", actions >= 8, f"{actions} ta")

        recent = await service.format_recent_actions(limit=20)
        check("audit ro'yxati formatlanadi", len(recent) > 0)
        check("audit ro'yxatida admin username bor", "@smoke_admin" in recent)
        check("audit ro'yxatida amal nomi bor", "Ovoz limiti" in recent)

        # Tozalash
        from bot.database.models import DailyUsage

        await session.execute(
            delete(AdminAction).where(AdminAction.admin_id.in_([admin_id, target_id]))
        )
        await session.execute(delete(DailyUsage).where(DailyUsage.user_id.in_([admin_id, target_id])))
        await session.execute(delete(UserSettings).where(UserSettings.user_id.in_([admin_id, target_id])))
        await session.execute(delete(User).where(User.id.in_([admin_id, target_id])))
        await session.commit()


async def test_translation() -> None:
    print("\n[7] Tarjima (tarmoq talab qiladi)")

    # Regressiya: Google'ning xato sahifasi aniqlanishi kerak — birinchi
    # urinishda apostrof ASCII (') bilan yozilgan edi, Google esa
    # tipografik (’, U+2019) ishlatadi, shuning uchun tekshiruv HECH NARSANI
    # ushlamagan edi (qo'lda real javob bilan tasdiqlanmaguncha sezilmadi).
    from bot.services.translation import _looks_like_google_error_page

    real_google_500 = (
        "Error 500 (Server Error)!!1500.That’s an error."
        "There was an error. Please try again later.That’s all we know."
    )
    check(
        "Google xato sahifasi (tipografik apostrof) aniqlanadi",
        _looks_like_google_error_page(real_google_500),
    )
    check(
        "Google xato sahifasi (oddiy ASCII apostrof) aniqlanadi",
        _looks_like_google_error_page(real_google_500.replace("’", "'")),
    )
    check(
        "haqiqiy tarjima xato deb hisoblanmaydi",
        not _looks_like_google_error_page("Hello World"),
    )

    service = TranslationService(redis=None)
    async with AsyncSessionLocal() as session:
        try:
            result = await service.translate(session, "Salom dunyo", "auto", "en")
            check("tarjima qaytdi", bool(result.text), repr(result.text))
            check("kechikish o'lchandi", result.latency_ms >= 0, f"{result.latency_ms}ms")
            # Muhitga qarab (server'da haqiqiy pullik kalitlar bor, lokal
            # sinovda esa yo'q) har xil provayder ishlatilishi mumkin — muhim
            # narsa qaysi biri emas, balki BIRORTASI yozilgani.
            check(
                "provayder yozildi",
                result.provider in (
                    "deep_translator", "google_translate", "azure_translator",
                    "deepl", "mymemory", "gemini", "local_mt",
                ),
                result.provider,
            )
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
                await service.translate(session, *args)
                check(f"xato ushlanadi: {label}", False, "xato chiqmadi")
            except TranslationError as exc:
                check(f"xato ushlanadi: {label}", exc.code == expected, exc.code)

    return result


async def test_translation_providers() -> None:
    """Uch darajali provayder zanjiri: 1) Google/Azure — ko'p kalitli
    tanlov, tasodifiy almashish, bepul hajmi tugasa chetlanadi; 2) Gemini —
    1-daraja tugagach/yo'q bo'lsa, hajm kuzatilmasdan tasodifiy kalit bilan;
    3) ikkalasi ham yo'q/tugagan/xato bo'lsa `deep_translator`ga qaytish.

    OCR.Space ko'p kalitli navbati bilan bir xil naqsh (`bot/services/ocr.py`),
    faqat so'rov soni emas, BELGI soni bo'yicha hisoblanadi (faqat 1-daraja).
    """
    print("\n[7b] Tarjima — ko'p darajali provayder zanjiri")
    from sqlalchemy import delete

    from bot.database.models import Translation, User, UserSettings
    from bot.services import translation_providers as tp
    from bot.services.translation import TranslationService

    original_google_keys = settings.GOOGLE_TRANSLATE_API_KEYS_RAW
    original_azure_keys = settings.AZURE_TRANSLATOR_KEYS_RAW
    original_deepl_keys = settings.DEEPL_API_KEYS_RAW
    original_mymemory_emails = settings.MYMEMORY_EMAILS_RAW
    original_gemini_keys = settings.GEMINI_API_KEYS_RAW
    original_usage_fn = tp.provider_key_usage
    original_google_call = tp.FREE_TIER_PROVIDER_CONFIG["google_translate"]["call"]
    original_azure_call = tp.FREE_TIER_PROVIDER_CONFIG["azure_translator"]["call"]
    original_deepl_call = tp.FREE_TIER_PROVIDER_CONFIG["deepl"]["call"]
    original_mymemory_call = tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"]
    original_gemini_call = tp.CHEAP_PROVIDER_CONFIG["gemini"]["call"]
    original_local_url = settings.LOCAL_MT_URL
    original_local_priority = settings.LOCAL_MT_PRIORITY
    original_local_call = tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"]
    original_deep_enabled = settings.DEEP_TRANSLATOR_ENABLED
    # Quyidagi sinovlar lokal servis O'CHIQ deb yozilgan; uniki alohida blokda.
    settings.LOCAL_MT_URL = ""
    # 3-daraja o'chiq (standart) — haqiqiy tarmoq chaqiruvi bo'lmasin,
    # "hamma tugadi" holati darhol TranslationError bilan yakunlanishi kutiladi.
    settings.DEEP_TRANSLATOR_ENABLED = False

    usage_by_provider: dict[str, dict[int, int]] = {}

    async def fake_usage(_session, provider: str, _period: str = "month") -> dict:
        return dict(usage_by_provider.get(provider, {}))

    async def fake_google(_text, _source, _target, api_key) -> str:
        return f"google:{api_key}"

    async def fake_azure(_text, _source, _target, api_key) -> str:
        return f"azure:{api_key}"

    async def fake_deepl(_text, _source, _target, api_key) -> str:
        return f"deepl:{api_key}"

    async def fake_mymemory(_text, _source, _target, api_key) -> str:
        return f"mymemory:{api_key}"

    async def fake_google_fails(_text, _source, _target, _api_key) -> str:
        raise RuntimeError("test xatosi")

    async def fake_gemini(_text, _source, _target, api_key) -> str:
        return f"gemini:{api_key}"

    async def fake_gemini_fails(_text, _source, _target, _api_key) -> str:
        raise RuntimeError("test xatosi")

    tp.provider_key_usage = fake_usage
    tp.FREE_TIER_PROVIDER_CONFIG["google_translate"]["call"] = fake_google
    tp.FREE_TIER_PROVIDER_CONFIG["azure_translator"]["call"] = fake_azure
    tp.FREE_TIER_PROVIDER_CONFIG["deepl"]["call"] = fake_deepl
    tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = fake_mymemory
    tp.CHEAP_PROVIDER_CONFIG["gemini"]["call"] = fake_gemini
    service = TranslationService(redis=None)

    try:
        async with AsyncSessionLocal() as session:
            settings.GOOGLE_TRANSLATE_API_KEYS_RAW = ""
            settings.AZURE_TRANSLATOR_KEYS_RAW = ""
            settings.DEEPL_API_KEYS_RAW = ""
            settings.MYMEMORY_EMAILS_RAW = ""
            settings.GEMINI_API_KEYS_RAW = ""
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            check("kalit yo'q -> nomzod yo'q", candidates == [])

            settings.GOOGLE_TRANSLATE_API_KEYS_RAW = "gkey1,gkey2"
            settings.AZURE_TRANSLATOR_KEYS_RAW = "akey1"
            usage_by_provider.clear()
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            names = {c[0] for c in candidates}
            check(
                "ikkalasi ham nomzod (tekin hajmda)",
                names == {"google_translate", "azure_translator"},
                str(candidates),
            )
            google_pick = next(c for c in candidates if c[0] == "google_translate")
            check("kam ishlatilgan (birinchi) kalit tanlanadi", google_pick[1] == 0, str(google_pick))

            usage_by_provider["google_translate"] = {
                0: settings.GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS
            }
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            google_pick = next(c for c in candidates if c[0] == "google_translate")
            check("bepul hajmi tugagan kalit chetlanadi", google_pick[1] == 1, str(google_pick))

            usage_by_provider["google_translate"] = {
                0: settings.GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS,
                1: settings.GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS,
            }
            usage_by_provider["azure_translator"] = {0: settings.AZURE_TRANSLATOR_FREE_MONTHLY_CHARS}
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            check("hamma kalit tugagan -> nomzod yo'q", candidates == [])

            # 1-daraja (Google/Azure) tugagan, Gemini ham sozlanmagan, 3-daraja
            # o'chiq (DEEP_TRANSLATOR_ENABLED=false) — darhol `no_provider`.
            try:
                _, provider_name, _, _ = await service._dispatch(session, "salom", "auto", "en")
                check("hamma pullik tugagan, Gemini yo'q, zaxira o'chiq -> xato", False, provider_name)
            except TranslationError as exc:
                check(
                    "hamma pullik tugagan, Gemini yo'q, zaxira o'chiq -> no_provider",
                    exc.code == "no_provider",
                    exc.code,
                )

            # 2-daraja: 1-daraja hali tugagan holatda Gemini sozlansa, shu ishlatiladi.
            settings.GEMINI_API_KEYS_RAW = "gemkey1"
            text_out, provider_name, key_index, _ = await service._dispatch(session, "salom", "auto", "en")
            check(
                "1-daraja tugagan, Gemini sozlangan -> Gemini ishlatiladi",
                provider_name == "gemini" and key_index == 0 and text_out == "gemini:gemkey1",
                f"{provider_name}, {key_index}, {text_out}",
            )

            # Gemini ham ishlamasa (xato), zaxira o'chiq -> xatoda Gemini ko'rinadi.
            tp.CHEAP_PROVIDER_CONFIG["gemini"]["call"] = fake_gemini_fails
            try:
                _, provider_name, _, _ = await service._dispatch(session, "salom", "auto", "en")
                check("Gemini ham ishlamasa, zaxira o'chiq -> xato", False, provider_name)
            except TranslationError as exc:
                check(
                    "Gemini ham ishlamasa, zaxira o'chiq -> provider_error (gemini)",
                    exc.code == "provider_error" and exc.provider == "gemini",
                    f"{exc.code}, {exc.provider}",
                )
            tp.CHEAP_PROVIDER_CONFIG["gemini"]["call"] = fake_gemini
            settings.GEMINI_API_KEYS_RAW = ""

            usage_by_provider["azure_translator"] = {}
            text_out, provider_name, key_index, _ = await service._dispatch(session, "salom", "auto", "en")
            check(
                "faqat Azure tekin qolgan -> aniq Azure tanlanadi",
                provider_name == "azure_translator" and key_index == 0 and text_out == "azure:akey1",
                f"{provider_name}, {key_index}, {text_out}",
            )

            usage_by_provider.clear()
            tp.FREE_TIER_PROVIDER_CONFIG["google_translate"]["call"] = fake_google_fails
            seen = set()
            for _ in range(5):
                _, provider_name, _, _ = await service._dispatch(session, "salom", "auto", "en")
                seen.add(provider_name)
            check(
                "google ishlamasa (xato) azure'ga o'tadi, deep_translator'ga emas",
                seen == {"azure_translator"},
                str(seen),
            )
            # Nomzodlar tartibi tasodifiy — google har safar sinalmaydi, shuning
            # uchun breaker'ni ANIQ 3 ta ketma-ket xato bilan tekshiramiz.
            for _ in range(3):
                await service._attempt(
                    "google_translate", 0, "gkey1", fake_google_fails, "salom", "auto", "en"
                )
            check(
                "3 ketma-ket xatodan keyin google breaker'i ochiq",
                await service.breaker.is_open("google_translate", 0),
            )
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            check(
                "breaker ochiq kalit nomzodlar ro'yxatiga kirmaydi",
                all(c[0] != "google_translate" for c in candidates),
                str(candidates),
            )

            # ── DeepL: til qo'llab-quvvatlash filtri ──
            settings.GOOGLE_TRANSLATE_API_KEYS_RAW = ""
            settings.DEEPL_API_KEYS_RAW = "dkey1:fx"
            names = {c[0] for c in await service._pick_free_tier_candidates(session, "auto", "en", 5)}
            check("DeepL en uchun nomzod", "deepl" in names, str(names))
            names = {c[0] for c in await service._pick_free_tier_candidates(session, "en", "uz", 5)}
            check("DeepL uz uchun nomzod (2026: qo'llab-quvvatlaydi)", "deepl" in names, str(names))
            names = {c[0] for c in await service._pick_free_tier_candidates(session, "auto", "am", 5)}
            check("DeepL amhar uchun NOMZOD EMAS (xato ham emas)", "deepl" not in names and "azure_translator" in names, str(names))
            check("DeepL breaker'i tegilmagan", not await service.breaker.is_open("deepl", 0))

            # ── MyMemory: past ustunlik + 500 belgi chegarasi + kunlik davr ──
            settings.MYMEMORY_EMAILS_RAW = "smoke@example.com"
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            check(
                "MyMemory faqat asosiylardan KEYIN (oxirgi o'rinda)",
                candidates and candidates[-1][0] == "mymemory"
                and all(c[0] != "mymemory" for c in candidates[:-1]),
                str(candidates),
            )
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 600)
            check("600 belgi -> MyMemory nomzod emas", all(c[0] != "mymemory" for c in candidates), str(candidates))
            usage_by_provider["mymemory"] = {0: settings.MYMEMORY_FREE_DAILY_CHARS}
            candidates = await service._pick_free_tier_candidates(session, "auto", "en", 5)
            check("MyMemory kunlik hajmi tugagan -> chetlanadi", all(c[0] != "mymemory" for c in candidates), str(candidates))
            usage_by_provider["mymemory"] = {}
            settings.AZURE_TRANSLATOR_KEYS_RAW = ""
            settings.DEEPL_API_KEYS_RAW = ""
            text_out, provider_name, key_index, _ = await service._dispatch(session, "salom", "auto", "en")
            check(
                "asosiylar yo'q -> MyMemory ishlatiladi (deep_translator emas)",
                provider_name == "mymemory" and text_out == "mymemory:smoke@example.com",
                f"{provider_name}, {text_out}",
            )

            # ── Lokal NLLB (`local_mt`): ustunlik, rad etish != nosozlik ──
            check("lokal o'chiq -> nomzod emas",
                  all(c[0] != "local_mt" for c in await service._pick_free_tier_candidates(session, "en", "uz", 5)))
            settings.LOCAL_MT_URL = "http://127.0.0.1:1"
            settings.AZURE_TRANSLATOR_KEYS_RAW = original_azure_keys or "k1"
            local_calls: list[str] = []

            async def fake_local(text, _source, _target, _api_key) -> str:
                local_calls.append(text)
                return f"local:{text}"

            tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = fake_local
            settings.LOCAL_MT_PRIORITY = 1
            names = [c[0] for c in await service._pick_free_tier_candidates(session, "en", "uz", 5)]
            check(
                "lokal: asosiylardan keyin, MyMemory'dan oldin",
                "local_mt" in names and names.index("local_mt") == len(names) - 2 and names[-1] == "mymemory",
                str(names),
            )
            check("lokal 600 belgida ham nomzod (MyMemory emas)",
                  "local_mt" in [c[0] for c in await service._pick_free_tier_candidates(session, "en", "uz", 600)])
            check("lokal qo'llanmaydigan til (pt) -> nomzod emas",
                  "local_mt" not in [c[0] for c in await service._pick_free_tier_candidates(session, "en", "pt", 5)])
            settings.LOCAL_MT_PRIORITY = 0
            names = [c[0] for c in await service._pick_free_tier_candidates(session, "en", "uz", 5)]
            check("LOCAL_MT_PRIORITY=0 -> asosiylar guruhida", names.index("local_mt") < len(names) - 1, str(names))

            settings.AZURE_TRANSLATOR_KEYS_RAW = ""
            settings.GOOGLE_TRANSLATE_API_KEYS_RAW = ""
            settings.LOCAL_MT_PRIORITY = 1
            text_out, provider_name, _, _ = await service._dispatch(session, "hello", "en", "uz")
            check("asosiylar yo'q -> lokal ishlatiladi", provider_name == "local_mt", f"{provider_name}, {text_out}")

            async def fake_local_declines(_text, _source, _target, _api_key) -> str:
                raise tp.ProviderDeclined("detection_failed")

            tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = fake_local_declines
            for _ in range(5):
                text_out, provider_name, _, _ = await service._dispatch(session, "hello there", "auto", "uz")
            check("lokal rad etsa -> keyingi provayder (MyMemory)", provider_name == "mymemory", provider_name)
            check("rad etish breaker'ni OCHMAYDI", not await service.breaker.is_open("local_mt", 0))

            declined = []
            try:
                await tp.call_local_mt("ok", "auto", "uz", "local")
            except tp.ProviderDeclined:
                declined.append(True)
            check("qisqa auto matn HTTP'siz rad etiladi", declined == [True])
    finally:
        settings.DEEP_TRANSLATOR_ENABLED = original_deep_enabled
        settings.LOCAL_MT_URL = original_local_url
        settings.LOCAL_MT_PRIORITY = original_local_priority
        tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = original_local_call
        settings.GOOGLE_TRANSLATE_API_KEYS_RAW = original_google_keys
        settings.AZURE_TRANSLATOR_KEYS_RAW = original_azure_keys
        settings.DEEPL_API_KEYS_RAW = original_deepl_keys
        settings.MYMEMORY_EMAILS_RAW = original_mymemory_emails
        settings.GEMINI_API_KEYS_RAW = original_gemini_keys
        tp.provider_key_usage = original_usage_fn
        tp.FREE_TIER_PROVIDER_CONFIG["google_translate"]["call"] = original_google_call
        tp.FREE_TIER_PROVIDER_CONFIG["azure_translator"]["call"] = original_azure_call
        tp.FREE_TIER_PROVIDER_CONFIG["deepl"]["call"] = original_deepl_call
        tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = original_mymemory_call
        tp.CHEAP_PROVIDER_CONFIG["gemini"]["call"] = original_gemini_call

    # ── provider_key_usage: haqiqiy SUM so'rovi (xato hisoblanmasligi kerak) ──
    async with AsyncSessionLocal() as session:
        test_user, _ = await UserRepository(session).get_or_create(
            TEST_TELEGRAM_ID + 100, first_name="TrProv"
        )
        await session.commit()

        before = (await tp.provider_key_usage(session, "google_translate")).get(0, 0)

        session.add(Translation(
            user_id=test_user.id, chat_id=1, chat_type="private", input_kind="text",
            source_lang_requested="auto", target_lang="en", source_text="x",
            source_hash="smoke_tr_1", source_chars=12345,
            provider="google_translate", provider_key_index=0, status="success",
        ))
        session.add(Translation(
            # Xato bo'lgan urinish HISOBLANMASLIGI kerak — odatda to'lovga tushmaydi.
            user_id=test_user.id, chat_id=1, chat_type="private", input_kind="text",
            source_lang_requested="auto", target_lang="en", source_text="y",
            source_hash="smoke_tr_2", source_chars=999,
            provider="google_translate", provider_key_index=0, status="error",
        ))
        await session.commit()

        after = (await tp.provider_key_usage(session, "google_translate")).get(0, 0)
        check(
            "SUM faqat muvaffaqiyatlini hisoblaydi",
            after - before == 12345,
            f"before={before}, after={after}",
        )

        await session.execute(delete(Translation).where(Translation.user_id == test_user.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == test_user.id))
        await session.execute(delete(User).where(User.id == test_user.id))
        await session.commit()


async def test_audit_fixes_2026_11() -> None:
    """2026-11 audit tuzatishlari — regressiya qo'riqchilari.

    1) /start — ismdagi `<`/`&` xabarni buzmasin; 2) admin alertlaridagi
    provayder xato matni escape qilinsin (aks holda alert jim yo'qoladi);
    3) lokal modelning RAD ETISHI "provayder ishlamayapti" alertini
    tetiklamasin; 4) lokal MT 503 `busy` — nosozlik emas; 5) /help amaldagi
    limitni ko'rsatsin; 6) muvaffaqiyatsiz yozuvda oxirgi sinalgan provayder
    va lokal muvaffaqiyatda model nomi bo'lsin.
    """
    print("\n[7d] Audit tuzatishlari (2026-11)")
    import inspect

    from bot.handlers.user import start as start_handlers
    from bot.services import translation as tr_module
    from bot.services import translation_providers as tp
    from bot.services.translation import TranslationService

    # ── 1. /start: ism escape ──
    for code in sorted(locales.SUPPORTED):
        t = locales.get(code)
        rendered = t.WELCOME.format(name=html_escape("<3 Ali & Co"), source="A", target="B")
        check(
            f"{code}: WELCOME'da ism escape qilinadi",
            "&lt;3 Ali &amp; Co" in rendered and "<3" not in rendered,
        )
    src_start = inspect.getsource(start_handlers.cmd_start)
    check(
        "cmd_start ismni html_escape orqali o'tkazadi",
        "html_escape(message.from_user.first_name" in src_start,
    )

    # ── 5. /help: admin o'zgartirgan amaldagi limit ──
    src_help = inspect.getsource(start_handlers.cmd_help)
    check(
        "cmd_help amaldagi limitni ko'rsatadi (.env standarti emas)",
        "get_effective_limits" in src_help and "DAILY_TRANSLATION_LIMIT" not in src_help,
    )

    # ── 4. Lokal MT javobini tahlil qilish (HTTP'siz) ──
    def outcome(status: int, data) -> str:
        try:
            result = tp.interpret_local_mt_response(status, data)
        except tp.ProviderDeclined:
            return "declined"
        except RuntimeError:
            return "error"
        return f"ok:{result}"

    check("503 busy -> rad etish (breaker'ga tushmaydi)", outcome(503, {"error": "busy"}) == "declined")
    check("422 detection_failed -> rad etish", outcome(422, {"error": "detection_failed"}) == "declined")
    check("500 internal -> nosozlik", outcome(500, {"error": "internal"}) == "error")
    check("503 busy'siz -> nosozlik", outcome(503, {"error": "down"}) == "error")
    check("JSON bo'lmagan javob -> nosozlik", outcome(200, "<html>") == "error")
    check(
        "200 -> (matn, model)",
        outcome(200, {"text": " Salom ", "model": "nllb-600m"}) == "ok:('Salom', 'nllb-600m')",
    )
    check("200 modelsiz -> model None", outcome(200, {"text": "Salom"}) == "ok:('Salom', None)")

    # ── 6a. TranslationError provayder nomini tashiydi ──
    check("TranslationError.provider standart None", TranslationError("x").provider is None)
    check(
        "TranslationError.provider beriladi",
        TranslationError("x", "m", provider="deep_translator").provider == "deep_translator",
    )

    # ── 2, 3, 6b: alert yo'li — soxta provayderlar bilan ──
    sent: list[tuple[str, str]] = []

    async def fake_alert(_bot, _redis, key: str, text: str) -> None:
        sent.append((key, text))

    async def fake_usage(_session, _provider: str, _period: str = "month") -> dict:
        return {}

    async def fake_mymemory(_text, _source, _target, api_key) -> str:
        return f"mymemory:{api_key}"

    async def fake_local_declines(_text, _source, _target, _api_key):
        raise tp.ProviderDeclined("band (navbat to'la)")

    async def fake_local_fails(_text, _source, _target, _api_key):
        raise RuntimeError("lokal MT HTTP 500: <html>Error</html> & co")

    async def fake_local_with_model(text, _source, _target, _api_key):
        return f"local:{text}", "nllb-ft-v1"

    class FakeDeepOk:
        """3-daraja soxta — haqiqiy tarmoq chaqiruvi bo'lmasin."""

        name = "deep_translator"

        async def translate(self, text, _source, _target) -> str:
            return f"deep:{text}"

    class FakeDeepDown(FakeDeepOk):
        async def translate(self, _text, _source, _target) -> str:
            raise TranslationError("provider_blocked", "Google <b>500</b> & xato sahifa")

    saved = {
        "alert": tr_module.alert_admins_once,
        "usage": tp.provider_key_usage,
        "local_call": tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"],
        "mymemory_call": tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"],
        "google": settings.GOOGLE_TRANSLATE_API_KEYS_RAW,
        "azure": settings.AZURE_TRANSLATOR_KEYS_RAW,
        "deepl": settings.DEEPL_API_KEYS_RAW,
        "mymemory": settings.MYMEMORY_EMAILS_RAW,
        "gemini": settings.GEMINI_API_KEYS_RAW,
        "local_url": settings.LOCAL_MT_URL,
        "local_priority": settings.LOCAL_MT_PRIORITY,
        "deep_enabled": settings.DEEP_TRANSLATOR_ENABLED,
    }
    tr_module.alert_admins_once = fake_alert
    tp.provider_key_usage = fake_usage
    # Bu sinov 3-darajani soxta (`FakeDeepOk/Down`) bilan tekshiradi —
    # zanjir unga yetib borishi uchun yoqilgan bo'lishi kerak.
    settings.DEEP_TRANSLATOR_ENABLED = True
    tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = fake_mymemory
    settings.GOOGLE_TRANSLATE_API_KEYS_RAW = ""
    settings.AZURE_TRANSLATOR_KEYS_RAW = ""
    settings.DEEPL_API_KEYS_RAW = ""
    settings.GEMINI_API_KEYS_RAW = ""
    # MyMemory O'CHIQ — lokal 1-darajadagi YAGONA nomzod (haqiqatda 500+
    # belgili matnda shunday bo'ladi: MyMemory `max_chars`dan chetlanadi).
    # Aynan shu holatda rad etish ilgari soxta alert berardi.
    settings.MYMEMORY_EMAILS_RAW = ""
    settings.LOCAL_MT_URL = "http://127.0.0.1:1"
    settings.LOCAL_MT_PRIORITY = 1

    try:
        async with AsyncSessionLocal() as session:
            # 3. Rad etish — alert YO'Q, zanjir 3-darajaga (soxta deep) o'tadi.
            tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = fake_local_declines
            service = TranslationService(redis=None)
            service.deep_provider = FakeDeepOk()
            attempt = await service._attempt(
                "local_mt", 0, "local", fake_local_declines, "hello there", "auto", "uz"
            )
            check("_attempt: rad etish -> (None, None, None), xato emas", attempt == (None, None, None), str(attempt))
            sent.clear()
            _, provider_name, _, _ = await service._dispatch(session, "hello there", "auto", "uz")
            check("lokal (yagona nomzod) rad etsa -> zaxiraga o'tadi", provider_name == "deep_translator", provider_name)
            check(
                "lokal rad etishi 'ishlamayapti' alertini TETIKLAMAYDI",
                not any(key.startswith("free_tier_failed") for key, _ in sent),
                str([k for k, _ in sent]),
            )
            check("rad etish breaker'ni OCHMAYDI", not await service.breaker.is_open("local_mt", 0))

            # 2. Haqiqiy xato — alert BOR va provayder xato matni escape qilingan.
            tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = fake_local_fails
            sent.clear()
            _, provider_name, _, _ = await service._dispatch(session, "hello there", "auto", "uz")
            check("lokal xato bersa -> zaxiraga o'tadi", provider_name == "deep_translator", provider_name)
            tier_alerts = [text for key, text in sent if key == "free_tier_failed:local_mt"]
            check("lokal xatosi 'ishlamayapti' alertini tetiklaydi", len(tier_alerts) == 1, str([k for k, _ in sent]))
            if tier_alerts:
                text = tier_alerts[0]
                check(
                    "tier alertida xato matni escape qilingan, <b>provayder</b> tegi saqlangan",
                    "&lt;html&gt;Error&lt;/html&gt; &amp; co" in text
                    and "<html>" not in text
                    and "<b>local_mt</b>" in text,
                    text,
                )
            # Breaker: yana 2 ta xato -> ochiladi, `circuit_open` alerti ham escape qilingan.
            sent.clear()
            for _ in range(2):
                await service._attempt("local_mt", 0, "local", fake_local_fails, "hello there", "auto", "uz")
            cb_alerts = [text for key, text in sent if key == "circuit_open:local_mt:0"]
            check("3 ketma-ket xato -> circuit_open alerti", len(cb_alerts) == 1, str([k for k, _ in sent]))
            if cb_alerts:
                check(
                    "circuit_open alertida xato matni escape qilingan",
                    "&lt;html&gt;" in cb_alerts[0] and "<html>" not in cb_alerts[0] and "<b>local_mt</b>" in cb_alerts[0],
                    cb_alerts[0],
                )

            # 6c. Lokal muvaffaqiyat — model nomi natijaga tushadi; `str`
            # qaytaradigan provayderda model None.
            tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = fake_local_with_model
            # Breaker xotirasi endi barcha nusxalar uchun umumiy — yangi
            # servis yaratish yetmaydi, holat aniq tozalanadi.
            service.breaker.reset_memory()
            service = TranslationService(redis=None)
            service.deep_provider = FakeDeepOk()
            text_out, provider_name, _, model = await service._dispatch(session, "hello there", "auto", "uz")
            check(
                "lokal (matn, model) qaytarsa -> model natijada",
                provider_name == "local_mt" and text_out == "local:hello there" and model == "nllb-ft-v1",
                f"{provider_name}, {text_out}, {model}",
            )
            settings.LOCAL_MT_URL = ""
            settings.MYMEMORY_EMAILS_RAW = "smoke@example.com"
            _, provider_name, _, model = await service._dispatch(session, "hello there", "auto", "uz")
            check("str qaytaradigan provayderda model None", provider_name == "mymemory" and model is None, f"{provider_name}, {model}")

            # 6b. Hamma daraja yo'q, deep_translator ham yiqildi -> xatoda
            # provayder nomi bor, `all_tiers_down` alerti escape qilingan.
            settings.MYMEMORY_EMAILS_RAW = ""
            service = TranslationService(redis=None)
            service.deep_provider = FakeDeepDown()
            sent.clear()
            caught = None
            try:
                await service._dispatch(session, "hello there", "auto", "uz")
            except TranslationError as exc:
                caught = exc
            check(
                "deep_translator yiqilsa -> exc.provider = deep_translator",
                caught is not None and caught.provider == "deep_translator",
                repr(caught),
            )
            down_alerts = [text for key, text in sent if key == "all_tiers_down"]
            check(
                "all_tiers_down alertida xato matni escape qilingan",
                len(down_alerts) == 1
                and "Google &lt;b&gt;500&lt;/b&gt; &amp; xato sahifa" in down_alerts[0]
                and "<b>Tarjima BUTUNLAY ishlamayapti</b>" in down_alerts[0],
                str(down_alerts),
            )
    finally:
        tr_module.alert_admins_once = saved["alert"]
        tp.provider_key_usage = saved["usage"]
        tp.FREE_TIER_PROVIDER_CONFIG["local_mt"]["call"] = saved["local_call"]
        tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = saved["mymemory_call"]
        settings.GOOGLE_TRANSLATE_API_KEYS_RAW = saved["google"]
        settings.AZURE_TRANSLATOR_KEYS_RAW = saved["azure"]
        settings.DEEPL_API_KEYS_RAW = saved["deepl"]
        settings.MYMEMORY_EMAILS_RAW = saved["mymemory"]
        settings.GEMINI_API_KEYS_RAW = saved["gemini"]
        settings.LOCAL_MT_URL = saved["local_url"]
        settings.LOCAL_MT_PRIORITY = saved["local_priority"]
        settings.DEEP_TRANSLATOR_ENABLED = saved["deep_enabled"]


async def test_translation_service_cleanup() -> None:
    """2026-11 audit (tarjima servisi tozalash) — regressiya qo'riqchilari.

    1) cheksiz limitli provayder (lokal) oylik SUM so'rovini chaqirmaydi;
    2) hajm hisobi Redis'da keshlanadi, muvaffaqiyat keshni oshiradi,
       Redis nosoz bo'lsa DB'ga tushiladi; 3) `DEEP_TRANSLATOR_ENABLED=false`
       -> 3-daraja CHAQIRILMAYDI, yakuniy xato darhol, `all_tiers_down`
       alerti bor; 4) breaker xotirasi servis nusxalari orasida umumiy;
       5) errors.py tarmoq shovqinini ajratadi.
    """
    print("\n[7e] Tarjima servisi tozalash (2026-11)")
    import time as _time

    from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError

    from bot.handlers import errors as errors_module
    from bot.services import translation as tr_module
    from bot.services import translation_providers as tp
    from bot.services.circuit_breaker import FAILURE_THRESHOLD, CircuitBreaker
    from bot.services.translation import TranslationService

    usage_calls: list[str] = []
    usage_value: dict[int, int] = {0: 100, 1: 150}

    async def counting_usage(_session, provider: str, _period: str = "month") -> dict:
        usage_calls.append(provider)
        return dict(usage_value)

    class FakeRedis:
        """Hash buyruqlari — `pick_key`/`record_key_usage` uchun yetarli."""

        def __init__(self):
            self.store: dict[str, dict[str, str]] = {}

        async def hgetall(self, key):
            return dict(self.store.get(key, {}))

        async def hset(self, key, mapping):
            self.store.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

        async def expire(self, _key, _ttl):
            return True

        async def exists(self, key):
            return 1 if key in self.store else 0

        async def hincrby(self, key, field, amount):
            bucket = self.store.setdefault(key, {})
            bucket[field] = str(int(bucket.get(field, "0")) + amount)

    class BrokenRedis(FakeRedis):
        async def hgetall(self, _key):
            raise ConnectionError("redis down")

        async def hset(self, _key, mapping):
            raise ConnectionError("redis down")

    sent: list[tuple[str, str]] = []

    async def fake_alert(_bot, _redis, key: str, text: str) -> None:
        sent.append((key, text))

    deep_calls: list[str] = []

    class FakeDeep:
        name = "deep_translator"

        async def translate(self, text, _source, _target) -> str:
            deep_calls.append(text)
            return f"deep:{text}"

    async def fake_mymemory_fails(_text, _source, _target, _api_key) -> str:
        raise RuntimeError("MyMemory 429: band")

    async def fake_mymemory_timeout(_text, _source, _target, _api_key) -> str:
        raise RuntimeError("timeout 15s")

    saved = {
        "usage": tp.provider_key_usage,
        "alert": tr_module.alert_admins_once,
        "mymemory_call": tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"],
        "google": settings.GOOGLE_TRANSLATE_API_KEYS_RAW,
        "azure": settings.AZURE_TRANSLATOR_KEYS_RAW,
        "deepl": settings.DEEPL_API_KEYS_RAW,
        "mymemory": settings.MYMEMORY_EMAILS_RAW,
        "gemini": settings.GEMINI_API_KEYS_RAW,
        "local_url": settings.LOCAL_MT_URL,
        "deep_enabled": settings.DEEP_TRANSLATOR_ENABLED,
    }
    tp.provider_key_usage = counting_usage
    tr_module.alert_admins_once = fake_alert
    settings.GOOGLE_TRANSLATE_API_KEYS_RAW = ""
    settings.AZURE_TRANSLATOR_KEYS_RAW = ""
    settings.DEEPL_API_KEYS_RAW = ""
    settings.MYMEMORY_EMAILS_RAW = ""
    settings.GEMINI_API_KEYS_RAW = ""
    CircuitBreaker.reset_memory()

    try:
        async with AsyncSessionLocal() as session:
            # ── 1. Cheksiz limit -> SUM so'rovi yo'q ──
            settings.LOCAL_MT_URL = "http://127.0.0.1:1"
            service = TranslationService(redis=None)
            usage_calls.clear()
            candidates = await service._pick_free_tier_candidates(session, "en", "uz", 5)
            check("lokal MT nomzod", any(c[0] == "local_mt" for c in candidates), str(candidates))
            check("cheksiz limitda SUM so'rovi CHAQIRILMAYDI", usage_calls == [], str(usage_calls))
            picked = await tp.pick_key(session, "local_mt", ["local"], 10**15, "month", redis=None)
            check("pick_key cheksiz -> birinchi kalit, so'rovsiz", picked == (0, "local") and usage_calls == [])
            settings.LOCAL_MT_URL = ""

            # ── 2. Hajm keshi ──
            fake_redis = FakeRedis()
            usage_calls.clear()
            p1 = await tp.pick_key(session, "google_translate", ["k0", "k1"], 500_000, "month", redis=fake_redis)
            p2 = await tp.pick_key(session, "google_translate", ["k0", "k1"], 500_000, "month", redis=fake_redis)
            check("ikki pick_key -> DB bir marta", usage_calls == ["google_translate"], str(usage_calls))
            check("keshdan ham kam ishlatilgan kalit (k0)", p1 == (0, "k0") and p2 == (0, "k0"), f"{p1}, {p2}")
            await tp.record_key_usage(fake_redis, "google_translate", 0, 100, "month")
            p3 = await tp.pick_key(session, "google_translate", ["k0", "k1"], 500_000, "month", redis=fake_redis)
            check("muvaffaqiyat keshni oshiradi -> endi k1 kamroq", p3 == (1, "k1") and len(usage_calls) == 1, f"{p3}, {usage_calls}")
            await tp.record_key_usage(fake_redis, "google_translate", 1, 500_000, "month")
            p4 = await tp.pick_key(session, "google_translate", ["k0", "k1"], 500_000, "month", redis=fake_redis)
            check("keshda limitga yetgan kalit chetlanadi", p4 == (0, "k0"), str(p4))
            empty_redis = FakeRedis()
            usage_value_backup = dict(usage_value)
            usage_value.clear()
            usage_calls.clear()
            await tp.pick_key(session, "azure_translator", ["a0"], 2_000_000, "month", redis=empty_redis)
            await tp.pick_key(session, "azure_translator", ["a0"], 2_000_000, "month", redis=empty_redis)
            check("bo'sh hajm ({}) ham keshlanadi", usage_calls == ["azure_translator"], str(usage_calls))
            usage_value.update(usage_value_backup)
            usage_calls.clear()
            p5 = await tp.pick_key(session, "google_translate", ["k0", "k1"], 500_000, "month", redis=BrokenRedis())
            check("Redis nosoz -> DB'ga tushadi (fail-open)", p5 == (0, "k0") and usage_calls == ["google_translate"], f"{p5}, {usage_calls}")
            cache_key = tp._usage_cache_key("google_translate", "month")
            check("kesh kaliti davr boshini o'z ichiga oladi", cache_key.startswith("tr:usage:google_translate:month:20"), cache_key)

            # ── 3. deep_translator o'chiq ──
            settings.DEEP_TRANSLATOR_ENABLED = False
            service = TranslationService(redis=None)
            service.deep_provider = FakeDeep()
            sent.clear()
            deep_calls.clear()
            started = _time.perf_counter()
            caught = None
            try:
                await service._dispatch(session, "hello there", "auto", "uz")
            except TranslationError as exc:
                caught = exc
            elapsed = _time.perf_counter() - started
            check("zaxira o'chiq, nomzod yo'q -> no_provider", caught is not None and caught.code == "no_provider", repr(caught))
            check("3-daraja CHAQIRILMAGAN", deep_calls == [])
            check("xato darhol (<2 s)", elapsed < 2, f"{elapsed:.2f}s")
            check("all_tiers_down alerti bor", any(k == "all_tiers_down" for k, _ in sent), str([k for k, _ in sent]))

            settings.MYMEMORY_EMAILS_RAW = "smoke@example.com"
            tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = fake_mymemory_fails
            caught = None
            try:
                await service._dispatch(session, "hello there", "auto", "uz")
            except TranslationError as exc:
                caught = exc
            check(
                "provayder xato bersa -> provider_error, exc.provider = mymemory",
                caught is not None and caught.code == "provider_error" and caught.provider == "mymemory",
                repr(caught) + f" provider={getattr(caught, 'provider', None)}",
            )
            CircuitBreaker.reset_memory()
            tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = fake_mymemory_timeout
            caught = None
            try:
                await service._dispatch(session, "hello there", "auto", "uz")
            except TranslationError as exc:
                caught = exc
            check("timeout sababi -> code=timeout (status statistikasi uchun)", caught is not None and caught.code == "timeout", repr(caught))
            check("3-daraja hali ham chaqirilmagan", deep_calls == [])

            settings.DEEP_TRANSLATOR_ENABLED = True
            CircuitBreaker.reset_memory()
            _, provider_name, _, _ = await service._dispatch(session, "hello there", "auto", "uz")
            check("yoqilsa -> 3-daraja chaqiriladi", provider_name == "deep_translator" and deep_calls == ["hello there"], f"{provider_name}, {deep_calls}")
            settings.MYMEMORY_EMAILS_RAW = ""
            settings.DEEP_TRANSLATOR_ENABLED = False

            # ── 4. Breaker nusxalar orasida umumiy ──
            CircuitBreaker.reset_memory()
            s1 = TranslationService(redis=None)
            s2 = TranslationService(redis=None)
            for _ in range(FAILURE_THRESHOLD):
                await s1.breaker.record_failure("shared_p", 0)
            check("bir nusxada ochilgan breaker ikkinchisida ko'rinadi", await s2.breaker.is_open("shared_p", 0))
            check("boshqa kalitga ta'sir qilmaydi", not await s2.breaker.is_open("shared_p", 1))
            CircuitBreaker.reset_memory()
            check("reset_memory tozalaydi", not await s2.breaker.is_open("shared_p", 0))

        # ── 5. errors.py: tarmoq shovqini ──
        classify = errors_module.classify_error
        check(
            "TelegramNetworkError 'Request timeout' -> network",
            classify(TelegramNetworkError(method=None, message="Request timeout")) == errors_module.NETWORK,
        )
        check("CancelledError -> network", classify(asyncio.CancelledError()) == errors_module.NETWORK)
        check("TimeoutError -> network", classify(TimeoutError()) == errors_module.NETWORK)
        check("asyncio.TimeoutError -> network", classify(asyncio.TimeoutError()) == errors_module.NETWORK)
        check(
            "TelegramForbiddenError -> ignore",
            classify(TelegramForbiddenError(method=None, message="bot was blocked")) == errors_module.IGNORE,
        )
        check("ValueError -> unexpected", classify(ValueError("x")) == errors_module.UNEXPECTED)
        check("KeyError -> unexpected", classify(KeyError("x")) == errors_module.UNEXPECTED)
    finally:
        tp.provider_key_usage = saved["usage"]
        tr_module.alert_admins_once = saved["alert"]
        tp.FREE_TIER_PROVIDER_CONFIG["mymemory"]["call"] = saved["mymemory_call"]
        settings.GOOGLE_TRANSLATE_API_KEYS_RAW = saved["google"]
        settings.AZURE_TRANSLATOR_KEYS_RAW = saved["azure"]
        settings.DEEPL_API_KEYS_RAW = saved["deepl"]
        settings.MYMEMORY_EMAILS_RAW = saved["mymemory"]
        settings.GEMINI_API_KEYS_RAW = saved["gemini"]
        settings.LOCAL_MT_URL = saved["local_url"]
        settings.DEEP_TRANSLATOR_ENABLED = saved["deep_enabled"]
        CircuitBreaker.reset_memory()


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
        check(f"{code}: menyuda 4 tugma", len(labels) == 4, ", ".join(labels))
        check(f"{code}: murojaat tugmasi bor", t.BTN_CONTACT in labels)
        check(f"{code}: homiylik tugmasi bor", t.BTN_DONATE in labels)
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
    """Adminga murojaat: matn tayyorlash (sarlavha)."""
    print("\n[12] Adminga murojaat")
    from types import SimpleNamespace

    from bot.handlers.user.support import _admin_view

    # Foydalanuvchi matnidagi HTML buzmasligi kerak: `<` bo'lsa Telegram
    # xabarni butunlay rad etadi va murojaat adminga yetmasdi.
    # Sarlavhada foydalanuvchi ismi bor — u escape qilinishi shart, aks holda
    # ismida `<` bo'lgan odam yozganda Telegram xabarni butunlay rad etadi
    # va murojaat adminga yetmasdi. Xabar matnining o'zi endi bu yerda emas:
    # u alohida xabar bo'lib `copy_message` bilan ko'chiriladi.
    fake = SimpleNamespace(
        username="tester", first_name="A <b>Bold</b>", telegram_id=123, telegram_lang="uz"
    )
    view = _admin_view(fake)
    check("ism escape qilinadi", "A &lt;b&gt;Bold&lt;/b&gt;" in view, view.split(chr(10))[2])
    check("telegram id ko'rinadi", "<code>123</code>" in view)
    check("username ko'rinadi", "@tester" in view)
    # `🆔` — suhbatni aniqlash belgisi, sarlavhada bo'lishi shart.
    check("ID belgisi joyida", "🆔" in view)

    # Username yo'q bo'lsa yiqilmasligi kerak.
    anon = SimpleNamespace(
        username=None, first_name=None, telegram_id=9, telegram_lang=None
    )
    check("username/ism yo'q bo'lsa ham ishlaydi", "—" in _admin_view(anon))


async def test_resilience() -> None:
    """Circuit breaker (xotira rejimi) va xato-darajasi ogohlantirishi."""
    print("\n[7c] Chidamlilik — circuit breaker va xato darajasi")
    from bot.services import error_rate
    from bot.services.circuit_breaker import FAILURE_THRESHOLD, CircuitBreaker

    breaker = CircuitBreaker(redis=None)
    opened = [await breaker.record_failure("p", 0) for _ in range(FAILURE_THRESHOLD)]
    check("chegaragacha ochilmaydi", not any(opened[:-1]), str(opened))
    check("chegarada ochiladi", opened[-1] is True)
    check("ochiq holat ko'rinadi", await breaker.is_open("p", 0))
    check("boshqa kalitga ta'sir qilmaydi", not await breaker.is_open("p", 1))
    await breaker.record_failure("q", 0)
    await breaker.record_success("q", 0)
    await breaker.record_failure("q", 0)
    await breaker.record_failure("q", 0)
    check("muvaffaqiyat hisobni nolga tushiradi", not await breaker.is_open("q", 0))

    try:
        from bot.database.redis import get_redis

        client = get_redis()
        await client.ping()
    except Exception:
        print("  ⏭  Redis yo'q — xato darajasi sinovi o'tkazib yuborildi")
        return

    prefix = "smoke:stat"
    try:
        for _ in range(5):
            await error_rate.record(client, True, prefix=prefix)
        for _ in range(25):
            await error_rate.record(client, False, prefix=prefix)
        ok, err = await error_rate.window_stats(client, prefix=prefix)
        check("oyna statistikasi to'g'ri", (ok, err) == (5, 25), f"{ok}, {err}")
        fired = await error_rate.check_and_alert(None, client, prefix=prefix)
        check("xato ulushi >10% -> ogohlantirish tetiklanadi", fired is True)
    finally:
        keys = [k async for k in client.scan_iter(f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_tts_health() -> None:
    """TTS xato-darajasi ogohlantirishi — sof mantiq, xotiradagi soxta Redis."""
    print("\n[7d] TTS salomatligi — xato darajasi ogohlantirishi")
    from bot.services import tts_health

    # Sof qaror mantig'i.
    check("kam so'rovda jim (4/4 xato)", not tts_health.should_alert(0, 4))
    check("aynan 50% — jim", not tts_health.should_alert(3, 3))
    check(">50% va >=5 so'rov — ogohlantiradi", tts_health.should_alert(2, 3))
    check("hammasi yiqilsa — ogohlantiradi", tts_health.should_alert(0, 5))
    check("sog'lom — jim", not tts_health.should_alert(20, 1))

    # Dinamik xato matni HTML'ni buzmasligi kerak.
    text = tts_health.alert_text(0, 5, "403 <b>&")
    check("xato matni ekranlanadi", "&lt;b&gt;&amp;" in text and "<b>&" not in text, text)

    # Redis yo'q — jim, xatosiz.
    await tts_health.record(None, ok=False)
    check("Redis yo'q — ogohlantirish yo'q", await tts_health.check_and_alert(None, None) is False)

    class _FakeRedis:
        """`atomic_rate_incr` (eval), `mget` va `set(nx=)` uchun yetarli."""

        def __init__(self):
            self.data: dict = {}

        async def eval(self, _script, _n, key, _ttl):
            self.data[key] = int(self.data.get(key, 0)) + 1
            return self.data[key]

        async def mget(self, *keys):
            return [self.data.get(k) for k in keys]

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.data:
                return None
            self.data[key] = value
            return True

    class _FakeBot:
        def __init__(self):
            self.sent: list = []

        async def send_message(self, chat_id, text):
            self.sent.append((chat_id, text))

    redis, bot = _FakeRedis(), _FakeBot()
    prefix = "smoke:tts:stat"
    for _ in range(2):
        await tts_health.record(redis, True, prefix=prefix)
    for _ in range(4):
        await tts_health.record(redis, False, prefix=prefix)
    ok, err = await tts_health.window_stats(redis, prefix=prefix)
    check("oyna statistikasi to'g'ri", (ok, err) == (2, 4), f"{ok}, {err}")
    fired = await tts_health.check_and_alert(bot, redis, last_error="403", prefix=prefix)
    check("xato ulushi >50% -> ogohlantirish tetiklanadi", fired is True)
    first = len(bot.sent)
    check("adminlarga yuborildi", first == len(settings.ADMIN_USER_IDS), str(first))
    await tts_health.check_and_alert(bot, redis, last_error="403", prefix=prefix)
    check("takroran yuborilmaydi (cooldown)", len(bot.sent) == first, str(len(bot.sent)))


async def test_donate(user_id: int) -> None:
    """Homiylik: klaviatura, yozuv va takroriy to'lov himoyasi."""
    print("\n[13] Homiylik (Telegram Stars)")
    from sqlalchemy import delete
    from sqlalchemy.exc import IntegrityError

    from bot.database.models import Donation
    from bot.keyboards.user import donate_amounts

    t = locales.get("uz")
    markup = donate_amounts(t, settings.DONATE_PRESETS)
    codes = [b.callback_data for row in markup.inline_keyboard for b in row]
    check(
        "presetlar tugma bo'ldi",
        all(f"donate:{n}" in codes for n in settings.DONATE_PRESETS),
        str(codes),
    )
    check("boshqa miqdor tugmasi bor", "donate:custom" in codes)

    async with AsyncSessionLocal() as session:
        charge = "test_charge_smoke_1"
        await session.execute(
            delete(Donation).where(Donation.telegram_payment_charge_id == charge)
        )
        await session.commit()

        session.add(
            Donation(
                user_id=user_id,
                stars=50,
                currency="XTR",
                telegram_payment_charge_id=charge,
                invoice_payload="donate:50",
                status="paid",
            )
        )
        await session.commit()
        row = (
            await session.execute(
                select(Donation).where(Donation.telegram_payment_charge_id == charge)
            )
        ).scalar_one()
        check("homiylik yozildi", row.stars == 50 and row.status == "paid")

    # Takroriy to'lov xabari ikkinchi yozuv yaratmasligi kerak — Telegram bir
    # to'lov haqida bir necha marta xabar berishi mumkin.
    async with AsyncSessionLocal() as session:
        session.add(
            Donation(
                user_id=user_id,
                stars=50,
                currency="XTR",
                telegram_payment_charge_id=charge,
                status="paid",
            )
        )
        try:
            await session.commit()
            check("takroriy to'lov rad etiladi", False, "ikkinchi yozuv o'tdi")
        except IntegrityError:
            await session.rollback()
            check("takroriy to'lov rad etiladi", True)

    # Manfiy va nol miqdor bazaga tushmasligi kerak.
    async with AsyncSessionLocal() as session:
        session.add(
            Donation(
                user_id=user_id,
                stars=0,
                telegram_payment_charge_id="test_charge_smoke_zero",
            )
        )
        try:
            await session.commit()
            check("nol miqdor rad etiladi", False, "0 ⭐ o'tdi")
        except IntegrityError:
            await session.rollback()
            check("nol miqdor rad etiladi", True)

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Donation).where(Donation.user_id == user_id)
        )
        await session.commit()


async def test_donate_on_paid_idempotent(user_id: int) -> None:
    """`on_paid` handleri: bir to'lov ikki marta kelsa — bitta yozuv, bitta VIP.

    `test_donate` faqat DB cheklovini tekshiradi; bu yerda HANDLER o'zi
    sinovdan o'tadi: Telegram `successful_payment`ni qayta yuborgan holatda
    (bir xil `telegram_payment_charge_id`) foydalanuvchi ikkala safar
    rahmat oladi, lekin VIP faqat bir marta beriladi va adminlarga
    "DB xatosi" ogohlantirishi ketmaydi (takroriy xabar — kutilgan holat).

    Eslatma: ikkinchi chaqiruv Postgres logida bitta `duplicate key`
    yozuvi qoldiradi — bu ataylab, handlerning aynan shu yo'li sinaladi.
    """
    print("\n[13b] Homiylik — on_paid idempotent (takroriy successful_payment)")
    from types import SimpleNamespace

    from sqlalchemy import delete, update

    from bot.database.models import Donation, User, UserSettings
    from bot.handlers.user.donate import on_paid

    t = locales.get("uz")
    charge = "test_charge_smoke_onpaid"
    stars = settings.QUICK_VIP_STARS

    class _FakeBot:
        def __init__(self) -> None:
            self.sent: list[tuple[int, str]] = []

        async def send_message(self, chat_id, text, *args, **kwargs) -> None:
            self.sent.append((chat_id, text))

    class _FakeMessage:
        def __init__(self) -> None:
            self.bot = _FakeBot()
            self.chat = SimpleNamespace(id=TEST_TELEGRAM_ID, type="private")
            self.successful_payment = SimpleNamespace(
                currency="XTR",
                total_amount=stars,
                invoice_payload="quickvip",
                telegram_payment_charge_id=charge,
                provider_payment_charge_id=None,
            )
            self.answers: list[str] = []

        async def answer(self, text, *args, **kwargs) -> None:
            self.answers.append(text)

    async def _state() -> tuple[int, int, object]:
        async with AsyncSessionLocal() as s:
            n_don = (
                await s.execute(
                    select(func.count()).select_from(Donation).where(
                        Donation.telegram_payment_charge_id == charge
                    )
                )
            ).scalar_one()
            n_grant = (
                await s.execute(
                    select(func.count()).select_from(Event).where(
                        Event.user_id == user_id,
                        Event.event_type == EventType.PREMIUM_GRANTED,
                        Event.payload["reason"].astext == "donation",
                    )
                )
            ).scalar_one()
            until = (
                await s.execute(
                    select(UserSettings.premium_until).where(
                        UserSettings.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            return n_don, n_grant, until

    async def _deliver() -> _FakeMessage:
        # Har bir yetkazish — alohida sessiya, middleware'dagidek: handler
        # flush qiladi, commit tashqarida.
        msg = _FakeMessage()
        async with AsyncSessionLocal() as s:
            user = (
                await s.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            await on_paid(
                msg, s, user, EventService(s), None, t, redis=None
            )
            await s.commit()
        return msg

    # Toza boshlanish: VIP yo'q, shu charge yo'q, oldingi grant voqealari yo'q.
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Donation).where(Donation.user_id == user_id))
        await s.execute(
            delete(Event).where(
                Event.user_id == user_id,
                Event.event_type.in_([EventType.PREMIUM_GRANTED, EventType.DONATE_PAID]),
            )
        )
        await s.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(premium_until=None)
        )
        await s.commit()

    first = await _deliver()
    n_don, n_grant, until1 = await _state()
    check("birinchi to'lov yozildi", n_don == 1, f"{n_don} yozuv")
    check("VIP berildi (premium.granted = 1)", n_grant == 1, str(n_grant))
    expected_days = settings.QUICK_VIP_DAYS
    check(
        f"quickvip -> {expected_days} kun",
        until1 is not None
        and abs((until1 - utcnow()).days - expected_days) <= 1,
        str(until1),
    )
    check("foydalanuvchiga rahmat yuborildi", len(first.answers) == 1)
    check(
        "adminga 'yangi homiylik' xabari ketdi",
        len(first.bot.sent) == len(settings.ADMIN_USER_IDS),
        str(len(first.bot.sent)),
    )

    # Telegram xuddi shu to'lovni qayta yubordi.
    second = await _deliver()
    n_don2, n_grant2, until2 = await _state()
    check("takroriy xabar ikkinchi yozuv yaratmadi", n_don2 == 1, f"{n_don2} yozuv")
    check("VIP ikki marta berilmadi", n_grant2 == 1 and until2 == until1, str(until2))
    check(
        "takrorda ham foydalanuvchi rahmat oladi",
        len(second.answers) == 1
        and second.answers[0] == t.DONATE_THANKS.format(stars=stars),
    )
    check(
        "takrorda admin ogohlantirishi (DB xatosi) ketmadi",
        not any("To'lov yozilmadi" in text for _, text in second.bot.sent),
        str(len(second.bot.sent)),
    )

    # Tozalash — `cleanup()` ham qiladi, lekin keyingi testlar VIP'siz boshlasin.
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Donation).where(Donation.user_id == user_id))
        await s.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(premium_until=None)
        )
        await s.commit()


async def test_broadcast() -> None:
    """Tarqatish: tezlik cheklovi va xatolarni tasniflash."""
    print("\n[14] Tarqatish")
    import time

    from bot.services.broadcast import (
        DEFAULT_CONCURRENCY,
        DEFAULT_RATE_PER_SEC,
        RateLimiter,
        is_permanently_unreachable,
    )

    # Tezlik: 40 slot / 20 per sek ≈ 2 soniya.
    limiter = RateLimiter(20)
    started = time.monotonic()
    await asyncio.gather(*(limiter.wait() for _ in range(40)))
    elapsed = time.monotonic() - started
    check("rate limiter tezlikni ushlaydi", 1.7 < elapsed < 2.6, f"{elapsed:.2f}s")

    # `pause()` — flood-wait kelganda hamma yuboruvchi birga kutadi.
    paused = RateLimiter(100)
    paused.pause(0.4)
    started = time.monotonic()
    await paused.wait()
    check("pause hammani kechiktiradi", time.monotonic() - started > 0.3)

    check("tezlik Telegram limitidan past", DEFAULT_RATE_PER_SEC <= 20)
    check("parallellik oqilona", 1 < DEFAULT_CONCURRENCY <= 20)

    # Qaytarib bo'lmaydigan xatolar — bunday userlar `deleted` ga o'tadi.
    permanent = [
        "Telegram server says - Bad Request: chat not found",
        "Bad Request: USER_BOT_TO_BOT_DISABLED",
        "Bad Request: PEER_ID_INVALID",
        "Forbidden: user is deactivated",
    ]
    for text in permanent:
        check(f"doimiy xato: {text[:38]}", is_permanently_unreachable(text))

    # `forbidden` bu ro'yxatda BO'LMASLIGI kerak: bloklagan odam botni qayta
    # ochsa holati tiklanadi, uni `deleted` qilish ma'lumot yo'qotish bo'lardi.
    for text in ["forbidden", "Too Many Requests: retry after 5", "Bad Gateway", None, ""]:
        check(f"vaqtinchalik: {text!r:34}", not is_permanently_unreachable(text))


async def test_broadcast_resumable() -> None:
    """Tarqatish: xato tasnifi (manager-bot dan moslashtirilgan), watermark
    kursor, va pauza/davom/bekor holat mashinasi (`broadcast_repository.py`).

    manager-bot (github.com/khusinboev/manager-bot) o'rganib olingan —
    tarjimon-8 bitta jarayon ichida moslashtirdi (alohida worker emas).
    """
    print("\n[14b] Tarqatish — davom ettiriladigan quvur")
    from collections import deque

    from aiogram.exceptions import (
        TelegramBadRequest,
        TelegramForbiddenError,
        TelegramNetworkError,
        TelegramRetryAfter,
        TelegramServerError,
    )
    from sqlalchemy import delete

    from bot.database.models import Broadcast, User, UserSettings
    from bot.database.repositories.broadcast_repository import BroadcastRepository
    from bot.services.broadcast_errors import Kind, classify
    from bot.services.broadcast_runner import _advance_watermark

    # ── Xato tasnifi ──
    cls = classify(TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"))
    check("bloklagan -> passiv, qayta urinilmaydi", cls.passivate and not cls.retriable and cls.kind == Kind.BLOCKED)

    cls = classify(TelegramBadRequest(method=None, message="Bad Request: chat not found"))
    check("chat topilmadi -> passiv", cls.passivate and cls.kind == Kind.CHAT_NOT_FOUND)

    cls = classify(TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=7))
    check(
        "429 -> vaqtinchalik, retry_after saqlanadi",
        cls.retriable and not cls.passivate and cls.retry_after == 7,
    )

    cls = classify(TelegramNetworkError(method=None, message="Connection reset"))
    check("tarmoq xatosi -> vaqtinchalik, passiv emas", cls.retriable and not cls.passivate)

    cls = classify(TelegramServerError(method=None, message="Internal Server Error"))
    check("Telegram 5xx -> vaqtinchalik", cls.retriable and not cls.passivate)

    # Xabarning O'ZIGA tegishli — hamma qabul qiluvchida takrorlanadi, shuning
    # uchun `fatal=True` (butun tarqatishni to'xtatadi), lekin passiv EMAS
    # (qabul qiluvchining aybi emas).
    cls = classify(TelegramBadRequest(method=None, message="Bad Request: message text is empty"))
    check(
        "bo'sh xabar -> fatal, passiv emas",
        cls.fatal and not cls.passivate and cls.kind == Kind.BAD_MESSAGE,
    )
    cls = classify(TelegramBadRequest(method=None, message="Bad Request: message to copy not found"))
    check("manba xabar yo'q -> fatal", cls.fatal and cls.kind == Kind.BAD_SOURCE)

    # Tanilmagan BadRequest — shubha bo'lganda ZARARSIZ tomonga: na passiv,
    # na fatal. Noto'g'ri tasnif minglab userni yoki butun tarqatishni
    # bekorga qurbon qiladi.
    cls = classify(TelegramBadRequest(method=None, message="Bad Request: something totally new"))
    check("noma'lum BadRequest -> na passiv, na fatal", not cls.passivate and not cls.fatal)

    # ── Watermark kursor: tartibsiz tugagan natijalar oraliqni tashlab
    # ketmasligi kerak ──
    order = deque([10, 20, 30, 40])
    done: set[int] = set()
    cursor = None

    done.add(20)  # 20 tugadi, lekin 10 hali yo'q -> kursor SILJIMAYDI
    cursor = _advance_watermark(order, done, cursor)
    check("o'rtadagi tugallanmagan kursorni to'xtatadi", cursor is None, f"cursor={cursor}")

    done.add(10)  # endi 10 ham tugadi -> 10 VA 20 uzluksiz, kursor 20 ga o'tadi
    cursor = _advance_watermark(order, done, cursor)
    check("uzluksiz prefiks kursorni siljitadi", cursor == 20, f"cursor={cursor}")
    check("faqat tugallangan boshi olib tashlanadi", list(order) == [30, 40], str(list(order)))

    done.add(40)  # 40 tugadi, lekin 30 hali yo'q -> kursor 20 da qoladi
    cursor = _advance_watermark(order, done, cursor)
    check("keyingi bo'shliqda ham to'xtaydi", cursor == 20, f"cursor={cursor}")

    # ── BroadcastRepository: holat mashinasi ──
    async with AsyncSessionLocal() as session:
        repo = BroadcastRepository(session)
        repo_user = UserRepository(session)
        admin, _ = await repo_user.get_or_create(TEST_TELEGRAM_ID + 96, first_name="BcastAdmin")
        await session.commit()

        bc = await repo.create_broadcast(
            created_by=admin.id, mode="copy", content_preview="test",
            total_targets=100, src_chat_id=admin.telegram_id, src_message_id=1,
        )
        check("yaratilganda running", bc.status == "running")

        active = await repo.get_active()
        check("get_active topadi", active is not None and active.id == bc.id)

        ok = await repo.request_pause(bc.id)
        check("pauza so'raladi (running -> pause_requested)", ok)
        status = await repo.get_status(bc.id)
        check("holat pause_requested", status == "pause_requested")

        await repo.mark_paused(
            bc.id, cursor_user_id=42, active_seconds=17, success_count=5, failed_count=1,
        )
        paused = await repo.get(bc.id)
        check(
            "pauzada kursor/hisob saqlanadi",
            paused.status == "paused" and paused.cursor_user_id == 42
            and paused.active_seconds == 17 and paused.success_count == 5,
        )

        ok = await repo.resume(bc.id)
        check("davom ettirish (paused -> running)", ok and (await repo.get_status(bc.id)) == "running")

        # Pauzada bekor qilish — ishlab turgan vazifa yo'q, DARHOL yakunlanadi
        # (signal emas, chunki uni ko'radigan hech kim yo'q).
        await repo.request_pause(bc.id)
        await repo.mark_paused(bc.id, cursor_user_id=42, active_seconds=17, success_count=5, failed_count=1)
        cancel_ok = await repo.request_cancel(bc.id)
        check(
            "pauzadan bekor qilish darhol yakunlanadi",
            cancel_ok and (await repo.get_status(bc.id)) == "cancelled",
        )

        # Restart tiklashi: "running" holatida qolgan qator (jarayon o'lgan)
        # `paused`ga o'tishi kerak.
        stuck = await repo.create_broadcast(
            created_by=admin.id, mode="copy", content_preview="stuck",
            total_targets=10, src_chat_id=admin.telegram_id, src_message_id=2,
        )
        recovered_ids = await repo.recover_interrupted()
        check("restart tiklashi 'running'ni ko'radi", stuck.id in recovered_ids, str(recovered_ids))
        check(
            "tiklangandan keyin paused",
            (await repo.get_status(stuck.id)) == "paused",
        )

        await session.execute(delete(Broadcast).where(Broadcast.created_by == admin.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == admin.id))
        await session.execute(delete(User).where(User.id == admin.id))
        await session.commit()

    # ── UserRepository: kursor-asosidagi sahifalash, yangi user qamrab olinadi ──
    async with AsyncSessionLocal() as session:
        repo_user = UserRepository(session)
        u1, _ = await repo_user.get_or_create(TEST_TELEGRAM_ID + 97, first_name="B1")
        u2, _ = await repo_user.get_or_create(TEST_TELEGRAM_ID + 98, first_name="B2")
        await session.commit()

        batch = await repo_user.fetch_broadcast_batch(after_id=u1.id - 1, limit=1)
        check(
            "kursor bittadan qaytaradi",
            len(batch) == 1 and batch[0][0] == u1.id,
            str(batch),
        )

        batch2 = await repo_user.fetch_broadcast_batch(after_id=u1.id, limit=50)
        ids_in_batch2 = [uid for uid, _ in batch2]
        check("kursordan keyingi user qamrab olinadi", u2.id in ids_in_batch2)

        # Tarqatish "davomida" qo'shilgan yangi user — kursordan katta `id`
        # olgani uchun keyingi chaqiruvda AVTOMATIK qamrab olinadi, alohida
        # sinxronlash kerak emas.
        u3, _ = await repo_user.get_or_create(TEST_TELEGRAM_ID + 99, first_name="B3")
        await session.commit()
        batch3 = await repo_user.fetch_broadcast_batch(after_id=u2.id, limit=50)
        check(
            "tarqatish davomida qo'shilgan user avtomatik qamrab olinadi",
            u3.id in [uid for uid, _ in batch3],
        )

        await session.execute(
            delete(UserSettings).where(UserSettings.user_id.in_([u1.id, u2.id, u3.id]))
        )
        await session.execute(delete(User).where(User.id.in_([u1.id, u2.id, u3.id])))
        await session.commit()


async def test_broadcast_runner_guards() -> None:
    """Tarqatish #64 (2026-10-19) saboqlari: bir ishga tushirishda har
    foydalanuvchi FAQAT BIR MARTA jo'natiladi, bufer watermark kursordan
    emas, fetch-chegaradan olinadi, `(broadcast, user)` uchun ko'pi bilan
    bitta delivery qatori, tizimli xato zanjiri va avto-pauza sababi.
    """
    print("\n[14c] Tarqatish — takror yuborish va aylanishdan himoya")
    from sqlalchemy import delete

    from bot.database.models import Broadcast, BroadcastDelivery, User, UserSettings
    from bot.database.repositories.broadcast_repository import BroadcastRepository
    from bot.services.broadcast_errors import Classification, Kind
    from bot.services.broadcast_progress import render_card
    from bot.services.broadcast_runner import (
        SYSTEMIC_FAILURE_STREAK,
        _DispatchTracker,
        _SystemicFailureGuard,
    )

    # ── _DispatchTracker: #64 ssenariysi ──
    # Bufer [1..5] olindi, hammasi jo'natildi; 3,4,5 tugadi, 1 va 2 hali
    # havoda. Eski kod keyingi buferni `id > cursor(None)` dan olib,
    # 1..5 ni QAYTA jo'natardi — endi chegara 5 da, kursor esa hali None.
    tracker = _DispatchTracker(cursor=None)
    batch = [(i, 1000 + i) for i in range(1, 6)]
    tracker.note_fetched(batch)
    claimed = [tracker.claim(uid) for uid, _ in batch]
    check("birinchi jo'natish hammaga ruxsat", all(claimed))
    for uid in (3, 4, 5):
        tracker.complete(uid)
    check("havodagi 1,2 tufayli watermark siljimaydi", tracker.cursor is None, f"cursor={tracker.cursor}")
    check("fetch-chegara esa oxirgi olingan id'da", tracker.fetch_after == 5, f"fetch_after={tracker.fetch_after}")

    # Xuddi shu foydalanuvchi qaytadan buferga tushsa — rad etiladi, logga tushadi.
    check("takror jo'natish rad etiladi", tracker.claim(3) is False)
    check("takror sanaladi", tracker.duplicates_skipped == 1, str(tracker.duplicates_skipped))
    check("rad etilgan takror navbatga qo'shilmaydi", tracker.in_flight == 5, str(tracker.in_flight))

    tracker.complete(1)
    check("1 tugadi -> kursor 1 (2 hali yo'q)", tracker.cursor == 1, f"cursor={tracker.cursor}")
    tracker.complete(2)
    check("2 tugadi -> kursor uzluksiz 5 gacha", tracker.cursor == 5 and tracker.in_flight == 0)

    # Pauzadan davom: kursor 5 — chegara ham 5 dan boshlanadi.
    resumed = _DispatchTracker(cursor=5)
    check("davom ettirishda chegara kursordan boshlanadi", resumed.fetch_after == 5)
    resumed.note_fetched([(7, 1007), (9, 1009)])
    check("chegara faqat oldinga yuradi", resumed.fetch_after == 9)
    resumed.note_fetched([])
    check("bo'sh bufer chegarani o'zgartirmaydi", resumed.fetch_after == 9)

    # ── _SystemicFailureGuard ──
    def _cls(kind: str, **kw) -> Classification:
        defaults = dict(detail=kind, passivate=False, retriable=False)
        defaults.update(kw)
        return Classification(kind=kind, **defaults)

    guard = _SystemicFailureGuard(threshold=3)
    check("1-tarmoq xatosi hali to'xtatmaydi", guard.record_failure(_cls(Kind.NETWORK)) is False)
    check("2-tarmoq xatosi hali to'xtatmaydi", guard.record_failure(_cls(Kind.SERVER)) is False)
    check("3-ketma-ket tizimli xato -> to'xtat", guard.record_failure(_cls(Kind.UNKNOWN)) is True)
    check("oxirgi sabab saqlanadi", guard.last_detail == Kind.UNKNOWN, str(guard.last_detail))

    guard = _SystemicFailureGuard(threshold=3)
    guard.record_failure(_cls(Kind.NETWORK))
    guard.record_failure(_cls(Kind.NETWORK))
    # Bloklagan foydalanuvchi — Telegram javob bergan, tizim ishlayapti.
    check(
        "passiv xato (bloklagan) zanjirni uzadi",
        guard.record_failure(_cls(Kind.BLOCKED, passivate=True)) is False and guard.streak == 0,
    )
    guard.record_failure(_cls(Kind.RATE_LIMIT, retriable=True))
    guard.record_failure(_cls(Kind.RATE_LIMIT, retriable=True))
    guard.record_success()
    check("muvaffaqiyat zanjirni uzadi", guard.streak == 0)
    check("`None` tasnif noma'lum (tizimli) deb sanaladi", guard.record_failure(None) is False and guard.streak == 1)
    check("standart chegara oqilona (10..500)", 10 <= SYSTEMIC_FAILURE_STREAK <= 500, str(SYSTEMIC_FAILURE_STREAK))

    # ── BroadcastRepository.add_delivery: (broadcast, user) uchun bitta qator ──
    async with AsyncSessionLocal() as session:
        repo = BroadcastRepository(session)
        repo_user = UserRepository(session)
        admin, _ = await repo_user.get_or_create(TEST_TELEGRAM_ID + 94, first_name="BcastGuard")
        await session.commit()

        bc = await repo.create_broadcast(
            created_by=admin.id, mode="copy", content_preview="guard",
            total_targets=5, src_chat_id=admin.telegram_id, src_message_id=1,
        )
        first = await repo.add_delivery(bc.id, admin.id, "failed", "Forbidden: bot was blocked by the user")
        second = await repo.add_delivery(bc.id, admin.id, "failed", "Forbidden: bot was blocked by the user")
        third = await repo.add_delivery(bc.id, admin.id, "failed", "boshqa matn")
        await session.commit()
        rows = (
            await session.execute(
                select(func.count(BroadcastDelivery.id)).where(BroadcastDelivery.broadcast_id == bc.id)
            )
        ).scalar_one()
        check("birinchi yozuv qo'shiladi", first is True)
        check("ikkinchi/uchinchi yozuv rad etiladi", second is False and third is False)
        check("jadvalda aynan BITTA qator", rows == 1, f"rows={rows}")
        reasons = await repo.failure_reasons(bc.id)
        check(
            "xato sabablari takrorsiz",
            reasons == [("Forbidden: bot was blocked by the user", 1)],
            str(reasons),
        )

        # Avto-pauza sababi kartada ko'rinadi, davom ettirilganda tozalanadi.
        await repo.request_pause(bc.id)
        await repo.mark_paused(
            bc.id, cursor_user_id=None, active_seconds=3, success_count=0, failed_count=1,
            error="50 ta ketma-ket yuborish tizimli xato bilan tugadi",
        )
        paused = await repo.get(bc.id)
        await session.refresh(paused)
        card = render_card(paused)
        check("avto-pauza sababi saqlanadi", paused.status == "paused" and paused.error is not None)
        check("sabab kartada ko'rinadi", "tizimli xato" in card)
        await repo.resume(bc.id)
        await session.refresh(paused)
        check("davom ettirish sababni tozalaydi", paused.status == "running" and paused.error is None)

        await session.execute(delete(Broadcast).where(Broadcast.created_by == admin.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == admin.id))
        await session.execute(delete(User).where(User.id == admin.id))
        await session.commit()


async def test_stats() -> None:
    """Statistika: ma'lumot yig'iladi va Telegram chegarasiga sig'adi."""
    print("\n[15] Statistika")
    from bot.services.stats import StatsService, bar, num, render, table

    check("raqam probel bilan", num(37457) == "37 457", num(37457))
    check("nol ulush bo'sh", bar(0, 100) == "░" * 10)
    check("to'liq ulush to'la", bar(100, 100) == "█" * 10)
    # Kichik lekin mavjud ulush ko'rinishi kerak, aks holda "umuman yo'q"
    # degan noto'g'ri taassurot berardi.
    check("kichik ulush ham ko'rinadi", bar(1, 1000).startswith("█"))
    check("nol jami yiqilmaydi", bar(5, 0) == "░" * 10)

    # Jadval ustunlari teng kenglikda bo'lishi kerak — `<pre>` monoshirina.
    rendered = table([("a", "1"), ("bbb", "22")], align="lr").split("\n")
    check("jadval ustunlari tekislanadi", len({len(r) for r in rendered}) == 1, str(rendered))

    async with AsyncSessionLocal() as session:
        data = await StatsService(session).collect()

    check("foydalanuvchilar sanaldi", data.users_total > 0, num(data.users_total))
    check("holatlar bo'yicha bo'lindi", bool(data.users_by_status), str(data.users_by_status))
    check("interfeys tillari bor", bool(data.users_by_lang), str(data.users_by_lang))
    check("tarjimalar sanaldi", data.translations_total > 0, num(data.translations_total))

    out = render(data)
    check("Telegram chegarasiga sig'adi", len(out) < 4096, f"{len(out)} belgi")

    # Telefonda `<pre>` bloki ekran kengligidan oshsa Telegram uni gorizontal
    # siljitadigan qiladi — xabar buzilmaydi, lekin to'liq ko'rinmaydi.
    import re as _re

    from bot.services.stats import MAX_PRE_WIDTH

    pre_lines = [
        line
        for block in _re.findall(r"<pre>(.*?)</pre>", out, _re.S)
        for line in block.split("\n")
    ]
    too_wide = [line for line in pre_lines if len(line) > MAX_PRE_WIDTH]
    longest = max((len(line) for line in pre_lines), default=0)
    check(
        f"barcha <pre> qatorlari <= {MAX_PRE_WIDTH} belgi (telefon)",
        not too_wide,
        f"eng uzun {longest}" + (f", oshgan: {too_wide[:2]}" if too_wide else ""),
    )
    # Emoji `len()` da 1-2 belgi, ekranda boshqacha kenglikda — ustunlar siljiydi.
    emoji_lines = [
        line for line in pre_lines if any(ord(ch) > 0x2500 and ch not in "█░→" for ch in line)
    ]
    check("<pre> ichida emoji yo'q", not emoji_lines, str(emoji_lines[:2]))
    check("yig'iladigan blok bor", "<blockquote expandable>" in out)
    check("monoshirina jadval bor", "<pre>" in out)
    # Ochilgan teglar yopilgan bo'lishi shart, aks holda Telegram xabarni rad etadi.
    for tag in ("b", "i", "pre", "blockquote"):
        check(
            f"<{tag}> teglari muvozanatda",
            out.count(f"<{tag}>") + (out.count("<blockquote expandable>") if tag == "blockquote" else 0)
            == out.count(f"</{tag}>"),
        )


async def test_support_record() -> None:
    """`SupportRepository.record()` — endi FAQAT tarix/audit uchun
    (2026-08-27dan suhbat aniqlash uchun ishlatilmaydi, buni pin qiladi).
    """
    print("\n[16] Murojaat tarixi (audit yozuvi)")
    from sqlalchemy import delete, func, select

    from bot.database.models import SupportMessage, User, UserSettings
    from bot.database.repositories.support_repository import SupportRepository

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 50, first_name="Ip")
        await session.commit()

        support = SupportRepository(session)
        await support.record(
            user_id=user.id, direction="in", text="savol",
            admin_chat_id=111, admin_message_id=900,
            user_chat_id=user.telegram_id, user_message_id=500,
        )
        await support.record(
            user_id=user.id, direction="out", text="javob",
            admin_chat_id=111, admin_message_id=901,
            user_chat_id=user.telegram_id, user_message_id=501,
        )
        await session.commit()

        count = (
            await session.execute(
                select(func.count(SupportMessage.id)).where(SupportMessage.user_id == user.id)
            )
        ).scalar_one()
        check("tarix yozuvlari saqlanadi", count == 2, str(count))

        await session.execute(delete(SupportMessage).where(SupportMessage.user_id == user.id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_admin_reply_pin() -> None:
    """"↩️ Javob yozish" tugmasi — Telegram `reply_to_message`ni ESKI
    xabarlar uchun har doim ham uzatavermasligi (2026-08-27 haqiqiy
    voqea) sabab qo'shilgan zaxira yo'l. FSM EMAS — oddiy DB qatori,
    muddatsiz, faqat ISHLATILGANDA o'chadi.

    Har bosqich ALOHIDA sessiyada (ishlab turgan botdagi kabi — bitta
    so'rov = bitta sessiya) — `AsyncSessionLocal` `autoflush=False` bilan
    sozlangani uchun bitta sessiya ichida ketma-ket chaqiruv `delete()`ni
    hali bazaga yubormasdan "eski" natijani ko'rsatishi mumkin edi, bu esa
    haqiqiy oqimni noto'g'ri aks ettirardi.
    """
    print("\n[16d] Admin javob-nishoni (pin) — reply ishlamasa ham")
    from sqlalchemy import delete

    from bot.database.models import AdminReplyTarget, User, UserSettings
    from bot.database.repositories.support_repository import SupportRepository

    admin_chat_id = 777777001
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 51, first_name="PinTarget")
        other, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 52, first_name="PinTarget2")
        await session.commit()
        user_id, other_id = user.id, other.id

    async with AsyncSessionLocal() as session:
        support = SupportRepository(session)
        check("boshida pin yo'q", await support.get_pinned_target(admin_chat_id) is None)

        found_by_id = await support.find_user_by_id(user_id)
        check(
            "ichki id bo'yicha topiladi (tugma shuni ishlatadi)",
            found_by_id is not None and found_by_id.telegram_id == user.telegram_id,
        )

        await support.set_pinned_target(admin_chat_id, user_id)
        await session.commit()

    async with AsyncSessionLocal() as session:
        pinned = await SupportRepository(session).get_pinned_target(admin_chat_id)
        check("pin o'rnatilgach topiladi", pinned is not None and pinned.id == user_id, str(pinned))
        # `deliver_admin_message` `target.settings.interface_lang`ni o'qiydi —
        # bu YANGI (bo'sh identity map) sessiyada eager-load qilinmagan
        # bo'lsa `MissingGreenlet` bilan jimgina yiqilardi (loyihada bu
        # xato ilgari boshqa joyda ham uchragan).
        try:
            _ = pinned.settings.interface_lang if pinned else None
            check("sozlamalar eager yuklangan (MissingGreenlet yo'q)", True)
        except Exception as exc:
            check("sozlamalar eager yuklangan (MissingGreenlet yo'q)", False, type(exc).__name__)
        await session.commit()

    # Bir martalik: OLDINGI so'rov PIN'ni allaqachon o'chirgan — keyingi
    # (yangi) so'rov bo'sh qaytishi kerak, aks holda admin bitta tugma
    # bosib, cheksiz keyingi xabarlarini ham shu userga yuborib yuborardi.
    async with AsyncSessionLocal() as session:
        check(
            "pin bir martalik — ishlatilgach o'chadi",
            await SupportRepository(session).get_pinned_target(admin_chat_id) is None,
        )

    # Qayta pin qilib, keyin BOSHQA userga qayta pin qilish eskisini
    # almashtirishi kerak (bitta admin — bitta faol nishon).
    async with AsyncSessionLocal() as session:
        support = SupportRepository(session)
        await support.set_pinned_target(admin_chat_id, user_id)
        await support.set_pinned_target(admin_chat_id, other_id)
        await session.commit()

    async with AsyncSessionLocal() as session:
        pinned2 = await SupportRepository(session).get_pinned_target(admin_chat_id)
        check(
            "qayta pin eskisini almashtiradi", pinned2 is not None and pinned2.id == other_id,
            str(pinned2),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await session.execute(delete(AdminReplyTarget).where(AdminReplyTarget.admin_chat_id == admin_chat_id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id.in_([user_id, other_id])))
        await session.execute(delete(User).where(User.id.in_([user_id, other_id])))
        await session.commit()


async def test_support_parsing() -> None:
    """Murojaat xabaridan kontent turini ajratish (`_describe`).

    Baza kerak emas — sof funksiya, `SimpleNamespace` bilan Telegram
    xabarini taqlid qilamiz.
    """
    print("\n[16c] Murojaat kontentini aniqlash")
    from types import SimpleNamespace

    from bot.handlers.user.support import _describe

    check(
        "matn o'zi olinadi",
        _describe(SimpleNamespace(text="salom", caption=None)) == "salom",
    )
    photo = SimpleNamespace(text=None, caption=None, photo=[object()])
    check("rasm belgilanadi", "rasm" in _describe(photo), _describe(photo))
    voice = SimpleNamespace(text=None, caption=None, photo=None, video=None,
                            animation=None, voice=object())
    check("ovoz belgilanadi", "ovoz" in _describe(voice), _describe(voice))
    captioned = SimpleNamespace(text=None, caption="izoh matni", photo=[object()])
    check("izoh matn sifatida olinadi", _describe(captioned) == "izoh matni")


async def test_content_extraction() -> None:
    """Har xil kontentdan matn ajratish: izoh, post, so'rovnoma, checklist."""
    print("\n[17] Kontentdan matn ajratish")
    from types import SimpleNamespace

    from aiogram.types import (
        Checklist, ChecklistTask, PhotoSize, Poll, PollOption,
        RichBlockBlockQuotation, RichBlockCaption, RichBlockList,
        RichBlockListItem, RichBlockMathematicalExpression, RichBlockParagraph,
        RichBlockPhoto, RichBlockPreformatted, RichBlockSectionHeading,
        RichMessage, RichTextBold, RichTextCode, RichTextItalic,
    )

    from bot.database.models import INPUT_KINDS
    from bot.utils.content import extract, rich_message_text

    post = RichMessage(blocks=[
        RichBlockSectionHeading(type="heading", text="Sarlavha", size=1),
        RichBlockParagraph(type="paragraph", text="Oddiy paragraf."),
        # Ichma-ich formatlash: rekursiya va takrorni tashlash sinovi.
        RichBlockParagraph(type="paragraph", text=RichTextBold(
            type="bold", text=RichTextItalic(type="italic", text="Qalin kursiv"))),
        RichBlockParagraph(type="paragraph", text=RichTextCode(
            type="code", text="inline_kod()")),
        RichBlockBlockQuotation(type="blockquote", credit="Muallif",
            blocks=[RichBlockParagraph(type="paragraph", text="Iqtibos")]),
        RichBlockList(type="list", items=[
            RichBlockListItem(label="1", blocks=[
                RichBlockParagraph(type="paragraph", text="Band bir")])]),
        RichBlockPhoto(type="photo", caption=RichBlockCaption(text="Rasm izohi"),
            photo=[PhotoSize(file_id="a", file_unique_id="b", width=1, height=1)]),
        RichBlockPreformatted(type="pre", text="def kod(): pass", language="python"),
        RichBlockMathematicalExpression(type="mathematical_expression", expression="E=mc^2"),
    ])
    text = rich_message_text(post)

    for want in ("Sarlavha", "Oddiy paragraf.", "Qalin kursiv", "Iqtibos",
                 "Muallif", "Band bir", "Rasm izohi"):
        check(f"postdan olinadi: {want}", want in text)

    # Kod va formula tarjima qilinmasligi kerak — tarjima ularni buzadi.
    for avoid, label in (("def kod", "kod bloki"), ("inline_kod", "inline kod"),
                         ("E=mc^2", "formula"), ("python", "kod tili")):
        check(f"olinmaydi: {label}", avoid not in text)
    check("ro'yxat belgisi olinmaydi", "\n1\n" not in text)

    # Ichma-ich formatlash bitta matn beradi, uch marta emas.
    check("takror yo'q", text.count("Qalin kursiv") == 1)

    # `extract` — kirish turini ham aniqlaydi.
    photo = SimpleNamespace(
        rich_message=None, text=None, caption="Rasm ostidagi izoh",
        photo=[1], video=None, document=None, audio=None, animation=None,
        voice=None, video_note=None, paid_media=None, poll=None, checklist=None,
    )
    got = extract(photo)
    check("rasm izohi olinadi", got == ("Rasm ostidagi izoh", "photo"), str(got))

    doc = SimpleNamespace(
        rich_message=None, text=None, caption="Hujjat izohi",
        photo=None, video=None, document=object(), audio=None, animation=None,
        voice=None, video_note=None, paid_media=None, poll=None, checklist=None,
    )
    check("hujjat izohi olinadi", extract(doc) == ("Hujjat izohi", "document"))

    poll = SimpleNamespace(
        rich_message=None, text=None, caption=None, poll=Poll(
            id="1", question="Savol?", options=[
                PollOption(persistent_id="1", text="Ha", voter_count=0),
                PollOption(persistent_id="2", text="Yo'q", voter_count=0)],
            total_voter_count=0, is_closed=False, is_anonymous=True,
            type="regular", allows_multiple_answers=False,
            allows_revoting=False, members_only=False),
        checklist=None,
    )
    got = extract(poll)
    check("so'rovnoma savol va variantlari", got is not None and "Savol?" in got[0] and "Ha" in got[0])
    check("so'rovnoma turi", got is not None and got[1] == "poll")

    checklist = SimpleNamespace(
        rich_message=None, text=None, caption=None, poll=None,
        checklist=Checklist(title="Ro'yxat", tasks=[
            ChecklistTask(id=1, text="Birinchi ish"),
            ChecklistTask(id=2, text="Ikkinchi ish")]),
    )
    got = extract(checklist)
    check("checklist sarlavha va vazifalari",
          got is not None and "Ro'yxat" in got[0] and "Ikkinchi ish" in got[0])

    empty = SimpleNamespace(
        rich_message=None, text=None, caption=None, poll=None, checklist=None,
    )
    check("matnsiz kontent None qaytaradi", extract(empty) is None)

    # Ajratgich qaytaradigan har bir tur bazadagi cheklovga sig'ishi shart,
    # aks holda tarjima yozuvi CheckViolation bilan yiqilardi.
    produced = {"text", "post", "poll", "checklist", "caption",
                "photo", "video", "document", "audio", "animation",
                "voice", "video_note", "paid_media"}
    check("barcha turlar bazada ruxsat etilgan",
          produced <= set(INPUT_KINDS), str(produced - set(INPUT_KINDS)))


async def test_membership_status() -> None:
    """`my_chat_member`: bloklash/blokdan chiqarish darhol `users.status`ga."""
    print("\n[my_chat_member] bloklash holati")
    from types import SimpleNamespace

    from bot.database.models import User
    from bot.handlers.user.membership import on_private_membership

    def upd(old: str, new: str, chat_id: int):
        return SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type="private"),
            old_chat_member=SimpleNamespace(status=old),
            new_chat_member=SimpleNamespace(status=new),
        )

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 61, first_name="Member")
        await session.execute(
            User.__table__.update().where(User.id == user.id).values(status="active", blocked_at=None)
        )
        await session.commit()
        events = EventService(session)

        async def status() -> str:
            await session.commit()
            return (await session.execute(select(User.status).where(User.id == user.id))).scalar_one()

        await on_private_membership(upd("member", "kicked", user.telegram_id), user, events, session)
        check("kicked -> blocked_bot", await status() == "blocked_bot")
        await on_private_membership(upd("kicked", "member", user.telegram_id), user, events, session)
        check("member (kicked'dan) -> active", await status() == "active")

        await session.execute(
            User.__table__.update().where(User.id == user.id).values(status="banned")
        )
        await on_private_membership(upd("kicked", "member", user.telegram_id), user, events, session)
        check("admin bani (banned) blokdan chiqishda o'zgarmaydi", await status() == "banned")

        await session.execute(
            User.__table__.update().where(User.id == user.id).values(status="active")
        )
        await on_private_membership(upd("member", "member", user.telegram_id), user, events, session)
        check("holat o'zgarmasa -> hech narsa", await status() == "active")
        await session.commit()


async def test_fallback_pin_admin_report() -> None:
    """Noma'lum buyruq filtri, pin muddati, yagona admin tekshiruvi va
    kunlik hisobot (render + idempotentlik) — DB'siz, sof mantiq."""
    print("\n[*] Fallback / pin TTL / admin_access / kunlik hisobot")
    from datetime import date, datetime, timedelta, timezone
    from types import SimpleNamespace

    from bot.handlers.admin import panel
    from bot.handlers.user import fallback, support
    from bot.services import admin_access, daily_report

    # ── Fallback filtri ──
    f = fallback.should_answer_unknown_command
    check("fallback: /foo shaxsiy chatda — javob", f("/foo", "private"))
    check("fallback: /foo@Bot arg — javob", f("/foo@SomeBot arg", "private"))
    check("fallback: guruhda — jim", not f("/foo", "group") and not f("/foo", "supergroup"))
    check("fallback: oddiy matn — jim", not f("salom", "private"))
    check("fallback: yo'l (/usr/bin) — buyruq emas", not f("/usr/bin", "private"))
    check("fallback: yolg'iz / va None — jim", not f("/", "private") and not f(None, "private"))
    check(
        "fallback: lokal kaliti hamma tilda",
        all(getattr(locales.get(c), "UNKNOWN_COMMAND", "") for c in locales.SUPPORTED),
    )
    main_src = (Path(__file__).resolve().parent.parent / "bot" / "main.py").read_text()
    check(
        "fallback routeri translate'dan KEYIN ulanadi",
        0 < main_src.find("include_router(translate.router)")
        < main_src.find("include_router(fallback.router)"),
    )

    # ── Pin muddati ──
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    check("pin TTL = 30 daqiqa", support.PIN_TTL == timedelta(minutes=30))
    check("pin: 29 daqiqa — amal qiladi", not support.is_pin_expired(now - timedelta(minutes=29), now))
    check("pin: 31 daqiqa — muddati o'tgan", support.is_pin_expired(now - timedelta(minutes=31), now))
    check("pin: set_at yo'q — o'tgan deb olinadi", support.is_pin_expired(None, now))
    check(
        "pin: naive vaqt UTC deb olinadi",
        not support.is_pin_expired(datetime(2026, 11, 10, 11, 50), now)
        and support.is_pin_expired(datetime(2026, 11, 10, 11, 0), now),
    )

    # ── Yagona admin tekshiruvi (imtiyoz oshmasligi) ──
    outsider = 999_000_777
    db_admin = SimpleNamespace(role="admin")
    db_owner = SimpleNamespace(role="owner")
    plain = SimpleNamespace(role="user")
    check("admin: None — hech qachon admin emas", not admin_access.is_admin(None, db_admin))
    check(
        "admin: DB roli panelga kirish BERMAYDI",
        not admin_access.is_admin(outsider, db_admin) and not panel.is_admin(outsider),
    )
    check(
        "admin: DB roli obuna/ban chetlab o'tishda hisoblanadi",
        admin_access.is_admin(outsider, db_admin, include_db_role=True)
        and admin_access.is_admin(outsider, db_owner, include_db_role=True),
    )
    check(
        "admin: oddiy rol — hech qayerda admin emas",
        not admin_access.is_admin(outsider, plain, include_db_role=True),
    )
    if settings.ADMIN_USER_IDS:
        env_id = settings.ADMIN_USER_IDS[0]
        check(
            "admin: .env admin — ikkala rejimda ham admin",
            panel.is_admin(env_id)
            and admin_access.is_admin(env_id, plain, include_db_role=True)
            and support._is_admin(env_id),
        )

    # ── Vaqt chegaralari (Toshkent = UTC+5) ──
    start, end = daily_report.day_bounds_utc(date(2026, 11, 9))
    check(
        "hisobot: Toshkent kuni UTC oralig'i",
        start == datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)
        and end == datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc),
    )
    check(
        "hisobot: UTC 20:00 = Toshkentda ertasi — oldingi kun to'g'ri",
        daily_report.previous_tashkent_day(datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc))
        == date(2026, 11, 9),
    )
    check(
        "hisobot: 09:00 Toshkent (04:00 UTC) — vaqti keldi",
        not daily_report.is_due(datetime(2026, 11, 10, 3, 59, tzinfo=timezone.utc))
        and daily_report.is_due(datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)),
    )
    wait = daily_report.seconds_until_next_check(datetime(2026, 11, 10, 3, 59, tzinfo=timezone.utc))
    check("hisobot: keyingi tekshiruvgacha kutish", 1 <= wait <= 60, f"{wait:.0f}s")

    # ── /report argumentlari ──
    p_now = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    check("/report — kecha", panel.parse_report_args(None, p_now)[0] == date(2026, 11, 9))
    today = panel.parse_report_args("today", p_now)
    check("/report today — bugun, hozirgacha", today[0] == date(2026, 11, 10) and today[2] == p_now)
    check("/report YYYY-MM-DD", panel.parse_report_args("2026-11-01", p_now)[0] == date(2026, 11, 1))
    check(
        "/report: kelajak/xato argument rad etiladi",
        panel.parse_report_args("2026-12-01", p_now) is None
        and panel.parse_report_args("abc", p_now) is None,
    )

    # ── Render (soxta raqamlar + escape) ──
    rep = daily_report.DailyReport(
        day=date(2026, 11, 9),
        translations_total=1000,
        translations_success=970,
        providers={"mymemory": 500, "local_mt": 300, "cache": 190, "<evil>&": 10},
        long_total=40,
        long_success=36,
        local_mt_p50_ms=812.4,
        ocr_total=25,
        ocr_failed=3,
        tts_requested=120,
        tts_failed=4,
        new_users=57,
        blocked_bot=6,
        donations_count=2,
        donations_stars=150,
    )
    rep.label = "<b>x</b>"
    html = daily_report.render(rep)
    check("render: provayder nomi escape qilingan", "&lt;evil&gt;&amp;" in html and "<evil>" not in html)
    check("render: yorliq escape qilingan", "&lt;b&gt;x&lt;/b&gt;" in html)
    check("render: <b> teglari juft", html.count("<b>") == html.count("</b>"))
    check("render: xato ulushi", "❌ 30 (3.0%)" in html)
    check("render: uzun matn ulushi", "Uzun (&gt;500): 40 · ✅ 90.0%" in html)
    check("render: local_mt p50", "p50: 812 ms" in html)
    check(
        "render: provayder ulushi va qolgan raqamlar",
        "mymemory — 500 (50.0%)" in html and "Yangi: 57" in html
        and "Bloklagan: 6" in html and "TTS: 120 · ❌ 4" in html
        and "OCR: 25 · ❌ 3" in html and "Homiylik: 2 (150" in html,
    )
    empty = daily_report.render(daily_report.DailyReport(day=date(2026, 11, 9)))
    check("render: bo'sh kun — nolga bo'linish yo'q", "0.0%" in empty and "p50: —" in empty)

    # ── Idempotentlik ──
    day = date(2026, 11, 9)
    check("hisobot kaliti", daily_report.idempotency_key(day) == "daily_report:sent:2026-11-09")
    local: set = set()
    first = await daily_report.claim_day(None, day, local)
    second = await daily_report.claim_day(None, day, local)
    other = await daily_report.claim_day(None, day + timedelta(days=1), local)
    check("Redis'siz: kuniga bir marta", first and not second and other)
    await daily_report.release_day(None, day, local)
    check("Redis'siz: bo'shatilgach qayta egallanadi", await daily_report.claim_day(None, day, local))

    class FakeRedis:
        def __init__(self):
            self.data: dict = {}
            self.ttl: dict = {}

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.data:
                return None
            self.data[key] = value
            self.ttl[key] = ex
            return True

        async def delete(self, key):
            self.data.pop(key, None)

    class BrokenRedis:
        async def set(self, *a, **k):
            raise ConnectionError("down")

        async def delete(self, *a, **k):
            raise ConnectionError("down")

    fake = FakeRedis()
    r1 = await daily_report.claim_day(fake, day, set())
    r2 = await daily_report.claim_day(fake, day, set())  # "restart" — yangi xotira
    check("Redis: restartdan keyin ham takror yo'q", r1 and not r2)
    check(
        "Redis: kalitda TTL bor",
        fake.ttl.get(daily_report.idempotency_key(day)) == daily_report.REDIS_KEY_TTL_SECONDS,
    )
    await daily_report.release_day(fake, day, set())
    check("Redis: bo'shatilgach qayta egallanadi", await daily_report.claim_day(fake, day, set()))
    broken_local: set = set()
    b1 = await daily_report.claim_day(BrokenRedis(), day, broken_local)
    b2 = await daily_report.claim_day(BrokenRedis(), day, broken_local)
    check("Redis nosoz: xotiradagi belgiga o'tadi", b1 and not b2)

async def test_no_db_transaction_during_provider_call() -> None:
    """P1 (audit 2026-11): provayder chaqiruvi paytida sessiyada OCHIQ
    TRANZAKSIYA bo'lmasligi kerak — aks holda pool ulanishi (10+20) va
    `daily_usage` qator qulfi 15–60 s band turardi.

    Haqiqiy handler (`_translate`) haqiqiy sessiya bilan ishga tushiriladi,
    faqat provayder (`TranslationService.translate`) monkeypatch qilinadi:
    u chaqirilgan NUQTADA `session.in_transaction()` tekshiriladi. Uch holat:
    provayder xatosi (kvota qaytariladi), muvaffaqiyat (kvota sarflanadi),
    kutilmagan xato (kvota qaytariladi, xato yuqoriga ko'tariladi).
    """
    print("\n[7c] Provayder chaqiruvida tranzaksiya ushlab turilmaydi")
    from types import SimpleNamespace

    from sqlalchemy import delete

    from bot.database.models import DailyUsage, User, UserSettings
    from bot.handlers.user import translate as handler
    from bot.services.translation import TranslationResult

    telegram_id = TEST_TELEGRAM_ID + 93
    seen: dict = {}
    outcome: dict = {}

    class _FakeSent:
        """`message.answer()` qaytargan xabar — faqat `delete()` kerak."""

        def __init__(self, log: list) -> None:
            self._log = log

        async def delete(self) -> None:
            self._log.append("deleted")

    class _FakeBot:
        async def send_chat_action(self, *args, **kwargs) -> None:
            return None

        async def send_message(self, *args, **kwargs) -> None:
            return None

    class _FakeMessage:
        def __init__(self) -> None:
            self.bot = _FakeBot()
            self.chat = SimpleNamespace(id=telegram_id, type="private")
            self.message_id = 1
            self.sent: list = []

        async def answer(self, text, **kwargs) -> _FakeSent:
            self.sent.append(text)
            return _FakeSent(self.sent)

    real_session_holder: dict = {}

    async def fake_translate(self, session, text, source, target):
        real_session = real_session_holder["session"]
        seen["wrapper_in_tx"] = session.in_transaction()
        seen["real_in_tx"] = real_session.in_transaction()
        # Servis ichidagi `pick_key` kabi SELECT ham ulanishni qoldirmasin
        # (handler `StatementScopedSession` beradi — har so'rov o'z commit'i).
        rows = await session.execute(select(func.count(User.id)))
        seen["select_result_usable"] = rows.scalar_one() >= 1
        seen["after_select_in_tx"] = real_session.in_transaction()
        mode = outcome["mode"]
        if mode == "fail":
            raise TranslationError("provider_error", "sinov", provider="deep_translator")
        if mode == "crash":
            raise RuntimeError("sinov: kutilmagan xato")
        return TranslationResult(
            text="ok", source_lang_detected="uz", provider="deep_translator",
            provider_model=None, latency_ms=1,
        )

    original_translate = TranslationService.translate
    TranslationService.translate = fake_translate  # type: ignore[method-assign]
    try:
        async with AsyncSessionLocal() as session:
            real_session_holder["session"] = session
            repo = UserRepository(session)
            user, _ = await repo.get_or_create(telegram_id, first_name="TxSinov")
            await session.commit()
            user_id = user.id
            # Oldingi (yarim yo'lda to'xtagan) ishga tushirishdan qolgan qatorlar
            # sanoqlarni buzmasin — sinov toza holatdan boshlanadi.
            from bot.database.models import DailyUsage
            await session.execute(delete(Translation).where(Translation.user_id == user_id))
            await session.execute(delete(DailyUsage).where(DailyUsage.user_id == user_id))
            await session.commit()
            events = EventService(session)
            t = locales.get("uz")
            usage = QuotaService(session).usage
            # > 500 belgi — "⏳" xabari chiqishi kerak.
            long_text = "Salom dunyo, bu uzun sinov matni. " * 20
            check("sinov matni 500 belgidan uzun", len(long_text) > handler.PROGRESS_NOTICE_MIN_CHARS)

            async def run(mode: str, text: str) -> _FakeMessage:
                outcome["mode"] = mode
                seen.clear()
                msg = _FakeMessage()
                try:
                    await handler._translate(
                        msg, session, user, events, None, t, None,
                        text=text, input_kind="text",
                    )
                    await session.commit()  # middleware kabi
                except RuntimeError:
                    await session.rollback()  # middleware kabi
                    seen["raised"] = True
                return msg

            # 1) Provayder xatosi.
            msg = await run("fail", long_text)
            check(
                "provayder chaqiruvida tranzaksiya yo'q",
                seen.get("real_in_tx") is False and seen.get("wrapper_in_tx") is False,
                f"real={seen.get('real_in_tx')} wrapper={seen.get('wrapper_in_tx')}",
            )
            check(
                "servis SELECT'idan keyin ham tranzaksiya yo'q",
                seen.get("after_select_in_tx") is False and seen.get("select_result_usable"),
            )
            row = await usage.get(user_id)
            used = row.translations_count if row else 0
            check("provayder xatosida kvota qaytarildi", used == 0, f"used={used}")
            check(
                "⏳ xabari yuborildi va o'chirildi",
                msg.sent[:1] == [t.TRANSLATING_LONG_TEXT] and "deleted" in msg.sent,
                str(msg.sent),
            )
            check("xato xabari yuborildi", t.ERRORS.get("provider_error", t.ERROR_DEFAULT) in msg.sent)
            failed = (
                await session.execute(
                    select(func.count(Translation.id)).where(
                        Translation.user_id == user_id, Translation.status == "error"
                    )
                )
            ).scalar_one()
            check("xato qatori yozildi", failed == 1, f"{failed} ta")

            # 2) Muvaffaqiyat.
            msg = await run("ok", long_text)
            row = await usage.get(user_id)
            used = row.translations_count if row else 0
            check("muvaffaqiyatda kvota sarflandi", used == 1, f"used={used}")
            check("natija yuborildi", any("<code>ok</code>" in s for s in msg.sent), str(msg.sent))
            check("muvaffaqiyatda ham ⏳ o'chirildi", "deleted" in msg.sent)
            ok_rows = (
                await session.execute(
                    select(func.count(Translation.id)).where(
                        Translation.user_id == user_id, Translation.status == "success"
                    )
                )
            ).scalar_one()
            check("muvaffaqiyat qatori yozildi", ok_rows == 1, f"{ok_rows} ta")

            # 3) Kutilmagan xato — erta commit'dan keyin middleware rollback'i
            # kvotani qaytara olmaydi, handler o'zi qaytarishi kerak.
            msg = await run("crash", "qisqa matn")
            # Middleware kabi rollback'dan so'ng — testning o'z SELECT'laridan OLDIN
            # (ular yangi tranzaksiyani o'zi ochib qo'yadi).
            check("handlerdan keyin sessiyada tranzaksiya yo'q", session.in_transaction() is False)
            check("kutilmagan xato yuqoriga ko'tarildi", seen.get("raised") is True)
            row = await usage.get(user_id)
            used = row.translations_count if row else 0
            check("kutilmagan xatoda ham kvota qaytarildi", used == 1, f"used={used}")
            check("qisqa matnda ⏳ chiqmaydi", t.TRANSLATING_LONG_TEXT not in msg.sent)

            await session.execute(delete(TranslationSignal).where(TranslationSignal.user_id == user_id))
            await session.execute(delete(Event).where(Event.user_id == user_id))
            await session.execute(delete(Translation).where(Translation.user_id == user_id))
            await session.execute(delete(DailyUsage).where(DailyUsage.user_id == user_id))
            await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()
    finally:
        TranslationService.translate = original_translate  # type: ignore[method-assign]


async def test_mymemory_entities() -> None:
    """MyMemory javobidagi HTML-entity'lar dekodlanadi (`&#39;` foydalanuvchiga chiqmasin)."""
    print("\n[MyMemory] HTML-entity dekodlash")
    import bot.services.translation_providers as tp

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"responseStatus": 200,
                    "responseData": {"translatedText": "Xayrli tong, dam olish kunlaringiz qanday o&#39;tdi? &quot;A&amp;B&quot;"}}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    original = tp.aiohttp.ClientSession
    tp.aiohttp.ClientSession = _Client
    try:
        out = await tp.call_mymemory("Good morning", "en", "uz", "smoke@example.com")
    finally:
        tp.aiohttp.ClientSession = original
    check("&#39; -> '", "o'tdi" in out and "&#39;" not in out, out)
    check("&quot;/&amp; ham dekodlanadi", '"A&B"' in out, out)
    check("handler escape'i bitta marta: &amp; -> &", html_escape(out).count("&amp;") == 1, html_escape(out))


async def cleanup() -> None:
    async with AsyncSessionLocal() as session:
        from sqlalchemy import delete

        from bot.database.models import DailyUsage, Donation, User

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
            await session.execute(delete(Donation).where(Donation.user_id == user.id))
            await session.execute(delete(DailyUsage).where(DailyUsage.user_id == user.id))
            await session.delete(user)
            await session.commit()


async def test_ocr_resilience() -> None:
    """OCR: javobni tasniflash, "matn yo'q" xato emasligi, vaqt byudjeti,
    rasmga bog'liq xatoda kalitlarni behuda aylanmaslik, katta rasm.

    Tarmoqsiz va bazasiz — chaqiruvlar va oylik hisoblar soxtalashtiriladi.
    """
    print("\n[18] OCR chidamliligi")
    from bot.services import ocr as ocr_module
    from bot.services.ocr import OcrError, parse_ocrspace_response, parse_vision_response

    def _code(fn, *args) -> str:
        try:
            fn(*args)
        except OcrError as exc:
            return exc.code
        return "ok"

    ok_page = {"FileParseExitCode": 1, "ParsedText": "Salom\r\ndunyo"}
    check(
        "OCR.Space: muvaffaqiyatli sahifa matni olinadi",
        parse_ocrspace_response(200, {"ParsedResults": [ok_page]}) == "Salom\r\ndunyo",
    )
    check(
        "OCR.Space: matnsiz rasm — xato emas, bo'sh satr",
        parse_ocrspace_response(
            200, {"ParsedResults": [{"FileParseExitCode": 1, "ParsedText": ""}]}
        ) == "",
    )
    check(
        "OCR.Space: E500 -> provider_error",
        _code(parse_ocrspace_response, 200,
              {"IsErroredOnProcessing": True, "ErrorMessage": ["E500: Error during OCR"]})
        == "provider_error",
    )
    check(
        "OCR.Space: E214 (hajm) -> invalid_image ('maximum' so'ziga qaramay)",
        _code(parse_ocrspace_response, 200, {
            "IsErroredOnProcessing": True,
            "ErrorMessage": ["E214: File failed validation. Maximum file size limit 1024 KB exceeded"],
        }) == "invalid_image",
    )
    check(
        "OCR.Space: soatlik limit (HTTP 403) -> key_error",
        _code(parse_ocrspace_response, 403,
              "You may only perform this action upto maximum 180 number of times") == "key_error",
    )
    check(
        "OCR.Space: HTML 503 sahifa -> provider_error",
        _code(parse_ocrspace_response, 503, None) == "provider_error",
    )
    check(
        "OCR.Space: sahifa timeout'i (-20) -> timeout",
        _code(parse_ocrspace_response, 200, {"ParsedResults": [
            {"FileParseExitCode": -20, "ErrorMessage": "Timeout waiting for OCR"}
        ]}) == "timeout",
    )
    check(
        "Vision: billing/kalit xatosi -> key_error",
        _code(parse_vision_response, 403,
              {"error": {"code": 403, "message": "This API method requires billing to be enabled"}})
        == "key_error",
    )
    check(
        "Vision: matnsiz rasm — xato emas, bo'sh satr",
        parse_vision_response(200, {"responses": [{}]}) == "",
    )
    check(
        "Vision: fullTextAnnotation matni olinadi",
        parse_vision_response(200, {"responses": [{"fullTextAnnotation": {"text": " Hi \n"}}]}) == "Hi",
    )

    originals = {
        "keys_raw": settings.OCRSPACE_API_KEYS_RAW,
        "key": settings.OCRSPACE_API_KEY,
        "vision": settings.GOOGLE_VISION_API_KEY,
        "free": settings.OCRSPACE_FREE_MONTHLY,
        "usage": ocr_module._ocrspace_key_usage,
        "vision_usage": ocr_module._vision_monthly_usage,
        "call_ocrspace": ocr_module._call_ocrspace,
        "call_vision": ocr_module._call_google_vision,
        "budget": ocr_module.OCR_TOTAL_BUDGET,
        "timeout": ocr_module.OCR_TIMEOUT,
        "min_attempt": ocr_module.OCR_MIN_ATTEMPT_SECONDS,
    }
    calls: list[str] = []

    async def _no_usage(_session) -> dict:
        return {}

    async def _vision_usage(_session) -> int:
        return 0

    async def _vision_ok(_image_bytes: bytes) -> str:
        calls.append("vision")
        return "vision matni"

    async def _ocrspace_invalid_image(_image_bytes: bytes, api_key: str) -> str:
        calls.append(api_key)
        raise OcrError("invalid_image", "E301: Unable to recognize the file type")

    async def _ocrspace_hangs(_image_bytes: bytes, api_key: str) -> str:
        calls.append(api_key)
        await asyncio.sleep(5)
        return "kech"

    async def _ocrspace_raw_timeout(_image_bytes: bytes, api_key: str) -> str:
        calls.append(api_key)
        raise asyncio.TimeoutError()

    ocr_module._ocrspace_key_usage = _no_usage
    ocr_module._vision_monthly_usage = _vision_usage
    ocr_module._call_google_vision = _vision_ok
    settings.OCRSPACE_FREE_MONTHLY = 25000
    settings.OCRSPACE_API_KEY = ""
    settings.OCRSPACE_API_KEYS_RAW = "keyA,keyB,keyC"
    try:
        # Rasmga bog'liq xato -> qolgan OCR.Space kalitlari sinalmaydi,
        # darhol Vision.
        calls.clear()
        settings.GOOGLE_VISION_API_KEY = "test-key"
        ocr_module._call_ocrspace = _ocrspace_invalid_image
        result = await ocr_module.extract_text(None, b"x")
        check(
            "invalid_image -> boshqa kalit sinalmaydi, Vision'ga o'tadi",
            calls == ["keyA", "vision"] and result.provider == "google_vision",
            f"calls={calls}",
        )

        # Vision yo'q, provayder osilib qoldi -> umumiy byudjet ichida
        # `timeout` bilan tugaydi (3 x timeout kutilmaydi).
        calls.clear()
        settings.GOOGLE_VISION_API_KEY = ""
        ocr_module.OCR_TOTAL_BUDGET = 0.6
        ocr_module.OCR_MIN_ATTEMPT_SECONDS = 0.3
        ocr_module._call_ocrspace = _ocrspace_hangs
        started = asyncio.get_running_loop().time()
        try:
            await ocr_module.extract_text(None, b"x")
            check("osilgan provayder -> xato", False, "OcrError kutilgan edi")
        except OcrError as exc:
            elapsed = asyncio.get_running_loop().time() - started
            check(
                "osilgan provayder -> byudjet ichida timeout",
                exc.code == "timeout" and elapsed < 2 and len(calls) == 1,
                f"code={exc.code}, {elapsed:.2f}s, calls={len(calls)}",
            )
        ocr_module.OCR_TOTAL_BUDGET = originals["budget"]
        ocr_module.OCR_MIN_ATTEMPT_SECONDS = originals["min_attempt"]

        # Xom asyncio.TimeoutError (bo'sh str) -> `timeout` kodi va sababli
        # xabar; boshqa kalitlar sinaladi (kalitga bog'liq bo'lishi mumkin).
        calls.clear()
        ocr_module._call_ocrspace = _ocrspace_raw_timeout
        try:
            await ocr_module.extract_text(None, b"x")
            check("xom timeout -> xato", False, "OcrError kutilgan edi")
        except OcrError as exc:
            check(
                "xom TimeoutError -> 'timeout' kodi, xabar bo'sh emas",
                exc.code == "timeout" and str(exc).strip() != "" and len(calls) == 3,
                f"code={exc.code}, calls={len(calls)}",
            )

        # OCR.Space chegarasidan katta rasm: Vision yo'q -> image_too_large,
        # OCR.Space umuman chaqirilmaydi; Vision bor -> Vision.
        big = b"\0" * (ocr_module.OCRSPACE_MAX_BYTES + 1)
        calls.clear()
        try:
            await ocr_module.extract_text(None, big)
            check("katta rasm -> xato", False, "OcrError kutilgan edi")
        except OcrError as exc:
            check(
                "katta rasm, Vision yo'q -> image_too_large (OCR.Space chaqirilmaydi)",
                exc.code == "image_too_large" and calls == [],
                f"code={exc.code}, calls={calls}",
            )
        settings.GOOGLE_VISION_API_KEY = "test-key"
        result = await ocr_module.extract_text(None, big)
        check(
            "katta rasm, Vision bor -> Vision, 'kvota tugagan' bayrog'i yo'q",
            result.provider == "google_vision" and not result.ocrspace_quota_exhausted
            and calls == ["vision"],
            f"calls={calls}",
        )
    finally:
        settings.OCRSPACE_API_KEYS_RAW = originals["keys_raw"]
        settings.OCRSPACE_API_KEY = originals["key"]
        settings.GOOGLE_VISION_API_KEY = originals["vision"]
        settings.OCRSPACE_FREE_MONTHLY = originals["free"]
        ocr_module._ocrspace_key_usage = originals["usage"]
        ocr_module._vision_monthly_usage = originals["vision_usage"]
        ocr_module._call_ocrspace = originals["call_ocrspace"]
        ocr_module._call_google_vision = originals["call_vision"]
        ocr_module.OCR_TOTAL_BUDGET = originals["budget"]
        ocr_module.OCR_TIMEOUT = originals["timeout"]
        ocr_module.OCR_MIN_ATTEMPT_SECONDS = originals["min_attempt"]


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
    await test_premium_and_referral()
    await test_image_translation()
    await test_admin_users()
    result = await test_translation()
    await test_translation_providers()
    await test_audit_fixes_2026_11()
    await test_translation_service_cleanup()

    await test_no_db_transaction_during_provider_call()
    await test_translation_record(user_id, result)
    await test_events(user_id)
    await test_tts()
    await test_keyboards()
    await test_support()
    await test_donate(user_id)
    await test_donate_on_paid_idempotent(user_id)
    await test_broadcast()
    await test_broadcast_resumable()
    await test_broadcast_runner_guards()
    await test_stats()
    await test_resilience()
    await test_tts_health()
    await test_membership_status()
    await test_support_record()
    await test_admin_reply_pin()
    await test_support_parsing()
    await test_content_extraction()
    await test_fallback_pin_admin_report()
    await test_ocr_resilience()
    await test_mymemory_entities()

    await cleanup()
    await engine.dispose()

    print("\n" + "=" * 60)
    print(f"Natija: {passed} o'tdi, {failed} yiqildi")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
