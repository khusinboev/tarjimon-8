# Tarjimon-8 — Qayta qurish rejasi

> Holat: **muhokama bosqichi**. Kod yozilmagan.
> Sana: 2026-07-29

---

## 0. Nimadan boshlaymiz — hozirgi manzara

| | Holat |
|---|---|
| Ishlab turgan bot | `tarjimon4` → `t4.service` (server 23.94.2.204) |
| Haqiqiy userlar | **37,430** unikal (PG 34,606 + SQLite 4,298, ustma-ust 1,474) |
| Tarjimalar | 155,675 (PG 96,427 + SQLite 59,248) |
| Oylik faol user | ~1,450 |
| Oylik tarjima | ~25,000 |
| Kritik nosozlik | `.env` da `DBTYPE=True` → SQLite fallback, PG 2026-04-29 dan muzlagan |
| O'lik kod | ~4,000 qator (lug'atlar, gamification, timetable) — `main.py` da ulanmagan |

**Eng shoshilinch:** har kuni ma'lumot ikkiga bo'linib ketyapti.

---

## 1. Arxitektura qarori

**Asos:** `tarjimon-8` (shu repo) — aiogram 3 + SQLAlchemy 2.0 async + asyncpg + Alembic + Redis.

**Nima olinadi:**
- `tarjimon-8` dan: skelet, admin panel, broadcast, majburiy obuna, analytics middleware
- `tarjimon4` dan: **faqat biznes mantiq** (kod ko'chirilmaydi, qayta yoziladi)
- `tarjimon7` dan: mini app va admin panel g'oyalari (agar kerak bo'lsa)

**Nima tashlanadi:** sync psycopg2, `DBTYPE` flagi, SQLite fallback, migratsiyasiz `CREATE TABLE`, o'lik kod.

**Qatlamlar:**
```
handlers/  → faqat Telegram I/O, biznes mantiq yo'q
services/  → biznes mantiq (tarjima, limit, obuna)
repositories/ → DB so'rovlari
database/models.py → SQLAlchemy modellar + Alembic
```

---

## 2. Tarjima dvigateli — model tanlash

Hozir: `googletrans` + `deep-translator` (bepul, lekin `NoneType has no attribute 'lower'` xatosi kuniga bir necha marta).

### Variant A — Gibrid (tavsiya) 💡

| Matn turi | Model | Narx (1M token) |
|---|---|---|
| Qisqa matn (<200 belgi), oddiy | **Haiku 4.5** `claude-haiku-4-5` | $1 in / $5 out |
| Uzun matn, badiiy, kontekstli | **Sonnet 5** `claude-sonnet-5` | $3 in / $15 out ($2/$10 — 2026-08-31 gacha) |

**Oylik xarajat hisobi** (25,000 tarjima × ~200 token):

| Model | Oylik |
|---|---|
| Faqat Haiku 4.5 | ~$15 |
| Gibrid (80% Haiku / 20% Sonnet) | ~$18 |
| Faqat Sonnet 5 | ~$30 (intro narx) |
| Faqat Opus 5 | ~$75 |

Prompt caching bilan tizim promptining narxi ~90% tushadi.

### Variant B — Faqat Opus 5
Eng yuqori sifat (`claude-opus-5`, $5/$25). Badiiy va nozik tarjima uchun eng yaxshisi, lekin oddiy "salom" tarjimasi uchun ortiqcha.

### Variant C — Bepul + AI zaxira
`deep-translator` asosiy, xato bo'lsa Claude'ga o'tadi. Deyarli bepul, lekin sifat past va hozirgi xatolar saqlanadi.

**Mening tavsiyam: Variant A.** Sifat sezilarli oshadi, oyiga ~$18 — 37k userli bot uchun arzimas. Lekin bu sizning qaroringiz — Opus 5 ni tanlasangiz ham hisob-kitob yuqorida.

> ⬜ **BELGILANG:** A / B / C

---

## 3. USER PANEL — funksiyalar ro'yxati

Belgilar: ✅ hozir ishlaydi · 💀 kod bor, ulanmagan · 🔨 "tez orada" stub · ➖ umuman yo'q

`[x]` = mening tavsiyam. O'zgartiring.

### A. Yadro tarjima

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Matn tarjimasi (asosiy oqim) | ✅ | S |
| `[x]` | Avto til aniqlash | ✅ | S |
| `[x]` | Tarjima natijasini nusxalash tugmasi | ➖ | XS |
| `[x]` | Qayta tarjima / muqobil variant | ➖ | S |
| `[ ]` | Tarjimani tushuntirish (nega shunday) — AI bonus | ➖ | S |
| `[ ]` | Grammatika tuzatish rejimi | ➖ | M |

