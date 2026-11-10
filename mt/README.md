# Lokal tarjima servisi (`tarjimon8-mt`)

NLLB-200 distilled **1.3B** (CTranslate2 int8, faqat CPU) — botdan alohida
systemd xizmati, `127.0.0.1:8790`. Bot uni `local_mt` provayderi sifatida
ishlatadi (`bot/services/translation_providers.py`). O'rnatish: `DEPLOY.md`.

## Nega bu kerak (2026-11-10 holati)

- Google (`User Rate Limit Exceeded`), Azure (kalit yaroqsiz) va DeepL (oylik
  hajm tugagan) **har so'rovda yiqilyapti** — amalda hamma tarjimani MyMemory
  qiladi.
- MyMemory 500 belgidan uzun matnni qabul qilmaydi -> bunday matnlar
  `deep_translator`ga tushib **100% xato** bo'lardi (haftasiga ~245 ta).
- Lokal model: kvota yo'q, tashqi tarmoq yo'q, uzun matnni ham qiladi.

## Joriy sozlama va sababi

`LOCAL_MT_PRIORITY=3` — MyMemory'dan **keyin**. Ya'ni lokal model faqat
MyMemory qila olmaydiganini bajaradi (500+ belgi, MyMemory yiqilgan/kvota
tugagan). Sabab: botdagi haqiqiy og'zaki/jargonli matnlarning qo'lda
yonma-yon solishtiruvida (60 ta, en/ru/uz) MyMemory ko'pincha yaxshiroq
edi. Rasmiy matnda (FLORES) NLLB yaxshi, chat tilida esa zaif
("lichkam" -> "личинка", aralash tilli matnlar).

## O'lchovlar

FLORES-200 dev, birinchi 200 gap, beam 4, chrF++ / BLEU(13a)
(`bench/run_bench.py`, bu repodan tashqarida):

| yo'nalish | 600M | 1.3B |
|---|---|---|
| en→uz | 45.96 / 13.14 | **49.29 / 16.12** |
| uz→en | 55.83 / 29.89 | **59.65 / 35.06** |
| ru→uz | 43.57 / 11.05 | **45.72 / 12.87** |
| uz→ru | 44.06 / 18.87 | **47.00 / 21.52** |
| en→ru | 53.33 / 29.14 | **56.00 / 32.45** |
| ru→en | 59.67 / 35.27 | **62.19 / 37.76** |
| en→am | 33.52 / 9.09 | 33.39 / 8.89 |
| en→id | 65.60 / 42.40 | **67.21 / 44.53** |

Mo'ljal uchun (boshqa sharoit, FLORES+): Google Translate en→uz BLEU 22.98,
uz→en 41.18 (behbudiy/Llama-3.1-8B-Instruct-Uz model kartasi). 600M en→uz
13.14 — OvozifyLabs kartasidagi NLLB-600M bazasi 13.07 bilan mos.

Server (4 vCPU EPYC 2.0 GHz, 3 oqim, beam 2):

| | 600M | 1.3B |
|---|---|---|
| qisqa (~16 belgi) | 0.46 s | 1.08 s |
| o'rta (~100 belgi) | 2.35 s | 4.0 s |
| 554 belgi (gapma-gap) | — | 6.1 s |
| ~1600 belgi | 7.5 s | 15.8 s |
| RAM (anon) | ~1.0 GB | ~1.5 GB |

Taqqoslash: MyMemory p50 0.83 s, p90 2.0 s.

Rad etilgan nomzodlar: NLLB-3.3B va MADLAD-400-3B (int8 ~3.5–4 GB RAM,
2–3 baravar sekin — bu serverga sig'maydi), HY-MT (o'zbek yo'q, litsenziya),
TranslateGemma/kichik LLM'lar (CPU'da juda sekin).

**Litsenziya:** NLLB-200 — CC-BY-NC-4.0 (tijoriy emas). Bot bepul, donat
ixtiyoriy — shu asosda tanlandi. Bot pullik bo'lsa, qayta ko'rib chiqing.

## Dvigatel qarorlari (`engine.py`)

- Matn qator -> gaplarga bo'linadi, hammasi bitta batch'da; gapma-gap
  tarjima tez (parallel). Faqat juda qisqa gap (<8 token) qo'shnisiga
  qo'shiladi — alohida "Привет!" gallyutsinatsiya qilardi.
- Harfsiz bo'lak (URL, raqam, emoji) tarjima qilinmaydi; bo'lak boshi/oxiridagi
  emoji ajratib olinib qaytariladi (NLLB lug'atida ko'pi yo'q).
- Chiqishni tozalash: NLLB tinish belgisiz kirishda "minutes ?", "don ' t"
  beradi — `_tidy`.
- Takror/uzun chiqish -> qattiqroq jarima bilan qayta; baribir yomon bo'lsa
  422 `suspicious_output` (bot keyingi provayderga o'tadi).
- O'zbek kirill -> lotin (NLLB faqat `uzn_Latn`).

## Til aniqlash (`lid.py`)

fastText lid.176 o'zbek lotinni deyarli tanimaydi (FLORES'da ishonchli
to'g'ri ~4%, "tr"/"en"/"pt" deydi), oromo umuman yo'q. Qo'shimcha qoida
(oʻ/gʻ, o'zbek so'zlari/qo'shimchalari, turkiy harflar yo'qligi) bilan:
o'zbek to'liq gap ~95%, 4 so'zli ~72%; boshqa tillar xato "uz" <=4%
(istisno: qisqa tojikcha ~15%). 12 belgidan qisqa `auto` matn — rad
(`detection_failed`), bot keyingi provayderga o'tadi. Rad etish circuit
breaker'ga hisoblanmaydi (`ProviderDeclined`).

## Keyingi qadamlar

1. Bir-ikki hafta kuzatish: `provider='local_mt'` ulushi, xato/rad, latency
   (`journalctl -u tarjimon8-mt`, `translations` jadvali).
2. Fine-tune (laptop RTX 4050'da, LoRA/to'liq): botning 155k+ tarixiy
   tarjimalari — haqiqiy chat tilidagi parallel korpus. Maqsad: og'zaki
   matnda MyMemory darajasiga chiqib, `LOCAL_MT_PRIORITY`ni 1 ga tushirish.
   Diqqat: tashqi provayder chiqishlaridan o'qitish ularning shartlariga
   bog'liq — boshlashdan oldin tekshirish kerak.
3. Google/Azure/DeepL kalitlarini tuzatish yoki `.env`dan olib tashlash —
   hozir har so'rovga bekor kechikish qo'shadi (breaker 5 daqiqada qayta
   sinaydi).
