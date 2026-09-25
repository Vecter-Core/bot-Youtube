"""Lồng tiếng (Text-to-Speech).

Engine:
  * edge  : Microsoft Edge TTS — miễn phí, giọng tự nhiên, có mốc thời gian từng từ (cần internet)
  * piper : Piper TTS — chạy offline 100% trên máy (https://github.com/rhasspy/piper)
  * dummy : tạo khoảng lặng đúng độ dài ước tính — để chạy thử khi chưa có TTS
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ROOT
from ..video import ffmpeg as ff

log = logging.getLogger(__name__)


@dataclass
class TTSResult:
    path: Path
    duration: float
    words: list[dict] = field(default_factory=list)  # [{"text", "start", "end"}]


def estimate_word_timings(text: str, duration: float, lead: float = 0.05) -> list[dict]:
    """Ước lượng thời gian từng từ theo độ dài ký tự (khi TTS không trả mốc thời gian)."""
    words = text.split()
    if not words or duration <= 0:
        return []
    # dấu câu = nghỉ thêm một chút
    weights = [len(w) + 1 + (3 if re.search(r"[.,!?;:…]$", w) else 0) for w in words]
    usable = max(0.1, duration - lead * 2)
    total = sum(weights)
    t, out = lead, []
    for w, wt in zip(words, weights):
        d = usable * wt / total
        out.append({"text": w, "start": round(t, 3), "end": round(t + d, 3)})
        t += d
    return out


def clean_for_tts(text: str) -> str:
    text = re.sub(r"[*_#`~>|\[\]{}]", " ", text)   # bỏ ký hiệu markdown
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


class BaseTTS:
    name = "base"

    def synthesize(self, text: str, out: str | Path) -> TTSResult:  # pragma: no cover
        raise NotImplementedError

    def list_voices(self, language: str = "") -> list[str]:
        return []


class EdgeTTS(BaseTTS):
    name = "edge"

    def __init__(self, voice: str = "vi-VN-HoaiMyNeural", rate: str = "+0%", pitch: str = "+0Hz"):
        self.voice, self.rate, self.pitch = voice, rate, pitch

    async def _run(self, text: str, out: Path, voice: str) -> list[dict]:
        import edge_tts

        kwargs = dict(rate=self.rate, pitch=self.pitch)
        try:
            comm = edge_tts.Communicate(text, voice, boundary="WordBoundary", **kwargs)
        except TypeError:  # edge-tts bản cũ
            comm = edge_tts.Communicate(text, voice, **kwargs)
        words = []
        with open(out, "wb") as fh:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append({"text": chunk["text"], "start": round(start, 3),
                                  "end": round(start + chunk["duration"] / 1e7, 3)})
        return words

    def synthesize(self, text: str, out: str | Path) -> TTSResult:
        out = Path(out).with_suffix(".mp3")
        text = clean_for_tts(text)
        last: Exception | None = None
        for attempt in range(3):
            try:
                words = asyncio.run(self._run(text, out, self.voice))
                if out.exists() and out.stat().st_size > 0:
                    dur = ff.duration(out)
                    if not words:
                        words = estimate_word_timings(text, dur)
                    return TTSResult(out, dur, words)
            except Exception as exc:  # mạng chập chờn -> thử lại
                last = exc
                log.warning("Edge TTS lỗi (lần %d): %s", attempt + 1, exc)
        raise RuntimeError(f"Edge TTS thất bại: {last}")

    def list_voices(self, language: str = "") -> list[str]:
        import edge_tts

        voices = asyncio.run(edge_tts.list_voices())
        lang = language.lower()
        return [f"{v['ShortName']} ({v.get('Gender', '')})" for v in voices if not lang or v["Locale"].lower().startswith(lang)]


class PiperTTS(BaseTTS):
    name = "piper"

    def __init__(self, model: str, length_scale: float = 1.0):
        p = Path(model)
        self.model = p if p.is_absolute() else ROOT / p
        self.length_scale = length_scale
        if not self.model.exists():
            raise FileNotFoundError(
                f"Không thấy giọng Piper: {self.model}. Chạy: python scripts/download_tools.py --piper"
            )

    def _cmd(self, out: Path) -> list[str]:
        exe = "piper.exe" if os.name == "nt" else "piper"
        args = ["-m", str(self.model), "-f", str(out)]
        if abs(self.length_scale - 1.0) > 1e-3:
            args += ["--length_scale", str(self.length_scale)]
        for cand in (ROOT / "tools" / "piper" / exe, ROOT / "tools" / "piper" / "piper" / exe):
            if cand.exists():  # bản chạy tải từ GitHub (scripts/download_tools.py)
                return [str(cand), *args]
        if shutil.which("piper"):
            return ["piper", *args]
        return [sys.executable, "-m", "piper", *args]  # gói pip "piper-tts"

    def synthesize(self, text: str, out: str | Path) -> TTSResult:
        out = Path(out).with_suffix(".wav")
        text = clean_for_tts(text)
        proc = subprocess.run(self._cmd(out), input=text, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"Piper lỗi: {proc.stderr[-1000:]}")
        dur = ff.duration(out)
        return TTSResult(out, dur, estimate_word_timings(text, dur))

    def list_voices(self, language: str = "") -> list[str]:
        vdir = ROOT / "tools" / "piper" / "voices"
        return [p.name for p in vdir.glob("*.onnx")] if vdir.exists() else []


class DummyTTS(BaseTTS):
    """Không phát âm — tạo khoảng lặng (tốc độ đọc ước tính ~14 ký tự/giây)."""

    name = "dummy"

    def synthesize(self, text: str, out: str | Path) -> TTSResult:
        out = Path(out).with_suffix(".wav")
        text = clean_for_tts(text)
        dur = max(1.0, len(text) / 14.0)
        ff.run(["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=mono", str(out)], desc="dummy tts")
        return TTSResult(out, dur, estimate_word_timings(text, dur))


def get_tts(cfg: dict) -> BaseTTS:
    engine = (cfg.get("engine") or "edge").lower()
    if engine == "edge":
        return EdgeTTS(cfg.get("voice") or "vi-VN-HoaiMyNeural", cfg.get("rate") or "+0%", cfg.get("pitch") or "+0Hz")
    if engine == "piper":
        return PiperTTS(cfg.get("piper_model") or "", float(cfg.get("length_scale", 1.0)))
    if engine == "dummy":
        return DummyTTS()
    raise ValueError(f"TTS engine không hỗ trợ: {engine}")