### B. Kirish turlari

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Matn | ✅ | — |
| `[x]` | Ovozli xabar → matn → tarjima (STT) | 🔨 stub | M |
| `[x]` | Rasm → OCR → tarjima | 🔨 stub | M |
| `[ ]` | Hujjat (PDF/DOCX) tarjimasi | 🔨 stub | L |
| `[ ]` | Video subtitr | ➖ | XL |
| `[ ]` | Forward qilingan xabarni tarjima | ➖ | XS |

> Ovoz va rasm — userlar eng ko'p so'raydigan, lekin hozir "tez orada" deb javob beradigan ikkita narsa.

### C. Chiqish

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Matn javob | ✅ | — |
| `[x]` | TTS — tarjimani ovozda eshitish | 💀 jadval bor | M |
| `[x]` | Uzun matnni bo'lib yuborish | ✅ | — |
| `[ ]` | Transliteratsiya (kiril ↔ lotin) | ➖ | S |
| `[ ]` | Talaffuz ko'rsatish | ➖ | S |

### D. Til boshqaruvi

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Til tanlash (21 til bor) | ✅ | S |
| `[x]` | Tillarni almashtirish (swap) | ➖ | XS |
| `[x]` | Oxirgi ishlatilgan tillar | ➖ | S |
| `[ ]` | Til juftliklarini saqlash (preset) | ➖ | S |
| `[ ]` | Interfeys tilini alohida tanlash | qisman | S |

### E. Tarix va sevimlilar

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Tarjimalar tarixi | ✅ `/history` | S |
| `[x]` | Sevimlilar (yulduzcha) | jadvalda bor, UI yo'q | S |
| `[x]` | Tarixdan qidirish | ➖ | S |
| `[ ]` | Tarixni eksport (CSV/TXT) | ➖ | S |
| `[ ]` | Tarixni tozalash | ➖ | XS |

### F. Lug'at va o'rganish — **eng katta blok**

`tarjimon4` da ~3,300 qator kod yozilgan, lekin **hech qachon ishga tushmagan**.
DB da: `vocab_entries` 307 qator, `practice_sessions` 0.

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[ ]` | Shaxsiy lug'at (so'z saqlash) | 💀 446 qator | M |
| `[ ]` | Mashqlar / flashcard | 💀 373 qator | L |
| `[ ]` | Ommaviy lug'atlar | 💀 465 qator | M |
| `[ ]` | Essential (asosiy so'zlar) | 💀 708 qator | L |
| `[ ]` | Parallel matnlar | 💀 894 qator | XL |
| `[ ]` | Kun so'zi | ➖ | S |

> **Ochiq savol:** bu blok kerakmi? Foydalanish statistikasi deyarli nol (307 so'z / 37k user).
> Mening tavsiyam: **1-relizga kiritmaslik.** Avval tarjima botini mukammal qilamiz, keyin lug'atni alohida faza sifatida — agar userlar so'rasa.

### G. Gamification

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[ ]` | Ballar / darajalar | 💀 556 qator | M |
| `[ ]` | Streak (ketma-ket kunlar) | 💀 | S |
| `[ ]` | Leaderboard | 💀 SQLite'da 4,298 qator | M |
| `[ ]` | Yutuqlar (achievements) | 💀 15 ta bor | M |

> Tavsiya: **1-relizga kiritmaslik.** Tarjima boti uchun ikkinchi darajali.

### H. Guruh va inline rejim

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Inline rejim (`@bot matn`) | ✅ | S |
| `[x]` | Guruhda tarjima (35 ta guruh bor) | ✅ qisman | M |
| `[ ]` | Guruhda avto-tarjima rejimi | ➖ | M |
| `[ ]` | Reply qilingan xabarni tarjima | ➖ | S |

### I. Sozlamalar

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Sozlamalar menyusi | ➖ | S |
| `[x]` | Tarixni yozishni o'chirish (maxfiylik) | ➖ | XS |
| `[ ]` | Tungi rejim / tema | ➖ | S |
| `[ ]` | Bildirishnomalar sozlamasi | ➖ | S |

### J. Mini App (Telegram WebApp)

| | Funksiya | tarjimon7 | Mehnat |
|---|---|---|---|
| `[ ]` | Mini app tarjima oynasi | bor (ishlamagan) | L |
| `[ ]` | Mini app tarix / sevimlilar | bor | M |
| `[ ]` | Web admin panel | bor | L |

> Tavsiya: keyingi faza. Avval bot mukammal ishlasin.

