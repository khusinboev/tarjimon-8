"""Kelgan xabardagi media faylni ANIQ belgilab olish.

Telegram har bir fayl uchun IKKITA identifikator beradi va ular bir-birini
almashtira olmaydi:

  `file_id`         — SHU bot uchun amal qiladigan yuklab olish belgisi.
                      Faylni qayta yuklab olish/yuborish uchun ishlatiladi,
                      lekin boshqa botda ishlamaydi va vaqt o'tishi bilan
                      qayta yozilishi mumkin.
  `file_unique_id`  — faylning O'ZGARMAS global identifikatori. Yuklab
                      olish uchun YARAMAYDI, lekin "bu ikki yozuv bir xil
                      faylmi?" degan savolga faqat shu javob beradi.

Shuning uchun IKKALASI ham saqlanadi: `file_id` — keyin qayta olish uchun,
`file_unique_id` — qidirish va takrorlarni aniqlash uchun.

Tartib muhim: Telegram GIF yuborilganda `animation` BILAN BIRGA `document`
ni ham to'ldiradi, ovozli xabarda `voice` va `audio` chalkashishi mumkin.
Shuning uchun quyidagi ro'yxat aniq tartibda yuriladi — eng aniq tur
birinchi tekshiriladi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MediaRef:
    """Bitta media faylning bazaga yoziladigan "manzili"."""

    kind: str
    file_id: str
    file_unique_id: str
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    file_name: Optional[str] = None


# `(xabar maydoni, input_kind)` — eng aniqdan umumiyga qarab.
# `animation` `document`dan OLDIN turishi shart (izoh: modul sarlavhasi).
_MEDIA_ATTRS = (
    "photo",
    "animation",
    "video_note",
    "video",
    "voice",
    "audio",
    "sticker",
    "document",
)


def _from_object(kind: str, obj: Any) -> Optional[MediaRef]:
    """Telegram media obyektidan `MediaRef`. Identifikatori bo'lmasa `None`.

    `file_size`/`mime_type`/`file_name` turga qarab bor yoki yo'q (masalan
    stikerda `mime_type` yo'q) — shuning uchun hammasi `getattr` orqali.
    """
    file_id = getattr(obj, "file_id", None)
    file_unique_id = getattr(obj, "file_unique_id", None)
    if not file_id or not file_unique_id:
        return None
    return MediaRef(
        kind=kind,
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_size=getattr(obj, "file_size", None),
        mime_type=getattr(obj, "mime_type", None),
        file_name=getattr(obj, "file_name", None),
    )


def _from_paid_media(message: Any) -> Optional[MediaRef]:
    """Pullik media — ichma-ich joylashgan (`PaidMediaInfo.paid_media[i]`).

    Juda kam uchraydi va tuzilishi o'zgarib turadi, shuning uchun qat'iy
    tiplarga emas, mavjud maydonlarga qarab yuriladi: ichkarida `photo`
    (ro'yxat) yoki `video` bo'lishi mumkin. Topilmasa — `None`, bu xato
    emas: shunchaki oldindan ko'rish (preview) bo'lishi mumkin, unda fayl
    umuman bo'lmaydi.
    """
    info = getattr(message, "paid_media", None)
    items = getattr(info, "paid_media", None) if info is not None else None
    for item in items or []:
        photo = getattr(item, "photo", None)
        if photo:
            ref = _from_object("paid_media", photo[-1])
            if ref is not None:
                return ref
        video = getattr(item, "video", None)
        if video is not None:
            ref = _from_object("paid_media", video)
            if ref is not None:
                return ref
    return None


def extract_media(message: Any) -> Optional[MediaRef]:
    """Xabardagi media faylni qaytaradi. Media bo'lmasa (oddiy matn) `None`.

    Rasm bir necha o'lchamda keladi (`message.photo` — ro'yxat, kichikdan
    kattaga). Har doim ENG KATTASI (`[-1]`) olinadi: OCR ham aynan shuni
    yuklab oladi, ya'ni bazadagi yozuv haqiqatda ishlatilgan faylga mos
    tushadi.
    """
    for attribute in _MEDIA_ATTRS:
        obj = getattr(message, attribute, None)
        if not obj:
            continue
        if attribute == "photo":
            obj = obj[-1]
        ref = _from_object(attribute, obj)
        if ref is not None:
            return ref

    return _from_paid_media(message)
