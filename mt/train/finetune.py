"""NLLB-200 ni bot korpusida LoRA bilan fine-tune qilish (GPU, 6 GB+ VRAM).

  python -m mt.train.finetune --data <prepared papka> --out <checkpoints papka> \
      [--base facebook/nllb-200-distilled-1.3B] [--epochs 1] [--max-steps N]

Qarorlar:
- LoRA (r=16, barcha attention + FFN chiziqli qatlamlari): 6 GB VRAM'ga sig'adi
  (to'liq fine-tune 1.3B uchun ~20 GB kerak) va asosiy bilimni (FLORES, boshqa
  tillar) kamroq "unuttiradi". Natija keyin asosiy modelga qo'shiladi
  (`merge_and_convert.py`) — serverda xizmat qiluvchi kod o'zgarmaydi.
- bf16 (Ada/Ampere) yoki fp16; gradient checkpointing; 128 tokengacha (korpus
  p90 ~119 belgi).
- Har `--eval-steps` da dev'da chrF++ (greedy, tez) — eng yaxshisi saqlanadi.
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
from peft import LoraConfig, get_peft_model
from torch.utils.data import DataLoader
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, get_cosine_schedule_with_warmup

from mt.langs import NLLB_CODES

MAX_LEN = 128


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def encode(tok, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        tok.src_lang = NLLB_CODES[r["src_lang"]]
        tok.tgt_lang = NLLB_CODES[r["tgt_lang"]]
        enc = tok(r["src"], text_target=r["tgt"], max_length=MAX_LEN, truncation=True)
        out.append({"input_ids": enc["input_ids"], "labels": enc["labels"]})
    return out


def batches_by_length(items: list[dict], max_tokens: int, rng: random.Random) -> list[list[dict]]:
    """Uzunligi o'xshashlarni bir batch'ga — padding kam, VRAM tejaladi."""
    items = sorted(items, key=lambda x: len(x["input_ids"]) + len(x["labels"]))
    batches, cur, cur_max = [], [], 0
    for it in items:
        L = max(len(it["input_ids"]), len(it["labels"]))
        if cur and max(cur_max, L) * (len(cur) + 1) > max_tokens:
            batches.append(cur)
            cur, cur_max = [], 0
        cur.append(it)
        cur_max = max(cur_max, L)
    if cur:
        batches.append(cur)
    rng.shuffle(batches)
    return batches


def collate(batch: list[dict], pad_id: int) -> dict:
    def pad(seqs, value):
        m = max(len(s) for s in seqs)
        return torch.tensor([s + [value] * (m - len(s)) for s in seqs])
    input_ids = pad([b["input_ids"] for b in batch], pad_id)
    return {"input_ids": input_ids, "attention_mask": (input_ids != pad_id).long(),
            "labels": pad([b["labels"] for b in batch], -100)}


@torch.no_grad()
def evaluate(model, tok, rows: list[dict], device, limit: int = 400) -> float:
    model.eval()
    hyps, refs = [], []
    for r in rows[:limit]:
        tok.src_lang = NLLB_CODES[r["src_lang"]]
        enc = tok(r["src"], return_tensors="pt", max_length=MAX_LEN, truncation=True).to(device)
        out = model.generate(**enc, forced_bos_token_id=tok.convert_tokens_to_ids(NLLB_CODES[r["tgt_lang"]]),
                             max_new_tokens=MAX_LEN, max_length=None, num_beams=1)
        hyps.append(tok.decode(out[0], skip_special_tokens=True))
        refs.append(r["tgt"])
    model.train()
    return sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="facebook/nllb-200-distilled-1.3B")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=0, help="0 — epochs bo'yicha; sinov uchun kichik son")
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--max-tokens", type=int, default=3000, help="bitta batch'dagi token (VRAM'ga qarab)")
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--eval-steps", type=int, default=500)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--eval-limit", type=int, default=400, help="dev'dan nechta gap (CPU sinovida kichik)")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported() else (
        torch.float16 if device == "cuda" else torch.float32)
    print(f"qurilma={device} dtype={dtype}", flush=True)

    data = Path(args.data).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForSeq2SeqLM.from_pretrained(args.base, dtype=dtype)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=2 * args.rank, lora_dropout=0.05, task_type="SEQ_2_SEQ_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "out_proj", "fc1", "fc2"]))
    model.print_trainable_parameters()
    model.to(device)

    train = encode(tok, load_jsonl(data / "train.jsonl"))
    dev_rows = load_jsonl(data / "dev.jsonl")
    rng.shuffle(dev_rows)
    batches = batches_by_length(train, args.max_tokens, rng)
    steps_per_epoch = math.ceil(len(batches) / args.grad_accum)
    total_steps = args.max_steps or int(steps_per_epoch * args.epochs)
    print(f"train={len(train)} batch={len(batches)} qadam={total_steps}", flush=True)

    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, int(0.03 * total_steps), total_steps)

    base_chrf = evaluate(model, tok, dev_rows, device, args.eval_limit)
    print(f"dev chrF++ (boshlang'ich): {base_chrf:.2f}", flush=True)
    best = base_chrf
    log = [{"step": 0, "dev_chrf": base_chrf}]

    step, micro, t0, running = 0, 0, time.time(), 0.0
    model.train()
    while step < total_steps:
        for batch in batches:
            b = {k: v.to(device) for k, v in collate(batch, tok.pad_token_id).items()}
            loss = model(**b).loss / args.grad_accum
            loss.backward()
            running += loss.item()
            micro += 1
            if micro % args.grad_accum:
                continue
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 50 == 0:
                print(f"qadam {step}/{total_steps} loss {running / 50:.3f} "
                      f"{(time.time() - t0) / step:.2f}s/qadam", flush=True)
                running = 0.0
            if step % args.eval_steps == 0 or step == total_steps:
                chrf = evaluate(model, tok, dev_rows, device, args.eval_limit)
                log.append({"step": step, "dev_chrf": chrf})
                print(f"qadam {step}: dev chrF++ {chrf:.2f} (eng yaxshi {best:.2f})", flush=True)
                if chrf > best:
                    best = chrf
                    model.save_pretrained(out / "best")
                    tok.save_pretrained(out / "best")
            if step >= total_steps:
                break

    # Oxirgi holat har doim saqlanadi — dev balli oshmagan bo'lsa ham
    # (`best` bo'lmaydi), keyingi qadamlar (merge, baholash) ishlay olsin.
    model.save_pretrained(out / "last")
    tok.save_pretrained(out / "last")
    (out / "train_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    chosen = out / "best" if (out / "best").exists() else out / "last"
    print(f"tugadi: dev chrF++ {base_chrf:.2f} -> {best:.2f}; adapter: {chosen}")


if __name__ == "__main__":
    main()
