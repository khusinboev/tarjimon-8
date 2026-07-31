#!/usr/bin/env python3
"""Serverdagi boshqa tarjimon botlarning foydalanuvchilarini ko'chiradi.

Asosiy migratsiya (`migrate_legacy.py`) `tarjimon` bazasi va SQLite faylini
oldi. Serverda esa yana ikkita tarjimon bazasi bor edi:

    ttbot  — 75 user   (accounts + user_langs)
    tt4    — 3 user    (accounts, sinov bazasi)
    tt7    — bo'sh     (loyiha bor, ma'lumot yo'q)

Ularning ko'pchiligi allaqachon asosiy bazada (bir odam bir necha botdan
foydalangan). Bu skript faqat **yangi** `telegram_id` larni qo'shadi.

Til kodlari tekshiriladi: manba bazalarda `zh-CN` kabi variantlar va bizda
yo'q kodlar uchraydi. Noma'lum kod standart qiymat bilan almashtiriladi —
aks holda CHECK cheklovi yoki bo'sh tanlov chiqardi.

Ishga tushirish:
    python scripts/import_legacy_bots.py --dry-run
    python scripts/import_legacy_bots.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
from sqlalchemy import select

from bot import locales
from bot.config.settings import settings
from bot.database.models import Language, User, UserSettings
from bot.database.session import AsyncSessionLocal, engine

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("import")


# Manba bazalar. Har biri `(nom, so'rov)` — so'rov quyidagi ustunlarni
# qaytarishi kerak: user_id, username, lang_code, in_lang, out_lang.
# Yo'q ustunlar `NULL` bilan to'ldiriladi, shunda ishlov bir xil bo'ladi.
SOURCES = {
    "ttbot": """
        SELECT a.user_id, a.username, a.lang_code, l.in_lang, l.out_lang
        FROM accounts a
        LEFT JOIN user_langs l ON l.user_id = a.user_id
    """,
    "tt4": """
        SELECT a.user_id, NULL::text, a.lang_code, NULL::text, NULL::text
        FROM accounts a
    """,
}


def _legacy_dsn(db: str) -> str:
    """Eski bazalar `postgres` roli ostida, paroli `tarjimon4` .env da."""
    env = Path("/home/tarjimon4/tarjimon4/.env")
    password = ""
    if env.exists():
        found = re.search(r"^DB_PASSWORD=(.+)$", env.read_text(), re.M)
        password = found.group(1).strip().strip('"') if found else ""
    return f"dbname={db} user=postgres password={password} host=localhost"


def _read(db: str, query: str) -> list[tuple]:
    with psycopg2.connect(_legacy_dsn(db)) as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


def _normalize(code: str | None, known: set[str], fallback: str) -> str:
    """Manba til kodini bizdagi kodga keltiradi.

    `zh-CN` -> `zh`; noma'lum yoki bo'sh -> standart qiymat.
    """
    if not code:
        return fallback
    code = code.strip().lower()
    if code in known:
        return code
    base = code.split("-")[0]
    return base if base in known else fallback


async def main() -> None:
    parser = argparse.ArgumentParser(description="Boshqa botlardan userlarni ko'chirish")
    parser.add_argument("--dry-run", action="store_true", help="yozmasdan hisoblash")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        known_langs = {
            code
            for (code,) in (await session.execute(select(Language.code))).all()
            if code != "auto"
        }
        existing = {
            tid for (tid,) in (await session.execute(select(User.telegram_id))).all()
        }
        log.info("tarjimon8 da hozir: %s user", f"{len(existing):,}".replace(",", " "))

        # Bir odam ikkala bazada ham bo'lishi mumkin — birinchi uchragani olinadi.
        candidates: dict[int, tuple] = {}
        for db, query in SOURCES.items():
            try:
                rows = _read(db, query)
            except Exception as exc:
                log.warning("%s o'qilmadi: %s", db, exc)
                continue

            fresh = [r for r in rows if r[0] and r[0] not in existing]
            log.info(
                "%-8s jami %s | yangi %s",
                db,
                f"{len(rows):,}".replace(",", " "),
                f"{len(fresh):,}".replace(",", " "),
            )
            for row in fresh:
                candidates.setdefault(row[0], row)

        if not candidates:
            log.info("Ko'chiradigan yangi user yo'q.")
            await engine.dispose()
            return

        log.info("Jami yangi: %s", f"{len(candidates):,}".replace(",", " "))

        if args.dry_run:
            for tid, (_, username, lang_code, in_lang, out_lang) in sorted(
                candidates.items()
            ):
                log.info(
                    "  %s @%s  %s → %s (interfeys: %s)",
                    tid,
                    username or "—",
                    _normalize(in_lang, known_langs, settings.DEFAULT_SOURCE_LANG),
                    _normalize(out_lang, known_langs, settings.DEFAULT_TARGET_LANG),
                    locales.resolve(lang_code),
                )
            log.info("--dry-run: hech narsa yozilmadi.")
            await engine.dispose()
            return

        added = 0
        for telegram_id, (_, username, lang_code, in_lang, out_lang) in sorted(
            candidates.items()
        ):
            user = User(
                telegram_id=telegram_id,
                username=username or None,
                telegram_lang=lang_code or None,
                # Manba bazada ro'yxatdan o'tish sanasi ishonchli emas —
                # `created_at` standart (hozir) bo'lib qoladi.
                source="legacy_import",
            )
            session.add(user)
            await session.flush()

            session.add(
                UserSettings(
                    user_id=user.id,
                    # `auto` manba til sifatida mantiqiy: eski bazalarda
                    # yo'nalish ko'pincha noto'g'ri yoki bir xil (de→de).
                    source_lang=_normalize(
                        in_lang, known_langs, settings.DEFAULT_SOURCE_LANG
                    ),
                    target_lang=_normalize(
                        out_lang, known_langs, settings.DEFAULT_TARGET_LANG
                    ),
                    interface_lang=locales.resolve(lang_code),
                )
            )
            added += 1

        await session.commit()
        log.info("✅ %s user qo'shildi", added)

        total = len((await session.execute(select(User.telegram_id))).all())
        log.info("tarjimon8 da endi: %s user", f"{total:,}".replace(",", " "))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
