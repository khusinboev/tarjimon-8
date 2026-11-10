# Tarjimon-8 — Ma'lumotlar bazasi arxitekturasi

> Maqsad: 37k user bilan ishlaydigan, kengaytirishga moyil, va **kelajakda AI/ML uchun ma'lumot to'playdigan** struktura.

---

## Loyihalash prinsiplari

**1. Fakt jadvallari o'zgarmas (append-only).**
`translations` va `events` hech qachon UPDATE/DELETE qilinmaydi. O'zgarish kerak bo'lsa — yangi qator. Sabab: ML uchun tarixiy haqiqat buzilmasligi kerak, va bu jadvallar keyinchalik ustun-saqlagichga (ClickHouse/Parquet) muammosiz ko'chiriladi.

**2. Har bir jadvalda `meta JSONB`.**
Yangi maydon kerak bo'lsa — migratsiyasiz `meta` ga yoziladi. Barqarorlashgandan keyin alohida ustunga ko'chiriladi. Bu "kengaytirishga moyil" degani.

**3. Enum'lar — `VARCHAR` + `CHECK`, PostgreSQL `ENUM` emas.**
PG'da `ENUM` ga yangi qiymat qo'shish jadval qulfini talab qiladi. `VARCHAR` + `CHECK` da esa constraint almashtiriladi, xolos.

**4. Tashqi kalitlar `users.id` ga, `telegram_id` ga emas.**
`telegram_id` — tabiiy kalit, lekin user o'chib qayta yozilishi mumkin. Ichki `BIGINT id` barqaror. *(Hozirgi `tarjimon-8` sxemasida FK'lar `telegram_id` ga qaragan — buni tuzatamiz.)*

**5. Issiq va sovuq ma'lumot ajratilgan.**
Bot ishlashi uchun kerakli jadvallar (`users`, `user_settings`) kichik va tez. Analitik jadvallar (`events`, `translations`) katta va faqat yoziladi.

**6. Maxfiylik ML dan ustun.**
`user_settings.save_history` va `allow_training` — ikki alohida flag. ML eksporti faqat `allow_training = true` bo'lganlarni oladi.

**7. Vaqt — har doim `TIMESTAMPTZ`, UTC.**
Hozirgi bazada `timestamp without time zone` — bu xato manba. Yangi sxemada hammasi TZ-aware.

---

## Jadvallar

### Blok 1 — Foydalanuvchi (issiq, kichik)

#### `users`
Kim bizning userimiz. Faqat identifikatsiya va holat.

| Ustun | Tur | Izoh |
|---|---|---|
| `id` | BIGSERIAL PK | Ichki barqaror kalit — barcha FK shunga |
| `telegram_id` | BIGINT UNIQUE NOT NULL | Telegram ID |
| `username` | VARCHAR(255) | @username, o'zgaruvchan |
| `first_name` / `last_name` | VARCHAR(255) | |
| `telegram_lang` | VARCHAR(10) | Telegram'dan kelgan til |
| `is_premium` | BOOL | Telegram Premium |
| `status` | VARCHAR(20) | `active` / `blocked_bot` / `banned` / `deleted` |
| `role` | VARCHAR(20) | `user` / `admin` / `owner` |
| `source` | VARCHAR(64) | `/start` deep-link parametri (referal manbai) |
| `referred_by` | BIGINT FK users.id | Kim taklif qilgan |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |
| `last_seen_at` | TIMESTAMPTZ | Har harakatda yangilanadi |
| `blocked_at` | TIMESTAMPTZ | Botni bloklagan vaqt |
| `meta` | JSONB | |

Indekslar: `telegram_id` (unique), `status`, `created_at`, `last_seen_at`, `referred_by`

> `is_blocked` + `is_active` ikki bool o'rniga bitta `status` — holatlar bir-birini inkor qiladi, ikki bool 4 ta kombinatsiya beradi, ulardan 2 tasi mantiqsiz.

#### `user_settings`
Users bilan 1:1. Alohida jadval, chunki tez-tez o'zgaradi va `users` ni shishirmasligi kerak.

