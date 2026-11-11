"""LoRA adapterini asosiy modelga qo'shib, serverdagi formatga (CTranslate2 int8) o'girish.

  python -m mt.train.merge_and_convert --adapter <checkpoints/best> --out <ct2 papka> \
      [--base facebook/nllb-200-distilled-1.3B]

Natija papkasini serverga ko'chirib, `deploy/tarjimon8-mt.service`dagi
`MT_MODEL_DIR` ga ko'rsatish kifoya (eski model papkasi orqaga qaytish uchun qoladi).
"""

from __future__ import annotations

import argparse
import shutil
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
        # fp16: vaqtinchalik nusxa 2.7 GB (fp32 da 5.5 GB — laptop diskiga sig'maydi);
        # keyin int8 ga kvantlanadi, fp16 aniqligi bunga yetarli.
        model = AutoModelForSeq2SeqLM.from_pretrained(args.base, dtype=torch.float16)
        model = PeftModel.from_pretrained(model, args.adapter).merge_and_unload()
        model.save_pretrained(tmp)
        AutoTokenizer.from_pretrained(args.base).save_pretrained(tmp)
        if out.exists():
            shutil.rmtree(out)
        # Python API — `ct2-transformers-converter` PATH'dan boshqa venv'nikini
        # (torch'siz) olib qolishi mumkin; bu yerda doim joriy muhit ishlatiladi.
        from ctranslate2.converters import TransformersConverter

        # Faqat tokenizer.json bo'lsa, transformers AutoTokenizer uni umumiy
        # tokenizator deb yuklaydi va `src_lang` e'tiborsiz qoladi (hamma manba
        # "eng_Latn" bo'lib qoladi) — FLORES'da uz->en 59 -> 20 ga "tushgan" edi.
        # mt.engine bunga bog'liq emas, lekin har qanday HF asbob to'g'ri ishlashi uchun.
        TransformersConverter(tmp, copy_files=["tokenizer.json", "tokenizer_config.json",
                                               "special_tokens_map.json", "sentencepiece.bpe.model"],
                              low_cpu_mem_usage=True).convert(
            str(out), quantization="int8", force=True)
    print("tayyor:", out)


if __name__ == "__main__":
    main()
