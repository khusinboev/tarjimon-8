# Serverga o'rnatish

Server: `23.94.2.204` · PostgreSQL 14 · Redis · systemd

> ⚠️ **Eski bot va bazalarga qo'l tegizilmaydi.** Yangi bot yonida quriladi,
> yangi `tarjimon8` bazasi bilan. Muammo chiqsa — `t4.service` ni qaytarib
> yoqish kifoya.

---

## 0. Nima bo'layotganini tushunish

Hozirgi holat: `t4.service` ishlaydi, lekin `.env` da `DBTYPE=True` yozilgani
uchun bot PostgreSQL o'rniga **SQLite faylga** yozyapti (`config.py` `DBTYPE`
qiymatini `postgres` deb kutadi). Natijada:

| Manba | Userlar | Tarjimalar | Holat |
|---|---|---|---|
| PostgreSQL `tarjimon` | 34,606 | 96,427 | 2026-04-29 dan muzlagan |
| SQLite fayl | ~4,310 | ~59,358 | faol |
| **Birlashgandan keyin** | **37,439** | **155,755** | |

Migratsiya ikkalasini birlashtiradi.

---

## 1. Tayyorgarlik (bot ishlab turaveradi)

```bash
ssh root@23.94.2.204

# Papka va kod
mkdir -p /home/tarjimon8
cd /home/tarjimon8
git clone https://github.com/khusinboev/tarjimon-8.git app
cd app

# Muhit
python3 -m venv /home/tarjimon8/venv
/home/tarjimon8/venv/bin/pip install -U pip
/home/tarjimon8/venv/bin/pip install -r requirements.txt
```

## 2. Baza yaratish

```bash
sudo -u postgres createdb tarjimon8
```

## 3. `.env` sozlash

```bash
cp .env.example .env
nano .env
```

To'ldirish kerak:

```ini
BOT_TOKEN=<ishlab turgan botning tokeni>
ADMIN_USER_ID=1918760732
POSTGRES_DB=tarjimon8
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<parol>
DAILY_TRANSLATION_LIMIT=50
EVENT_LOG_LEVEL=all
```

## 4. Sxemani qo'llash

```bash
cd /home/tarjimon8/app
/home/tarjimon8/venv/bin/alembic upgrade head
```

Tekshirish — 13 jadval va 21 til bo'lishi kerak:

```bash
sudo -u postgres psql -d tarjimon8 -c "\dt"
sudo -u postgres psql -d tarjimon8 -tAc "SELECT count(*) FROM languages;"
```

## 5. Zaxira nusxa (majburiy)

```bash
mkdir -p /home/backups
pg_dump -U postgres -h localhost tarjimon | gzip > /home/backups/tarjimon-$(date +%F).sql.gz
cp /home/tarjimon4/tarjimon4/tarjimon /home/backups/tarjimon-sqlite-$(date +%F).db
ls -lh /home/backups/
```

## 6. Migratsiyani quruq sinash

```bash
cd /home/tarjimon8/app
/home/tarjimon8/venv/bin/python scripts/migrate_legacy.py --dry-run \
  --legacy-pg 'dbname=tarjimon user=postgres password=<parol> host=localhost' \
  --sqlite /home/tarjimon4/tarjimon4/tarjimon
```

Kutilgan natija: ~37,439 user, ~155,755 tarjima. Sonlar keskin farq qilsa —
to'xtang va sababini aniqlang.

---

## 7. To'xtatish oynasi (~15 daqiqa)

Bu yerdan boshlab bot vaqtincha ishlamaydi. Kam faollik vaqtida qiling.

```bash
# 7.1 Eski botni to'xtatamiz — SQLite ga yozish to'xtashi kerak,
#     aks holda migratsiya paytida yangi yozuvlar yo'qoladi.
systemctl stop t4

# 7.2 Migratsiya
cd /home/tarjimon8/app
/home/tarjimon8/venv/bin/python scripts/migrate_legacy.py \
  --legacy-pg 'dbname=tarjimon user=postgres password=<parol> host=localhost' \
  --sqlite /home/tarjimon4/tarjimon4/tarjimon \
  --target 'dbname=tarjimon8 user=postgres password=<parol> host=localhost'
```

Tekshirish:

```bash
sudo -u postgres psql -d tarjimon8 -c "
SELECT
  (SELECT count(*) FROM users)          AS userlar,
  (SELECT count(*) FROM user_settings)  AS sozlamalar,
  (SELECT count(*) FROM translations)   AS tarjimalar,
  (SELECT count(*) FROM chats)          AS guruhlar;
"
```

```bash
# 7.3 Yangi botni ishga tushirish
cp deploy/tarjimon8.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tarjimon8
systemctl status tarjimon8 --no-pager
journalctl -u tarjimon8 -f
```

Logda `Bot ishga tushdi: @<username>` ko'rinishi kerak.

## 8. Tekshirish

Telegramda botga yozing:

- [ ] `/start` — xush kelibsiz + menyu chiqadi
- [ ] Matn yuboring — tarjima keladi
- [ ] 🔊 tugmasi — ovoz keladi
- [ ] 🌐 Tillar — yo'nalish o'zgaradi
- [ ] 📜 Tarix — **eski tarjimalaringiz ko'rinadi** (migratsiya ishlaganini tasdiqlaydi)
- [ ] ⚙️ Sozlamalar — tugmalar ishlaydi
- [ ] `/admin` — statistika ko'rinadi

Baza tomonidan:

```bash
sudo -u postgres psql -d tarjimon8 -c "
SELECT event_type, count(*) FROM events
WHERE created_at > now() - interval '10 minutes'
GROUP BY 1 ORDER BY 2 DESC;
"
```

## 9. Eski botni o'chirish (bir necha kundan keyin)

Yangi bot muammosiz ishlaganiga ishonch hosil qilgach:

```bash
systemctl disable t4
```

`/home/tarjimon4` va eski bazalarni **hali o'chirmang** — kamida bir oy tursin.

---

## Orqaga qaytish (rollback)

Har qanday bosqichda muammo chiqsa:

```bash
systemctl stop tarjimon8
systemctl start t4
```

Eski bot va uning bazalari o'zgarmagan — darhol ishlaydi.

---

## Kundalik ish

```bash
# Loglar
journalctl -u tarjimon8 -f
journalctl -u tarjimon8 --since "1 hour ago" -p err

# Qayta ishga tushirish
systemctl restart tarjimon8

# Kodni yangilash
cd /home/tarjimon8/app
git pull
/home/tarjimon8/venv/bin/pip install -r requirements.txt
/home/tarjimon8/venv/bin/alembic upgrade head
systemctl restart tarjimon8
```

### Kunlik zaxira (cron)

```bash
crontab -e
```

```cron
0 3 * * * pg_dump -U postgres -h localhost tarjimon8 | gzip > /home/backups/tarjimon8-$(date +\%F).sql.gz
0 4 * * * find /home/backups -name 'tarjimon8-*.sql.gz' -mtime +14 -delete
```

---

## Foydali so'rovlar

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

-- Limitga yetgan userlar
SELECT count(*) FROM daily_usage
WHERE date = current_date AND translations_count >= 50;

-- Voqealar voronkasi
SELECT event_type, count(*) FROM events
WHERE created_at > current_date GROUP BY 1 ORDER BY 2 DESC;
```