| Ustun | Tur | Default |
|---|---|---|
| `user_id` | BIGINT PK FK users.id | |
| `source_lang` | VARCHAR(10) | `auto` |
| `target_lang` | VARCHAR(10) | `uz` |
| `interface_lang` | VARCHAR(10) | `uz` |
| `tts_enabled` | BOOL | `true` |
| `tts_auto` | BOOL | `false` — tarjimadan keyin avtomatik ovoz |
| `tts_voice` | VARCHAR(64) | NULL |
| `save_history` | BOOL | `true` — **maxfiylik** |
| `allow_training` | BOOL | `true` — **ML roziligi** |
| `daily_limit_override` | INT | NULL — admin bergan alohida limit |
| `updated_at` | TIMESTAMPTZ | |
| `meta` | JSONB | |

#### `languages`
Statik ma'lumotnoma (hozirgi bazada 21 ta).

`code` PK · `name_uz` · `name_en` · `name_native` · `flag` · `supports_tts` · `supports_detection` · `is_active` · `sort_order`

---

### Blok 2 — Tarjima (ML uchun asosiy qiymat)

#### `translations`
**Bu jadval — loyihaning eng qimmatli aktivi.** Har bir qator = parallel korpus juftligi. Hozir 155k ta bor, oyiga +25k.

