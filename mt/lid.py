"""Til aniqlash: fastText lid.176 + o'zbek (lotin) uchun qo'shimcha qoida.

Nega qoida kerak: lid.176 o'zbek lotin matnini deyarli tanimaydi (FLORES
dev'da ishonchli to'g'ri javob ~4%, ko'pincha "tr"/"en"/"pt" deydi), oromo
(om) tili esa umuman yo'q. O'zbek — botning asosiy tili, shuning uchun
o'zbekka xos belgilar bo'yicha alohida tekshiruv qilinadi (o'lchov:
bench/lid_eval.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from mt.langs import has_cyrillic, looks_uzbek_cyrillic

# Turk/ozarbayjon/turkman/nemis va h.k. harflari — o'zbek lotinida yo'q.
_NOT_UZ_LETTERS = re.compile(r"[şçğıöüəäňýžñéèêàâáíóúãõß]")
_UZ_APOS = re.compile(r"[og][ʻ‘’'`ʼ]")
_WORD = re.compile(r"[a-zʻ‘’'`ʼ]+")

_UZ_WORDS = frozenset("""
va bu men sen biz siz ular u uni unga uning bilan uchun emas ham yoki lekin ammo
qilib qildi qiladi qilish kerak nima qanday qachon qayerda nega necha menga sizga
haqida orqali yil esa hamda edi ekan bor yoq yo'q bo'ldi bo'lgan bo'lib bo'ladi
juda katta kichik yangi yaxshi yomon salom rahmat iltimos xayr ha mumkin kabi
keyin oldin hozir bugun ertaga kecha shu ushbu o'sha har barcha hamma biror
dedi deb degan aytdi qarab olib berib kelib ketdi keldi bilan birga tomonidan
""".split())
_UZ_SUFFIXES = ("ning", "larni", "larga", "lardan", "larda", "dagi", "moqda",
                "yapti", "ishga", "ishni", "gani", "ganini", "ligi", "sizmi", "mizmi")


def uz_latin_score(text: str) -> float:
    """0..1 — matn o'zbek lotin ekanligi bahosi."""
    low = text.lower()
    if has_cyrillic(low) or _NOT_UZ_LETTERS.search(low):
        return 0.0
    words = _WORD.findall(low)
    if not words:
        return 0.0
    apos = len(_UZ_APOS.findall(low))
    hits = sum(w in _UZ_WORDS for w in words)
    suffix = sum(w.endswith(_UZ_SUFFIXES) for w in words)
    qx = sum(w.count("q") + w.count("x") for w in words)
    score = (2.0 * apos + 1.5 * hits + 1.0 * suffix + 0.5 * qx) / len(words)
    return min(1.0, score)


class LanguageDetector:
    def __init__(self, lid_path: Optional[str]):
        self._model = None
        if lid_path and Path(lid_path).exists():
            import fasttext

            fasttext.FastText.eprint = lambda *a, **k: None
            self._model = fasttext.load_model(lid_path)

    def detect(self, text: str, min_chars: int = 12) -> tuple[Optional[str], float]:
        clean = " ".join(text.split())
        if len(clean) < min_chars:
            return None, 0.0
        code, prob = None, 0.0
        if self._model is not None:
            labels, probs = self._model.predict(clean, k=1)
            code, prob = labels[0].replace("__label__", ""), float(probs[0])
        if looks_uzbek_cyrillic(clean):
            # "Ў" faqat o'zbekda; Қ/Ғ/Ҳ tojik/qozoqda ham bor — lid ular
            # deyishiga ishonch bo'lsa, o'zbek emas.
            if "ў" in clean.lower() or not (code in ("tg", "kk", "ky") and prob >= 0.5):
                return "uz", 0.9
        uz = uz_latin_score(clean)
        # lid ishonchli va o'zbek emas deydi (masalan en 0.95) — unga ishonamiz,
        # faqat o'zbek belgilari juda kuchli bo'lsa bundan mustasno.
        if uz >= THRESHOLD_STRONG or (uz >= THRESHOLD and not (code not in (None, "uz") and prob >= 0.9)):
            return "uz", max(prob if code == "uz" else 0.0, 0.6 + 0.4 * uz)
        return code, prob


# bench/lid_eval2.py (FLORES dev, ajratilgan qism): uz to'liq gap ~96%,
# 4 so'zli ~77%; boshqa tillar xato 'uz' bo'lishi <=2% (om qisqa ~5%).
THRESHOLD = 0.3
THRESHOLD_STRONG = 0.55
