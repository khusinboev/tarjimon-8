"""Bot tarixidan (translations jadvali) fine-tune korpusi: tozalash, dedup, bo'lish.

Kirish: `COPY (...) TO STDOUT WITH (FORMAT csv, HEADER)` eksporti (.csv.gz),
ustunlar: id, created_at, provider, source_lang_requested, source_lang_detected,
target_lang, source_hash, source_text, target_text. Eksport buyrug'i: README.md.

Chiqish (--out papkasi): train.jsonl, dev.jsonl, test.jsonl, stats.json.
Har qator: {"src_lang","tgt_lang","src","tgt","provider","id"}.

Ma'lumot foydalanuvchi xabarlari — repoga QO'SHILMAYDI, papka 700 huquqli.

Ishga tushirish (repo ildizidan):
  python -m mt.train.prepare_data --csv ~/.../corpus.csv.gz --out ~/.../prepared
"""

from __future__ import annotations

import argparse
import csv
import gzip
import html
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from mt.langs import NLLB_CODES, has_cyrillic, looks_uzbek_cyrillic, uz_cyr_to_lat
from mt.lid import uz_latin_score

CORE = {"en-uz", "uz-en", "ru-uz", "uz-ru", "en-ru", "ru-en"}

# Tarjima sifati bo'yicha "o'qituvchi" ustunligi (dedup'da yaxshirog'i qoladi).
# legacy_* va deep_translator — Google veb-tarjima (eski botlar).
PROVIDER_RANK = {
    "deepl": 0, "google_translate": 0, "azure_translator": 1,
    "legacy_pg": 1, "legacy_sqlite": 1, "deep_translator": 1,
    "gemini": 2, "mymemory": 3,
}

_ERROR_MARKERS = ("mymemory warning", "query length limit", "that's an error",
                  "invalid source language", "please select two distinct languages")
_LETTER = re.compile(r"[^\W\d_]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_WS = re.compile(r"[ \t]+")
_CYR_CHAR = re.compile(r"[А-Яа-яЁёЎўҚқҒғҲҳ]")
_LAT_CHAR = re.compile(r"[A-Za-z]")


def normalize(text: str) -> str:
    text = html.unescape(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def cyr_share(text: str) -> float:
    c, l = len(_CYR_CHAR.findall(text)), len(_LAT_CHAR.findall(text))
    return c / (c + l) if c + l else 0.0


def script_ok(text: str, lang: str) -> bool:
    """Yozuv tilga mos kelishi: ru — kirill, en/uz — lotin (uz kirill oldin o'giriladi)."""
    share = cyr_share(text)
    if lang == "ru":
        return share >= 0.7
    if lang in ("en", "uz"):
        return share <= 0.1
    return True


def reject_reason(src: str, tgt: str, s: str, t: str) -> str | None:
    if not src or not tgt:
        return "bo'sh"
    if not _LETTER.search(_URL.sub("", src)):
        return "harfsiz"
    low = tgt.lower()
    if any(m in low for m in _ERROR_MARKERS):
        return "provayder_xatosi"
    if src.strip().lower() == tgt.strip().lower():
        return "tarjima_qilinmagan"
    if len(src) > 1000 or len(tgt) > 1500:
        return "juda_uzun"
    ls, lt = len(src), len(tgt)
    if max(ls, lt) >= 20 and not (1 / 3 <= lt / ls <= 3):
        return "uzunlik_nisbati"
    if not script_ok(src, s):
        return "manba_yozuvi"
    if not script_ok(tgt, t):
        return "maqsad_yozuvi"
    # en deb belgilangan, lekin aslida o'zbekcha ("Men uchun emas" -> "Erkaklar uchun emas").
    if s == "en" and len(src) >= 12 and uz_latin_score(src) >= 0.55:
        return "manba_tili_xato"
    if t == "en" and len(tgt) >= 12 and uz_latin_score(tgt) >= 0.55:
        return "maqsad_tili_xato"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pairs", default="core", help="'core' (uz/ru/en 6 yo'nalish) yoki 'all'")
    ap.add_argument("--test-per-pair", type=int, default=300)
    ap.add_argument("--dev-per-pair", type=int, default=300)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    csv.field_size_limit(sys.maxsize)
    rejected: Counter = Counter()
    best: dict[tuple[str, str], dict] = {}  # (yo'nalish, manba_kalit) -> qator
    total = 0
    with gzip.open(args.csv, "rt", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            s = row["source_lang_requested"]
            if s == "auto":
                s = row["source_lang_detected"] or ""
            t = row["target_lang"]
            if s not in NLLB_CODES or t not in NLLB_CODES or s == t:
                rejected["til_juftligi"] += 1
                continue
            pair = f"{s}-{t}"
            if args.pairs == "core" and pair not in CORE:
                rejected["yo'nalish_tanlanmagan"] += 1
                continue
            src, tgt = normalize(row["source_text"]), normalize(row["target_text"])
            if s == "uz" and has_cyrillic(src) and looks_uzbek_cyrillic(src):
                src = uz_cyr_to_lat(src)
            if t == "uz" and has_cyrillic(tgt) and looks_uzbek_cyrillic(tgt):
                tgt = uz_cyr_to_lat(tgt)  # MyMemory ba'zan kirillda qaytaradi
            reason = reject_reason(src, tgt, s, t)
            if reason:
                rejected[reason] += 1
                continue
            key = (pair, src.lower())
            cand = {"src_lang": s, "tgt_lang": t, "src": src, "tgt": tgt,
                    "provider": row["provider"], "id": int(row["id"]), "created_at": row["created_at"]}
            old = best.get(key)
            if old is None:
                best[key] = cand
            else:
                rejected["takror"] += 1
                if PROVIDER_RANK.get(cand["provider"], 9) < PROVIDER_RANK.get(old["provider"], 9):
                    best[key] = cand

    by_pair: dict[str, list[dict]] = defaultdict(list)
    for (pair, _), row in best.items():
        by_pair[pair].append(row)

    rng = random.Random(args.seed)
    train, dev, test = [], [], []
    for pair, rows in sorted(by_pair.items()):
        # Test/dev — eng yangi oylardan (2026-09+: hozirgi foydalanuvchilar uslubi)
        # va 5..400 belgili matnlardan; manbasi train'da yo'q (dedup kaliti bo'yicha).
        recent = [r for r in rows if r["created_at"] >= "2026-09-01" and 5 <= len(r["src"]) <= 400]
        rng.shuffle(recent)
        held = recent[: args.test_per_pair + args.dev_per_pair]
        held_ids = {r["id"] for r in held}
        test.extend(held[: args.test_per_pair])
        dev.extend(held[args.test_per_pair:])
        train.extend(r for r in rows if r["id"] not in held_ids)
    rng.shuffle(train)

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    for name, rows in (("train", train), ("dev", dev), ("test", test)):
        p = out / f"{name}.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                r = {k: v for k, v in r.items() if k != "created_at"}
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.chmod(p, 0o600)

    stats = {
        "jami_qator": total,
        "qoldi_unikal": len(best),
        "rad_sabablari": dict(rejected.most_common()),
        "train": dict(Counter(f"{r['src_lang']}-{r['tgt_lang']}" for r in train)),
        "dev": dict(Counter(f"{r['src_lang']}-{r['tgt_lang']}" for r in dev)),
        "test": dict(Counter(f"{r['src_lang']}-{r['tgt_lang']}" for r in test)),
        "train_provayder": dict(Counter(r["provider"] for r in train)),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
