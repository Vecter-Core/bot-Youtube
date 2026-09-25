"""Chọn nhạc nền từ thư viện local, hoặc tự tổng hợp một đoạn nhạc nền đơn giản."""
from __future__ import annotations

import random
import re
from pathlib import Path

from ..video import ffmpeg as ff

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

MOOD_SYNONYMS = {
    "calm": ["calm", "chill", "relax", "soft", "ambient", "lofi", "piano", "peaceful", "nhe", "em"],
    "epic": ["epic", "cinematic", "dramatic", "trailer", "powerful", "hung"],
    "happy": ["happy", "upbeat", "fun", "bright", "joy", "vui"],
    "sad": ["sad", "emotional", "melancholy", "buon"],
    "mystery": ["mystery", "dark", "suspense", "horror", "tension", "bi-an"],
    "tech": ["tech", "electronic", "future", "synth", "corporate"],
    "inspiring": ["inspiring", "motivational", "uplifting", "hope"],
}

CHORDS = {  # tần số (Hz) cho vài hợp âm pad
    "calm": [[220.0, 277.18, 329.63], [196.0, 246.94, 293.66]],
    "happy": [[261.63, 329.63, 392.0], [293.66, 369.99, 440.0]],
    "sad": [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63]],
    "mystery": [[146.83, 174.61, 220.0], [138.59, 164.81, 207.65]],
    "epic": [[110.0, 164.81, 220.0], [98.0, 146.83, 196.0]],
}


def list_music(music_dir: str | Path) -> list[Path]:
    d = Path(music_dir)
    if not d.exists():
        return []
    return sorted(p for p in d.rglob("*") if p.suffix.lower() in AUDIO_EXT)


def pick_music(music_dir: str | Path, mood: str = "", seed: str | None = None) -> Path | None:
    files = list_music(music_dir)
    if not files:
        return None
    rnd = random.Random(seed)
    mood = (mood or "").lower()
    keys = set(re.findall(r"[a-z]+", mood))
    for base, syns in MOOD_SYNONYMS.items():
        if base in keys or keys & set(syns):
            keys |= set(syns) | {base}
    if keys:
        matched = [f for f in files if keys & set(re.findall(r"[a-z]+", str(f.relative_to(music_dir)).lower()))]
        if matched:
            return rnd.choice(matched)
    return rnd.choice(files)


def synth_ambient(out: str | Path, duration: float, mood: str = "calm") -> Path:
    """Tạo nhạc pad ambient đơn giản bằng FFmpeg (khi thư viện nhạc trống)."""
    chords = CHORDS.get(mood if mood in CHORDS else "calm")
    seg = 8.0
    n_seg = max(1, int(duration // seg) + 1)
    expr_parts = []
    for i in range(n_seg):
        freqs = chords[i % len(chords)]
        t0, t1 = i * seg, (i + 1) * seg
        tone = "+".join(f"sin(2*PI*{f}*t)+0.3*sin(2*PI*{f * 2}*t)" for f in freqs)
        expr_parts.append(f"between(t,{t0},{t1})*({tone})")
    expr = f"0.06*({'+'.join(expr_parts)})*(0.75+0.25*sin(2*PI*0.2*t))"
    af = "lowpass=f=1200,aecho=0.8:0.7:120|240:0.35|0.25,afade=t=in:d=2"
    ff.run(["-f", "lavfi", "-i", f"aevalsrc='{expr}|{expr}':s=44100:d={duration:.2f}", "-af", af, str(out)],
           desc="tổng hợp nhạc nền")
    return Path(out)
