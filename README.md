# 🤖 Tarjimon-8 — Universal Telegram Bot Template

`khusinboev/mser` loyihasidan ajratib olingan, faqat umumiy (universal) qismlarni
o'z ichiga olgan Telegram bot shabloni. Loyihaga xos (masalan, natija ko'rish,
webapp havolalari kabi) handler va tugmalar olib tashlangan — bu shablon
ustiga istalgan yangi bot (jumladan AI bilan qurollangan tarjimon bot)
qurish mumkin.

## ✨ Features

- ✅ Admin panel (statistika, kanal boshqaruvi, broadcast)
- 📢 Broadcast system (forward / copy, progress tracking, cancel qilish)
- 📺 Majburiy obuna (forced subscription) tizimi
- 🗄️ PostgreSQL database (SQLAlchemy async + Alembic migratsiyalar)
- 🚀 Redis caching (obuna tekshiruvini keshlash)
- 📈 Foydalanuvchi tracking (analytics middleware)
- 🔒 Xatoliklarni boshqarish va validatsiya

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.11+
- PostgreSQL 16+
- Redis 7+
- Docker (optional)

### 2. Installation

```bash
git clone <your-repo-url>
cd tarjimon-8

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# .env faylini o'z ma'lumotlaringiz bilan to'ldiring
```

### 3. Database Setup

```bash
alembic upgrade head
```

### 4. Run Bot

```bash
# Development
python -m bot.main

# Production (Docker)
docker-compose up -d
```

## 📁 Project Structure

```
tarjimon-8/
├── bot/
│   ├── config/          # Sozlamalar (pydantic-settings)
│   ├── database/        # Modellar, session, redis, repositories
│   ├── handlers/        # Admin va user handlerlar
│   ├── keyboards/       # Reply/Inline klaviaturalar
│   ├── middlewares/     # Analytics middleware
│   ├── services/        # Biznes logika
│   ├── states/          # FSM holatlari
│   └── utils/           # Yordamchi funksiyalar
├── alembic/             # Database migratsiyalari
└── docker-compose.yml   # Docker konfiguratsiyasi
```

## 🎯 Usage

### Admin Commands

- `/admin` yoki `/panel` — Admin panelga kirish (faqat `ADMIN_USER_ID(S)`)
- `/developer` — Bot dasturchisi haqida

### User Flow

- `/start` — ro'yxatdan o'tish va xush kelibsiz xabari
- Majburiy obuna tekshiruvidan o'tmagan userlarga kanal ro'yxati ko'rsatiladi
- Boshqa har qanday matn uchun umumiy javob qaytariladi (bu joyni loyihangizga
  moslab almashtirasiz — masalan, AI tarjima logikasi shu yerga ulanadi)

## 📊 Database Schema

- `users` — Foydalanuvchilar
- `channels` — Majburiy obuna kanallari
- `broadcasts` / `broadcast_deliveries` — Xabar yuborish va yetkazish tracking

## 🔧 Configuration

`.env` faylda quyidagi parametrlarni sozlang:

- `BOT_TOKEN` — Telegram bot token
- `ADMIN_USER_ID` / `ADMIN_USER_IDS` — Admin user ID(lar)
- `DATABASE_URL` yoki `POSTGRES_*` — PostgreSQL ulanish sozlamalari
- `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` — Redis sozlamalari

## 🧭 Keyingi qadam

Bu shablon "sof" holatda — AI bilan ishlaydigan tarjimon funksiyalari
(matn/ovoz/rasm tarjimasi va h.k.) keyingi bosqichda shu shablon ustiga
qo'shiladi.

## 📝 License

MIT License
