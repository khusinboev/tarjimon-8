"""CTranslate2 modelini botning haqiqiy test to'plamida baholash (chrF++, BLEU).

Fine-tune'dan oldin va keyin bir xil skript — natijalar solishtiriladi.
Referens — o'sha paytdagi provayder tarjimasi ("o'qituvchi"), ya'ni bu ko'rsatkich
modelning Google/DeepL/Azure darajasiga qanchalik yaqinlashganini o'lchaydi.

  python -m mt.train.evaluate --model <ct2 papka> --test <test.jsonl> --out <natija.json>
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

import sacrebleu

from mt.engine import Engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--beam", type=int, default=2, help="serverdagi bilan bir xil")
    ap.add_argument("--device", default="cpu", help="cuda — laptopda tez baholash uchun")
    args = ap.parse_args()

    engine = Engine(args.model, threads=args.threads, beam_size=args.beam, device=args.device)
    groups: dict[str, list[dict]] = defaultdict(list)
    for line in open(args.test, encoding="utf-8"):
        r = json.loads(line)
        groups[f"{r['src_lang']}-{r['tgt_lang']}"].append(r)

    report = {"model": args.model, "beam": args.beam, "pairs": {}}
    all_h, all_r = [], []
    for pair, rows in sorted(groups.items()):
        t0 = time.time()
        hyps = []
        for r in rows:
            try:
                hyps.append(engine.translate(r["src"], r["src_lang"], r["tgt_lang"]).text)
            except Exception as exc:  # rad etilgan — bo'sh deb hisoblanadi (jazo)
                hyps.append("")
                print("rad:", pair, type(exc).__name__)
        refs = [r["tgt"] for r in rows]
        chrf = sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score
        bleu = sacrebleu.corpus_bleu(hyps, [refs]).score
        report["pairs"][pair] = {"n": len(rows), "chrf++": round(chrf, 2), "bleu": round(bleu, 2),
                                 "s_per_item": round((time.time() - t0) / len(rows), 3),
                                 "samples": [{"src": r["src"], "ref": r["tgt"], "hyp": h}
                                             for r, h in list(zip(rows, hyps))[:8]]}
        all_h += hyps
        all_r += refs
        print(f"{pair}: n={len(rows)} chrF++ {chrf:.2f} BLEU {bleu:.2f}", flush=True)
    report["overall_chrf++"] = round(sacrebleu.corpus_chrf(all_h, [all_r], word_order=2).score, 2)
    print("umumiy chrF++:", report["overall_chrf++"])
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
