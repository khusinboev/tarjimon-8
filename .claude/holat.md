# tarjimon-8 — lokal tarjima modeli: holat

Yangilangan: 2026-11-10 · commit: (oxirgi `feat/mt-finetune-prep`) · server: `main` @ 687af86

| Faza | Bandlar | Ulushi | Bajarilgani | Holat |
|---|---|---|---|---|
| 1. Kashfiyot | laptop/server parametrlari, model tadqiqoti, bot/baza tahlili | 15 % | 100 % | ✅ |
| 2. Benchmark va tanlov | FLORES 600M/1.3B, til aniqlash, server tezligi, haqiqiy matnlarda yonma-yon | 20 % | 100 % | ✅ 1.3B |
| 3. Servis va integratsiya | `mt/`, `local_mt` provayderi, smoke testlar, hujjatlar | 25 % | 100 % | ✅ PR #1 merge |
| 4. Deploy va kuzatuv | systemd xizmati, botga ulash, 1–2 hafta kuzatuv | 15 % | 60 % | 🔄 kuzatuv (2026-11-24 gacha) |
| 5. Fine-tune | korpus ✅, baholash ✅, skriptlar ✅, muhit ⬜, o'qitish ⬜, deploy ⬜ | 25 % | 30 % | ⏸ egasi ruxsati + GPU |

## Kutilayotgan narsalar (egasi)
- Fine-tune'ni boshlashga ruxsat va joy: laptop (bepul, sekin internet) yoki ijaraga GPU (~$1–3).
- Laptop NVIDIA drayveri: kernel 7.2.15 uchun modul yo'q —
  `sudo apt install system76-driver-nvidia && sudo system76-power graphics hybrid`, reboot.
- Google/Azure/DeepL kalitlari — egasi qarori: hozircha qoladi.

## Muhim raqamlar
- Bot test to'plamida bazaviy chrF++ 54.18 (eng zaif uz→ru 40.56) — `mt/train/README.md`.
- FLORES chrF++ 1.3B: en→uz 49.29, uz→en 59.65, ru→uz 45.72, uz→ru 47.00 — `mt/README.md`.
- Server: 1.3B ~1.5 GB RAM, qisqa ~1 s, 554 belgi ~6 s.

## Kechqurun davom ettirish (2026-11-13 yozildi) — dekoder distillatsiyasi
Production o'zgarmagan: v1p (`MT_THREADS=2`, beam 2). Natijalar: `mt/README.md` > "Dekoder distillatsiyasi".
Branch `feat/mt-distill` (push qilingan, PR ochilmagan). Eng yaxshi o'quvchi: **s12b** — bot 60.50 (v1p 61.01), FLORES 52.94 (54.86).

Laptopda QOLGAN (kerakli): `hf-base/v1-merged-fp16` (o'qituvchi), `models/nllb-1.3B-tarjimon-v1p-ct2-int8`,
`data/distill/s12b/best` (`--init` uchun), `data/distill/teacher.jsonl` + `teacher2.jsonl`, `data/curated`, `data/prepared`,
`data/distill/dev-multi.jsonl`, `data/distill/mono*.txt`, `data/wiki`, `data/ckpt/best` (LoRA v1), `train-venv`, `bench/.venv`.
O'CHIRILGAN (serverda nusxasi bor yoki qayta olinadi): v1 va asl 1.3B CT2 (server `/home/tarjimon8/models`),
`hf-base/nllb-200-distilled-1.3B-fp16` (HF'dan qayta yuklanadi), s12, s12b/last, s12b CT2 (`bench/build_student.py` bilan 2 daqiqada yig'iladi).
Serverdagi zaxira: `/root/mt-stage/` — s12b CT2, `ckpt/lora-v1-best`, `ckpt/s12b-decoder-best`.

Keyingi qadamlar:
1. Yana o'qituvchi ma'lumoti: `bench/make_mono2.py` + `bench/teach2.py` (en→X har til 20k+, `data/wiki` dan; uz manba kam — 9k).
2. `--init ../data/distill/s12b/best` bilan davom (lr 5e-5..7e-5), so'ng `bench/build_student.py` -> `data/distill/eval_s12b.sh` (GPU uchun LD_LIBRARY_PATH train-venv/nvidia ichida).
3. Serverda tezlik: `bench/srv_speed.py` -> `/root/mt-stage/`, `setsid nohup` bilan.
4. Deploy mezoni: bot testi ≥ 60.8 va FLORES yo'qotishi ≤ 1.0.
Diqqat: laptop diski ~13 GB bo'sh — teacher3 + o'qitish uchun yetadi (~2–3 GB).

## 2026-11-16 ertalab
- **s16** (16 qatlamli dekoder) tayyor: bot 60.92 (v1p 61.01), FLORES 54.02 (54.86) — deploy mezoniga mos.
  Server tezligi `/root/mt-stage/speed-s16.log` da (laptop o'chishidan oldin ishga tushirildi). Tezlik
  ≥1.2x bo'lsa — deploy (MT_MODEL_DIR -> s16, orqaga qaytish: v1p papkasi).
- Og'ir fayllar `/var/tmp/myllm-work` da (`/home` to'la): teacher3.jsonl, s16/best, s12c, s16 CT2.
- Production tuzatishlari (deploy qilingan): PR #11 MT navbati (qisqa oldin, muddatli rad), PR #12
  maqsad til `auto` (142 foydalanuvchi, zaxira `/root/backup-019-auto-target.csv`).
- Ochiq: "matn allaqachon maqsad tilida" UX, oromo qisqa salomlari aniqlanmaydi.

## Disk (2026-11-17)
`/home` to'la edi — og'ir fayllar Windows bo'limiga (`nvme0n1p5`, "New Volume", NTFS) ko'chirildi,
eski joylarida symlink: `myllm/train-venv`, `myllm/hf-base`, `myllm/models` ->
`/media/adhambek/New Volume/myllm-store/...`; s16 checkpoint: `.../myllm-store/ckpt/s16/best`.
**Qayta yoqilgandan keyin bo'lim avtomatik ulanmaydi** — ishdan oldin:
`udisksctl mount -b /dev/nvme0n1p5` (parolsiz ishlaydi). Ulanmasa symlinklar bo'sh.
Korpusdan olingan ma'lumot (`data/`, teacher*.jsonl) NTFS'ga ko'chirilmadi — u yerda ruxsatlar
hamma uchun ochiq; `/home` dagi 700 papkada qoladi.
