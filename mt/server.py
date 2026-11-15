"""Lokal tarjima HTTP servisi (faqat 127.0.0.1 da) — bot uchun provayder.

  POST /translate  {"text": "...", "source": "en"|"auto", "target": "uz"}
       200 -> {"text", "source", "model", "segments", "ms"}
       422 -> {"error": "unsupported_language"|"detection_failed"|"suspicious_output"}
       503 -> {"error": "busy"}   navbat to'la — bot boshqa provayderga o'tadi
  GET  /health     -> {"ok": true, "model", "queue"}

Sozlamalar (muhit o'zgaruvchilari):
  MT_MODEL_DIR   model papkasi (CTranslate2 int8)       [majburiy]
  MT_LID_PATH    fastText lid.176.ftz (auto uchun)      [ixtiyoriy]
  MT_HOST/MT_PORT                                      [127.0.0.1:8790]
  MT_THREADS     CPU oqimlari                           [2]
  MT_BEAM        beam size                              [2]
  MT_MAX_QUEUE   navbatdagi so'rovlar chegarasi         [8]
  MT_MAX_CHARS   bitta so'rov chegarasi                 [5000]
  MT_DEADLINE_S  so'rov muddati (bot LOCAL_MT_TIMEOUT dan biroz kam)  [55]
  MT_MS_PER_CHAR hisob tezligining boshlang'ich taxmini (o'rganiladi)  [6]

Navbat (`Scheduler`) — bitta ishchi (CPU oqimlari dvigatel ichida):
- QISQA matn oldin: uzun hujjat bo'laklari ortida oddiy xabar kutib qolmaydi.
- Muddat: ish boshlanishidan oldin "kutilgan vaqt + taxminiy hisob" muddatdan
  oshsa, ish BAJARILMAYDI va darrov 503 `busy` qaytadi (bot boshqa provayderga
  o'tadi, breaker'ga hisoblanmaydi). 2026-11-15: bir foydalanuvchining 8 ta
  4096 belgili bo'lagi — 1-si 35 s da tayyor, qolgan 7 tasi bot 60 s dan keyin
  voz kechgach 69..326 s da tugagan: server 5.5 daqiqa keraksiz ishlagan, boshqa
  so'rovlar shu vaqtda navbatda qolgan, timeout'lar esa breaker'ni ochgan.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web

from mt.engine import Engine, EngineError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mt")

MAX_QUEUE = int(os.environ.get("MT_MAX_QUEUE", "8"))
MAX_CHARS = int(os.environ.get("MT_MAX_CHARS", "5000"))
DEADLINE_S = float(os.environ.get("MT_DEADLINE_S", "55"))
# Boshlang'ich taxmin; ishlab turib o'rganiladi (server 2 oqim: 4096 belgi ~35 s ≈ 8.6).
MS_PER_CHAR = float(os.environ.get("MT_MS_PER_CHAR", "6"))


class Expired(Exception):
    """Muddatgacha tugatib bo'lmaydi — ish boshlanmadi."""


