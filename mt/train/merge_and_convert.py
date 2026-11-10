"""LoRA adapterini asosiy modelga qo'shib, serverdagi formatga (CTranslate2 int8) o'girish.

  python -m mt.train.merge_and_convert --adapter <checkpoints/best> --out <ct2 papka> \
      [--base facebook/nllb-200-distilled-1.3B]

Natija papkasini serverga ko'chirib, `deploy/tarjimon8-mt.service`dagi
`MT_MODEL_DIR` ga ko'rsatish kifoya (eski model papkasi orqaga qaytish uchun qoladi).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="facebook/nllb-200-distilled-1.3B")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    with tempfile.TemporaryDirectory() as tmp:
        model = AutoModelForSeq2SeqLM.from_pretrained(args.base, dtype=torch.float32)
        model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
        model.save_pretrained(tmp)
        AutoTokenizer.from_pretrained(args.base).save_pretrained(tmp)
        if out.exists():
            shutil.rmtree(out)
        subprocess.run(["ct2-transformers-converter", "--model", tmp, "--output_dir", str(out),
                        "--quantization", "int8", "--copy_files", "tokenizer.json"], check=True)
    print("tayyor:", out)


if __name__ == "__main__":
    main()
