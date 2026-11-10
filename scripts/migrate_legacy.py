#!/usr/bin/env python3
"""Eski bazalardan yangi `tarjimon8` bazasiga ma'lumot ko'chirish.

Manbalar (ikkalasi ham FAQAT O'QISH uchun ochiladi):
  1. PostgreSQL `tarjimon`  — 2025-08 … 2026-04-29 (muzlagan)
  2. SQLite fayl            — 2026-04-29 … hozir (faol)

Ma'lumot ikkiga bo'linib qolgan, chunki `.env` da `DBTYPE=True` yozilgan va
`config.py` uni `postgres` deb tanimay SQLite fallback'ga tushib ketgan.
Bu skript ikkalasini birlashtiradi.

Birlashtirish qoidalari:
  - Kalit: `telegram_id`. Ikkalasida bo'lsa — SQLite yozuvi yangiroq, u ustun.
  - `created_at`: ikkisining eng ERTASI (ro'yxatdan o'tgan sana yo'qolmasin).
  - Tarjimalar: vaqt oralig'i deyarli kesishmaydi, lekin `(user_id, created_at,
    source_text)` bo'yicha dedup baribir qilinadi.

Ishga tushirish:
    python scripts/migrate_legacy.py --dry-run     # faqat hisob-kitob
    python scripts/migrate_legacy.py               # haqiqiy ko'chirish
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 kerak: pip install psycopg2-binary")

BATCH = 5_000
_WHITESPACE = re.compile(r"\s+")


# ─────────────────────────────────────────────────────────────
#  Yordamchilar
# ─────────────────────────────────────────────────────────────


def content_hash(text: str, source_lang: str, target_lang: str) -> str:
    """`bot/utils/text.py` dagi bilan bir xil bo'lishi SHART.

    Aks holda ko'chirilgan yozuvlar kesh qidiruviga tushmaydi.
    """
    normalized = _WHITESPACE.sub(" ", text).strip().lower()
    payload = f"{normalized}\x00{source_lang}\x00{target_lang}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_dt(value: Any) -> Optional[datetime]:
    """SQLite sanalari matn ko'rinishida; PG dan datetime keladi."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def earliest(*values: Optional[datetime]) -> Optional[datetime]:
    present = [v for v in values if v is not None]
    return min(present) if present else None


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


# ─────────────────────────────────────────────────────────────
#  Manbalarni o'qish
# ─────────────────────────────────────────────────────────────


def read_legacy_pg(dsn: str) -> Dict[str, Any]:
    log("PostgreSQL `tarjimon` o'qilmoqda...")
    conn = psycopg2.connect(dsn)
    # Manbaga hech narsa yozilmasligiga kafolat.
    conn.set_session(readonly=True, autocommit=True)
    data: Dict[str, Any] = {}

    with conn.cursor() as cur:
        cur.execute("SELECT user_id, username, lang_code, date FROM accounts")
        data["accounts"] = cur.fetchall()

        cur.execute("SELECT user_id, in_lang, out_lang FROM user_langs")
        data["user_langs"] = cur.fetchall()

        cur.execute("SELECT user_id, from_lang, to_lang FROM user_languages")
        data["user_languages"] = cur.fetchall()

        cur.execute("SELECT user_id, tts FROM users_tts")
        data["users_tts"] = cur.fetchall()

        cur.execute(
            "SELECT user_id, from_lang, to_lang, original_text, translated_text,"
            " created_at, is_favorite FROM translation_history"
        )
        data["translations"] = cur.fetchall()

        cur.execute("SELECT chat_id, title, username, types FROM groups")
        data["groups"] = cur.fetchall()

    conn.close()
    log(
        f"  accounts={len(data['accounts'])} translations={len(data['translations'])} "
        f"groups={len(data['groups'])}"
    )
    return data


