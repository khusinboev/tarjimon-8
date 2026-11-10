# Tarjimon-8 — Barqarorlashtirish rejasi

> Holat: **2026-09-11 — P0 (1–4), P1 (5–6), P2 (8) BAJARILDI va deploy qilindi.**
> Qolgan: P1 §7 (qisqa matn edge-case), P2 §9 (journal), P2 §10 (kunlik hisobot).
> Qo'shimcha (2026-09-11): 1-darajaga DeepL (1M/oy) va MyMemory (50k/kun,
> past ustunlik) qo'shildi; Gemini `.env`da o'chirildi; circuit breaker
> (`bot/services/circuit_breaker.py`) — buzilgan kalit har so'rovga 1 s
> qo'shmaydi; xato-darajasi ogohlantirishi (`bot/services/error_rate.py`).
> Sana: 2026-08-31
> Asos: jonli serverdagi loglar va bazadagi 194,741 ta tarjima yozuvi tahlili
> (taxmin emas — har bir band quyidagi dalillarga tayanadi)

---

## 0. Dalillar — muammo qayerda

### 0.1 Tarjima xatolari (kunlik ulush, `translations` jadvali)

| Sana | Xato % | Izoh |
|---|---|---|
| 17–22 avgust | 0.1–0.4% | sog'lom |
| 23 avgust | 2.2% | boshlandi |
| 24 avgust | **17%** | |
| **25 avgust** | **45.6%** | har ikkinchi tarjima yiqilgan |
| 26 avgust | 4.3% | Google kvotasi yangilandi |
| 27–28 avgust | 0% | Google ishlayapti |
| 29–30 avgust | 3–4% | Google tugadi → Gemini + deep_translator |
| 31 avgust | 0.6% | Gemini'ga to'lov qilingandan keyin |

**30 kunda 874 ta foydalanuvchi xato olgan** (`provider_error` 874, `empty_result` 40,
`provider_blocked` 27; `events` jadvalida `translate.failed` — 942).

**Eng muhim xulosa:** 25-avgustdagi 45% falokat paytida **hech qanday ogohlantirish
kelmagan**, chunki mavjud signal faqat "uchala daraja ham yiqildi" holatini ushlaydi —
deep_translator "ishlagan" bo'lsa, tizim buni muvaffaqiyat deb hisoblaydi.

### 0.2 Kechikish (7 kun, `latency_ms`)

| Provayder | o'rtacha | p50 | p95 |
|---|---|---|---|
| cache | 1 ms | 0 ms | 5 ms |
| google_translate | 360 ms | 187 ms | 1,179 ms |
| gemini | 1,384 ms | 614 ms | 6,305 ms |
| **deep_translator** | **3,956 ms** | 1,379 ms | **17,202 ms** |

Zaxiraga tushgan foydalanuvchi p95 da **17 soniya** kutadi.

### 0.3 Ushlanmagan istisnolar (~20 soatda 10 ta)

Global `@dp.error` handler **yo'q** → istisno faqat log'ga tushadi,
**foydalanuvchi hech qanday javob olmaydi** (bot "jim qoladi").

| Joy | Istisno | Soni |
|---|---|---|
| `bot/handlers/user/languages.py:133` `set_language` | `TelegramBadRequest: MESSAGE_ID_INVALID` | 4 |
| `bot/handlers/user/languages.py:196` `swap_languages` | `AttributeError: 'InaccessibleMessage' object has no attribute 'edit_text'` | 2 |
| `bot/handlers/user/tts.py:147` `send_voice` | `TelegramBadRequest: VOICE_MESSAGES_FORBIDDEN` | 2 |
| `bot/handlers/user/translate.py:378` `handle_photo` | `TelegramNetworkError: Request timeout` | 1 |
| `languages.py` (edit) | `message is not modified` | 1 |

### 0.4 Diagnostika teshigi

Gemini xatolarining **62% (21/34)** log'da **bo'sh sabab** bilan yozilgan:

```
bot.services.translation - WARNING - gemini (kalit #0) ishlamadi:
```

Sabab: `bot/services/translation_providers.py:186` da `except aiohttp.ClientError` —
lekin `aiohttp.ClientTimeout(total=15)` **`asyncio.TimeoutError`** ko'taradi, u
`ClientError`ning bolasi emas. Shu sababli u yuqoridagi umumiy `except Exception`ga
tushadi va `str(exc)` bo'sh bo'ladi. **Ya'ni nima uchun yiqilayotganini bilmayapmiz.**

