"""Dvigatel uchun tez sinov (model kerak). Ishga tushirish (repo ildizidan):

  MT_MODEL_DIR=/path/to/model MT_LID_PATH=/path/to/lid.176.ftz python -m mt.test_engine
"""

from __future__ import annotations

import os
import sys
import time

from mt.engine import DetectionFailed, Engine, UnsupportedLanguage, _is_passthrough
from mt.langs import uz_cyr_to_lat

failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed
    print(("  OK  " if ok else "  FAIL"), label, ("— " + detail if detail else ""))
    if not ok:
        failed += 1


def main() -> int:
    # Modelsiz qismlar
    check("translit: Ўзбекистон", uz_cyr_to_lat("Ўзбекистон") == "Oʻzbekiston", uz_cyr_to_lat("Ўзбекистон"))
    check("translit: Ер, келди", uz_cyr_to_lat("Ер келди") == "Yer keldi", uz_cyr_to_lat("Ер келди"))
    check("passthrough: URL", _is_passthrough("https://t.me/x"))
    check("passthrough: 12:30 😀", _is_passthrough("12:30 😀"))
    check("passthrough emas: so'z", not _is_passthrough("Salom 😀"))

    from mt.engine import _split_edges, _tidy
    check("edges: ❌ Bekor qilish", _split_edges("❌ Bekor qilish") == ("❌", "Bekor qilish", ""), str(_split_edges("❌ Bekor qilish")))
    check("edges: Dars vaqti📚", _split_edges("Dars vaqti📚") == ("", "Dars vaqti", "📚"), str(_split_edges("Dars vaqti📚")))
    check("edges: oddiy gap.", _split_edges("Salom, dunyo!") == ("", "Salom, dunyo!", ""))
    for raw, want in [("Can you wait 15 minutes ?", "Can you wait 15 minutes?"),
                      ("Salom . Sendan", "Salom. Sendan"), ("I don ' t know", "I don't know"),
                      ("O ' rnatish", "O'rnatish"), ("0: 06", "0:06"), ("uyimiz.Biz", "uyimiz. Biz")]:
        check(f"tidy: {raw!r}", _tidy(raw, "en") == want, _tidy(raw, "en"))
    check("tidy fr: probel qoladi", _tidy("Comment ?", "fr") == "Comment ?")

    t = time.time()
    e = Engine(os.environ["MT_MODEL_DIR"], threads=4, lid_path=os.environ.get("MT_LID_PATH"))
    print(f"  yuklandi {time.time()-t:.1f}s")

    r = e.translate("Hello, how are you?", "en", "uz")
    check("en->uz oddiy", bool(r.text) and r.text != "Hello, how are you?", r.text)

    long_text = ("The weather is nice today. We are going to the park!\n\n"
                 "Call me at 12:30. https://example.com\n"
                 "Do you want to come with us?")
    r = e.translate(long_text, "en", "ru")
    lines = r.text.split("\n")
    check("qatorlar soni saqlandi", len(lines) == 4, repr(r.text))
    check("bo'sh qator saqlandi", lines[1] == "", repr(lines))
    check("URL o'zgarmadi", "https://example.com" in r.text, lines[2] if len(lines) > 2 else "")

    r = e.translate("Сегодня очень хорошая погода, пойдём гулять.", "auto", "uz")
    check("auto: rus aniqlandi", r.source == "ru", f"{r.source}: {r.text}")
    r = e.translate("Bugun havo juda yaxshi, sayrga chiqamiz.", "auto", "en")
    check("auto: o'zbek aniqlandi", r.source == "uz", f"{r.source}: {r.text}")
    r = e.translate("Бугун ҳаво жуда яхши, сайрга чиқамиз.", "uz", "en")
    check("uz kirill -> en", "weather" in r.text.lower() or "today" in r.text.lower(), r.text)

    try:
        e.translate("ok", "auto", "uz")
        check("qisqa auto -> DetectionFailed", False)
    except DetectionFailed:
        check("qisqa auto -> DetectionFailed", True)
    try:
        e.translate("hello", "en", "xx")
        check("noma'lum til -> Unsupported", False)
    except UnsupportedLanguage:
        check("noma'lum til -> Unsupported", True)

    r = e.translate("❌ Cancel", "en", "uz")
    check("emoji saqlandi (bosh)", r.text.startswith("❌"), r.text)
    r = e.translate("We have class now📚", "en", "ru")
    check("emoji saqlandi (oxir)", r.text.endswith("📚"), r.text)

    words = ("This is a long sentence with many words that keeps going without any "
             "punctuation at all and it should still be translated properly ") * 12
    t = time.time()
    r = e.translate(words, "en", "uz")
    check("juda uzun gap bo'lindi va tarjima qilindi", r.segments >= 2 and len(r.text) > 200,
          f"seg={r.segments} {time.time()-t:.1f}s")

    print("XATO:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