def read_legacy_sqlite(path: str) -> Dict[str, Any]:
    log(f"SQLite fayl o'qilmoqda: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    data: Dict[str, Any] = {}

    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username, first_name, lang_code, created_at, is_blocked FROM accounts"
    )
    data["accounts"] = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT user_id, from_lang, to_lang FROM user_languages")
    data["user_languages"] = [dict(r) for r in cur.fetchall()]

    cur.execute(
        "SELECT user_id, source_text, translated_text, from_lang, to_lang, detected_lang,"
        " provider, response_time_ms, chat_type, chat_id, created_at FROM translation_history"
    )
    data["translations"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    log(
        f"  accounts={len(data['accounts'])} translations={len(data['translations'])}"
    )
    return data


# ─────────────────────────────────────────────────────────────
#  Birlashtirish
# ─────────────────────────────────────────────────────────────


def merge_users(pg: Dict[str, Any], lite: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    users: Dict[int, Dict[str, Any]] = {}

    for user_id, username, lang_code, date in pg["accounts"]:
        if not user_id:
            continue
        users[int(user_id)] = {
            "telegram_id": int(user_id),
            "username": username or None,
            "first_name": None,
            "telegram_lang": (lang_code or None),
            "created_at": parse_dt(date),
            "status": "active",
        }

    for row in lite["accounts"]:
        user_id = row.get("user_id")
        if not user_id:
            continue
        user_id = int(user_id)
        created = parse_dt(row.get("created_at"))
        existing = users.get(user_id)

        merged = {
            "telegram_id": user_id,
            # SQLite yozuvi yangiroq — profil maydonlarida u ustun.
            "username": row.get("username") or (existing or {}).get("username"),
            "first_name": row.get("first_name") or (existing or {}).get("first_name"),
            "telegram_lang": row.get("lang_code") or (existing or {}).get("telegram_lang"),
            # Ro'yxatdan o'tgan sana — eng ertasi.
            "created_at": earliest(created, (existing or {}).get("created_at")),
            "status": "blocked_bot" if row.get("is_blocked") else "active",
        }
        users[user_id] = merged

    return users


def merge_settings(pg: Dict[str, Any], lite: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    settings: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"source_lang": "auto", "target_lang": "uz", "tts_enabled": True}
    )

    # Eng eski manbadan boshlab, yangiroq ustiga yozadi.
    for user_id, in_lang, out_lang in pg["user_langs"]:
        if not user_id:
            continue
        entry = settings[int(user_id)]
        if in_lang:
            entry["source_lang"] = in_lang
        if out_lang:
            entry["target_lang"] = out_lang

    for user_id, from_lang, to_lang in pg["user_languages"]:
        if not user_id:
            continue
        entry = settings[int(user_id)]
        if from_lang:
            entry["source_lang"] = from_lang
        if to_lang:
            entry["target_lang"] = to_lang

    for user_id, tts in pg["users_tts"]:
        if user_id:
            settings[int(user_id)]["tts_enabled"] = bool(tts)

    for row in lite["user_languages"]:
        user_id = row.get("user_id")
        if not user_id:
            continue
        entry = settings[int(user_id)]
        if row.get("from_lang"):
            entry["source_lang"] = row["from_lang"]
        if row.get("to_lang"):
            entry["target_lang"] = row["to_lang"]

    return dict(settings)


def merge_translations(pg: Dict[str, Any], lite: Dict[str, Any]) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    seen: set[tuple] = set()

    def key(user_id: int, created: Optional[datetime], source_text: str) -> tuple:
        return (user_id, created.isoformat() if created else "", (source_text or "")[:200])

    for user_id, from_lang, to_lang, original, translated, created_at, is_favorite in pg[
        "translations"
    ]:
        if not user_id or not original:
            continue
        created = parse_dt(created_at)
        k = key(int(user_id), created, original)
        if k in seen:
            continue
        seen.add(k)
        rows.append(
            {
                "telegram_id": int(user_id),
                "source_text": original,
                "target_text": translated,
                "source_lang_requested": from_lang or "auto",
                "source_lang_detected": from_lang if from_lang != "auto" else None,
                "target_lang": to_lang or "uz",
                "detected": None,
                "provider": "legacy_pg",
                "latency_ms": None,
                "chat_type": "private",
                "chat_id": None,
                "created_at": created,
                "is_favorite": bool(is_favorite),
            }
        )

    for row in lite["translations"]:
        user_id = row.get("user_id")
        source_text = row.get("source_text")
        if not user_id or not source_text:
            continue
        created = parse_dt(row.get("created_at"))
        k = key(int(user_id), created, source_text)
        if k in seen:
            continue
        seen.add(k)

        chat_type = row.get("chat_type") or "private"
        if chat_type not in ("private", "group", "supergroup", "channel", "inline"):
            chat_type = "private"

        rows.append(
            {
                "telegram_id": int(user_id),
                "source_text": source_text,
                "target_text": row.get("translated_text"),
                "source_lang_requested": row.get("from_lang") or "auto",
                "source_lang_detected": row.get("detected_lang") or None,
                "target_lang": row.get("to_lang") or "uz",
                "detected": row.get("detected_lang"),
                "provider": row.get("provider") or "legacy_sqlite",
                "latency_ms": row.get("response_time_ms"),
                "chat_type": chat_type,
                "chat_id": row.get("chat_id"),
                "created_at": created,
                "is_favorite": False,
            }
        )

    return rows


# ─────────────────────────────────────────────────────────────
#  Yozish
# ─────────────────────────────────────────────────────────────


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def write_users(cur, users: Dict[int, Dict[str, Any]]) -> None:
    payload = [
        (
            u["telegram_id"],
            u["username"],
            u["first_name"],
            (u["telegram_lang"] or None),
            u["status"],
            u["created_at"] or datetime.now(timezone.utc),
            u["created_at"] or datetime.now(timezone.utc),
        )
        for u in users.values()
    ]

    for batch in chunks(payload, BATCH):
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO users
                (telegram_id, username, first_name, telegram_lang, status,
                 created_at, last_seen_at)
            VALUES %s
            ON CONFLICT (telegram_id) DO NOTHING
            """,
            batch,
            page_size=1000,
        )


def load_id_map(cur) -> Dict[int, int]:
    cur.execute("SELECT telegram_id, id FROM users")
    return {int(tg): int(uid) for tg, uid in cur.fetchall()}


def write_settings(
    cur, settings: Dict[int, Dict[str, Any]], id_map: Dict[int, int]
) -> int:
    payload = []
    for telegram_id, entry in settings.items():
        user_id = id_map.get(telegram_id)
        if user_id is None:
            continue
        payload.append(
            (
                user_id,
                (entry["source_lang"] or "auto")[:10],
                (entry["target_lang"] or "uz")[:10],
                "uz",
                bool(entry.get("tts_enabled", True)),
            )
        )

    for batch in chunks(payload, BATCH):
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO user_settings
                (user_id, source_lang, target_lang, interface_lang, tts_enabled)
            VALUES %s
            ON CONFLICT (user_id) DO UPDATE SET
                source_lang = EXCLUDED.source_lang,
                target_lang = EXCLUDED.target_lang,
                tts_enabled = EXCLUDED.tts_enabled
            """,
            batch,
            page_size=1000,
        )
    return len(payload)