class Scheduler:
    """Bitta ishchili navbat: qisqa matn oldin, muddati o'tadigan ish tashlanadi.

    Hisob tezligi (ms/belgi) bajarilgan uzun ishlardan siljuvchi o'rtacha bilan
    o'rganiladi; taxmin 0.8 koeffitsient bilan yumshatilgan (chegaradagi ishni
    behuda tashlab yubormaslik uchun). Darhol boshlanadigan ish (deyarli
    kutmagan) hech qachon tashlanmaydi — bu uning eng yaxshi imkoniyati.
    """

    LEARN_MIN_CHARS = 300

    def __init__(self, fn, deadline_s: float = DEADLINE_S, ms_per_char: float = MS_PER_CHAR):
        self.fn = fn
        self.deadline_s = deadline_s
        self.ms_per_char = ms_per_char
        self.pool = ThreadPoolExecutor(max_workers=1)
        self._queue: asyncio.PriorityQueue | None = None
        self._seq = 0
        self.pending = 0
        self._worker: asyncio.Task | None = None

    def estimate_s(self, chars: int) -> float:
        return chars * self.ms_per_char / 1000

    async def submit(self, text: str, *args):
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
            self._worker = asyncio.create_task(self._run())
        fut = asyncio.get_running_loop().create_future()
        self._seq += 1
        self.pending += 1
        await self._queue.put((len(text), self._seq, time.perf_counter(), text, args, fut))
        try:
            return await fut
        finally:
            self.pending -= 1

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            chars, _, arrived, text, args, fut = await self._queue.get()
            if fut.done():                       # mijoz ketgan (bekor qilingan)
                continue
            waited = time.perf_counter() - arrived
            if waited > min(1.0, 0.05 * self.deadline_s) and waited + 0.8 * self.estimate_s(chars) > self.deadline_s:
                fut.set_exception(Expired(f"kutildi {waited:.0f} s, taxmin {self.estimate_s(chars):.0f} s"))
                continue
            t0 = time.perf_counter()
            try:
                result = await loop.run_in_executor(self.pool, self.fn, text, *args)
            except Exception as exc:  # noqa: BLE001 — chaqiruvchiga uzatiladi
                if not fut.done():
                    fut.set_exception(exc)
                continue
            if chars >= self.LEARN_MIN_CHARS:
                rate = (time.perf_counter() - t0) * 1000 / chars
                self.ms_per_char = 0.5 * self.ms_per_char + 0.5 * rate
            if not fut.done():
                fut.set_result(result)


async def translate(request: web.Request) -> web.Response:
    app = request.app
    try:
        body = await request.json()
        text, source, target = body["text"], body.get("source", "auto"), body["target"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)
    if len(text) > MAX_CHARS:
        return web.json_response({"error": "too_long"}, status=413)
    sched: Scheduler = app["sched"]
    if sched.pending >= MAX_QUEUE:
        return web.json_response({"error": "busy"}, status=503)

    started = time.perf_counter()
    try:
        result = await sched.submit(text, source, target)
    except Expired as exc:
        log.info("tashlandi (muddat): chars=%d %s", len(text), exc)
        return web.json_response({"error": "busy", "message": str(exc)}, status=503)
    except EngineError as exc:
        log.info("rad: %s %s->%s (%s)", exc.code, source, target, exc)
        return web.json_response({"error": exc.code, "message": str(exc)}, status=422)
    except Exception:
        log.exception("kutilmagan xato")
        return web.json_response({"error": "internal"}, status=500)

    ms = int((time.perf_counter() - started) * 1000)
    log.info("ok %s->%s chars=%d seg=%d ms=%d", result.source, target, len(text), result.segments, ms)
    return web.json_response({
        "text": result.text, "source": result.source, "model": app["engine"].model_name,
        "segments": result.segments, "ms": ms,
    })


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "model": request.app["engine"].model_name,
                              "queue": request.app["sched"].pending,
                              "ms_per_char": round(request.app["sched"].ms_per_char, 2)})


def make_app() -> web.Application:
    engine = Engine(
        os.environ["MT_MODEL_DIR"],
        threads=int(os.environ.get("MT_THREADS", "2")),
        beam_size=int(os.environ.get("MT_BEAM", "2")),
        lid_path=os.environ.get("MT_LID_PATH"),
    )
    # Isitish — birinchi foydalanuvchi so'rovi sekin bo'lmasin.
    engine.translate("Hello, how are you?", "en", "uz")
    app = web.Application(client_max_size=64 * 1024)
    app["engine"] = engine
    app["sched"] = Scheduler(engine.translate)
    app.router.add_post("/translate", translate)
    app.router.add_get("/health", health)
    log.info("tayyor: %s", engine.model_name)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host=os.environ.get("MT_HOST", "127.0.0.1"),
                port=int(os.environ.get("MT_PORT", "8790")), access_log=None)
