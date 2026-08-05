"""Antarmuka bahasa Indonesia.

Kunci harus sama persis dengan `uz.py`; `bot/locales/__init__.py` memeriksanya
saat impor sehingga terjemahan yang belum lengkap langsung menimbulkan error.
"""

CODE = "id"
NAME = "Indonesia"

# ── Tombol menu utama ────────────────────────────────────────
BTN_LANGUAGES = "🌐 Bahasa"
BTN_DONATE = "⭐ Donasi"
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
    "• <b>⭐ Donasi</b> — dukung proyek ini dengan Telegram Stars\n"
    "• <b>✉️ Hubungi admin</b> — kirim pertanyaan atau saran\n"
    "• <b>/invite</b> — undang teman, Anda berdua dapat hari VIP\n\n"
    "Tombol di bawah setiap terjemahan:\n"
    "🔊 — dengarkan teksnya\n"
    "🔄 — balik arah terjemahan\n"
    "🌐 — pilih bahasa\n\n"
    "Terjemahan dikirim <code>seperti ini</code> — ketuk untuk menyalin.\n\n"
    "Batas harian: <b>{limit}</b> terjemahan.\n\n"
    "👤 Untuk hal lainnya: @{admin}"
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

BANNED = (
    "🚫 <b>Akun Anda diblokir</b>\n\n"
    "Akses ke bot sementara ditutup. Pertanyaan: @{admin}"
)

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
    "🔍 Tidak ada teks yang bisa diterjemahkan di pesan ini.\n\n"
    "Kirim teks, atau beri <b>keterangan</b> pada foto, video atau dokumen — "
    "keterangan juga saya terjemahkan."
)
NO_TEXT_FOUND = "🔍 Tidak ada teks yang bisa diterjemahkan di pesan ini."

TRANSLATION_NOT_FOUND = "Terjemahan ini tidak ditemukan (tombol kedaluwarsa)."
LANGUAGE_NOT_AVAILABLE = "Bahasa ini tidak tersedia"

DEVELOPER = (
    "👨‍💻 <b>Pengembang bot</b>\n\n"
    "@{admin}\n\n"
    "Anda juga bisa menulis saran, pertanyaan atau laporan bug lewat "
    "<b>✉️ Hubungi admin</b>."
)

# ── Hubungi admin ────────────────────────────────────────────
CONTACT_PROMPT = (
    "✉️ <b>Hubungi admin</b>\n\n"
    "Tulis pesan Anda — pesan akan langsung sampai ke admin.\n\n"
    "Teks, foto, video, pesan suara, dokumen — semua jenis bisa dikirim.\n\n"
    "<i>Balasan admin akan muncul di sini. Untuk melanjutkan percakapan, "
    "cukup balas pesannya — kapan saja, tanpa membuka menu ini lagi.</i>"
)
CONTACT_SENT = (
    "✅ <b>Pesan Anda sudah dikirim ke admin</b>\n\n"
    "Balasannya akan muncul di sini. Balas pesan itu untuk melanjutkan percakapan."
)
CONTACT_CANCELLED = "Dibatalkan."
CONTACT_TOO_LONG = "📏 Pesan terlalu panjang (<b>{length}</b> karakter). Maksimum: <b>{limit}</b>."
CONTACT_RATE_LIMITED = (
    "⏳ Anda baru saja mengirim pesan.\n\n"
    "Anda bisa mengirim lagi dalam <b>{minutes}</b> menit."
)
CONTACT_FAILED = "⚠️ Pesan tidak bisa dikirim. Coba lagi nanti."
CONTACT_REPLY_HEADER = (
    "✉️ <b>Balasan dari admin</b>\n\n"
    "<i>Balas pesan ini untuk menjawab.</i>"
)
CONTACT_REPLY_SENT = "✅ Balasan Anda sudah dikirim ke admin."