| Ustun | Tur | Izoh |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `user_id` | BIGINT FK users.id | |
| `chat_id` | BIGINT | Telegram chat (guruh bo'lsa boshqa) |
| `chat_type` | VARCHAR(20) | `private` / `group` / `supergroup` / `inline` |
| `input_kind` | VARCHAR(20) | `text` / `voice` / `photo` / `forward` / `inline` |
| `source_lang_requested` | VARCHAR(10) | `auto` yoki aniq kod |
| `source_lang_detected` | VARCHAR(10) | Haqiqatda aniqlangan |
| `target_lang` | VARCHAR(10) | |
| `source_text` | TEXT | |
| `target_text` | TEXT | |
| `source_hash` | CHAR(64) | SHA-256(normallashtirilgan matn + til juftligi) — **kesh va dedup** |
| `source_chars` / `target_chars` | INT | |
| `provider` | VARCHAR(32) | `deep_translator` / `google` / kelajakda `claude` |
| `provider_model` | VARCHAR(64) | NULL — AI'ga o'tganda to'ladi |
| `status` | VARCHAR(20) | `success` / `error` / `timeout` |
| `error_code` | VARCHAR(64) | |
| `latency_ms` | INT | |
| `cache_hit` | BOOL | Redis'dan kelganmi |
| `created_at` | TIMESTAMPTZ | |
| `meta` | JSONB | |

Indekslar:
- `(user_id, created_at DESC)` — tarix ko'rsatish
- `source_hash` — kesh qidiruvi
- `(source_lang_detected, target_lang)` — til juftligi statistikasi
- `created_at` BRIN — analitik skanlar uchun arzon
- `status` (partial: `WHERE status <> 'success'`) — xatolar monitoringi

> **Nima uchun bu ML uchun qimmat:** 155k+ real foydalanuvchi matni, o'zbek tili juftliklari bilan. Bu ochiq korpuslarda kam uchraydigan ma'lumot. Fine-tuning, sifat baholash, yoki o'z modelini o'qitish uchun asos.

#### `translation_signals`
Implicit va explicit feedback — **tarjima sifatini o'lchash uchun**.

| Ustun | Tur | Izoh |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `translation_id` | BIGINT FK | |
| `user_id` | BIGINT FK | |
| `signal` | VARCHAR(32) | quyida |
| `correction` | TEXT | User o'zi tuzatgan variant (NULL) |
| `created_at` | TIMESTAMPTZ | |

Signal turlari:

| Signal | Ma'nosi | ML uchun |
|---|---|---|
| `copied` | Nusxa oldi | ➕ yaxshi tarjima |
| `tts_played` | Ovozda eshitdi | ➕ qiziqish |
| `favorited` | Sevimlilarga qo'shdi | ➕➕ juda yaxshi |
| `retranslated` | 60 soniya ichida qayta yubordi | ➖ yomon tarjima |
| `lang_switched_after` | Tarjimadan keyin tilni o'zgartirdi | ➖ noto'g'ri til aniqlangan |
| `reported` | Shikoyat qildi | ➖➖ |
| `corrected` | O'z variantini yubordi | ➖ + oltin namuna |

> Bu jadval — hozirgi tizimda umuman yo'q narsa. Aynan shu tufayli "qaysi tarjimalar yomon" degan savolga javob yo'q. Uni birinchi kundan yig'ish kerak, chunki keyin orqaga qaytarib to'plab bo'lmaydi.

#### `tts_requests`
| `id` · `user_id` · `translation_id` (NULL) · `text` · `lang` · `voice` · `provider` · `duration_ms` · `file_size` · `telegram_file_id` · `status` · `error_code` · `latency_ms` · `created_at` · `meta` |

`telegram_file_id` saqlanadi — bir xil matn qayta so'ralsa, Telegram'ning o'z fayli qayta yuboriladi (tez va bepul).

---

### Blok 3 — Harakatlar jurnali (ML substrati)

#### `events`
**Botdagi har bir harakat shu yerga tushadi.**

| Ustun | Tur | Izoh |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `user_id` | BIGINT FK NULL | Anonim voqealar uchun NULL |
| `chat_id` | BIGINT NULL | |
| `session_id` | UUID NULL | 30 daqiqalik faollik oynasi |
| `event_type` | VARCHAR(64) | `domain.action` formatida |
| `source` | VARCHAR(20) | `bot` / `admin` / `system` / `webhook` |
| `payload` | JSONB | Voqeaga xos ma'lumot |
| `created_at` | TIMESTAMPTZ | |

Indekslar:
- `(user_id, created_at DESC)` — user yo'lini tiklash
- `(event_type, created_at DESC)` — voronka tahlili
- `created_at` BRIN — arzon vaqt skani
- `payload` GIN — JSONB ichidan qidiruv

**Voqealar taksonomiyasi:**

| `event_type` | `payload` |
|---|---|
| `user.start` | `{source, is_new, referred_by}` |
| `user.blocked_bot` | `{}` |
| `subscription.checked` | `{passed, missing[]}` |
| `subscription.joined` | `{channel_id}` |
| `lang.selected` | `{slot: source\|target, code, method: menu\|swap\|auto}` |
| `settings.changed` | `{field, old, new}` |
| `translate.requested` | `{input_kind, chars, src, dst}` |
| `translate.succeeded` | `{translation_id, latency_ms, provider, detected}` |
| `translate.failed` | `{error_code, provider}` |
| `translate.quota_exceeded` | `{limit, used}` |
| `tts.requested` / `tts.succeeded` / `tts.failed` | `{translation_id, lang, chars}` |
| `feedback.given` | `{translation_id, signal}` |
| `history.viewed` | `{page}` |
| `menu.opened` | `{menu}` |
| `button.clicked` | `{callback_data, screen}` |
| `inline.query` | `{chars, lang_pair}` |
| `broadcast.delivered` | `{broadcast_id}` |
| `error.unhandled` | `{exc_type, handler}` |

> **Nega bitta keng jadval, har voqea uchun alohida emas:** yangi voqea turi qo'shish migratsiya talab qilmaydi — shunchaki yangi `event_type` yoziladi. Bu "kengaytirishga moyil" degani. Kamchiligi — tip xavfsizligi yo'q; buni Python tarafda `EventType` konstanta klassi bilan qoplaymiz.

**Bo'laklash (partitioning) haqida:** hozircha kerak emas. Hisob: ~150k voqea/oy = 1.8M/yil. PostgreSQL 14 buni indekslar bilan yillar davomida muammosiz olib boradi. `events` 20M qatordan oshganda oylik `RANGE` bo'laklashga o'tamiz — `created_at` BRIN indeksi shu o'tishni oldindan tayyorlab qo'yadi.

#### `daily_usage`
Kunlik limit hisobi (50 ta/kun). Alohida jadval — `events` dan `COUNT(*)` qilish qimmat.

| `user_id` + `date` PK · `translations_count` · `tts_count` · `chars_count` · `updated_at` |

Redis'da ham dublikat turadi (tezlik uchun), PG — haqiqat manbai.

---

### Blok 4 — Boshqaruv (tarjimon-8 da tayyor)

- `channels` — majburiy obuna kanallari
- `broadcasts` / `broadcast_deliveries` — xabar tarqatish
- `chats` — bot qo'shilgan guruhlar (`chat_id`, `title`, `type`, `member_count`, `is_active`, `added_by`)
- `admin_actions` — admin nima qilgani (audit): `admin_id`, `action`, `target_type`, `target_id`, `payload`, `created_at`

---

## Migratsiya: eski → yangi

```
PostgreSQL `tarjimon`          SQLite fayl              Yangi `tarjimon8`
─────────────────────          ───────────              ─────────────────
accounts (34,606)         ┐
                          ├──→ dedup(telegram_id) ──→   users (37,430)
accounts (4,298)          ┘                             + user_settings

user_langs (34,606)       ┐
user_languages (11,412)   ├──→ birlashtirish ──────→    user_settings
users_tts (34,606)        ┘

translation_history (96,427) ┐
translation_history (59,248) ├→ dedup(id+vaqt) ────→    translations (155,675)
                             ┘

languages (21)              ──────────────────────→     languages
groups (35)                 ──────────────────────→     chats
```

**Dedup qoidasi:** `telegram_id` bo'yicha guruhlash, eng yangi `date` g'olib. Sozlamalarda SQLite (yangiroq) ustun.

**Migratsiya paytida yo'qotmaslik kerak:**
- `accounts.date` → `users.created_at` (ro'yxatdan o'tgan sana — qimmatli)
- `translation_history.is_favorite` → `translation_signals(signal='favorited')`
- `users_tts` sozlamasi → `user_settings.tts_enabled`

**Xavfsizlik:** eski `tarjimon` bazasi va SQLite fayli **qo'l tegizilmaydi**. Yangi baza `tarjimon8` nomi bilan yonida yaratiladi. Muammo chiqsa — `t4.service` ni qaytarib yoqish kifoya.

---

## ML uchun nima chiqadi

6 oydan keyin qo'lda bo'ladigan ma'lumot:

| Ma'lumot | Hajm | Nimaga yaraydi |
|---|---|---|
| Parallel korpus (`translations`) | 300k+ juftlik | Tarjima modelini fine-tune qilish, sifat baholash |
| Sifat signallari | ~50k signal | Qaysi tarjimalar yomon — RLHF/DPO uchun preference juftliklari |
| User yo'llari (`events`) | 2M+ | Voronka tahlili, churn bashorati, tavsiya tizimi |
| Til juftliklari taqsimoti | — | Qaysi yo'nalishga investitsiya qilish |
| Foydalanish vaqti naqshlari | — | Yuklama bashorati, keshni oldindan isitish |

Eksport uchun `views` tayyorlanadi:
```sql
-- ML eksport: faqat rozilik bergan userlar, muvaffaqiyatli tarjimalar
CREATE VIEW ml_parallel_corpus AS
SELECT t.source_text, t.target_text, t.source_lang_detected, t.target_lang,
       t.created_at,
       COALESCE(s.positive, 0) - COALESCE(s.negative, 0) AS quality_score
FROM translations t
JOIN user_settings us ON us.user_id = t.user_id
LEFT JOIN (...) s ON s.translation_id = t.id
WHERE us.allow_training = true
  AND t.status = 'success'
  AND t.source_chars BETWEEN 3 AND 5000;
```

---

## Birinchi reliz doirasi

Kelishilgan: **faqat matn tarjimasi + TTS**, lug'at va gamification yo'q.

| Jadval | 1-relizda |
|---|---|
| `users`, `user_settings`, `languages` | ✅ |
| `translations`, `translation_signals` | ✅ |
| `tts_requests` | ✅ |
| `events`, `daily_usage` | ✅ |
| `channels`, `broadcasts`, `broadcast_deliveries` | ✅ (tayyor) |
| `chats`, `admin_actions` | ✅ |
| lug'at / mashq / gamification jadvallari | ❌ keyinga |

Tarjima provayderi: **`deep-translator`** (boshqa botlar bilan bir xil).
`provider` / `provider_model` ustunlari sxemada bor — kelajakda AI qo'shilsa, migratsiyasiz ishlaydi.
