"""Tạo phụ đề SRT (để upload YouTube) và ASS (để in cứng lên video, có hiệu ứng karaoke)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ..video import ffmpeg as ff
from ..video.text_render import draw_text_block, find_font

log = logging.getLogger(__name__)


def group_words(words: list[dict], max_words: int = 7, max_duration: float = 3.5) -> list[dict]:
    """Gom từ thành từng dòng phụ đề; ngắt ở dấu câu, giới hạn số từ/độ dài."""
    cues, cur = [], []
    for w in words:
        cur.append(w)
        text = w["text"]
        end_sentence = bool(re.search(r"[.!?…]$", text))
        soft_break = bool(re.search(r"[,;:]$", text)) and len(cur) >= max(3, max_words // 2)
        too_long = len(cur) >= max_words or (cur[-1]["end"] - cur[0]["start"]) >= max_duration
        if end_sentence or soft_break or too_long:
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    out = []
    for c in cues:
        out.append({"start": c[0]["start"], "end": c[-1]["end"], "words": c,
                    "text": " ".join(w["text"] for w in c)})
    # kéo dài cue tới cue kế (tránh nhấp nháy), tối đa +0.6s
    for a, b in zip(out, out[1:]):
        a["end"] = max(a["end"], min(b["start"], a["end"] + 0.6))
    return out


def _ts_srt(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(cues: list[dict], out: str | Path) -> Path:
    lines = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}", c["text"], ""]
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return Path(out)


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    c = hex_color.lstrip("#")
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def write_ass(
    cues: list[dict],
    out: str | Path,
    size: tuple[int, int],
    font_name: str = "Arial",
    font_size: int = 0,
    color: str = "#FFFFFF",
    highlight: str = "#FFD400",
    outline: str = "#000000",
    position: str = "bottom",
    karaoke: bool = True,
) -> Path:
    w, h = size
    vertical = h > w
    fs = font_size or int(h * (0.055 if not vertical else 0.042))
    align = 5 if position == "center" else 2
    margin_v = int(h * (0.08 if not vertical else 0.22)) if align == 2 else 0
    # Karaoke \k: chữ đã đọc = PrimaryColour, chưa đọc = SecondaryColour
    primary = _ass_color(highlight if karaoke else color)
    secondary = _ass_color(color)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{fs},{primary},{secondary},{_ass_color(outline)},&H80000000,-1,0,0,0,100,100,0,0,1,{max(2, fs // 14)},{max(1, fs // 30)},{align},{int(w * 0.06)},{int(w * 0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for c in cues:
        if karaoke and c.get("words"):
            parts, t = [], c["start"]
            for wd in c["words"]:
                gap = max(0, int(round((wd["start"] - t) * 100)))
                if gap:
                    parts.append(f"{{\\k{gap}}}")
                dur = max(1, int(round((wd["end"] - wd["start"]) * 100)))
                parts.append(f"{{\\k{dur}}}{_ass_escape(wd['text'])} ")
                t = wd["end"]
            text = "".join(parts).rstrip()
        else:
            text = _ass_escape(c["text"])
        events.append(f"Dialogue: 0,{_ts_ass(c['start'])},{_ts_ass(c['end'])},Default,,0,0,0,,{{\\fad(80,80)}}{text}")
    Path(out).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return Path(out)


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def font_family(font_path: str | None) -> str:
    """Tên họ font (family name) cho libass."""
    if not font_path:
        return "Arial"
    try:
        from PIL import ImageFont

        return ImageFont.truetype(font_path, 20).getname()[0]
    except Exception:
        return Path(font_path).stem.split("-")[0]


def burn_with_pillow(video: str | Path, cues: list[dict], out: str | Path, color: str = "#FFFFFF",
                     position: str = "bottom") -> Path:
    """Phương án dự phòng khi FFmpeg không có libass: vẽ phụ đề thành ảnh PNG rồi chồng lên."""
    from PIL import Image

    info = ff.probe(video)
    w, h, total = info["width"], info["height"], info["duration"]
    work = Path(out).parent / "_subpng"
    work.mkdir(exist_ok=True)
    blank = work / "blank.png"
    Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(blank)
    entries, t = [], 0.0
    for i, c in enumerate(cues):
        if c["start"] > t:
            entries.append((blank, c["start"] - t))
        png = work / f"c{i:04d}.png"
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        fs = int(h * 0.045)
        box = (int(w * 0.06), int(h * 0.62), int(w * 0.94), int(h * 0.92)) if position != "center" else \
            (int(w * 0.06), int(h * 0.35), int(w * 0.94), int(h * 0.65))
        draw_text_block(img, c["text"], fs, box, color=color, valign="bottom" if position != "center" else "center")
        img.save(png)
        entries.append((png, max(0.04, c["end"] - max(t, c["start"]))))
        t = c["end"]
    if total > t:
        entries.append((blank, total - t))
    lst = work / "list.txt"
    body = "".join(f"file '{p.resolve().as_posix()}'\nduration {d:.3f}\n" for p, d in entries)
    body += f"file '{entries[-1][0].resolve().as_posix()}'\n"
    lst.write_text(body, encoding="utf-8")
    args = ["-i", str(video), "-f", "concat", "-safe", "0", "-i", str(lst),
            "-filter_complex", "[1:v]format=rgba[s];[0:v][s]overlay=0:0:eof_action=pass[v]", "-map", "[v]"]
    if info["has_audio"]:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    ff.run(args, desc="in phụ đề (Pillow)")
    return Path(out)


def whisper_words(audio: str | Path, model_size: str = "small", language: str | None = None) -> list[dict]:
    """Nhận dạng giọng nói bằng faster-whisper (offline) -> mốc thời gian từng từ."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Chưa cài faster-whisper: pip install faster-whisper") from exc
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segments, _ = model.transcribe(str(audio), language=language, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"text": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    return words


def default_font_name() -> str:
    return font_family(find_font())