# ── Donasi (Telegram Stars) ──────────────────────────────────
DONATE_INTRO = (
    "⭐ <b>Donasi</b>\n\n"
    "Bot ini gratis dan tanpa iklan. Namun server dan layanan terjemahan "
    "membutuhkan biaya.\n\n"
    "Jika bot ini bermanfaat, Anda bisa mendukungnya dengan Telegram Stars. "
    "Berapa pun jumlahnya sangat membantu.\n\n"
    "Pilih jumlah di bawah:"
)
DONATE_INVOICE_TITLE = "Dukung bot penerjemah"
DONATE_INVOICE_DESC = "{stars} ⭐ — terima kasih telah mendukung bot ini!"
DONATE_THANKS = (
    "❤️ <b>Terima kasih!</b>\n\n"
    "Anda berdonasi <b>{stars} ⭐</b>. Ini langsung membantu bot tetap berjalan.\n\n"
    "Terima kasih banyak atas dukungan Anda!"
)
DONATE_THANKS_PREMIUM = (
    "❤️ <b>Terima kasih!</b>\n\n"
    "Anda berdonasi <b>{stars} ⭐</b> dan mendapatkan <b>{days} hari VIP</b>!\n\n"
    "💎 Selama VIP, batas harian terjemahan dan suara tidak terbatas.\n"
    "⏳ Berlaku sampai: <b>{until}</b>.\n\n"
    "Terima kasih banyak atas dukungan Anda!"
)
DONATE_VIP_ACTIVE = "💎 <b>VIP aktif</b> — batas harian tidak terbatas sampai <b>{until}</b>."
DONATE_FAILED = "⚠️ Pembayaran tidak bisa dimulai. Coba lagi nanti."
DONATE_CUSTOM = "✏️ Jumlah lain"
DONATE_CUSTOM_PROMPT = (
    "✏️ Berapa ⭐ yang ingin Anda donasikan?\n\n"
    "Kirim angkanya ({min}–{max})."
)
DONATE_CUSTOM_INVALID = "❌ Kirim angka saja, antara {min} dan {max}."
DONATE_CARD_BUTTON = "💳 Lewat kartu"
DONATE_CARDS = (
    "💳 <b>Donasi lewat kartu</b>\n\n"
    "Ketuk nomornya untuk menyalin.\n\n"
    "🔵 <b>VISA</b>\n<code>{visa}</code>\n\n"
    "🟢 <b>UzCard</b>\n<code>{uzcard}</code>\n\n"
    "👤 {holder}\n\n"
    "<i>Berapa pun membantu. Terima kasih!</i>"
)

# ── Program referal ────────────────────────────────────────────
INVITE_TEXT = (
    "🎁 <b>Undang teman-teman Anda</b>\n\n"
    "Tautan pribadi Anda:\n<code>{link}</code>\n\n"
    "Begitu teman yang bergabung lewat tautan ini membuat terjemahan pertamanya:\n"
    "• Anda dapat <b>{referrer_days} hari VIP</b>\n"
    "• dia dapat <b>{welcome_days} hari VIP</b>\n\n"
    "Bagikan ke sebanyak mungkin orang — tidak ada batasnya."
)
REFERRAL_WELCOME_NOTICE = (
    "🎁 <b>Bonus selamat datang!</b>\n\n"
    "Anda bergabung lewat tautan undangan, jadi Anda mendapat <b>{days} hari VIP</b>.\n"
    "⏳ Berlaku sampai: <b>{until}</b>."
)
REFERRAL_BONUS_NOTICE = (
    "🎉 <b>Bonus referal!</b>\n\n"
    "Teman yang Anda undang mulai memakai bot — Anda mendapat "
    "<b>{days} hari VIP</b>.\n"
    "⏳ Berlaku sampai: <b>{until}</b>."
)

# ── Pengumuman peluncuran ulang (scripts/broadcast_relaunch.py) ──
RELAUNCH = (
    "🎉 <b>Bot sudah diperbarui dan kembali aktif!</b>\n\n"
    "Hai! Kami menulis ulang bot ini dari awal — sekarang lebih cepat, lebih "
    "stabil, dan lebih nyaman.\n\n"
    "<b>Yang baru:</b>\n\n"
    "📋 <b>Mudah disalin</b> — ketuk hasil terjemahan, teks langsung tersalin\n"
    "🔄 <b>Tombol tukar</b> — balik arah terjemahan dengan sekali ketuk\n"
    "🔊 <b>Suara</b> — dengarkan hasil terjemahan\n"
    "🌐 <b>23 bahasa</b> — Indonesia, Inggris, Rusia, Turki, Arab, Amharik, dan lainnya\n"
    "⚡️ <b>Cepat</b> — terjemahan yang sama kembali seketika\n"
    "🗣 <b>Bahasa Anda</b> — antarmuka mengikuti bahasa Telegram Anda\n\n"
    "Kirim teks apa pun untuk mencoba 👇"
)
