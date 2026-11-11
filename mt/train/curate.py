"""Korpus sifatini avtomatik baholash: "muvaffaqiyatli" yozilgan, lekin xato
tarjimalarni ajratish (fine-tune uchun "o'qituvchi" sifati muhim).

Signallar (har juftlik uchun, `prepare_data.py` chiqishiga qo'shiladi):
- `fwd`  — NLLB-1.3B majburiy dekodlash: referens tarjimaning o'rtacha
           log-ehtimoli (manba -> maqsad). Noto'g'ri/aloqasiz tarjima — past.
- `bwd`  — teskari yo'nalish (maqsad -> manba): erkin, lekin to'g'ri tarjimani
           (parafraz) jazolamaslik uchun ikki yo'nalish o'rtachasi olinadi.
- `copy` — tarjima qilinmay qolgan so'zlar ulushi (lotin<->lotin juftliklarda).
- `lang_bad` — maqsad matn boshqa tilda (masalan uz o'rniga en).

Qaror (`label`): har yo'nalish ichida `score=(fwd+bwd)/2` bo'yicha eng past
`--drop-pct` foiz + aniq qoidalar (copy, lang_bad) -> "drop"; qolgani "keep".
Chegaradagi namunalar hisobotga chiqadi — qo'lda ko'rib tasdiqlash uchun.

  python -m mt.train.curate --data prepared/ --model <nllb ct2> --out curated/
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path

from mt.langs import NLLB_CODES
from mt.lid import LanguageDetector, uz_latin_score

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def copy_ratio(src: str, tgt: str) -> float:
    """Maqsaddagi so'zlarning qanchasi manbadan o'zgarishsiz ko'chirilgan."""
    s = {w.lower() for w in _WORD.findall(src)}
    t = [w.lower() for w in _WORD.findall(tgt)]
    if not t or not s:
        return 0.0
    return sum(w in s for w in t) / len(t)


def length_bucket(src: str) -> str:
    """Majburiy dekodlash bali uzunlikka bog'liq: 1 so'zli lug'at juftligi
    tabiatan past ball oladi ("Lab" -> "lip" to'g'ri, lekin -6). Shuning uchun
    chegara har yo'nalish x uzunlik guruhi ichida alohida hisoblanadi."""
    n = len(src.split())
    return "1" if n <= 1 else "2-3" if n <= 3 else "4-8" if n <= 8 else "9+"


def lang_bad(tgt: str, tgt_lang: str, det: LanguageDetector) -> bool:
    if len(tgt) < 20:
        return False
    code, prob = det.detect(tgt)
    if tgt_lang == "uz":
        return code in ("en", "ru") and prob >= 0.8 and uz_latin_score(tgt) < 0.15
    if tgt_lang == "en":
        return code == "uz" or (code not in (None, "en") and prob >= 0.9)
    if tgt_lang == "ru":
        return code not in (None, "ru", "uk", "bg", "mk", "sr", "kk", "ky", "tg") and prob >= 0.9
    return False


