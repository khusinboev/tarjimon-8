"""Bot til kodlari <-> NLLB-200 kodlari, va o'zbek kirill -> lotin.

Botdagi barcha 22 til (`languages` jadvali, 2026-11) NLLB-200 da bor.
"""

from __future__ import annotations

import re

NLLB_CODES: dict[str, str] = {
    "am": "amh_Ethi",
    "ar": "arb_Arab",
    "az": "azj_Latn",
    "de": "deu_Latn",
    "en": "eng_Latn",
    "es": "spa_Latn",
    "fa": "pes_Arab",
    "fr": "fra_Latn",
    "hi": "hin_Deva",
    "id": "ind_Latn",
    "it": "ita_Latn",
    "ja": "jpn_Jpan",
    "kk": "kaz_Cyrl",
    "ko": "kor_Hang",
    "ky": "kir_Cyrl",
    "om": "gaz_Latn",
    "ru": "rus_Cyrl",
    "tg": "tgk_Cyrl",
    "tk": "tuk_Latn",
    "tr": "tur_Latn",
    "uz": "uzn_Latn",
    "zh": "zho_Hans",
}

# Gaplar probelsiz qo'shiladigan tillar (maqsad til sifatida).
NO_SPACE_JOIN = {"ja", "zh"}

_CYR = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")
# O'zbek kirillida bo'ladi, rus tilida bo'lmaydi. Қ/Ғ/Ҳ qozoq/tojikda ham
# bor, shuning uchun ularga xos harflar bo'lsa — o'zbek emas.
_UZ_CYR_ONLY = re.compile(r"[ЎўҚқҒғҲҳ]")
_OTHER_CYR_TURKIC = re.compile(r"[ӘәӨөҮүҰұҢңІіҺһӢӣӮӯҶҷ]")

_UZ_MAP = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ж": "J", "З": "Z",
    "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "X",
    "Ч": "Ch", "Ш": "Sh", "Ъ": "ʼ", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
    "Ё": "Yo", "Ў": "Oʻ", "Қ": "Q", "Ғ": "Gʻ", "Ҳ": "H", "Ц": "Ts", "Щ": "Sh",
    "Ы": "I",
}
_UZ_MAP.update({k.lower(): v.lower() for k, v in _UZ_MAP.items()})


def has_cyrillic(text: str) -> bool:
    return bool(_CYR.search(text))


def looks_uzbek_cyrillic(text: str) -> bool:
    return bool(_UZ_CYR_ONLY.search(text)) and not _OTHER_CYR_TURKIC.search(text)


def uz_cyr_to_lat(text: str) -> str:
    """O'zbek kirill -> lotin. NLLB faqat `uzn_Latn` ni biladi.

    "Е" so'z boshida va unlidan keyin "Ye", aks holda "E" — asosiy qoida.
    Bosh harfli ikki harfli tovushlar (Ш -> Sh) so'z to'liq katta harf bo'lsa
    ham "Sh" bo'lib qoladi — tarjima uchun bu muhim emas.
    """
    out: list[str] = []
    prev = ""
    for ch in text:
        if ch in "Ее":
            start = not prev.isalpha() or prev.lower() in "аоуэиеёюяўaouei"
            base = "Ye" if start else "E"
            out.append(base if ch == "Е" else base.lower())
        else:
            out.append(_UZ_MAP.get(ch, ch))
        prev = ch
    return "".join(out)
