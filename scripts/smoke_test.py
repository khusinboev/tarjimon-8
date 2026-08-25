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
            t.HELP.format(limit=50, admin="someone")
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

        status = await quota.check_translation(user_id)
        check("boshlang'ich holat ruxsat", status.allowed and status.used == 0)

        for _ in range(3):
            await quota.consume_translation(user_id, chars=10)
        await session.commit()

        status = await quota.check_translation(user_id)
        check("hisob oshdi", status.used == 3, f"used={status.used}")

        status = await quota.check_translation(user_id, limit_override=3)
        check("limitga yetganda taqiqlanadi", not status.allowed)

        # Regressiya: `limit = limit_override or DEFAULT` Python'da `0` ni
        # yolg'on qiymat deb hisoblab, har doim DEFAULT'ga (50) tushib
        # qolgan edi. Usage hali DEFAULT'dan past bo'lgani uchun natija
        # tasodifan "allowed=True" chiqib, xatoni sinov ushlamagan edi.
        # Shu sababli bu yerda usage'ni ataylab DEFAULT'dan oshiramiz —
        # faqat shunda "0 = cheksiz" haqiqatan tekshiriladi.
        await quota.usage.increment(user_id, translations=settings.DAILY_TRANSLATION_LIMIT)
        await session.commit()
        status = await quota.check_translation(user_id, limit_override=0)
        check(
            "0 = cheksiz (usage DEFAULT'dan yuqori bo'lsa ham)",
            status.allowed,
            f"used={status.used}, limit={status.limit}",
        )

        # `None` — override yo'q, standart limit ishlaydi.
        status = await quota.check_translation(user_id, limit_override=None)
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
            resolve_limit_override(user, kind="translation") is None,
        )

        # 2. Faol VIP -> 0 (cheksiz).
        user.settings.premium_until = utcnow() + timedelta(days=1)
        check(
            "faol VIP -> cheksiz",
            resolve_limit_override(user, kind="translation") == 0,
        )

        # 3. Tugagan VIP -> yana None (standart limitga qaytadi).
        user.settings.premium_until = utcnow() - timedelta(days=1)
        check(
            "tugagan VIP -> None",
            resolve_limit_override(user, kind="translation") is None,
        )

        # 4. Qo'lda qo'yilgan override VIP'dan USTUN turadi (aniq admin qarori).
        user.settings.premium_until = utcnow() + timedelta(days=1)
        user.settings.daily_limit_override = 7
        check(
            "qo'lda override VIP'dan ustun",
            resolve_limit_override(user, kind="translation") == 7,
        )
        # TTS uchun alohida ustun ishlatiladi, tarjima override'iga bog'liq emas.
        check(
            "TTS override alohida (hali yo'q -> VIP ishlaydi)",
            resolve_limit_override(user, kind="tts") == 0,
        )
        user.settings.daily_limit_override = None

        # 5. Admin roli hammasidan ustun.
        user.role = "admin"
        user.settings.premium_until = None
        check(
            "admin roli -> har doim cheksiz",
            resolve_limit_override(user, kind="translation") == 0,
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

        status = await quota.check_image(user_id)
        check("boshlang'ich holat ruxsat", status.allowed and status.used == 0)

        for _ in range(3):
            await quota.consume_image(user_id)
        await session.commit()

        status = await quota.check_image(user_id, limit_override=3)
        check("rasm limitiga yetganda taqiqlanadi", not status.allowed)

        # Rasm sarfi matn/ovoz hisoblagichiga umuman tegmasligi kerak —
        # bular butunlay alohida o'q (`images_count` vs `translations_count`).
        text_status = await quota.check_translation(user_id)
        check(
            "rasm sarfi matn hisobiga ta'sir qilmaydi",
            text_status.used == 0,
            f"matn used={text_status.used}",
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
        status = await quota.check_translation(
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
    """Adminga murojaat: matn tayyorlash va spam cheklovi."""
    print("\n[12] Adminga murojaat")
    from types import SimpleNamespace

    from bot.handlers.user.support import _admin_view, _rate_limited

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


async def test_support_thread_lookup() -> None:
    """Ikki tomonlama murojaat: reply orqali ip topiladi.

    Diqqat: bu funksiya ilgari `test_support_thread` deb nomlangan edi va
    quyida boshqa bir `test_support_thread` bilan bir xil nom bo'lgani
    uchun Python uni jimgina almashtirib qo'ygan — bu yerdagi 7 ta tekshiruv
    hech qachon ishlamagan (`main()` faqat oxirgi ta'rifni chaqirgan).
    """
    print("\n[16] Murojaat yozishmasi")
    from sqlalchemy import delete

    from bot.database.models import SupportMessage
    from bot.database.repositories.support_repository import SupportRepository

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        user, _ = await repo.get_or_create(TEST_TELEGRAM_ID + 50, first_name="Ip")
        await session.commit()

        support = SupportRepository(session)

        # 1. Foydalanuvchi murojaat yozdi, admin chatiga tushdi.
        await support.record(
            user_id=user.id, direction="in", text="savol",
            admin_chat_id=111, admin_message_id=900,
            user_chat_id=user.telegram_id, user_message_id=500,
        )
        await session.commit()

        # 2. Admin o'sha xabarga reply qildi — ip topilishi kerak.
        found = await support.by_admin_message(111, 900)
        check("admin javobi ipni topadi", found is not None and found.user_id == user.id)
        check("ip foydalanuvchini biladi", found.user is not None and found.user.telegram_id == user.telegram_id)

        # Handler `found.user.settings.interface_lang` ni o'qiydi — ikki qavat
        # chuqur. Sinov faqat `found.user` ni tekshirgani uchun `settings`
        # yuklanmagani sezilmay qolgan va ishlab turgan botda admin javoblari
        # `MissingGreenlet` bilan jimgina yo'qolgan edi.
        try:
            lang = found.user.settings.interface_lang
            check("ip foydalanuvchi sozlamasini ham biladi", bool(lang), lang)
        except Exception as exc:  # MissingGreenlet — eager load tushib qolgan
            check("ip foydalanuvchi sozlamasini ham biladi", False, type(exc).__name__)

        # Boshqa xabarga reply — ip yo'q, tarjimaga o'tishi kerak.
        check("begona xabar ipsiz", await support.by_admin_message(111, 999) is None)

        # 3. Admin javobi yozildi, foydalanuvchi chatidagi id bilan.
        await support.record(
            user_id=user.id, direction="out", text="javob",
            admin_chat_id=111, admin_message_id=901,
            user_chat_id=user.telegram_id, user_message_id=501,
        )
        await session.commit()

        # 4. Foydalanuvchi javobga reply qildi — ip yana topiladi.
        back = await support.by_user_message(user.telegram_id, 501)
        check("foydalanuvchi javobi ipni topadi", back is not None and back.direction == "out")
        check("begona reply ipsiz", await support.by_user_message(user.telegram_id, 777) is None)

        check("yozishma sanaladi", await support.thread_size(user.id) == 2)

        await session.execute(delete(SupportMessage).where(SupportMessage.user_id == user.id))
        from bot.database.models import User as U, UserSettings as US
        await session.execute(delete(US).where(US.user_id == user.id))
        await session.execute(delete(U).where(U.id == user.id))
        await session.commit()


async def test_support_thread_lazy_load() -> None:
    """Murojaat ipi: admin javobi foydalanuvchi tilida yuborilishi kerak.

    Diqqat — qidiruv **yangi sessiyada** bajariladi. Aynan shu shart bo'lmasa
    sinov muammoni ko'rsatmaydi: bir sessiyada yaratilgan `User` identity
    map'da sozlamalari bilan yotadi va lazy yuklanish umuman bo'lmaydi.
    Ishlab turgan botda esa admin javob berayotgan foydalanuvchi boshqa
    obyekt bo'ladi va `user.settings` ga murojaat `MissingGreenlet` beradi.
    """
    print("\n[16b] Murojaat ipi — yangi sessiyada lazy-load")
    from sqlalchemy import delete

    from bot.database.models import SupportMessage, User, UserSettings
    from bot.database.repositories.support_repository import SupportRepository

    tg_id = TEST_TELEGRAM_ID + 50
    async with AsyncSessionLocal() as session:
        user, _ = await UserRepository(session).get_or_create(tg_id, telegram_lang="id")
        await session.commit()
        user_id = user.id

        await SupportRepository(session).record(
            user_id=user_id,
            direction="in",
            text="sinov",
            admin_chat_id=777001,
            admin_message_id=888001,
            user_chat_id=tg_id,
            user_message_id=888000,
        )
        await session.commit()

    # Yangi sessiya = bo'sh identity map, ya'ni ishlab turgan botdagi holat.
    async with AsyncSessionLocal() as fresh:
        thread = await SupportRepository(fresh).by_admin_message(777001, 888001)
        check("ip admin xabari bo'yicha topildi", thread is not None)
        check("foydalanuvchi yuklandi", thread is not None and thread.user is not None)
        try:
            lang = thread.user.settings.interface_lang
            check("sozlamalar eager yuklangan", lang == "id", lang)
        except Exception as exc:
            # MissingGreenlet aynan shu yerda chiqardi.
            check("sozlamalar eager yuklangan", False, type(exc).__name__)

        back = await SupportRepository(fresh).by_user_message(tg_id, 888000)
        check("ip foydalanuvchi xabari bo'yicha topildi", back is not None)

        missing = await SupportRepository(fresh).by_admin_message(777001, 999999)
        check("noma'lum xabar uchun ip yo'q", missing is None)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(SupportMessage).where(SupportMessage.user_id == user_id))
        await session.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_support_parsing() -> None:
    """Murojaat sarlavhasidan foydalanuvchi ID'sini va kontent turini ajratish.

    Baza kerak emas — sof funksiyalar, `SimpleNamespace` bilan Telegram
    xabarini taqlid qilamiz.
    """
    print("\n[16c] Murojaat sarlavhasini tahlil qilish")
    from types import SimpleNamespace

    from bot.handlers.user.support import _describe, _extract_target_id

    header = (
        "\u2709\ufe0f Yangi murojaat\n\n"
        "\U0001f464 Ali \u00b7 @ali\n"
        "\U0001f194 5718446822\n"
        "\U0001f310 uz"
    )
    check(
        "sarlavhadan ID ajratiladi",
        _extract_target_id(SimpleNamespace(text=header, caption=None)) == 5718446822,
    )
    check(
        "izohdan ham ajratiladi",
        _extract_target_id(SimpleNamespace(text=None, caption="\U0001f194 123456789")) == 123456789,
    )
    check(
        "oddiy matnda ID yo'q",
        _extract_target_id(SimpleNamespace(text="salom dunyo", caption=None)) is None,
    )
    check("reply bo'lmasa None", _extract_target_id(None) is None)

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
    await test_translation_record(user_id, result)
    await test_events(user_id)
    await test_tts()
    await test_keyboards()
    await test_support()
    await test_donate(user_id)
    await test_broadcast()
    await test_broadcast_resumable()
    await test_stats()
    await test_support_thread_lookup()
    await test_support_thread_lazy_load()
    await test_support_parsing()
    await test_content_extraction()

    await cleanup()
    await engine.dispose()

    print("\n" + "=" * 60)
    print(f"Natija: {passed} o'tdi, {failed} yiqildi")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