class Scorer:
    def __init__(self, model_dir: str, threads: int):
        import ctranslate2
        from tokenizers import Tokenizer

        self.tr = ctranslate2.Translator(model_dir, device="cpu", compute_type="int8",
                                         intra_threads=threads, inter_threads=1)
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))

    def _t(self, text: str) -> list[str]:
        return self.tok.encode(text, add_special_tokens=False).tokens[:200]

    def score(self, items: list[tuple[str, str, str, str]], chunk: int = 256,
              max_tokens: int = 512) -> list[float]:
        """items: (src_lang, src, tgt_lang, tgt) -> o'rtacha log-ehtimol (prefiks tokensiz).

        XOTIRA: skorlashda har pozitsiya uchun butun lug'at (256k) bo'yicha
        log-softmax hisoblanadi — batch'ni TOKEN soni bilan cheklash shart
        (64 juftlik x 200 token ~ 13 GB; 2026-11-10 da laptopni qotirgan).
        512 token ~ 0.5 GB.
        """
        order = sorted(range(len(items)), key=lambda i: len(items[i][1]) + len(items[i][3]))
        out = [0.0] * len(items)
        for k in range(0, len(order), chunk):
            idx = order[k : k + chunk]
            src = [[NLLB_CODES[items[i][0]]] + self._t(items[i][1]) + ["</s>"] for i in idx]
            tgt = [[NLLB_CODES[items[i][2]]] + self._t(items[i][3]) for i in idx]
            res = self.tr.score_batch(src, tgt, max_batch_size=max_tokens, batch_type="tokens")
            for i, r in zip(idx, res):
                lp = r.log_probs[1:] or r.log_probs  # 0-pozitsiya — til kodi
                out[i] = sum(lp) / len(lp)
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--lid", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--drop-pct", type=float, default=10.0)
    # Og'zaki o'zbekcha MANBA'da o'qituvchi provayder ko'p adashadi (2000 lik
    # namunada chegaradan sal yuqoridagi uz->ru juftliklarning ko'pi xato) —
    # u yerda ko'proq olib tashlanadi.
    ap.add_argument("--drop-pct-uz-src", type=float, default=15.0)
    ap.add_argument("--limit", type=int, default=0, help="sinov uchun: har fayldan N qator")
    ap.add_argument("--reuse", action="store_true",
                    help="--out dagi *.scored.jsonl ballaridan foydalanish (qayta skorlamaslik)")
    args = ap.parse_args()

    data, out = Path(args.data).expanduser(), Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    scorer = None if args.reuse else Scorer(args.model, args.threads)
    det = LanguageDetector(args.lid)
    report: dict = {"drop_pct": args.drop_pct, "drop_pct_uz_src": args.drop_pct_uz_src, "files": {}}

    for name in ("train", "dev", "test"):
        scored_path = out / f"{name}.scored.jsonl"
        if args.reuse and scored_path.exists():
            rows = [json.loads(l) for l in open(scored_path, encoding="utf-8")]
        else:
            rows = [json.loads(l) for l in open(data / f"{name}.jsonl", encoding="utf-8")]
            if args.limit:
                rows = rows[: args.limit]
            t0 = time.time()
            fwd = scorer.score([(r["src_lang"], r["src"], r["tgt_lang"], r["tgt"]) for r in rows])
            bwd = scorer.score([(r["tgt_lang"], r["tgt"], r["src_lang"], r["src"]) for r in rows])
            print(f"{name}: {len(rows)} juftlik baholandi, {time.time() - t0:.0f}s", flush=True)
            for i, r in enumerate(rows):
                r["fwd"], r["bwd"] = round(fwd[i], 4), round(bwd[i], 4)
                r["score"] = round((fwd[i] + bwd[i]) / 2, 4)
            # Ballar darhol saqlanadi — keyingi qadam yiqilsa qayta skorlash shart emas.
            with open(scored_path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        by_pair: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            latin = r["src_lang"] in ("en", "uz") and r["tgt_lang"] in ("en", "uz")
            r["copy"] = round(copy_ratio(r["src"], r["tgt"]), 3) if latin else 0.0
            r["lang_bad"] = lang_bad(r["tgt"], r["tgt_lang"], det)
            r["bucket"] = length_bucket(r["src"])
            by_pair[f"{r['src_lang']}-{r['tgt_lang']}|{r['bucket']}"].append(i)

        stats = {}
        for pair, idx in sorted(by_pair.items()):
            scores = sorted(rows[i]["score"] for i in idx)
            pct = args.drop_pct_uz_src if pair.startswith("uz-") else args.drop_pct
            cut = scores[max(0, int(len(scores) * pct / 100) - 1)] if len(scores) >= 30 else -math.inf
            reasons: dict[str, int] = defaultdict(int)
            for i in idx:
                r = rows[i]
                why = []
                if r["score"] <= cut:
                    why.append("past_ball")
                if r["copy"] >= 0.6 and len(_WORD.findall(r["tgt"])) >= 3:
                    why.append("tarjima_qilinmagan")
                if r["lang_bad"]:
                    why.append("boshqa_til")
                r["label"] = "drop" if why else "keep"
                r["why"] = why
                for w in why:
                    reasons[w] += 1
            kept = sum(rows[i]["label"] == "keep" for i in idx)
            stats[pair] = {"n": len(idx), "keep": kept, "cut": round(cut, 3),
                           "median": round(statistics.median(scores), 3), "sabablar": dict(reasons)}
        report["files"][name] = stats

        with open(out / f"{name}.scored.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                if r["label"] == "keep":
                    f.write(json.dumps({k: r[k] for k in ("src_lang", "tgt_lang", "src", "tgt", "provider", "id")},
                                       ensure_ascii=False) + "\n")
        for p in out.iterdir():
            os.chmod(p, 0o600)

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
