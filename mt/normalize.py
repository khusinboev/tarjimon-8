"""Tokenizatsiyadan OLDINGI tinish belgisi normallashtirish.

NLLB o'qitilganda matn Moses `normalize-punctuation` dan o'tgan: tipografik
qo'shtirnoq/apostrof/tire ASCII ko'rinishiga keltirilgan. Shu sababli model
lug'atida `’ ‘ ‚ “ ” „ « » – —` belgilari YO'Q — tokenizator ularni `<unk>`
qiladi (HF tokenizatori bu normallashtirishni o'zi qilmaydi).

O'lchov (2026-11-12, bot korpusi 252k manba matn): 7.5% matnda `<unk>` bor;
eng ko'pi `’` (12 454), `—` (11 757), `‘`, `“”`, `«»`. Telefon klaviaturasida
o'zbekcha "o’", "g’" aynan shu belgilar bilan yoziladi — model "bo<unk>ladi"
ni ko'rardi, chiqishda esa qo'shtirnoq va tirelar yo'qolib qolardi.
"""

from __future__ import annotations

# Lug'atda yo'q (`<unk>` bo'ladigan) belgilar -> o'qitishdagi ko'rinishi.
_UNK_MAP = {
    "’": "'", "‘": "'", "‚": "'",          # ’ ‘ ‚
    "“": '"', "”": '"', "„": '"',          # “ ” „
    "«": '"', "»": '"',                          # « »
    "–": "-", "—": "-",                          # – —
}
_UNK_TABLE = str.maketrans(_UNK_MAP)

# O'zbek lotinida oʻ/gʻ va tutuq belgisi uchun ishlatiladigan boshqa variantlar.
# Ular lug'atda bor, lekin NLLB'ning o'zbekcha ma'lumotida deyarli faqat ASCII
# apostrof uchraydi (FLORES: o' 1661 marta, oʻ 7 marta) — bir xil ko'rinishga
# keltirish modelga tanish shaklni beradi. Faqat o'zbekcha manba uchun.
_UZ_APOS_TABLE = str.maketrans({c: "'" for c in "ʻʼ`´‛ʹʽ′"})


# A/B (2026-11-12, GPU int8, v1 model): ta'sirlangan 242 bot matnida chrF++
# 63.40 -> 63.77 (ruscha manba 58.20 -> 59.77); FLORES uz->en "’" bilan
# 60.60 -> 61.03, uz->ru 48.21 -> 48.62 (ASCII apostrofli asl bilan teng).
def normalize_punct(text: str, lang: str | None = None, *, uz_apostrophe: bool = False) -> str:
    text = text.translate(_UNK_TABLE)
    if lang == "uz" and uz_apostrophe:
        text = text.translate(_UZ_APOS_TABLE)
    return text