### 0.5 Chidamlilik

- Har bir provayderda **1 tadan kalit** (Google 1, Gemini 1, Azure 0) — zaxira yo'q.
- Transient xatoda **qayta urinish yo'q**: bitta timeout → darhol 17-soniyalik
  deep_translator'ga tushadi.
- `deep_translator` qisqa/noaniq matnni uddalay olmaydi
  (masalan `"see, seen"` → `No translation was found using the current translator`).
- `journald` ~20 soatda rotatsiya bo'lyapti — orqaga qarab tekshirib bo'lmaydi.

---

## P0 — Crashlar: foydalanuvchi jim qoladigan holatlar

> ~2 soat · eng arzon, eng ko'p foyda

1. **Global `@dp.error` handler** — bitta joyda yuqoridagi 10 ta crashni qoplaydi:
   foydalanuvchiga tushunarli xabar + adminga signal (dedup bilan).
   *Busiz har qanday yangi xato ham jimgina yo'qoladi.*

2. **`safe_edit()` yordamchisi** — `InaccessibleMessage`, `MESSAGE_ID_INVALID`,
   `message is not modified` holatlarini ushlaydi.
   Chaqiruv joylari atigi 7 ta: `languages.py`, `subscription.py`.

3. **`answer_voice` himoyasi** (`tts.py:147`) — foydalanuvchi ovozli xabarlarni
   yopib qo'ygan bo'lsa (`VOICE_MESSAGES_FORBIDDEN`), tushunarli javob berilsin.
   Hozir: crash + sarflangan TTS limiti qaytarilmaydi.

4. **`bot.download` timeout himoyasi** (`translate.py:378`) — rasm yuklanmasa,
   band qilingan rasm limiti ortga qaytarilsin va xabar berilsin.

---

## P1 — Tarjima ishonchliligi

> ~3 soat · P0 dan keyin

5. **`asyncio.TimeoutError` alohida ushlansin** (`translation_providers.py`) —
   log'da aniq sabab ko'rinsin (`"timeout 15s"`).
   *Bu birinchi bo'lishi shart: busiz 6- va 7-bandlar ko'r-ko'rona bo'ladi.*

6. **Gemini'ga 1 marta qayta urinish** — transient `high demand` / timeout'da
   darhol 17-soniyalik deep_translator'ga tushmasin.

7. **Qisqa matn edge-case** — deep_translator `No translation found` bersa,
   foydalanuvchiga xato o'rniga oqilona javob.

---

## P2 — Ko'rinuvchanlik

> ~2 soat

8. **Xato DARAJASI bo'yicha ogohlantirish** — oxirgi 15 daqiqada xato ulushi >10%
   bo'lsa adminga xabar.
   *Aynan shu 25-avgustdagi falokatni birinchi soatidayoq ushlagan bo'lardi.*

9. **Journal saqlash muddati** — `journald` sozlamasi (hozir ~20 soat).

10. **Kunlik avto-hisobot** adminga: tarjima soni, xato %, provayder taqsimoti,
    o'rtacha kechikish, Gemini xarajati.

---

## P3 — Keyingi bosqichlar (alohida muhokama)

Bular barqarorlashtirishdan **keyin**, alohida reja bilan:

- **Monetizatsiya** — 194k tarjima, 1,982 oylik faol user, lekin jami **1 ta to'lov ($0.1)**.
- **Amhar tili segmenti** — `en↔am` juftligi 17.6k (ru↔uz dan katta), lekin amhar
  interfeys tili yo'q.
- **Xarajatni kamaytirish** — 194k haqiqiy (matn, tarjima) juftligi asosida eng ko'p
  ishlatiladigan yo'nalishlar (`en-uz`, `ru-uz`, `uz-en` — ~65k) uchun o'z modelini
  moslashtirish.
- **Churn tahlili** — 41,566 userdan 30% botni bloklagan, 25% akkountini o'chirgan.

---

## Bajarilish tartibi

```
P0 (crashlar) → P1.5 (timeout log) → P1 (ishonchlilik) → P2 (monitoring)
```

Har bir bosqichdan keyin: `scripts/smoke_test.py` (hozir 268 ta tekshiruv) →
deploy → jonli loglarda tasdiqlash.
