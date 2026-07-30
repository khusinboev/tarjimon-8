"""English interface — also the fallback for every language except `uz` and `id`.

Keys must mirror `uz.py` exactly; `bot/locales/__init__.py` verifies this at
import time so a half-finished translation fails loudly instead of silently
falling back.
"""

CODE = "en"
NAME = "English"

# ── Reply menu buttons ───────────────────────────────────────
BTN_LANGUAGES = "🌐 Languages"
BTN_CONTACT = "✉️ Contact admin"
BTN_HELP = "ℹ️ Help"

# ── Inline buttons ───────────────────────────────────────────
BTN_VOICE = "🔊 Voice"
BTN_SWAP = "🔄 Swap"
BTN_BACK = "⬅️ Back"
BTN_CANCEL = "❌ Cancel"
BTN_CHECK_SUBSCRIPTION = "✅ Check subscription"

# Name for the `auto` pseudo-language — `name_native` is empty in the database
# because it is not a real language.
AUTO_DETECT = "Auto detect"

# ── Greetings ────────────────────────────────────────────────
WELCOME = (
    "👋 Hello, {name}!\n\n"
    "I am a <b>translator bot</b>. Send me any text and I will translate it.\n\n"
    "🌐 Current direction: <b>{source} → {target}</b>\n"
    "Tap <b>🌐 Languages</b> to change it."
)

WELCOME_BACK = (
    "Welcome back, {name}!\n\n"
    "🌐 Direction: <b>{source} → {target}</b>\n"
    "Send a text to translate."
)

HELP = (
    "ℹ️ <b>How to use the bot</b>\n\n"
    "• Send any text — it is translated automatically\n"
    "• <b>🌐 Languages</b> — change the translation direction\n"
    "• <b>✉️ Contact admin</b> — send a question or suggestion\n\n"
    "Buttons under each translation:\n"
    "🔊 — listen to the text\n"
    "🔄 — reverse the direction\n"
    "🌐 — pick languages\n\n"
    "Translations are sent <code>like this</code> — tap to copy.\n\n"
    "Daily limit: <b>{limit}</b> translations."
)

# ── Languages ────────────────────────────────────────────────
LANGUAGE_MENU = (
    "🌐 <b>Translation direction</b>\n\n"
    "Current: <b>{source} → {target}</b>\n\n"
    "Pick what to change:"
)

PICK_SOURCE = "🔤 Pick the <b>source language</b> (what you will send):"
PICK_TARGET = "🎯 Pick the <b>target language</b> (what to translate into):"

LANG_SAVED = "✅ Direction: <b>{source} → {target}</b>"
LANG_SWAPPED = "🔄 Swapped: <b>{source} → {target}</b>"

# Cannot swap while the source is `auto` and nothing has been detected yet.
SWAP_NEEDS_SOURCE = (
    "Auto detect cannot be a target language. Pick a source language first."
)


# ── Subscription ─────────────────────────────────────────────
SUBSCRIBE_REQUIRED = "❗️ Please join the channels below to use the bot:"
SUBSCRIBE_OK = "✅ Thank you! You can use the bot now."
SUBSCRIBE_STILL_MISSING = "❌ You have not joined all the channels yet."
SEND_TEXT_PROMPT = "Send a text to translate."

# ── Limits and errors ────────────────────────────────────────
QUOTA_EXCEEDED = (
    "🚫 <b>Daily limit reached</b>\n\n"
    "You have used {limit} translations today.\n"
    "The limit resets every day — you can continue tomorrow."
)

TTS_QUOTA_EXCEEDED = "🚫 Today's voice limit ({limit}) is used up."

RATE_LIMITED = "⏳ A little slower, please. Try again in a few seconds."

TOO_LONG = "📏 The text is too long (<b>{length}</b> characters).\n\nMaximum: <b>{limit}</b>."

TTS_TOO_LONG = "📏 The text is too long for voice. Maximum: <b>{limit}</b> characters."

SAME_LANGUAGE = (
    "🤔 Source and target languages are the same (<b>{lang}</b>).\n\n"
    "Change the direction in 🌐 Languages."
)

NO_TTS_FOR_LANG = "🔇 Voice is not available for <b>{lang}</b>."

ERRORS = {
    "timeout": "⌛ The translation service did not respond. Please try again.",
    "provider_rate_limited": "⏳ The translation service is busy. Try again in a minute.",
    "invalid_language": "🌐 Wrong language selected. Pick again in 🌐 Languages.",
    "empty_result": "😕 Could not translate. Try changing the text.",
    "empty_input": "✍️ Send a text to translate.",
}
ERROR_DEFAULT = "⚠️ Something went wrong. Please try again."

TTS_ERRORS = {
    "timeout": "⌛ Voice was not generated — the service did not respond.",
    "no_voice": "🔇 Voice is not available for this language.",
    "too_long": "📏 The text is too long for voice.",
}
TTS_ERROR_DEFAULT = "⚠️ Could not generate the voice."

UNSUPPORTED_INPUT = (
    "📄 Only <b>text</b> translation is available for now.\n\n"
    "Voice messages and images will come in a later update."
)

TRANSLATION_NOT_FOUND = "This translation was not found (outdated button)."
LANGUAGE_NOT_AVAILABLE = "This language is not available"

# ── Contact admin ────────────────────────────────────────────
CONTACT_PROMPT = (
    "✉️ <b>Contact admin</b>\n\n"
    "Write your message — it goes straight to the admin.\n\n"
    "<i>This is not a chat: you write, the admin reads. If you need a reply, "
    "leave your contact details.</i>"
)
CONTACT_SENT = "✅ Your message was sent to the admin. Thank you!"
CONTACT_CANCELLED = "Cancelled."
CONTACT_TOO_LONG = "📏 The message is too long (<b>{length}</b> characters). Maximum: <b>{limit}</b>."
CONTACT_ONLY_TEXT = "✍️ Please send your message as text."
CONTACT_RATE_LIMITED = (
    "⏳ You sent a message recently.\n\n"
    "You can send the next one in <b>{minutes}</b> minutes."
)
CONTACT_FAILED = "⚠️ Could not send the message. Please try again later."
