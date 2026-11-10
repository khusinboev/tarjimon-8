# Server va ekspluatatsiya

> Holat: 2026-11-10. Bot shu serverda 2026-08 dan beri ishlaydi; eski
> `t4.service` dan migratsiya tugagan (tarix — oxirida).

---

## 1. Server va xizmatlar

Server: `193.187.129.197` · Ubuntu 24.04 · 4 vCPU · 7.8 GB RAM · GPU yo'q ·
PostgreSQL · Redis · systemd

| Nima | Qayerda |
|---|---|
| Kod (git, `main`) | `/home/tarjimon8/app` |
| Bot venv | `/home/tarjimon8/venv` |
| Sozlamalar | `/home/tarjimon8/app/.env` (git'da yo'q) |
| Lokal MT venv | `/home/tarjimon8/mt-venv` |
| MT modellari | `/home/tarjimon8/models` |
| Baza | PostgreSQL `tarjimon8` |
| Xizmatlar | `tarjimon8.service` (bot), `tarjimon8-mt.service` (lokal MT) — fayllar `deploy/` da |

> ⚠️ **Shu serverda boshqa loyihalar ham bor** (`tizor-*` xizmatlari, nginx,
> ularning bazalari). Ularga **qo'l tegizilmaydi**: restart, `apt upgrade`,
> Postgres/Redis sozlamasini o'zgartirish — faqat egasi bilan kelishib.

```bash
systemctl status tarjimon8 tarjimon8-mt --no-pager
journalctl -u tarjimon8 -f
journalctl -u tarjimon8 --since "1 hour ago" -p warning
```

---

## 2. Kundalik ish: kodni yangilash

```bash
cd /home/tarjimon8/app
git log --oneline -1            # hozirgi commit — rollback uchun yozib qo'ying
git pull
/home/tarjimon8/venv/bin/pip install -r requirements.txt
/home/tarjimon8/venv/bin/alembic upgrade head
systemctl restart tarjimon8
journalctl -u tarjimon8 -n 50 --no-pager   # "Bot ishga tushdi: @..." bo'lishi kerak
```

