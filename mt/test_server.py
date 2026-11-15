"""`mt/server.py` navbati (Scheduler) uchun modelsiz sinov. Ishga tushirish (repo ildizidan):

  python -m mt.test_server

Vaqt masshtabi kichraytirilgan: "dvigatel" har belgiga 1 ms uxlaydi, muddat 1 s.
"""

from __future__ import annotations

import asyncio
import sys
import time

from mt.server import Expired, Scheduler

failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failed
    print(("  OK  " if ok else "  FAIL"), label, ("— " + detail if detail else ""))
    if not ok:
        failed += 1


def fake_translate(text: str, source: str, target: str) -> str:
    time.sleep(len(text) / 1000)
    return f"{target}:{len(text)}"


async def run(coro):
    try:
        return await coro
    except Expired:
        return "EXPIRED"


async def burst() -> None:
    # 2026-11-15 holati: bir vaqtda 6 ta uzun bo'lak (har biri ~0.4 s, muddat 1 s),
    # ortidan oddiy foydalanuvchining qisqa xabari.
    sched = Scheduler(fake_translate, deadline_s=1.0, ms_per_char=1.0)
    t0 = time.perf_counter()
    longs = [asyncio.create_task(run(sched.submit("x" * 400, "en", "uz"))) for _ in range(6)]
    await asyncio.sleep(0.05)
    short = asyncio.create_task(run(sched.submit("y" * 20, "en", "uz")))
    short_res = await short
    short_t = time.perf_counter() - t0
    res = await asyncio.gather(*longs)
    total = time.perf_counter() - t0

    done = [r for r in res if r != "EXPIRED"]
    check("qisqa xabar uzunlar ortida qolmaydi", short_res == "uz:20" and short_t < 0.9, f"{short_t:.2f} s")
    check("muddatga sig'adigan uzunlar bajarildi", len(done) == 2, f"bajarildi {len(done)}/6")
    check("qolganlari tashlandi (bajarilmadi)", res.count("EXPIRED") == 4)
    check("navbat muddatdan ortiq band bo'lmaydi", total < 1.2, f"jami {total:.2f} s (eski xatti-harakat ~2.4 s)")
    check("hisoblagich nolga qaytdi", sched.pending == 0)


async def learning() -> None:
    # Boshlang'ich taxmin juda optimistik (0.1 ms/belgi) — birinchi ishlardan o'rganadi.
    sched = Scheduler(fake_translate, deadline_s=5.0, ms_per_char=0.1)
    for _ in range(4):
        await sched.submit("z" * 400, "en", "uz")
    check("tezlik o'rganiladi", 0.7 < sched.ms_per_char < 1.3, f"{sched.ms_per_char:.2f} ms/belgi")


async def first_chance() -> None:
    # Taxmin juda pessimistik bo'lsa ham, navbat bo'sh bo'lganda ish darhol bajariladi.
    sched = Scheduler(fake_translate, deadline_s=1.0, ms_per_char=50.0)
    check("bo'sh navbatda ish tashlanmaydi", await run(sched.submit("q" * 100, "en", "uz")) == "uz:100")


async def errors() -> None:
    def boom(text, source, target):
        raise ValueError("dvigatel xatosi")

    sched = Scheduler(boom, deadline_s=1.0)
    try:
        await sched.submit("salom dunyo", "uz", "en")
        check("dvigatel xatosi uzatiladi", False)
    except ValueError:
        check("dvigatel xatosi uzatiladi", True)
    # Xatodan keyin ishchi tirik.
    sched.fn = fake_translate
    check("xatodan keyin navbat ishlaydi", await sched.submit("ok", "uz", "en") == "en:2")


async def cancelled() -> None:
    calls = []

    def counted(text, source, target):
        calls.append(text[0])
        return fake_translate(text, source, target)

    sched = Scheduler(counted, deadline_s=5.0, ms_per_char=1.0)
    first = asyncio.create_task(sched.submit("a" * 300, "en", "uz"))
    await asyncio.sleep(0.02)
    gone = asyncio.create_task(sched.submit("b" * 300, "en", "uz"))
    await asyncio.sleep(0.02)
    gone.cancel()                       # mijoz uzildi
    await first
    await sched.submit("c" * 10, "en", "uz")   # navbatdan keyingi ish
    check("bekor qilingan ish bajarilmaydi", calls == ["a", "c"] and sched.pending == 0, str(calls))


def main() -> int:
    for case in (burst, learning, first_chance, errors, cancelled):
        print(case.__name__)
        asyncio.run(case())
    print("XATO:", failed if failed else "yo'q")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
