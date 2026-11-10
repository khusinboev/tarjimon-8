# Tarjimon-8 — Ma'lumotlar bazasi arxitekturasi

> Maqsad: 37k user bilan ishlaydigan, kengaytirishga moyil, va **kelajakda AI/ML uchun ma'lumot to'playdigan** struktura.
>
> Holat: 2026-11-10 — **17 jadval** (`bot/database/models.py`), migratsiyalar **001–018** (`alembic/versions/`).

---

## Loyihalash prinsiplari

**1. Fakt jadvallari o'zgarmas (append-only).**
`translations` va `events` hech qachon UPDATE/DELETE qilinmaydi. O'zgarish kerak bo'lsa — yangi qator. Sabab: ML uchun tarixiy haqiqat buzilmasligi kerak, va bu jadvallar keyinchalik ustun-saqlagichga (ClickHouse/Parquet) muammosiz ko'chiriladi.

**2. Har bir jadvalda `meta JSONB`.**
Yangi maydon kerak bo'lsa — migratsiyasiz `meta` ga yoziladi. Barqarorlashgandan keyin alohida ustunga ko'chiriladi. Bu "kengaytirishga moyil" degani.

**3. Enum'lar — `VARCHAR` + `CHECK`, PostgreSQL `ENUM` emas.**
PG'da `ENUM` ga yangi qiymat qo'shish jadval qulfini talab qiladi. `VARCHAR` + `CHECK` da esa constraint almashtiriladi, xolos.

**4. Tashqi kalitlar `users.id` ga, `telegram_id` ga emas.**
`telegram_id` — tabiiy kalit, lekin user o'chib qayta yozilishi mumkin. Ichki `BIGINT id` barqaror.

**5. Issiq va sovuq ma'lumot ajratilgan.**
Bot ishlashi uchun kerakli jadvallar (`users`, `user_settings`) kichik va tez. Analitik jadvallar (`events`, `translations`) katta va faqat yoziladi.

**6. Maxfiylik ML dan ustun.**
`user_settings.save_history` va `allow_training` — ikki alohida flag. ML eksporti faqat `allow_training = true` bo'lganlarni oladi. *(2026-11: ikkalasini o'zgartiradigan UI hali yo'q — ustunlar tayyor, hammada standart `true`.)*

**7. Vaqt — har doim `TIMESTAMPTZ`, UTC.**
Eski bazada `timestamp without time zone` edi — xato manbai. Bu sxemada hammasi TZ-aware.

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
| `interface_lang` | VARCHAR(10) | `en` — Telegram tilidan avtomatik (uz/en/id), user o'zgartirmaydi |
| `tts_enabled` | BOOL | `true` |
| `tts_auto` | BOOL | `false` — tarjimadan keyin avtomatik ovoz |
| `tts_voice` | VARCHAR(64) | NULL |
| `save_history` | BOOL | `true` — **maxfiylik** |
| `allow_training` | BOOL | `true` — **ML roziligi** |
| `daily_limit_override` | INT | NULL — admin bergan alohida tarjima limiti |
| `tts_limit_override` | INT | NULL — alohida ovoz limiti (010) |
| `image_limit_override` | INT | NULL — alohida rasm limiti (011) |
| `premium_until` | TIMESTAMPTZ | NULL — bot ichidagi VIP muddati (Telegram Premium emas) (010) |
| `referral_bonus_granted` | BOOL | `false` — referal bonusi ikki marta berilmasin (010) |
| `updated_at` | TIMESTAMPTZ | |
| `meta` | JSONB | |

#### `languages`
Statik ma'lumotnoma — **23 til** (`auto` dan tashqari): 002 da 21 ta, 006 da amhar (`am`), 009 da oromo (`om`). TTS ovozi 18 tilda bor (ky/tg/tk/om da yo'q).

`code` PK · `name_uz` · `name_en` · `name_native` · `flag` · `supports_tts` · `supports_detection` · `tts_voice` · `is_active` · `sort_order` · `meta`

---

### Blok 2 — Tarjima (ML uchun asosiy qiymat)

#### `translations`
**Bu jadval — loyihaning eng qimmatli aktivi.** Har bir qator = parallel korpus juftligi. Migratsiyada 155k ta kelgan, 2026-08-31 da ~195k.