Smoke test (Telegramsiz, ~280 tekshiruv; haqiqiy bazada sinov user
`999000001` bilan ishlaydi va o'zidan keyin tozalaydi, provayderlarga haqiqiy
so'rov yuboradi):

```bash
cd /home/tarjimon8/app
/home/tarjimon8/venv/bin/python scripts/smoke_test.py
```

Oxirida `Natija: N o'tdi, 0 yiqildi` bo'lishi kerak. Keyin Telegramda qo'lda:
`/start`, matn tarjimasi, 🔊, tillar menyusi, rasm, `/admin`.

---

## 3. Lokal tarjima modeli (`tarjimon8-mt`)

NLLB-200 (CTranslate2 int8, CPU) — botdan ALOHIDA xizmat, faqat
`127.0.0.1:8790`. Bot uni `LOCAL_MT_URL` orqali `local_mt` provayderi
sifatida ishlatadi (`bot/services/translation_providers.py`).
Servis yiqilsa/sekinlashsa bot o'zi boshqa provayderlarga o'tadi —
foydalanuvchi uchun xavfsiz. Qaror va o‘lchovlar: `mt/README.md`.

O'rnatish (bir marta):

```bash
cd /home/tarjimon8
python3 -m venv mt-venv
mt-venv/bin/pip install -r app/mt/requirements.txt huggingface_hub
mkdir -p models/lid
curl -sL -o models/lid/lid.176.ftz https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz
mt-venv/bin/hf download OpenNMT/nllb-200-distilled-1.3B-ct2-int8 --local-dir models/nllb-200-distilled-1.3B-ct2-int8

cp app/deploy/tarjimon8-mt.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tarjimon8-mt
curl -s 127.0.0.1:8790/health
curl -s 127.0.0.1:8790/translate -H 'content-type: application/json' \
  -d '{"text":"Hello, how are you?","source":"en","target":"uz"}'
```

Botga ulash — `.env`:

```ini
LOCAL_MT_URL=http://127.0.0.1:8790
LOCAL_MT_PRIORITY=3     # 0 — Google/Azure/DeepL bilan teng, 1 — ulardan keyin, 3 — MyMemory'dan ham keyin (prod)
LOCAL_MT_TIMEOUT=60
```

`systemctl restart tarjimon8`. O'chirish (rollback): `.env`dan
`LOCAL_MT_URL`ni olib tashlab botni qayta ishga tushirish kifoya.

Model almashtirish: `deploy/tarjimon8-mt.service`dagi `MT_MODEL_DIR`
(masalan tezroq `nllb-200-distilled-600M-ct2-int8`), keyin
`systemctl daemon-reload && systemctl restart tarjimon8-mt`.

```bash
journalctl -u tarjimon8-mt -f          # har so'rov: yo'nalish, uzunlik, ms
systemctl status tarjimon8-mt          # RAM (MemoryMax=2560M)
```

---

## 4. Zaxira va tiklash

### Zaxira qanday olinadi

Botning o'z cron'i **yo'q**. Bazani serverning umumiy `tizor-backup.timer`i
har kuni ~22:30 da oladi:

```
/var/backups/tizor/daily/tarjimon8-YYYYMMDD.dump
```

7 ta kunlik + haftalik nusxalar saqlanadi, **faqat shu serverda** (tashqi
nusxa yo'q — server yo'qolsa zaxira ham yo'qoladi).

```bash
systemctl list-timers tizor-backup.timer --no-pager   # oxirgi/keyingi ishga tushish
ls -lh /var/backups/tizor/daily/ | grep tarjimon8
```

> Timer `tizor` loyihasiga tegishli — uni o'zgartirmang.

### Tiklash

> ⚠️ **Avval nusxada sinang.** Quyidagi tartib ishlab turgan `tarjimon8`
> bazasini o'chirmaydi — yangi bazaga tiklab, tekshirib, keyin almashtiradi.
> Dump olingandan keyingi ma'lumot (≤ ~24 soat) yo'qoladi — buni
> tushunib qiling.

```bash
DUMP=/var/backups/tizor/daily/tarjimon8-20261109.dump   # kerakli sana

# 1. Fayl butunmi (custom format). "text format dump" desa —
#    pg_restore emas, `psql -f` kerak bo'ladi.
sudo -u postgres pg_restore --list "$DUMP" | head

# 2. Yangi bazaga tiklash (ishlab turgan baza tegilmaydi)
sudo -u postgres createdb tarjimon8_restore
sudo -u postgres pg_restore --no-owner -d tarjimon8_restore "$DUMP"
```

```bash
# 3. Tekshirish — sonlar ishlab turgan bazadan biroz kam bo'lishi tabiiy
sudo -u postgres psql -d tarjimon8_restore -c "
SELECT (SELECT version_num FROM alembic_version)  AS migratsiya,
       (SELECT count(*) FROM users)               AS userlar,
       (SELECT count(*) FROM translations)        AS tarjimalar,
       (SELECT max(created_at) FROM translations) AS oxirgi_tarjima;"
```

`migratsiya` joriy kod bilan mos bo'lishi kerak
(`/home/tarjimon8/venv/bin/alembic current`). Eski bo'lsa — almashtirgandan
keyin `alembic upgrade head`.

```bash
# 4. Almashtirish (bot to'xtaydi, ~1 daqiqa)
systemctl stop tarjimon8
sudo -u postgres psql -c "ALTER DATABASE tarjimon8 RENAME TO tarjimon8_old_$(date +%Y%m%d);"
sudo -u postgres psql -c "ALTER DATABASE tarjimon8_restore RENAME TO tarjimon8;"
systemctl start tarjimon8
journalctl -u tarjimon8 -n 50 --no-pager
```

`RENAME` bazaga ulanish bo'lsa ishlamaydi — bot to'xtaganini tekshiring
(`SELECT datname, count(*) FROM pg_stat_activity GROUP BY 1;`).
Keyin smoke test (§2). Eski `tarjimon8_old_*` bazani **kamida bir hafta
o'chirmang** — orqaga qaytish uchun teskari `RENAME` kifoya.

Bitta jadvalni tiklash kerak bo'lsa — butun bazani almashtirmang:
`tarjimon8_restore` dan kerakli qatorlarni SQL bilan ko'chiring.

---

## 5. Nosozlik bo'lsa

Adminga Telegramda `🚨 Tarjima xatolari ko'paydi` (15 daqiqada ≥30 so'rov,
xato ≥10%, `bot/services/error_rate.py`) yoki `🆘 Tarjima BUTUNLAY ishlamayapti`
keldi:

1. **Bot tirikmi?** `systemctl status tarjimon8 --no-pager`. Yiqilish
   siklida (`StartLimitBurst=5` / 5 daqiqa) to'xtagan bo'lsa —
   `systemctl reset-failed tarjimon8 && systemctl start tarjimon8`.
2. **Qaysi provayder?**
   ```bash
   journalctl -u tarjimon8 --since "30 min ago" | grep -E "ishlamadi|bo'sh javob|rad etdi|xato darajasi"
   ```
   va §6 dagi "Provayder sog'lig'i" so'rovi (`provider` ustuni bo'yicha).
   Ma'lum holat (2026-11): Google rate-limit, Azure kaliti yaroqsiz, DeepL
   oylik hajm tugagan — bular breaker orqali chetlanadi, amalda tarjimani
   MyMemory va `local_mt` qiladi.
3. **Circuit breaker.** Bir kalit 2 daqiqada 3 marta yiqilsa, 5 daqiqaga
   chetlanadi (`bot/services/circuit_breaker.py`). Ochiq breaker'lar:
   ```bash
   redis-cli -n 0 --scan --pattern 'cb:open:*'
   ```
   Kalit tuzatilgach kutmasdan qaytarish: `redis-cli -n 0 del cb:open:deepl:0`.
4. **Lokal MT.** `systemctl status tarjimon8-mt`, `curl -s 127.0.0.1:8790/health`.
   Sekinlashtirsa yoki RAM yetmasa — o'chirish: `.env`da `LOCAL_MT_URL=`
   (bo'sh) → `systemctl restart tarjimon8`; kerak bo'lsa
   `systemctl stop tarjimon8-mt`.
5. **Yangi kod buzgan bo'lsa — rollback:**
   ```bash
   cd /home/tarjimon8/app
   git log --oneline -5
   # Yangi commit migratsiya qo'shgan bo'lsa — AVVAL (yangi kod turganda):
   #   /home/tarjimon8/venv/bin/alembic downgrade -1
   git checkout <oldingi_commit>
   systemctl restart tarjimon8
   ```
   Tuzatish chiqqach: `git checkout main && git pull` → §2.
6. **Baza buzilgan** — §4 (tiklash).

---

## 6. Foydali so'rovlar

```bash
sudo -u postgres psql -d tarjimon8
```

```sql
-- Bugungi faollik
SELECT count(*) FILTER (WHERE status = 'success') AS muvaffaqiyatli,
       count(*) FILTER (WHERE status <> 'success') AS xato,
       count(DISTINCT user_id) AS faol_user
FROM translations WHERE created_at > current_date;

-- Ommabop yo'nalishlar
SELECT source_lang_detected, target_lang, count(*)
FROM translations WHERE status = 'success'
GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;

-- Provayder sog'lig'i (xato ulushi soatlar bo'yicha)
SELECT date_trunc('hour', created_at) AS soat,
       round(100.0 * count(*) FILTER (WHERE status <> 'success') / count(*), 1) AS xato_foiz,
       count(*) AS jami
FROM translations WHERE created_at > now() - interval '24 hours'
GROUP BY 1 ORDER BY 1 DESC;

-- Qaysi provayder qancha tarjima qilyapti (oxirgi 24 soat)
SELECT provider, status, count(*)
FROM translations WHERE created_at > now() - interval '24 hours'
GROUP BY 1, 2 ORDER BY 3 DESC;

-- Limitga yetgan userlar
SELECT count(*) FROM daily_usage
WHERE date = current_date AND translations_count >= 50;

-- Voqealar voronkasi
SELECT event_type, count(*) FROM events
WHERE created_at > current_date GROUP BY 1 ORDER BY 2 DESC;
```

---

## Ilova: Tarix (2026-08 migratsiya) — eskirgan

> Faqat ma'lumot uchun. Bu qadamlarni **qayta bajarmang** — `t4.service` va
> eski server (`23.94.2.204`) endi yo'q.

2026-08 da eski `tarjimon4` bot (`t4.service`) o'rniga shu bot qo'yildi.
Eski bot `.env`dagi `DBTYPE=True` xatosi tufayli PostgreSQL `tarjimon` o'rniga
SQLite faylga yozib kelgan edi; `scripts/migrate_legacy.py` ikkala manbani
`telegram_id` bo'yicha birlashtirdi (~37,4k user, ~155,7k tarjima). Keyin
`scripts/import_legacy_bots.py` boshqa kichik tarjimon botlar (`ttbot`, `tt4`)
userlarini qo'shdi. Qadamlar: kodni klonlash → `createdb tarjimon8` →
`alembic upgrade head` → eski bazalarni zaxiralash → `migrate_legacy.py
--dry-run` → `systemctl stop t4` → migratsiya → `tarjimon8.service` yoqish.
