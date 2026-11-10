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
    if app["queue"] >= MAX_QUEUE:
        return web.json_response({"error": "busy"}, status=503)

    app["queue"] += 1
    started = time.perf_counter()
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(app["pool"], app["engine"].translate, text, source, target)
    except EngineError as exc:
        log.info("rad: %s %s->%s (%s)", exc.code, source, target, exc)
        return web.json_response({"error": exc.code, "message": str(exc)}, status=422)
    except Exception:
        log.exception("kutilmagan xato")
        return web.json_response({"error": "internal"}, status=500)
    finally:
        app["queue"] -= 1

    ms = int((time.perf_counter() - started) * 1000)
    log.info("ok %s->%s chars=%d seg=%d ms=%d", result.source, target, len(text), result.segments, ms)
    return web.json_response({
        "text": result.text, "source": result.source, "model": app["engine"].model_name,
        "segments": result.segments, "ms": ms,
    })


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "model": request.app["engine"].model_name,
                              "queue": request.app["queue"]})


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
    app["pool"] = ThreadPoolExecutor(max_workers=1)
    app["queue"] = 0
    app.router.add_post("/translate", translate)
    app.router.add_get("/health", health)
    log.info("tayyor: %s", engine.model_name)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host=os.environ.get("MT_HOST", "127.0.0.1"),
                port=int(os.environ.get("MT_PORT", "8790")), access_log=None)