| Ustun | Tur | Izoh |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `user_id` | BIGINT FK users.id | |
| `chat_id` | BIGINT | Telegram chat (guruh bo'lsa boshqa) |
| `chat_type` | VARCHAR(20) | `private` / `group` / `supergroup` / `channel` / `inline` |
| `input_kind` | VARCHAR(20) | `text` / `photo` / `caption` / `post` / `poll` / `checklist` / `video` / `audio` / `animation` / `video_note` / `paid_media` / `document` / `voice` / `forward` / `inline` (008) |
| `source_message_id` | BIGINT | Manba xabar (`chat_id` bilan birga) |
| `media_file_id` / `media_file_unique_id` | TEXT / VARCHAR(64) | Media manbasi (018); `unique_id` bo'yicha qisman indeks |
| `media_mime_type` / `media_file_size` / `media_file_name` | | (018) |
| `source_lang_requested` | VARCHAR(10) | `auto` yoki aniq kod |
| `source_lang_detected` | VARCHAR(10) | Haqiqatda aniqlangan |
| `target_lang` | VARCHAR(10) | |
| `source_text` | TEXT | |
| `target_text` | TEXT | |
| `source_hash` | CHAR(64) | SHA-256(normallashtirilgan matn + til juftligi) — **kesh va dedup** |
| `source_chars` / `target_chars` | INT | |
| `provider` | VARCHAR(32) | Bajargan dvigatel: `google_translate` / `azure_translator` / `deepl` / `mymemory` / `local_mt` / `gemini` / `deep_translator` / `cache` |
| `provider_model` | VARCHAR(64) | NULL |
| `provider_key_index` | INT | Provayderning qaysi kaliti (0-based) — oylik bepul hajm hisobi (014) |
| `ocr_provider` / `ocr_key_index` | VARCHAR(32) / INT | Faqat `photo`: `ocrspace` / `google_vision` va kalit (011, 012) |
| `status` | VARCHAR(20) | `success` / `error` / `timeout` |
| `error_code` / `error_message` | VARCHAR(64) / TEXT | |
| `latency_ms` | INT | |
| `cache_hit` | BOOL | Redis'dan kelganmi |
| `created_at` | TIMESTAMPTZ | |
| `meta` | JSONB | |

Indekslar:
- `(user_id, created_at)` — user bo'yicha so'rovlar
- `source_hash` — kesh qidiruvi
- `(provider, provider_key_index, created_at)` va `(input_kind, ocr_provider, ocr_key_index, created_at)` — oylik bepul hajm hisobi
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
| `favorited` / `unfavorited` | Sevimlilarga qo'shdi / olib tashladi (UI hali yo'q; migratsiyadan kelgan) | ➕➕ / ➖ |
| `retranslated` | 60 soniya ichida qayta yubordi | ➖ yomon tarjima |
| `lang_switched_after` | Tarjimadan keyin tilni o'zgartirdi | ➖ noto'g'ri til aniqlangan |
| `reported` | Shikoyat qildi | ➖➖ |
| `corrected` | O'z variantini yubordi | ➖ + oltin namuna |

> Bu jadval — hozirgi tizimda umuman yo'q narsa. Aynan shu tufayli "qaysi tarjimalar yomon" degan savolga javob yo'q. Uni birinchi kundan yig'ish kerak, chunki keyin orqaga qaytarib to'plab bo'lmaydi.

#### `tts_requests`
| `id` · `user_id` · `translation_id` (NULL) · `text` · `text_hash` · `lang` · `voice` · `provider` · `duration_ms` · `file_size` · `telegram_file_id` · `status` · `error_code` · `latency_ms` · `created_at` · `meta` |

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
| `menu.opened` | `{menu}` |
| `button.clicked` | `{callback_data, screen}` |
| `inline.query` | `{chars, lang_pair}` |
| `broadcast.delivered` | `{broadcast_id}` |
| `error.unhandled` | `{exc_type, handler}` |
| `image.ocr_*` / `image.quota_exceeded` | rasm tarjimasi (011) |
| `support.*`, `donate.*`, `premium.granted`, `user.referred`, `referral.bonus_granted` | murojaat, homiylik, VIP, referal |

To'liq ro'yxat: `bot/services/events.py` (`EventType`).

