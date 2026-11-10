"""NLLB-200 (CTranslate2, int8, CPU) tarjima dvigateli — HTTP'siz, sinash oson.

Asosiy qarorlar:
- NLLB gap darajasida o'qitilgan: uzun matn qatorlarga, qatorlar gaplarga
  bo'linadi, hammasi BITTA batch'da tarjima qilinadi va qayta yig'iladi
  (qator tuzilishi saqlanadi).
- Harfsiz bo'laklar (raqam, emoji, URL, @username) tarjimaga berilmaydi —
  NLLB ularda "gallyutsinatsiya" qiladi.
- Chiqish kirishdan shubhali uzun yoki takrorlanuvchi bo'lsa — o'sha bo'lak
  qattiqroq takror-jarimasi bilan qayta tarjima qilinadi, baribir yomon
  bo'lsa `SuspiciousOutput` (bot boshqa provayderga o'tadi).
- `auto` manba: `mt/lid.py` (fastText lid.176 + o'zbek qoidasi); ishonch past bo'lsa
  `DetectionFailed` (bot boshqa provayderga o'tadi).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mt.langs import NLLB_CODES, NO_SPACE_JOIN, has_cyrillic, uz_cyr_to_lat
from mt.lid import LanguageDetector

# Gap chegarasi: tinish belgisi (+ yopuvchi qo'shtirnoq/qavs) dan keyin probel.
_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])[\"'»”)\]]*\s+")
_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")
_HAS_LETTER = re.compile(r"[^\W\d_]")
_PASSTHROUGH = re.compile(r"(?:https?://\S+|www\.\S+|@\w+|#\w+|\S+@\S+\.\w+)")

MAX_SEGMENT_TOKENS = 200
MIN_DETECT_CHARS = 12
MIN_DETECT_PROB = 0.60


class EngineError(Exception):
    code = "engine_error"


class UnsupportedLanguage(EngineError):
    code = "unsupported_language"


class DetectionFailed(EngineError):
    code = "detection_failed"


class SuspiciousOutput(EngineError):
    code = "suspicious_output"


@dataclass
class Result:
    text: str
    source: str
    segments: int


def _is_passthrough(seg: str) -> bool:
    return not _HAS_LETTER.search(_PASSTHROUGH.sub("", seg))


def _repetitive(tokens: list[str], n: int = 3, limit: int = 4) -> bool:
    if len(tokens) < n * limit:
        return False
    counts: dict[tuple, int] = {}
    for i in range(len(tokens) - n + 1):
        key = tuple(tokens[i : i + n])
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > limit:
            return True
    return False


class Engine:
    def __init__(self, model_dir: str, threads: int = 2, beam_size: int = 2,
                 lid_path: Optional[str] = None):
        import ctranslate2
        from tokenizers import Tokenizer

        self.model_dir = Path(model_dir)
        self.model_name = self.model_dir.name
        self.beam_size = beam_size
        self.translator = ctranslate2.Translator(
            str(self.model_dir), device="cpu", compute_type="int8",
            intra_threads=threads, inter_threads=1,
        )
        self.tok = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self.detector = LanguageDetector(lid_path)
        # CTranslate2 Translator thread-safe, lekin bitta CPU yadrosi
        # to'plamini bo'lishamiz — bir vaqtda bitta so'rov (navbat serverda).
        self._lock = threading.Lock()

    # ── til aniqlash ────────────────────────────────────────────
    def detect(self, text: str) -> tuple[Optional[str], float]:
        return self.detector.detect(text, MIN_DETECT_CHARS)

    # ── bo'laklash ──────────────────────────────────────────────
    def _tokens(self, text: str) -> list[str]:
        return self.tok.encode(text, add_special_tokens=False).tokens

    def _split_long(self, sentence: str) -> list[str]:
        if len(self._tokens(sentence)) <= MAX_SEGMENT_TOKENS:
            return [sentence]
        parts, current = [], ""
        for clause in _CLAUSE_SPLIT.split(sentence):
            candidate = f"{current} {clause}".strip()
            if current and len(self._tokens(candidate)) > MAX_SEGMENT_TOKENS:
                parts.append(current)
                current = clause
            else:
                current = candidate
        if current:
            parts.append(current)
        # Vergulsiz juda uzun "gap" — qattiq kesish (kamdan-kam).
        final = []
        for p in parts:
            toks = self._tokens(p)
            if len(toks) <= MAX_SEGMENT_TOKENS:
                final.append(p)
                continue
            words = p.split()
            step = max(1, len(words) * MAX_SEGMENT_TOKENS // len(toks))
            final.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
        return final

    def _layout(self, text: str) -> list[list[str]]:
        """Matn -> qatorlar -> bo'laklar (bo'sh qator = bo'sh ro'yxat)."""
        layout = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                layout.append([])
                continue
            segs: list[str] = []
            for sentence in _SENT_SPLIT.split(line):
                if sentence.strip():
                    segs.extend(self._split_long(sentence.strip()))
            layout.append(segs)
        return layout

    # ── tarjima ─────────────────────────────────────────────────
    def _run(self, batch: list[list[str]], tgt_code: str, strict: bool) -> list[list[str]]:
        max_len = max(len(s) for s in batch)
        results = self.translator.translate_batch(
            batch,
            target_prefix=[[tgt_code]] * len(batch),
            beam_size=self.beam_size,
            max_batch_size=16,
            max_decoding_length=min(512, int(max_len * 3) + 16),
            repetition_penalty=1.25 if strict else 1.0,
            no_repeat_ngram_size=3 if strict else 0,
        )
        return [r.hypotheses[0][1:] for r in results]  # 0-token — maqsad til kodi

    def _bad(self, src: list[str], out: list[str]) -> bool:
        # Kirishning o'zi takrorlansa (masalan "ha ha ha ..."), chiqishdagi takror normal.
        return not out or len(out) > 3 * len(src) + 12 or (_repetitive(out) and not _repetitive(src))

    def translate(self, text: str, source: str, target: str) -> Result:
        if target not in NLLB_CODES:
            raise UnsupportedLanguage(f"maqsad til qo'llanmaydi: {target}")
        if source == "auto":
            code, prob = self.detect(text)
            if code not in NLLB_CODES or prob < MIN_DETECT_PROB:
                raise DetectionFailed(f"til aniqlanmadi ({code}, {prob:.2f})")
            source = code
        if source not in NLLB_CODES:
            raise UnsupportedLanguage(f"manba til qo'llanmaydi: {source}")
        if source == target:
            return Result(text=text, source=source, segments=0)

        if source == "uz" and has_cyrillic(text):
            text = uz_cyr_to_lat(text)

        src_code, tgt_code = NLLB_CODES[source], NLLB_CODES[target]
        layout = self._layout(text)

        jobs: list[tuple[int, int]] = []
        batch: list[list[str]] = []
        out_layout: list[list[str]] = [list(segs) for segs in layout]
        for li, segs in enumerate(layout):
            for si, seg in enumerate(segs):
                if _is_passthrough(seg):
                    continue
                jobs.append((li, si))
                batch.append([src_code] + self._tokens(seg) + ["</s>"])

        if batch:
            with self._lock:
                outs = self._run(batch, tgt_code, strict=False)
                retry = [i for i, (s, o) in enumerate(zip(batch, outs)) if self._bad(s, o)]
                if retry:
                    fixed = self._run([batch[i] for i in retry], tgt_code, strict=True)
                    for i, o in zip(retry, fixed):
                        if self._bad(batch[i], o):
                            raise SuspiciousOutput("model chiqishi shubhali (takror/uzun)")
                        outs[i] = o
            for (li, si), toks in zip(jobs, outs):
                ids = [self.tok.token_to_id(t) for t in toks]
                out_layout[li][si] = self.tok.decode([i for i in ids if i is not None],
                                                     skip_special_tokens=True).strip()

        joiner = "" if target in NO_SPACE_JOIN else " "
        translated = "\n".join(joiner.join(segs) for segs in out_layout).strip()
        return Result(text=translated, source=source, segments=len(batch))
