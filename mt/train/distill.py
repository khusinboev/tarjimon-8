"""Dekoderni ixchamlash: 24 qatlamli o'qituvchidan 12 qatlamli o'quvchi (distillatsiya).

Nega: CPU'da tarjima vaqtining asosiy qismi dekoderda (har chiqish tokeni uchun
barcha dekoder qatlamlari). Qatlamlarni shunchaki olib tashlab bo'lmaydi
(o'lchov, dev 300 gap, greedy: 24 -> 58.0; 18 -> 54.6; 12 -> 32.1 chrF++) —
qolgan qatlamlar qayta o'qitiladi.

Usul (6 GB VRAM'ga sig'adigan):
- O'qituvchi = v1 (LoRA qo'shilgan, fp16). Enkoder va embedding MUZLATILGAN (fp16).
- O'quvchi dekoderi: o'qituvchi dekoderining har ikkinchi qatlami + oxirgisi
  (0,2,...,20,23), fp32 da to'liq o'qitiladi; Adafactor (holati kichik).
- Ma'lumot: (a) bot korpusi (saralangan, haqiqiy referenslar), (b) o'qituvchi
  tarjimalari — 22 til, Vikipediya gaplari (`bench/teach.py`) — boshqa tillar
  sifati saqlanishi uchun (ketma-ketlik darajasidagi distillatsiya).
- Chiqish qatlami faqat saqlanadigan lug'at (133k) bo'yicha — logits xotirasi 2x kam.

  python -m mt.train.distill --teacher <hf fp16 papka> --data a.jsonl b.jsonl \
      --dev dev.jsonl --keep-vocab <ct2>/shared_vocabulary.json --out <papka>
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import sacrebleu
import torch
import torch.nn.functional as F
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
from transformers.optimization import Adafactor

from mt.langs import NLLB_CODES
from mt.normalize import normalize_punct

MAX_LEN = 128


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        rows += [json.loads(line) for line in open(p, encoding="utf-8")]
    return rows


def encode(tok, rows: list[dict]) -> list[tuple[list[int], list[int]]]:
    out = []
    for r in rows:
        tok.src_lang, tok.tgt_lang = NLLB_CODES[r["src_lang"]], NLLB_CODES[r["tgt_lang"]]
        enc = tok(normalize_punct(r["src"]), text_target=normalize_punct(r["tgt"]),
                  max_length=MAX_LEN, truncation=True)
        out.append((enc["input_ids"], enc["labels"]))
    return out


def make_batches(items, max_tokens: int, rng: random.Random):
    items = sorted(items, key=lambda x: (len(x[1]), len(x[0])))
    batches, cur, mx_s, mx_t = [], [], 0, 0
    for s, t in items:
        ns, nt = max(mx_s, len(s)), max(mx_t, len(t))
        if cur and max(ns, nt) * (len(cur) + 1) > max_tokens:
            batches.append(cur)
            cur, ns, nt = [], len(s), len(t)
        cur.append((s, t))
        mx_s, mx_t = ns, nt
    if cur:
        batches.append(cur)
    rng.shuffle(batches)
    return batches


def pad(seqs, value):
    m = max(len(s) for s in seqs)
    return torch.tensor([s + [value] * (m - len(s)) for s in seqs])


class Student:
    """Muzlatilgan fp16 enkoder/embedding + o'qitiladigan fp32 dekoder.

    VRAM (6 GB) uchun: embedding o'qitish paytida saqlanadigan lug'atga (133k)
    qisqartiriladi (token id'lari `remap` orqali), yo'qotish bo'laklab hisoblanadi
    (logits xotirasi bo'lak bilan cheklanadi). Diskka faqat DEKODER og'irliklari
    yoziladi — to'liq model `assemble()` bilan o'qituvchidan yig'iladi.
    """

    def __init__(self, teacher_dir: str, layers: list[int], keep_ids: torch.Tensor, device: str):
        self.model = AutoModelForSeq2SeqLM.from_pretrained(teacher_dir, dtype=torch.float16)
        m = self.model.model
        m.decoder.layers = torch.nn.ModuleList([m.decoder.layers[i] for i in layers])
        self.model.config.decoder_layers = len(layers)
        # Attention modullari kesh uchun o'z qatlam indeksini eslab qoladi —
        # qatlamlar tanlangach yangi tartibga keltirilmasa generate() yiqiladi.
        for i, layer in enumerate(m.decoder.layers):
            for attn in (layer.self_attn, layer.encoder_attn):
                if hasattr(attn, "layer_idx"):
                    attn.layer_idx = i
        # Lug'atni qisqartirish (faqat o'qitish/baholash uchun, diskka yozilmaydi).
        v = self.model.config.vocab_size
        pruned = torch.nn.Parameter(m.shared.weight.data[keep_ids].clone(), requires_grad=False)
        for e in (m.shared, m.encoder.embed_tokens, m.decoder.embed_tokens):
            e.weight = pruned
            e.num_embeddings = len(keep_ids)
        self.model.lm_head.weight = pruned
        self.model.lm_head.out_features = len(keep_ids)
        self.model.config.vocab_size = len(keep_ids)
        remap = torch.full((v,), -1, dtype=torch.long)
        remap[keep_ids] = torch.arange(len(keep_ids))
        remap[remap < 0] = remap[3]                      # tashlangan token -> <unk>
        self.model.to(device)
        for p in self.model.parameters():
            p.requires_grad_(False)
        self.dec = m.decoder
        self.embed = m.decoder.embed_tokens
        for mod in list(self.dec.layers) + [self.dec.layer_norm]:
            mod.float()
            for p in mod.parameters():
                p.requires_grad_(True)
        self.keep_ids = keep_ids.to(device)
        self.remap = remap.to(device)
        self.device = device
        self.start_id = int(remap[self.model.config.decoder_start_token_id])
        self.pad_id = int(remap[self.model.config.pad_token_id])
        if device == "cuda":
            torch.cuda.empty_cache()

    def trainable(self):
        return [p for p in self.model.parameters() if p.requires_grad]

    def loss_backward(self, src, labels, smoothing: float, scale: float, chunk: int = 192) -> float:
        """Yo'qotishni hisoblab, gradientni yig'adi (bo'laklab — logits xotirasi kichik)."""
        src = self.remap[src]
        mask = src != self.pad_id
        valid = labels != -100
        tgt = self.remap[labels.masked_fill(~valid, 0)]
        with torch.no_grad():
            enc = self.model.model.encoder(input_ids=src, attention_mask=mask).last_hidden_state.float()
            dec_in = torch.cat([torch.full_like(tgt[:, :1], self.start_id), tgt[:, :-1]], 1)
            dec_in = dec_in.masked_fill(~torch.cat([valid[:, :1], valid[:, :-1]], 1), self.pad_id)
            emb = self.embed(dec_in).float()
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=self.device == "cuda"):
            h = self.dec(inputs_embeds=emb, encoder_hidden_states=enc, encoder_attention_mask=mask).last_hidden_state
        hv = h[valid]
        leaf = hv.detach().float().requires_grad_(True)
        target = tgt[valid]
        n = target.numel()
        total = 0.0
        head = self.embed.weight
        for a in range(0, n, chunk):
            logits = F.linear(leaf[a : a + chunk].to(head.dtype), head).float()
            part = F.cross_entropy(logits, target[a : a + chunk], label_smoothing=smoothing, reduction="sum") / n
            (part * scale).backward()
            total += part.item()
        hv.backward(leaf.grad.to(hv.dtype))
        return total

    @torch.no_grad()
    def evaluate(self, tok, rows: list[dict]) -> float:
        self.model.eval()
        hyps, refs = [], []
        self.dec.layers.half(); self.dec.layer_norm.half()
        try:
            for r in rows:
                tok.src_lang = NLLB_CODES[r["src_lang"]]
                enc = tok(normalize_punct(r["src"]), return_tensors="pt", max_length=MAX_LEN, truncation=True).to(self.device)
                bos = int(self.remap[tok.convert_tokens_to_ids(NLLB_CODES[r["tgt_lang"]])])
                out = self.model.generate(input_ids=self.remap[enc["input_ids"]], attention_mask=enc["attention_mask"],
                                          forced_bos_token_id=bos, max_new_tokens=MAX_LEN, max_length=None, num_beams=1,
                                          pad_token_id=self.pad_id, eos_token_id=2, decoder_start_token_id=self.start_id)
                hyps.append(tok.decode(self.keep_ids[out[0]], skip_special_tokens=True))
                refs.append(r["tgt"])
        finally:
            self.dec.layers.float(); self.dec.layer_norm.float()
        self.model.train()
        if self.device == "cuda":
            torch.cuda.empty_cache()
        return sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score

    def save(self, path: Path, layers: list[int]) -> None:
        """Faqat dekoder og'irliklari (fp16) + qaysi qatlamlar ekani."""
        from safetensors.torch import save_file

        path.mkdir(parents=True, exist_ok=True)
        state = {f"layers.{i}.{k}": v.detach().half().cpu() for i, layer in enumerate(self.dec.layers)
                 for k, v in layer.state_dict().items()}
        state.update({f"layer_norm.{k}": v.detach().half().cpu() for k, v in self.dec.layer_norm.state_dict().items()})
        save_file(state, str(path / "decoder.safetensors"))
        (path / "student.json").write_text(json.dumps({"layers": layers}), encoding="utf-8")


def assemble(teacher_dir: str, student_dir: str, out_dir: str) -> None:
    """O'qituvchi (fp16) + o'quvchi dekoderi -> to'liq HF model (CT2'ga o'girish uchun)."""
    from safetensors.torch import load_file

    info = json.loads((Path(student_dir) / "student.json").read_text())
    model = AutoModelForSeq2SeqLM.from_pretrained(teacher_dir, dtype=torch.float16)
    dec = model.model.decoder
    dec.layers = torch.nn.ModuleList([dec.layers[i] for i in info["layers"]])
    state = load_file(str(Path(student_dir) / "decoder.safetensors"))
    for i, layer in enumerate(dec.layers):
        layer.load_state_dict({k[len(f"layers.{i}."):]: v for k, v in state.items() if k.startswith(f"layers.{i}.")})
        for attn in (layer.self_attn, layer.encoder_attn):
            if hasattr(attn, "layer_idx"):
                attn.layer_idx = i
    dec.layer_norm.load_state_dict({k[len("layer_norm."):]: v for k, v in state.items() if k.startswith("layer_norm.")})
    model.config.decoder_layers = len(info["layers"])
    model.save_pretrained(out_dir)
    AutoTokenizer.from_pretrained(teacher_dir).save_pretrained(out_dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teacher", required=True)
    ap.add_argument("--data", nargs="+", required=True)
    ap.add_argument("--dev", nargs="+", required=True, help="har biri alohida baholanadi; birinchisi 'eng yaxshi' mezoni")
    ap.add_argument("--keep-vocab", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layers", default="0,2,4,6,8,10,12,14,16,18,20,23")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--max-tokens", type=int, default=450)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--eval-steps", type=int, default=250)
    ap.add_argument("--eval-limit", type=int, default=300)
    ap.add_argument("--smoothing", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(args.teacher)
    keep_tokens = json.loads(Path(args.keep_vocab).read_text(encoding="utf-8"))
    keep_ids = torch.tensor(sorted(tok.convert_tokens_to_ids(keep_tokens)))
    layers = [int(x) for x in args.layers.split(",")]
    st = Student(args.teacher, layers, keep_ids, device)
    n_train = sum(p.numel() for p in st.trainable())
    print(f"qurilma={device} dekoder qatlamlari={layers} o'qitiladigan={n_train/1e6:.0f}M lug'at={len(keep_ids)}", flush=True)

    devs = []
    for p in args.dev:
        rows = load_rows([p])
        random.Random(13).shuffle(rows)
        devs.append((Path(p).stem, rows[: args.eval_limit]))

    items = encode(tok, load_rows(args.data))
    batches = make_batches(items, args.max_tokens, rng)
    total = args.max_steps or int(math.ceil(len(batches) / args.grad_accum) * args.epochs)
    print(f"juftliklar={len(items)} batch={len(batches)} qadam={total}", flush=True)

    opt = Adafactor(st.trainable(), lr=args.lr, relative_step=False, scale_parameter=False,
                    warmup_init=False, clip_threshold=1.0)
    warm = max(1, int(0.03 * total))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * max(0.1, 1 - s / total))

    def run_eval(step: int) -> float:
        scores = {name: st.evaluate(tok, rows) for name, rows in devs}
        print(f"qadam {step}: " + "  ".join(f"{k} {v:.2f}" for k, v in scores.items()), flush=True)
        log.append({"step": step, **scores})
        (out / "train_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
        return scores[devs[0][0]]

    log: list[dict] = []
    best = run_eval(0)
    best = -1.0  # 0-qadam (o'qitilmagan) saqlanmaydi
    step = micro = 0
    running = 0.0
    t0 = time.time()
    st.model.train()
    while step < total:
        for batch in batches:
            src = pad([b[0] for b in batch], 1).to(device)
            labels = pad([b[1] for b in batch], -100).to(device)
            running += st.loss_backward(src, labels, args.smoothing, 1.0 / args.grad_accum) / args.grad_accum
            micro += 1
            if micro % args.grad_accum:
                continue
            opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
            step += 1
            if step % 25 == 0:
                print(f"qadam {step}/{total} loss {running / 25:.3f} {(time.time() - t0) / step:.2f}s/qadam", flush=True)
                running = 0.0
            if step % args.eval_steps == 0 or step == total:
                score = run_eval(step)
                st.save(out / "last", layers)
                if score > best:
                    best = score
                    st.save(out / "best", layers)
            if step >= total:
                break
    print(f"tugadi: eng yaxshi {devs[0][0]} chrF++ {best:.2f}; model: {out / 'best'}", flush=True)


if __name__ == "__main__":
    main()
