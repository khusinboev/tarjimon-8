# NLLB fine-tune (bot korpusida)

Maqsad: lokal model og'zaki/chat matnida "o'qituvchi" provayderlar
(Google/DeepL/Azure) darajasiga yaqinlashsin, keyin `LOCAL_MT_PRIORITY` 3 -> 1.

Qayerda o'qitiladi: **GPU kerak** (laptop RTX 4050 6 GB yoki ijaraga GPU).
Server (4 vCPU, GPU yo'q) — bir epoch ~5–7 kun va RAM yetmaydi, boshqa
xizmatlarni bo'g'adi; u faqat tayyor modelni xizmat qiladi.

## 1. Korpus eksporti (serverda, faqat o'qish)

```bash
mkdir -p /root/mt-data && chmod 700 /root/mt-data && cd /tmp
sudo -u postgres psql -d tarjimon8 -tAc "COPY (SELECT id, created_at, provider,
  source_lang_requested, source_lang_detected, target_lang, source_hash,
  source_text, target_text FROM translations WHERE status='success'
  AND input_kind='text' AND target_text IS NOT NULL
  AND provider NOT IN ('cache','local_mt')) TO STDOUT WITH (FORMAT csv, HEADER)" \
  | gzip > /root/mt-data/corpus.csv.gz
```

GPU mashinaga ko'chirib, serverdagi nusxani o'chiring. Bu foydalanuvchi
xabarlari — repoga qo'shilmaydi.

## 2. Tozalash va bo'lish

```bash
python -m mt.train.prepare_data --csv corpus.csv.gz --out prepared/
```

2026-11-10 eksporti: 252 367 qator -> 94 758 unikal juftlik (uz/ru/en 6
yo'nalish). Train ~92k (en→uz 37k, ru→uz 22k, uz→en 19k, uz→ru 14k), dev
4×300, test ~1350 (2026-09+ dan, 5..400 belgi, manbasi train'da yo'q).
Tushib qolganlar: boshqa yo'nalishlar, takror (eng yaxshi provayder qoladi),
tarjima qilinmagan, noto'g'ri yozuv/til belgisi.

## 3. Bazaviy o'lchov (o'qitishdan oldin)

```bash
python -m mt.train.evaluate --model <ct2 model> --test prepared/test.jsonl --out baseline.json
```

Bazaviy natija — NLLB-1.3B (o'qitilmagan), beam 2, 2026-11-10 test to'plami:

| yo'nalish | n | chrF++ | BLEU |
|---|---|---|---|
| en→uz | 300 | 60.56 | 36.26 |
| uz→en | 300 | 59.32 | 40.05 |
| ru→uz | 300 | 48.91 | 23.05 |
| uz→ru | 300 | 40.56 | 20.09 |
| en→ru | 81 | 58.10 | 39.82 |
| ru→en | 66 | 61.21 | 41.79 |
| **umumiy** | 1347 | **54.18** | |

## 4. O'qitish (GPU)

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r mt/train/requirements.txt
python -m mt.train.finetune --data prepared/ --out ckpt/ --max-steps 50   # tutun sinovi
python -m mt.train.finetune --data prepared/ --out ckpt/ --epochs 1
```

## 5. Qo'shish, o'girish, tekshirish, deploy

```bash
python -m mt.train.merge_and_convert --adapter ckpt/best --out nllb-1.3B-tarjimon-v1-ct2-int8
python -m mt.train.evaluate --model nllb-1.3B-tarjimon-v1-ct2-int8 --test prepared/test.jsonl --out v1.json
# FLORES (rasmiy matn) pasaymaganini ham tekshiring — bench/run_bench.py
```

Qabul mezoni: test chrF++ sezilarli oshgan, FLORES ≤1 chrF++ pasaygan,
`mt/test_engine.py` o'tadi. Keyin serverga `/home/tarjimon8/models/` ostiga
ko'chirib, `MT_MODEL_DIR`ni almashtiring (eski papka orqaga qaytish uchun qoladi).
