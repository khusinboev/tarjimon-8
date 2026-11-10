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