> **Nega bitta keng jadval, har voqea uchun alohida emas:** yangi voqea turi qo'shish migratsiya talab qilmaydi — shunchaki yangi `event_type` yoziladi. Bu "kengaytirishga moyil" degani. Kamchiligi — tip xavfsizligi yo'q; buni Python tarafda `EventType` konstanta klassi bilan qoplaymiz.

**Bo'laklash (partitioning) haqida:** hozircha kerak emas. Hisob: ~150k voqea/oy = 1.8M/yil. PostgreSQL 14 buni indekslar bilan yillar davomida muammosiz olib boradi. `events` 20M qatordan oshganda oylik `RANGE` bo'laklashga o'tamiz — `created_at` BRIN indeksi shu o'tishni oldindan tayyorlab qo'yadi.

#### `daily_usage`
Kunlik limit hisobi. Alohida jadval — `events` dan `COUNT(*)` qilish qimmat.

| `user_id` + `date` PK · `translations_count` · `tts_count` · `images_count` (011) · `chars_count` · `updated_at` |

Redis'da ham dublikat turadi (tezlik uchun), PG — haqiqat manbai.

---

### Blok 4 — Boshqaruv

#### `system_settings`
Yagona qator (`id=1`) — admin paneldan o'zgartiriladigan umumiy limitlar (011): `daily_translation_limit` · `daily_tts_limit` · `daily_image_limit_free` · `daily_image_limit_vip` · `updated_at`. `NULL` — `.env` dagi qiymat ishlaydi. `user_settings.*_override` dan farqi: bu HAMMA uchun standart.

#### `channels`
Majburiy obuna kanallari: `channel_id` (unique) · `channel_username` · `channel_title` · `button_text` · `button_url` · `is_active` · `priority` · `added_by` → users · `created_at` · `updated_at` · `meta`.

#### `broadcasts`
| Ustun | Izoh |
|---|---|
| `id`, `created_by` → users | |
| `mode` | `copy` / `forward` |
| `status` | `created` / `running` / `pause_requested` / `paused` / `cancel_requested` / `cancelled` / `completed` / `failed` |
| `content_preview` | |
| `src_chat_id` / `src_message_id` | Manba xabar — pauza va restartdan keyin davom ettirish uchun (013) |
| `cursor_user_id` | Oxirgi ishlangan `users.id` — davom shu yerdan (013) |
| `active_seconds` | Faqat haqiqiy yuborish vaqti — tezlik/qolgan vaqt hisobi (013) |
| `total_targets` / `success_count` / `failed_count` | |
| `error` | `failed` sababi |
| `created_at` / `started_at` / `finished_at` / `meta` | |

Bir vaqtda faqat **bitta faol** tarqatish — `ux_broadcasts_single_active` qisman unique indeks (015). Restartda `running`/`*_requested` qatorlar `paused` ga o'tkaziladi.

#### `broadcast_deliveries`
`broadcast_id` → broadcasts (CASCADE) · `user_id` → users · `status` (`delivered` / `failed` / `blocked` / `skipped`) · `error` · `created_at`.

#### `chats`
Bot qo'shilgan guruhlar: `chat_id` (unique) · `title` · `username` · `type` · `member_count` · `is_active` · `added_by` · `source_lang` / `target_lang` · `created_at` · `last_seen_at` · `meta`.

#### `support_messages` (007, 018)
Adminga murojaat yozishmasi. Har yetkazilgan xabar = bitta qator (bir necha adminga ketsa — har biriga alohida).

