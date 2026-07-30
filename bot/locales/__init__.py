"""Interfeys tillari.

Qoida (foydalanuvchi belgilagan):
    Telegram tili `uz`  → o'zbekcha
    Telegram tili `id`  → indonez
    qolgan hammasi      → ingliz

Har bir lokal — oddiy Python moduli, satrlar modul darajasidagi konstantalar.
Handlerlar `t` nomi bilan tayyor modulni oladi (ContextMiddleware uzatadi) va
`t.WELCOME.format(...)` deb ishlatadi. Lug'at emas, chunki modul bilan
imlo xatosi import paytida `AttributeError` beradi, lug'atda esa `KeyError`
faqat ish vaqtida chiqadi.
"""

from __future__ import annotations

from types import ModuleType
from typing import Dict, FrozenSet, Optional

from bot.locales import en, id as id_, uz

DEFAULT = "en"

LOCALES: Dict[str, ModuleType] = {
    "uz": uz,
    "id": id_,
    "en": en,
}

SUPPORTED: FrozenSet[str] = frozenset(LOCALES)


# ─────────────────────────────────────────────────────────────
#  Kalitlar mosligini tekshirish
# ─────────────────────────────────────────────────────────────
def _public_keys(module: ModuleType) -> FrozenSet[str]:
    return frozenset(
        name for name in vars(module) if name.isupper() and not name.startswith("_")
    )


def _verify() -> None:
    """Barcha lokallarda bir xil kalitlar borligini tekshiradi.

    Yarim tarjima qilingan holat eng yomon nosozlik: bot ishlaydi, lekin
    ba'zi joyda `AttributeError` bilan yiqiladi. Shuning uchun import paytida
    to'xtatamiz — deploy'dan oldin biladigan joy.
    """
    reference = _public_keys(uz)
    problems = []

    for code, module in LOCALES.items():
        if code == "uz":
            continue
        keys = _public_keys(module)
        if missing := reference - keys:
            problems.append(f"{code}.py da yo'q: {', '.join(sorted(missing))}")
        if extra := keys - reference:
            problems.append(f"{code}.py da ortiqcha: {', '.join(sorted(extra))}")

    # Xabar lug'atlarining kalitlari ham mos bo'lishi kerak.
    for attr in ("ERRORS", "TTS_ERRORS"):
        ref_keys = frozenset(getattr(uz, attr))
        for code, module in LOCALES.items():
            if code == "uz":
                continue
            if frozenset(getattr(module, attr)) != ref_keys:
                problems.append(f"{code}.{attr} kalitlari uz bilan mos emas")

    if problems:
        raise RuntimeError("Lokallar mos emas:\n  " + "\n  ".join(problems))


_verify()


# ─────────────────────────────────────────────────────────────
#  Tanlash
# ─────────────────────────────────────────────────────────────
def resolve(telegram_lang: Optional[str]) -> str:
    """Telegram til kodidan interfeys tilini aniqlaydi.

    Telegram `en-US`, `pt-br` kabi mintaqa qo'shimchali kodlar yuboradi,
    shuning uchun boshlanishi bo'yicha solishtiramiz.
    """
    if not telegram_lang:
        return DEFAULT
    code = telegram_lang.strip().lower().replace("_", "-")
    if code.startswith("uz"):
        return "uz"
    if code.startswith("id"):
        return "id"
    return DEFAULT


def get(code: Optional[str]) -> ModuleType:
    """Lokal modulini qaytaradi. Noma'lum kod bo'lsa — standart til."""
    return LOCALES.get(code or "", LOCALES[DEFAULT])


def names() -> Dict[str, str]:
    """Interfeys tili tanlash menyusi uchun: kod → o'z tilidagi nomi."""
    return {code: module.NAME for code, module in LOCALES.items()}


# ─────────────────────────────────────────────────────────────
#  Reply-menyu filtrlari
# ─────────────────────────────────────────────────────────────
# Reply-menyu tugmalari har bir tilda boshqa matn. Handler `F.text == "..."`
# bilan bitta matnga bog'lanib qolmasligi kerak, shuning uchun har bir tugma
# uchun barcha tillardagi variantlar to'plamini yig'amiz.
def _variants(attr: str) -> FrozenSet[str]:
    return frozenset(getattr(module, attr) for module in LOCALES.values())


LANGUAGES_BUTTONS = _variants("BTN_LANGUAGES")
SETTINGS_BUTTONS = _variants("BTN_SETTINGS")
HELP_BUTTONS = _variants("BTN_HELP")

# Tarjima handleri shu to'plamdagi matnlarni tarjima qilinadigan matn deb
# hisoblamaydi — aks holda menyu tugmasi bosilganda uni tarjima qilib yuborardi.
MENU_BUTTONS: FrozenSet[str] = LANGUAGES_BUTTONS | SETTINGS_BUTTONS | HELP_BUTTONS

__all__ = [
    "DEFAULT",
    "HELP_BUTTONS",
    "LANGUAGES_BUTTONS",
    "LOCALES",
    "MENU_BUTTONS",
    "SETTINGS_BUTTONS",
    "SUPPORTED",
    "get",
    "names",
    "resolve",
]
