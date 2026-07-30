"""Antarmuka bahasa Indonesia.

Kunci harus sama persis dengan `uz.py`; `bot/locales/__init__.py` memeriksanya
saat impor sehingga terjemahan yang belum lengkap langsung menimbulkan error.
"""

CODE = "id"
NAME = "Indonesia"

# ── Tombol menu utama ────────────────────────────────────────
BTN_LANGUAGES = "🌐 Bahasa"
BTN_CONTACT = "✉️ Hubungi admin"
BTN_HELP = "ℹ️ Bantuan"

# ── Tombol inline ────────────────────────────────────────────
BTN_VOICE = "🔊 Suara"
BTN_SWAP = "🔄 Tukar"
BTN_BACK = "⬅️ Kembali"
BTN_CANCEL = "❌ Batal"
BTN_CHECK_SUBSCRIPTION = "✅ Periksa langganan"

# Nama untuk pseudo-bahasa `auto` — `name_native` kosong di basis data
# karena ini bukan bahasa sungguhan.
AUTO_DETECT = "Deteksi otomatis"

# ── Sambutan ─────────────────────────────────────────────────
WELCOME = (
    "👋 Halo, {name}!\n\n"
    "Saya <b>bot penerjemah</b>. Kirim teks apa pun dan saya akan menerjemahkannya.\n\n"
    "🌐 Arah saat ini: <b>{source} → {target}</b>\n"
    "Tekan <b>🌐 Bahasa</b> untuk mengubahnya."
)

WELCOME_BACK = (
    "Selamat datang kembali, {name}!\n\n"
    "🌐 Arah: <b>{source} → {target}</b>\n"
    "Kirim teks untuk diterjemahkan."
)

HELP = (
    "ℹ️ <b>Cara memakai bot</b>\n\n"
    "• Kirim teks apa pun — akan diterjemahkan otomatis\n"
    "• <b>🌐 Bahasa</b> — ubah arah terjemahan\n"
    "• <b>✉️ Hubungi admin</b> — kirim pertanyaan atau saran\n\n"
    "Tombol di bawah setiap terjemahan:\n"
    "🔊 — dengarkan teksnya\n"
    "🔄 — balik arah terjemahan\n"
    "🌐 — pilih bahasa\n\n"
    "Terjemahan dikirim <code>seperti ini</code> — ketuk untuk menyalin.\n\n"
    "Batas harian: <b>{limit}</b> terjemahan."
)

# ── Bahasa ───────────────────────────────────────────────────
LANGUAGE_MENU = (
    "🌐 <b>Arah terjemahan</b>\n\n"
    "Saat ini: <b>{source} → {target}</b>\n\n"
    "Pilih yang ingin diubah:"
)

PICK_SOURCE = "🔤 Pilih <b>bahasa sumber</b> (bahasa teks yang Anda kirim):"
PICK_TARGET = "🎯 Pilih <b>bahasa tujuan</b> (akan diterjemahkan ke bahasa apa):"

LANG_SAVED = "✅ Arah: <b>{source} → {target}</b>"
LANG_SWAPPED = "🔄 Ditukar: <b>{source} → {target}</b>"

# Tidak bisa menukar saat sumbernya `auto` dan belum ada bahasa terdeteksi.
SWAP_NEEDS_SOURCE = (
    "Deteksi otomatis tidak bisa menjadi bahasa tujuan. Pilih bahasa sumber dulu."
)


# ── Langganan ────────────────────────────────────────────────
SUBSCRIBE_REQUIRED = "❗️ Silakan gabung ke kanal di bawah untuk memakai bot:"
SUBSCRIBE_OK = "✅ Terima kasih! Sekarang Anda bisa memakai bot."
SUBSCRIBE_STILL_MISSING = "❌ Anda belum gabung ke semua kanal."
SEND_TEXT_PROMPT = "Kirim teks untuk diterjemahkan."

# ── Batas dan galat ──────────────────────────────────────────
QUOTA_EXCEEDED = (
    "🚫 <b>Batas harian tercapai</b>\n\n"
    "Anda sudah memakai {limit} terjemahan hari ini.\n"
    "Batas diperbarui setiap hari — bisa dilanjutkan besok."
)

TTS_QUOTA_EXCEEDED = "🚫 Batas suara hari ini ({limit}) sudah habis."

RATE_LIMITED = "⏳ Mohon sedikit lebih lambat. Coba lagi beberapa detik."

TOO_LONG = "📏 Teks terlalu panjang (<b>{length}</b> karakter).\n\nMaksimum: <b>{limit}</b>."

TTS_TOO_LONG = "📏 Teks terlalu panjang untuk suara. Maksimum: <b>{limit}</b> karakter."

SAME_LANGUAGE = (
    "🤔 Bahasa sumber dan tujuan sama (<b>{lang}</b>).\n\n"
    "Ubah arahnya di 🌐 Bahasa."
)

NO_TTS_FOR_LANG = "🔇 Suara tidak tersedia untuk <b>{lang}</b>."

ERRORS = {
    "timeout": "⌛ Layanan terjemahan tidak menjawab. Silakan coba lagi.",
    "provider_rate_limited": "⏳ Layanan terjemahan sedang sibuk. Coba lagi satu menit.",
    "invalid_language": "🌐 Bahasa salah dipilih. Pilih ulang di 🌐 Bahasa.",
    "empty_result": "😕 Tidak bisa menerjemahkan. Coba ubah teksnya.",
    "empty_input": "✍️ Kirim teks untuk diterjemahkan.",
}
ERROR_DEFAULT = "⚠️ Terjadi kesalahan. Silakan coba lagi."

TTS_ERRORS = {
    "timeout": "⌛ Suara gagal dibuat — layanan tidak menjawab.",
    "no_voice": "🔇 Suara tidak tersedia untuk bahasa ini.",
    "too_long": "📏 Teks terlalu panjang untuk suara.",
}
TTS_ERROR_DEFAULT = "⚠️ Tidak bisa membuat suara."

UNSUPPORTED_INPUT = (
    "📄 Untuk saat ini hanya terjemahan <b>teks</b> yang tersedia.\n\n"
    "Pesan suara dan gambar akan ditambahkan di pembaruan berikutnya."
)

TRANSLATION_NOT_FOUND = "Terjemahan ini tidak ditemukan (tombol kedaluwarsa)."
LANGUAGE_NOT_AVAILABLE = "Bahasa ini tidak tersedia"

# ── Hubungi admin ────────────────────────────────────────────
CONTACT_PROMPT = (
    "✉️ <b>Hubungi admin</b>\n\n"
    "Tulis pesan Anda — pesan akan langsung sampai ke admin.\n\n"
    "<i>Ini bukan obrolan: Anda menulis, admin membaca. Jika perlu balasan, "
    "cantumkan kontak Anda.</i>"
)
CONTACT_SENT = "✅ Pesan Anda sudah dikirim ke admin. Terima kasih!"
CONTACT_CANCELLED = "Dibatalkan."
CONTACT_TOO_LONG = "📏 Pesan terlalu panjang (<b>{length}</b> karakter). Maksimum: <b>{limit}</b>."
CONTACT_ONLY_TEXT = "✍️ Mohon kirim pesan Anda dalam bentuk teks."
CONTACT_RATE_LIMITED = (
    "⏳ Anda baru saja mengirim pesan.\n\n"
    "Anda bisa mengirim lagi dalam <b>{minutes}</b> menit."
)
CONTACT_FAILED = "⚠️ Pesan tidak bisa dikirim. Coba lagi nanti."
