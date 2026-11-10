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

## 2b. Sifat bo'yicha saralash (curate)

"Muvaffaqiyatli" yozilgan tarjima ham xato bo'lishi mumkin (o'qituvchi
provayder og'zaki o'zbekchada ko'p adashadi). `curate.py` har juftlikni
NLLB-1.3B bilan ikki yo'nalishda majburiy dekodlab baholaydi (`fwd`, `bwd`),
tarjima qilinmagan (`copy`) va boshqa tildagi (`lang_bad`) maqsadni aniqlaydi.
Chegara har yo'nalish x uzunlik guruhi (1 / 2-3 / 4-8 / 9+ so'z) ichida —
qisqa lug'at juftliklari tabiatan past ball oladi. Olib tashlanadi: eng past
10% (o'zbekcha manbada 15%) + qoidalar.

```bash
# XOTIRA: skorlash 256k lug'at bo'yicha log-softmax — batch token bilan cheklangan;
# baribir qattiq chegara ostida ishga tushiring (laptop bir marta qotgan).
systemd-run --user --scope -p MemoryMax=4G -p MemorySwapMax=0 nice -n 15 \
  python -m mt.train.curate --data prepared/ --model <nllb-1.3B ct2> --lid lid.176.ftz --out curated/
```

2000 lik namunada qo'lda tekshiruv: olib tashlanganlarning ~60-75% haqiqatan
xato (`otvechay -> огонь`, `moshli makron -> Macron`), qolgani to'g'ri qisqa
lug'at juftliklari — narxi qabul qilinadigan. `curated/*.jsonl` — o'qitish
uchun; `*.scored.jsonl` — barcha signallar bilan (ko'rib chiqish uchun).

Natija (2026-11-11, to'liq korpus, laptop CPU 6 oqim, ~2.5 soat): train
92 211 -> 80 625 (87.4%); sabablar: past ball 10 842, tarjima qilinmagan 1 066,
boshqa til 43. dev 1 200 -> 1 051, test 1 347 -> 1 187.

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

Tozalangan test (`curated/test.jsonl`) da bazaviy chrF++ **55.73** (en→uz 62.13,
uz→en 60.15, ru→uz 50.52, uz→ru 43.99). DIQQAT: bu raqam NLLB foydasiga
og'gan — saralashni shu model qilgan, u "rozi bo'lmagan" juftliklar tushib
qolgan. Fine-tune'ni baholashda ASOSIY o'lchov — tozalanmagan test (54.18),
tozalangani — qo'shimcha.

## 4. O'qitish (GPU)

Laptop muhiti tayyor (2026-11-11): `../train-venv` (torch cu128, transformers 5.22,
peft 0.21), asosiy model fp16 da `../hf-base/nllb-200-distilled-1.3B-fp16`
(5.5 GB fp32 -> 2.7 GB; disk tor). Butun zanjir CPU'da 2 qadam bilan sinalgan:
o'qitish -> merge -> CT2 int8 -> `mt.engine` tarjimasi ishladi.

Oldin NVIDIA drayveri (kernel 7.2.15 uchun modul yo'q edi):
`sudo apt install -y system76-driver-nvidia && sudo system76-power graphics hybrid`, reboot,
`nvidia-smi` ishlashini tekshiring.

```bash
cd tarjimon-8
B=../hf-base/nllb-200-distilled-1.3B-fp16
# tutun sinovi (GPU, ~1 daqiqa)
../train-venv/bin/python -m mt.train.finetune --data ../data/curated --out ../data/ckpt --base $B --max-steps 50 --eval-steps 50 --eval-limit 50
# to'liq (1 epoch, RTX 4050 6 GB; OOM bo'lsa --max-tokens 2000)
../train-venv/bin/python -m mt.train.finetune --data ../data/curated --out ../data/ckpt --base $B --epochs 1
```

## 5. Qo'shish, o'girish, tekshirish, deploy

```bash
python -m mt.train.merge_and_convert --adapter ../data/ckpt/best --base $B --out ../models/nllb-1.3B-tarjimon-v1-ct2-int8
python -m mt.train.evaluate --model nllb-1.3B-tarjimon-v1-ct2-int8 --test prepared/test.jsonl --out v1.json
# FLORES (rasmiy matn) pasaymaganini ham tekshiring — bench/run_bench.py
```

Qabul mezoni: test chrF++ sezilarli oshgan, FLORES ≤1 chrF++ pasaygan,
`mt/test_engine.py` o'tadi. Keyin serverga `/home/tarjimon8/models/` ostiga
ko'chirib, `MT_MODEL_DIR`ni almashtiring (eski papka orqaga qaytish uchun qoladi).
