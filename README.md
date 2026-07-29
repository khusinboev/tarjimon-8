# 🌐 Tarjimon-8

Telegram tarjimon bot. `tarjimon4` ning o'rniga quriladi — 37,000+ foydalanuvchi
va 155,000+ tarjima bazasini meros qilib oladi.

## Hujjatlar

| Fayl | Nima haqida |
|---|---|
| [SXEMA.md](SXEMA.md) | Ma'lumotlar bazasi arxitekturasi va loyihalash qarorlari |
| [DEPLOY.md](DEPLOY.md) | Serverga o'rnatish va migratsiya bosqichma-bosqich |
| [REJA.md](REJA.md) | Funksiyalar ro'yxati va bosqichlar |

---

## Nima ishlaydi

**Foydalanuvchi uchun**
- Matn tarjimasi (21 til, avto aniqlash)
- Ovozda eshitish — TTS, 17 tilda
- Tarjima yo'nalishini tanlash va almashtirish
- Tarix va sevimlilar
- Kunlik limit (standart 50 ta), spam himoyasi
- Maxfiylik: tarixni saqlashni o'chirish mumkin

**Admin uchun**
- Statistika: userlar, tarjimalar, xato ulushi, ommabop yo'nalishlar
- Xabar tarqatish (forward/copy, progress, bekor qilish)
- Majburiy obuna kanallarini boshqarish

**Ichkarida**
- Har bir harakat `events` jadvaliga yoziladi — kelajakda ML/tahlil uchun
- Tarjima sifati signallari (`translation_signals`) — qaysi tarjima yomon chiqqanini o'lchash
- Redis kesh: bir xil matn qayta tarjima qilinmaydi; TTS fayllari `file_id` bo'yicha qayta ishlatiladi

## Nima yo'q (ataylab)

Ovoz/rasm tarjimasi, lug'at va mashqlar, gamification, mini app. Bular
`tarjimon4` da yozilgan, lekin hech qachon ishga tushmagan (`main.py` da
ulanmagan) — shuning uchun birinchi relizga kiritilmadi. Batafsil: [REJA.md](REJA.md).

---

## Texnologiyalar

Python 3.11+ · aiogram 3 · SQLAlchemy 2 (async) · PostgreSQL · Alembic · Redis
· deep-translator · edge-tts

## Struktura

```
bot/
├── config/        # pydantic-settings sozlamalari
├── database/
│   ├── models.py      # 13 jadval (SXEMA.md ga qarang)
│   ├── repositories/  # DB so'rovlari
│   └── session.py
├── handlers/
│   ├── user/      # start, tillar, tarjima, tts, tarix, sozlamalar
│   └── admin/     # panel
├── keyboards/
├── middlewares/
│   ├── context.py       # session + user + events (har update uchun bir marta)
│   └── subscription.py  # majburiy obuna
├── services/      # translation, tts, quota, events, subscription, admin
└── utils/         # matn, texts
alembic/versions/  # 001 sxema, 002 tillar
scripts/
├── migrate_legacy.py  # eski PG + SQLite → tarjimon8
└── smoke_test.py      # uchidan-uchiga sinov (Telegramsiz)
deploy/
```

---

## Lokal ishga tushirish

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env
# .env ni to'ldiring: BOT_TOKEN, ADMIN_USER_ID, POSTGRES_*

./venv/bin/alembic upgrade head
./venv/bin/python -m bot.main
```

### Sinov

```bash
./venv/bin/python scripts/smoke_test.py
```

Bazani, tarjimani, TTS ni va limitni Telegramsiz tekshiradi (35 ta tekshiruv).

---

## Muhim sozlamalar

| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `DAILY_TRANSLATION_LIMIT` | 50 | Kunlik bepul tarjima. 0 = cheksiz |
| `DAILY_TTS_LIMIT` | 30 | Kunlik ovoz limiti |
| `EVENT_LOG_LEVEL` | `all` | `all` / `important` / `off` |
| `TRANSLATION_CACHE_TTL` | 86400 | Redis kesh muddati (soniya) |
| `TRANSLATION_PROVIDER` | `deep_translator` | Sxemada AI provayder uchun joy tayyor |
| `TTS_PROVIDER` | `edge` | edge-tts, bepul |

To'liq ro'yxat: [.env.example](.env.example)

## Litsenziya

MIT
