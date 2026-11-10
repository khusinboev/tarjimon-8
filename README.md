# 🌐 Tarjimon-8

Telegram tarjimon bot (production). 2026-08 da eski `tarjimon4` o'rniga
qo'yilgan — uning 37,000+ foydalanuvchi va 155,000+ tarjima bazasini meros
qilib olgan.

## Hujjatlar

| Fayl | Nima haqida |
|---|---|
| [SXEMA.md](SXEMA.md) | Ma'lumotlar bazasi arxitekturasi va loyihalash qarorlari |
| [DEPLOY.md](DEPLOY.md) | Server, kod yangilash, zaxira/tiklash, nosozlikda nima qilish |
| [REJA.md](REJA.md) | Barqarorlashtirish rejasi va bosqichlar |
| [mt/README.md](mt/README.md) | Lokal tarjima modeli (NLLB-200) — qaror va o'lchovlar |

---

## Nima ishlaydi

**Foydalanuvchi uchun**
- Matn tarjimasi — 23 til (shu jumladan amhar `am`, oromo `om`), avto aniqlash
- Tarjima yo'nalishini tanlash va almashtirish (⇄), tarjima ostidagi tugmalar bilan ham
- Ovozda eshitish — 🔊 tugmasi (edge-tts, 18 tilda)
- Rasmdan tarjima (OCR: OCR.Space / Google Vision) — kuniga 3 ta, VIP 15 ta
- Postlar, media izohlari (caption), so'rovnoma va checklist matnlarini tarjima
- Interfeys: o'zbek / ingliz / indonez — Telegram tilidan avtomatik
- Homiylik: Telegram Stars (`/donate`) va karta rekvizitlari
- VIP: homiylik yoki referal orqali — muddat ichida kunlik limitlar cheksiz
- Referal: `/invite` havolasi, bonus taklif qilingan userning birinchi tarjimasidan keyin
- Adminga murojaat (`/contact`) — ikki tomonlama yozishma
- Kunlik limit (standart 50 ta), spam himoyasi, majburiy obuna kanallari

**Admin uchun** (`/admin` yoki `/panel`)
- Statistika va audit jurnali
- Umumiy limitlar (tarjima, ovoz, rasm) — `.env`siz, paneldan
- Majburiy obuna kanallarini qo'shish/olib tashlash
- Xabar tarqatish (forward/copy, sinov yuborish, progress, pauza/davom ettirish, bekor qilish, tarix)
- Foydalanuvchi qidirish, ban/unban, shaxsiy limitlar, xabar yuborish, murojaatlarga javob

**Ichkarida**
- Tarjima zanjiri: Google / Azure / DeepL → MyMemory → lokal NLLB → Gemini → `deep_translator`
  (batafsil: [SXEMA.md](SXEMA.md#tarjima-provayderlari)); circuit breaker va
  xato-darajasi ogohlantirishi
- Har bir harakat `events` jadvaliga yoziladi — kelajakda ML/tahlil uchun
- Tarjima sifati signallari (`translation_signals`)
- Redis kesh: bir xil matn qayta tarjima qilinmaydi; TTS fayllari `file_id` bo'yicha qayta ishlatiladi

## Rejada (hozircha yo'q)

Tarix va sevimlilar ko'rinishi, tarixni saqlashni o'chirish sozlamasi
(bazada `save_history`/`favorited` uchun joy bor, lekin handler yo'q), ovoz
xabar tarjimasi, lug'at va mashqlar, mini app. Batafsil: [REJA.md](REJA.md).

---

## Texnologiyalar

Python 3.11+ · aiogram 3 · SQLAlchemy 2 (async) · PostgreSQL · Alembic · Redis
· edge-tts · CTranslate2 (lokal MT)

## Struktura

```
bot/
├── config/        # pydantic-settings sozlamalari
├── database/
│   ├── models.py      # 17 jadval (SXEMA.md ga qarang)
│   ├── repositories/  # DB so'rovlari
│   └── session.py
├── handlers/
│   ├── user/      # start, languages, translate, tts, donate, support, subscription, common
│   └── admin/     # panel
├── keyboards/
├── locales/       # uz, en, id
├── middlewares/
│   ├── context.py       # session + user + events (har update uchun bir marta)
│   └── subscription.py  # majburiy obuna
├── services/      # translation(+_providers), circuit_breaker, error_rate, tts, ocr,
│                  # quota, referral, broadcast*, stats, admin_*, events ...
└── utils/
alembic/versions/  # 001–018 migratsiyalar
mt/                # lokal tarjima xizmati (NLLB-200, alohida systemd unit)
scripts/
├── migrate_legacy.py      # eski PG + SQLite → tarjimon8 (2026-08, bajarilgan)
├── import_legacy_bots.py  # boshqa eski botlar userlari (bajarilgan)
├── broadcast_relaunch.py, mark_unreachable.py
└── smoke_test.py          # uchidan-uchiga sinov (Telegramsiz)
deploy/            # tarjimon8.service, tarjimon8-mt.service
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

Baza, tarjima, TTS, limitlar, referal, tarqatish va boshqalarni Telegramsiz
tekshiradi (~280 ta tekshiruv).

---

## Muhim sozlamalar

| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `DAILY_TRANSLATION_LIMIT` | 50 | Kunlik bepul tarjima. 0 = cheksiz |
| `DAILY_TTS_LIMIT` | 30 | Kunlik ovoz limiti |
| `DAILY_IMAGE_LIMIT_FREE` / `_VIP` | 3 / 15 | Kunlik rasm tarjimasi |
| `LOCAL_MT_URL` / `LOCAL_MT_PRIORITY` | bo'sh / 1 | Lokal NLLB; prod'da `3` |
| `PREMIUM_STARS_PER_DAY` | 5 | N stars = 1 kun VIP |
| `EVENT_LOG_LEVEL` | `all` | `all` / `important` / `off` |
| `TRANSLATION_CACHE_TTL` | 86400 | Redis kesh muddati (soniya) |
| `TTS_PROVIDER` | `edge` | edge-tts, bepul |

To'liq ro'yxat: [.env.example](.env.example)

## Litsenziya

MIT