### K. Limit va monetizatsiya

| | Funksiya | tarjimon4 | Mehnat |
|---|---|---|---|
| `[x]` | Rate limiting (spam himoya) | 💀 87 qator | S |
| `[x]` | Kunlik bepul limit | ➖ | S |
| `[x]` | Majburiy obuna (kanal) | ✅ | — |
| `[ ]` | Premium obuna (Telegram Stars) | ➖ | L |
| `[ ]` | Referal tizimi | ➖ | M |
| `[ ]` | Reklama bloklari | tarjimon7 da bor | M |

> AI tarjima pullik bo'lgani uchun **kunlik limit shart** — aks holda bitta user oyiga $50 ni yeb qo'yishi mumkin.

**Mehnat belgilari:** XS = 1-2 soat · S = yarim kun · M = 1-2 kun · L = 3-5 kun · XL = 1 hafta+

---

## 4. Admin panel

`tarjimon-8` da tayyor: statistika, kanal boshqaruvi, broadcast (forward/copy, progress, cancel).

Qo'shiladigan:
- `[x]` Tarjima statistikasi (til juftliklari, kunlik hajm)
- `[ ]` AI xarajat monitoringi (token/dollar hisobi)
- `[x]` User qidirish va bloklash
- `[x]` Limit sozlash (kunlik bepul tarjima soni — user override)
- `[ ]` A/B test (model taqqoslash)

---

## 5. Ma'lumot migratsiyasi — 1-navbatdagi ish

```
SQLite (4,298 user)  ─┐
                      ├─→ Yangi PostgreSQL (37,430 user)
PostgreSQL (34,606)  ─┘
```

Qadamlar:
1. Ikkala bazadan to'liq zaxira (backup)
2. Yangi sxema Alembic bilan
3. Migratsiya skripti: `accounts` → `users`, `translation_history` → `translations`, `user_langs` → `user_settings`
4. Dedup: `telegram_id` bo'yicha, eng yangi `date` g'olib
5. Tekshiruv: sonlar mos kelishi, tasodifiy 100 ta yozuv solishtirish
6. `t4.service` to'xtatish → yangi bot ishga tushirish

**Xavf:** migratsiya paytida bot to'xtaydi (~15-30 daqiqa). Kechasi qilish kerak.

---

## 6. Bosqichlar

| Faza | Nima | Muddat |
|---|---|---|
| **0. Ma'lumot qutqaruv** | SQLite + PG merge, backup | 1 kun |
| **1. Yadro** | Modellar, migratsiya, /start, majburiy obuna, til tanlash | 2 kun |
| **2. Tarjima** | AI dvigatel, matn tarjimasi, limit, cache | 2 kun |
| **3. Kengaytirish** | Ovoz (STT), rasm (OCR), TTS, tarix, sevimlilar | 3 kun |
| **4. Admin** | Statistika, broadcast, xarajat monitoringi | 1 kun |
| **5. Deploy** | systemd/docker, nginx, monitoring, rollback rejasi | 1 kun |
| **6+.** | Lug'at bloki / Mini app / Premium — alohida qaror | — |

Jami 1-5: **~10 ish kuni**

---

## 7. Texnik qarorlar

| Savol | Tavsiya |
|---|---|
| Polling yoki webhook? | **Webhook** — 37k user uchun tezroq, nginx allaqachon bor |
| Deploy | **systemd** (docker-compose emas — server 1.5GB RAM, oddiyroq) |
| Cache | **Redis** — bir xil matn qayta tarjima qilinmasin (katta tejamkorlik) |
| FSM storage | **Redis** (hozir MemoryStorage — restart'da yo'qoladi) |
| Loglar | `loguru` + fayl rotatsiyasi (hozir 64MB broadcast.log to'planib qolgan) |
| Xatoliklar | Sentry yoki admin'ga Telegram orqali xabar |
| Testlar | Kritik yo'llar uchun pytest (tarjima, limit, migratsiya) |

---

## 8. Ochiq savollar — javob kerak

1. **Tarjima modeli:** Variant A (gibrid) / B (Opus 5) / C (bepul+zaxira)?
2. **Lug'at bloki:** 1-relizga kiritamizmi yoki keyinga?
3. **Gamification:** keraklimi?
4. **Mini App:** keraklimi, qachon?
5. **Kunlik bepul limit:** nechta tarjima? (masalan 50/kun bepul, keyin premium)
6. **Bot tokeni:** eskisini ishlatamizmi (`5098772001:...`) yoki yangisini?
7. **Domen:** `ilmda.uz` tarjimon uchun ishlatilsinmi?
