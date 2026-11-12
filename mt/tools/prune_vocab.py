"""CTranslate2 NLLB modelining lug'atini qisqartirish (keraksiz tillar tokenlarini olib tashlash).

NLLB-200 lug'ati 256 206 token (200 til). Bot 22 tilni qo'llaydi. Lug'at
matritsasi (V x 1024) modelning ~262 MB qismi VA har chiqish tokeni uchun
to'liq ko'paytiriladi (dekoder qadamining ~30% hisobi). Qisqartirish RAM va
vaqtni tejaydi.

Usul — aniq, qayta o'qitishsiz:
- `model.bin` oqim bilan o'qiladi; birinchi o'lchami V bo'lgan o'zgaruvchilardan
  (embedding `weight` int8 + qator bo'yicha `weight_scale`) faqat saqlanadigan
  qatorlar olinadi. Qolgan barcha og'irliklar bayt-ma-bayt ko'chiriladi —
  saqlangan tokenlar uchun model AYNAN o'sha model (qayta kvantlash yo'q).
- Saqlanadigan tokenlar: berilgan matnlarda (bot korpusi, FLORES, Vikipediya
  namunasi) uchraganlar + maxsus/til kodlari + barcha bir belgili tokenlar +
  BPE yopilmasi (saqlangan tokenni hosil qiluvchi har bir birlashtirishning
  ikkala bo'lagi). Yopilma tufayli korpusdagi so'zlar AYNAN avvalgidek
  tokenlanadi; uchramagan so'z mayda saqlangan bo'laklarga bo'linadi.

  python -m mt.tools.prune_vocab --model <ct2 papka> --out <yangi papka> \
      --text a.txt b.txt --text-min2 wiki/*.txt

`--text` dagi tokenlar 1 marta uchrasa ham saqlanadi, `--text-min2` dagilar
(shovqinli manba) kamida 2 marta uchrasa.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
from collections import Counter
from pathlib import Path

import numpy as np

from mt.normalize import normalize_punct

_DTYPES = {0: np.float32, 1: np.int8, 2: np.int16, 3: np.int32, 4: np.float16}


def count_tokens(tok, paths: list[str], counter: Counter, batch_chars: int = 2_000_000) -> int:
    """Fayllarni qatorma-qator tokenlab, token (string) sanog'ini yig'adi."""
    total = 0
    for path in paths:
        batch, size = [], 0
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = normalize_punct(line.strip())
                if not line:
                    continue
                batch.append(line[:5000])
                size += len(line)
                if size >= batch_chars:
                    for enc in tok.encode_batch(batch, add_special_tokens=False):
                        counter.update(enc.tokens)
                        total += len(enc.tokens)
                    batch, size = [], 0
        if batch:
            for enc in tok.encode_batch(batch, add_special_tokens=False):
                counter.update(enc.tokens)
                total += len(enc.tokens)
    return total


def bpe_closure(keep: set[str], merges: list) -> set[str]:
    """Saqlangan tokenni hosil qiluvchi HAR birlashtirishning bo'laklarini qo'shadi."""
    producers: dict[str, list[tuple[str, str]]] = {}
    for m in merges:
        a, b = m if isinstance(m, (list, tuple)) else m.split(" ", 1)
        producers.setdefault(a + b, []).append((a, b))
    stack = list(keep)
    while stack:
        t = stack.pop()
        for a, b in producers.get(t, ()):
            for part in (a, b):
                if part not in keep:
                    keep.add(part)
                    stack.append(part)
    return keep


def prune_model_bin(src: Path, dst: Path, vocab_size: int, rows: np.ndarray) -> list[str]:
    """model.bin ni ko'chiradi; birinchi o'lchami `vocab_size` bo'lganlardan `rows` olinadi."""
    touched = []
    with open(src, "rb") as f, open(dst, "wb") as g:
        def copy_string():
            raw = f.read(2)
            n = struct.unpack("H", raw)[0]
            body = f.read(n)
            g.write(raw + body)
            return body[:-1].decode()

        g.write(f.read(4))                      # binary version
        copy_string()                           # spec nomi
        g.write(f.read(4))                      # spec revision
        raw = f.read(4)
        g.write(raw)
        for _ in range(struct.unpack("I", raw)[0]):
            name = copy_string()
            rank = struct.unpack("B", f.read(1))[0]
            dims = list(struct.unpack("I" * rank, f.read(4 * rank)))
            dtype_id = struct.unpack("B", f.read(1))[0]
            nbytes = struct.unpack("I", f.read(4))[0]
            if rank >= 1 and dims[0] == vocab_size:
                dt = _DTYPES[dtype_id]
                arr = np.frombuffer(f.read(nbytes), dtype=dt).reshape(dims)[rows]
                arr = np.ascontiguousarray(arr)
                dims[0] = len(rows)
                data = arr.tobytes()
                touched.append(f"{name} -> {tuple(dims)}")
            else:
                data = None
            g.write(struct.pack("B", rank))
            g.write(struct.pack("I" * rank, *dims))
            g.write(struct.pack("B", dtype_id))
            if data is None:
                g.write(struct.pack("I", nbytes))
                remaining = nbytes
                while remaining:
                    chunk = f.read(min(remaining, 16 << 20))
                    g.write(chunk)
                    remaining -= len(chunk)
            else:
                g.write(struct.pack("I", len(data)))
                g.write(data)
        g.write(f.read())                       # aliaslar
    return touched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--text", nargs="*", default=[], help="har uchragan token saqlanadi")
    ap.add_argument("--text-min2", nargs="*", default=[], help="kamida 2 marta uchragan token saqlanadi")
    args = ap.parse_args()

    from tokenizers import Tokenizer

    src, out = Path(args.model), Path(args.out)
    tj = json.loads((src / "tokenizer.json").read_text(encoding="utf-8"))
    vocab: dict[str, int] = tj["model"]["vocab"]
    ct2_vocab: list[str] = json.loads((src / "shared_vocabulary.json").read_text(encoding="utf-8"))
    assert all(ct2_vocab[i] == t for t, i in vocab.items()), "tokenizer va CT2 lug'ati mos emas"

    tok = Tokenizer.from_file(str(src / "tokenizer.json"))
    strict, loose = Counter(), Counter()
    n1 = count_tokens(tok, args.text, strict)
    n2 = count_tokens(tok, args.text_min2, loose)
    print(f"tokenlandi: {n1 + n2} token; turlar: aniq {len(strict)}, shovqinli {len(loose)}", flush=True)

    keep = set(strict) | {t for t, c in loose.items() if c >= 2}
    keep &= set(vocab)
    seen = len(keep)
    keep |= {a["content"] for a in tj["added_tokens"]}
    keep |= {t for t in vocab if len(t) == 1 or (len(t) == 2 and t[0] == "▁")}
    with_single = len(keep)
    keep = bpe_closure(keep, tj["model"]["merges"])
    keep.add(tj["model"].get("unk_token") or "<unk>")
    print(f"saqlanadi: uchragan {seen} -> +maxsus/bir belgili {with_single} -> +BPE yopilmasi {len(keep)} "
          f"(asl {len(ct2_vocab)})", flush=True)

    old_ids = sorted(vocab[t] for t in keep)
    rows = np.array(old_ids, dtype=np.int64)
    remap = {old: new for new, old in enumerate(old_ids)}

    out.mkdir(parents=True, exist_ok=True)
    touched = prune_model_bin(src / "model.bin", out / "model.bin", len(ct2_vocab), rows)
    print("qisqartirildi:", touched, flush=True)

    new_vocab_list = [ct2_vocab[i] for i in old_ids]
    (out / "shared_vocabulary.json").write_text(json.dumps(new_vocab_list, ensure_ascii=False), encoding="utf-8")

    tj["model"]["vocab"] = {t: remap[i] for t, i in vocab.items() if t in keep}
    kept_merges = []
    for m in tj["model"]["merges"]:
        a, b = m if isinstance(m, (list, tuple)) else m.split(" ", 1)
        if a + b in keep:
            kept_merges.append(m)
    tj["model"]["merges"] = kept_merges
    for a in tj["added_tokens"]:
        a["id"] = remap[a["id"]]
    special = (tj.get("post_processor") or {}).get("special_tokens") or {}
    for spec in special.values():
        spec["ids"] = [remap[i] for i in spec["ids"]]
    (out / "tokenizer.json").write_text(json.dumps(tj, ensure_ascii=False), encoding="utf-8")
    shutil.copy(src / "config.json", out / "config.json")
    (out / "prune_info.json").write_text(json.dumps({
        "source_model": src.name, "vocab_before": len(ct2_vocab), "vocab_after": len(new_vocab_list),
        "merges_before": None, "merges_after": len(kept_merges),
        "texts": [os.path.basename(p) for p in args.text + args.text_min2],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    size = lambda p: round(os.path.getsize(p) / 1e6)
    print(f"model.bin: {size(src / 'model.bin')} MB -> {size(out / 'model.bin')} MB; tayyor: {out}")


if __name__ == "__main__":
    main()