def write_default_settings(cur) -> int:
    """Sozlamasi yo'q userlarga standart qator — `user.settings` hech qachon None bo'lmasin."""
    cur.execute(
        """
        INSERT INTO user_settings (user_id, source_lang, target_lang, interface_lang)
        SELECT u.id, 'auto', 'uz', 'uz'
        FROM users u
        LEFT JOIN user_settings s ON s.user_id = u.id
        WHERE s.user_id IS NULL
        """
    )
    return cur.rowcount


def write_translations(cur, rows: list[Dict[str, Any]], id_map: Dict[int, int]) -> tuple[int, int]:
    payload = []

    for row in rows:
        user_id = id_map.get(row["telegram_id"])
        if user_id is None:
            continue
        source_text = row["source_text"]
        target_lang = (row["target_lang"] or "uz")[:10]
        source_req = (row["source_lang_requested"] or "auto")[:10]

        payload.append(
            (
                user_id,
                row["chat_id"],
                row["chat_type"],
                "text",
                source_req,
                (row["source_lang_detected"] or None),
                target_lang,
                source_text,
                row["target_text"],
                content_hash(source_text, source_req, target_lang),
                len(source_text),
                len(row["target_text"] or ""),
                row["provider"][:32],
                "success" if row["target_text"] else "error",
                row["latency_ms"],
                row["created_at"] or datetime.now(timezone.utc),
                row["is_favorite"],
            )
        )

    inserted = 0
    favorite_count = 0

    for batch in chunks(payload, BATCH):
        # `is_favorite` ustuni `translations` da yo'q — uni RETURNING orqali
        # signal jadvaliga o'tkazamiz.
        values = [item[:-1] for item in batch]
        flags = [item[-1] for item in batch]

        results = psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO translations
                (user_id, chat_id, chat_type, input_kind, source_lang_requested,
                 source_lang_detected, target_lang, source_text, target_text,
                 source_hash, source_chars, target_chars, provider, status,
                 latency_ms, created_at)
            VALUES %s
            RETURNING id, user_id
            """,
            values,
            page_size=1000,
            fetch=True,
        )
        inserted += len(results)

        signal_rows = [
            (translation_id, user_id, "favorited")
            for (translation_id, user_id), is_fav in zip(results, flags)
            if is_fav
        ]
        if signal_rows:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO translation_signals (translation_id, user_id, signal) VALUES %s",
                signal_rows,
                page_size=1000,
            )
            favorite_count += len(signal_rows)

    return inserted, favorite_count


def write_chats(cur, groups: list) -> int:
    payload = []
    for chat_id, title, username, types in groups:
        if not chat_id:
            continue
        chat_type = (types or "group").lower()
        if chat_type not in ("group", "supergroup", "channel", "private"):
            chat_type = "group"
        payload.append((int(chat_id), title, username, chat_type))

    if payload:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO chats (chat_id, title, username, type)
            VALUES %s
            ON CONFLICT (chat_id) DO NOTHING
            """,
            payload,
        )
    return len(payload)


# ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Eski bazalardan tarjimon8 ga ko'chirish")
    parser.add_argument(
        "--legacy-pg",
        default=os.getenv("LEGACY_PG_DSN", "dbname=tarjimon user=postgres host=/var/run/postgresql"),
        help="Eski PostgreSQL DSN",
    )
    parser.add_argument(
        "--sqlite",
        default=os.getenv("LEGACY_SQLITE", "/home/tarjimon4/tarjimon4/tarjimon"),
        help="Eski SQLite fayl yo'li",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("TARGET_PG_DSN", "dbname=tarjimon8 user=postgres host=/var/run/postgresql"),
        help="Yangi PostgreSQL DSN",
    )
    parser.add_argument("--dry-run", action="store_true", help="Yozmasdan faqat hisoblash")
    args = parser.parse_args()

    pg = read_legacy_pg(args.legacy_pg)
    lite = read_legacy_sqlite(args.sqlite)

    log("Birlashtirilmoqda...")
    users = merge_users(pg, lite)
    settings = merge_settings(pg, lite)
    translations = merge_translations(pg, lite)

    log(f"  unikal user      : {len(users):,}".replace(",", " "))
    log(f"  sozlamalar       : {len(settings):,}".replace(",", " "))
    log(f"  tarjimalar       : {len(translations):,}".replace(",", " "))
    log(f"  sevimlilar       : {sum(1 for r in translations if r['is_favorite']):,}".replace(",", " "))
    log(f"  guruhlar         : {len(pg['groups']):,}".replace(",", " "))

    if args.dry_run:
        log("--dry-run: hech narsa yozilmadi.")
        return

    target = psycopg2.connect(args.target)
    target.autocommit = False

    try:
        with target.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            if cur.fetchone()[0] > 0:
                sys.exit(
                    "XATO: `users` jadvali bo'sh emas. Migratsiya faqat toza bazaga "
                    "qilinadi. Avval bazani tozalang yoki yangisini yarating."
                )

            log("users yozilmoqda...")
            write_users(cur, users)
            id_map = load_id_map(cur)
            log(f"  {len(id_map):,} user".replace(",", " "))

            log("user_settings yozilmoqda...")
            written = write_settings(cur, settings, id_map)
            defaults = write_default_settings(cur)
            log(f"  {written:,} ko'chirildi, {defaults:,} standart".replace(",", " "))

            log("translations yozilmoqda (biroz vaqt oladi)...")
            inserted, favorites = write_translations(cur, translations, id_map)
            log(f"  {inserted:,} tarjima, {favorites:,} sevimli".replace(",", " "))

            log("chats yozilmoqda...")
            chats = write_chats(cur, pg["groups"])
            log(f"  {chats:,} guruh".replace(",", " "))

        target.commit()
        log("✅ Migratsiya yakunlandi.")
    except Exception:
        target.rollback()
        log("❌ Xatolik — o'zgarishlar bekor qilindi (rollback).")
        raise
    finally:
        target.close()


if __name__ == "__main__":
    main()