| Ustun | Izoh |
|---|---|
| `id`, `user_id` → users (CASCADE) | |
| `direction` | `in` — userdan adminga, `out` — admindan userga |
| `text` | Media bo'lsa belgi (`[🖼 rasm]`) |
| `admin_chat_id` / `admin_message_id` / `user_chat_id` / `user_message_id` | Faqat tarix/audit uchun (017 dan beri suhbatni aniqlashda ishlatilmaydi, indekslari o'chirilgan) |
| `media_kind` / `media_file_id` / `media_file_unique_id` / `media_mime_type` / `media_file_size` / `media_file_name` | (018) |
| `created_at` / `meta` | |

#### `admin_reply_targets` (016)
Admin "↩️ Javob yozish" tugmasini bosib tanlagan suhbat: `admin_chat_id` PK · `target_user_id` → users · `set_at`. Har admin uchun bitta qator; keyingi oddiy xabari shu userga ketadi. Sabab: Telegram eski xabarlar uchun `reply_to_message` ni uzatmasligi mumkin (2026-08-27 voqeasi).

#### `donations` (005)
Telegram Stars homiyligi — o'chirilmaydi, o'zgartirilmaydi (faqat qaytarishda `status`).

`id` · `user_id` → users · `stars` (> 0) · `currency` (`XTR`) · `telegram_payment_charge_id` (unique — takroriy xabar va `refundStarPayment` uchun) · `provider_payment_charge_id` · `invoice_payload` · `status` (`paid` / `refunded`) · `refunded_at` · `created_at` · `meta`.

#### `admin_actions`
Audit izi (`📜 Audit`): `admin_id` → users · `action` · `target_type` · `target_id` · `payload` JSONB · `created_at`.

---

## Tarjima provayderlari

`bot/services/translation.py` + `translation_providers.py`. Har so'rov avval Redis keshidan qidiriladi (`provider='cache'`), keyin zanjir:

| Tartib | Provayder | Shart | Holat (2026-11-10) |
|---|---|---|---|
| 0 (tasodifiy) | `google_translate`, `azure_translator`, `deepl` | Kalit bor, oylik bepul hajm tugamagan; DeepL `am` ni qilmaydi | Uchchalasi yiqilyapti (Google rate-limit, Azure kalit yaroqsiz, DeepL kvota) — kalitlar saqlanadi |
| 2 | `mymemory` | ≤ 500 belgi, 50k belgi/kun | Amalda asosiy |
| `LOCAL_MT_PRIORITY` (prod: 3) | `local_mt` | `LOCAL_MT_URL` bor | NLLB-200 1.3B, `mt/README.md` |
| keyin | `gemini` | `GEMINI_API_KEYS` bor | O'chirilgan (kalit yo'q) |
| oxirgi | `deep_translator` | har doim | Scraper, hozir yiqiladi |

Bir xil ustunlikdagilar tasodifiy tartibda. Kalit 2 daqiqada 3 marta yiqilsa 5 daqiqaga chetlanadi (`circuit_breaker.py`); til mos kelmasligi xato hisoblanmaydi. 15 daqiqada xato ≥10% bo'lsa adminga ogohlantirish (`error_rate.py`). Oylik hajm `translations` dan `(provider, provider_key_index)` bo'yicha sanaladi.

---

## Migratsiyalar

| № | Nima |
|---|---|
| 001–004 | Asosiy sxema, 21 til, server default'lar, `interface_lang` to'ldirish |
| 005 | `donations` |
| 006 / 009 | Amhar / oromo tillari |
| 007 | `support_messages` |
| 008 | `input_kind` kengaytirildi (caption, post, poll, …) |
| 010 | VIP (`premium_until`), referal bayrog'i, `tts_limit_override` |
| 011 | Rasm tarjimasi: `ocr_provider`, `images_count`, `image_limit_override`, `system_settings` |
| 012 | `ocr_key_index` |
| 013 | Tarqatishni pauza/davom ettirish ustunlari |
| 014 | `provider_key_index` |
| 015 | Bitta faol tarqatish (qisman unique indeks) |
| 016 | `admin_reply_targets` |
| 017 | Murojaat reply-indekslari o'chirildi |
| 018 | Media fayl identifikatorlari (`translations`, `support_messages`) |

---

## Migratsiya: eski → yangi (2026-08, bajarilgan)

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

Migratsiya eski serverda bajarilgan; `t4.service` endi yo'q.

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

## Birinchi reliz doirasi (2026-07, tarix)

Kelishilgan edi: **faqat matn tarjimasi + TTS**, lug'at va gamification yo'q. Keyin qo'shilganlar: homiylik, murojaat, VIP/referal, rasm tarjimasi (005–018).

| Jadval | 1-relizda |
|---|---|
| `users`, `user_settings`, `languages` | ✅ |
| `translations`, `translation_signals` | ✅ |
| `tts_requests` | ✅ |
| `events`, `daily_usage` | ✅ |
| `channels`, `broadcasts`, `broadcast_deliveries` | ✅ (tayyor) |
| `chats`, `admin_actions` | ✅ |
| lug'at / mashq / gamification jadvallari | ❌ keyinga |

O'shanda provayder faqat `deep-translator` edi; hozirgi zanjir — [Tarjima provayderlari](#tarjima-provayderlari).
