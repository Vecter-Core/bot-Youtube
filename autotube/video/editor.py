"""Các thao tác dựng/chỉnh sửa video bằng FFmpeg.

Gồm cả các bước cho pipeline tự động (tạo clip cảnh, ghép có chuyển cảnh, trộn nhạc,
in phụ đề) và các công cụ chỉnh sửa rời mà AI có thể tự gọi (cắt, ghép, đổi tốc độ,
chèn chữ, tách âm thanh, trích khung hình...).
"""
from __future__ import annotations

import logging
import random
import shutil
import tempfile
from pathlib import Path

from . import ffmpeg as ff
from .text_render import text_overlay_png

log = logging.getLogger(__name__)

RESOLUTIONS = {
    "landscape": (1920, 1080),
    "shorts": (1080, 1920),
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "720p": (1280, 720),
}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}

XFADE_TRANSITIONS = [
    "fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown", "circleopen", "circleclose", "radial",
    "smoothleft", "smoothright", "zoomin", "pixelize", "hblur", "distance",
]
EFFECTS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "none"]


def resolution(fmt: str) -> tuple[int, int]:
    return RESOLUTIONS.get(fmt, RESOLUTIONS["landscape"])


def media_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "unknown"


def _venc(crf: int = 20, preset: str = "medium", fps: int = 30) -> list[str]:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(fps)]


# --------------------------------------------------------------------------- clips
def make_scene_clip(
    media: str | Path,
    duration: float,
    out: str | Path,
    size: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    effect: str = "auto",
    media_start: float = 0.0,
    overlay_png: str | Path | None = None,
    crf: int = 20,
    preset: str = "medium",
) -> Path:
    """Biến một ảnh hoặc video thành clip đúng độ dài/độ phân giải, không có tiếng."""
    w, h = size
    duration = max(0.3, float(duration))
    frames = max(1, int(round(duration * fps)))
    kind = media_kind(media)
    args: list[str] = []
    if kind == "image":
        if effect == "auto":
            effect = random.choice(["zoom_in", "zoom_out", "pan_left", "pan_right"])
        W2, H2 = w * 2, h * 2  # phóng to trước để zoompan mượt hơn
        pre = f"scale={W2}:{H2}:force_original_aspect_ratio=increase,crop={W2}:{H2},setsar=1"
        n = frames
        if effect == "zoom_in":
            zp = f"zoompan=z='1+0.18*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        elif effect == "zoom_out":
            zp = f"zoompan=z='1.18-0.18*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        elif effect == "pan_left":
            zp = f"zoompan=z='1.15':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'"
        elif effect == "pan_right":
            zp = f"zoompan=z='1.15':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'"
        else:
            zp = "zoompan=z='1'"
        vf = f"{pre},{zp}:d={n}:s={w}x{h}:fps={fps},setsar=1,format=yuv420p"
        args += ["-i", str(media)]
    elif kind == "video":
        src_dur = ff.duration(media) or 0
        start = media_start if src_dur and media_start < src_dur - 0.5 else 0.0
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p"
        )
        args += ["-stream_loop", "-1", "-ss", f"{start:.3f}", "-i", str(media)]
    else:
        raise ValueError(f"Không hỗ trợ file hình ảnh/video: {media}")

    if overlay_png:
        args += ["-loop", "1", "-i", str(overlay_png)]
        fc = f"[0:v]{vf}[base];[1:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1[ov];[base][ov]overlay=0:0:format=auto[v]"
        args += ["-filter_complex", fc, "-map", "[v]"]
    else:
        args += ["-vf", vf]
    args += ["-t", f"{duration:.3f}", "-frames:v", str(frames), "-an", *_venc(crf, preset, fps), str(out)]
    ff.run(args, desc=f"tạo clip cảnh {Path(out).name}")
    return Path(out)


def transition_overlaps(transitions: list[str], transition_duration: float, fps: int = 30) -> list[float]:
    """Số giây chồng lấn tại mỗi điểm nối (dùng để bù độ dài clip cho khớp giọng đọc)."""
    if transition_duration <= 0 or not any(t and t != "none" for t in transitions):
        return [0.0] * len(transitions)
    return [transition_duration if t and t != "none" else 1.0 / fps for t in transitions]


def concat_clips(
    clips: list[str | Path],
    out: str | Path,
    transitions: list[str] | str = "fade",
    transition_duration: float = 0.5,
    fps: int = 30,
    crf: int = 20,
    preset: str = "medium",
) -> Path:
    """Ghép các clip (cùng độ phân giải, không tiếng) kèm hiệu ứng chuyển cảnh xfade.

    Mỗi chuyển cảnh chồng lên nhau vài phần giây -> video ngắn đi. Pipeline bù trừ bằng
    cách kéo dài mỗi clip (trừ clip cuối) thêm đúng `transition_overlaps(...)[i]` giây.
    """
    clips = [Path(c) for c in clips]
    if not clips:
        raise ValueError("Không có clip nào để ghép")
    if isinstance(transitions, str):
        transitions = [transitions] * (len(clips) - 1)
    overlaps = transition_overlaps(transitions, transition_duration, fps)
    if len(clips) == 1 or not any(overlaps):
        lst = Path(out).with_suffix(".txt")
        lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
        ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", str(out)], desc="ghép clip")
        lst.unlink(missing_ok=True)
        return Path(out)

    durations = [ff.duration(c) for c in clips]
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]
    parts, prev, acc = [], "[0:v]", durations[0]
    for i in range(1, len(clips)):
        tr = transitions[i - 1] if transitions[i - 1] in XFADE_TRANSITIONS else "fade"
        ov = overlaps[i - 1]
        offset = max(0.0, acc - ov)
        label = f"[x{i}]"
        parts.append(f"{prev}[{i}:v]xfade=transition={tr}:duration={ov:.3f}:offset={offset:.3f}{label}")
        acc = offset + durations[i]
        prev = label
    args += ["-filter_complex", ";".join(parts), "-map", prev, "-an", *_venc(crf, preset, fps), str(out)]
    ff.run(args, desc="ghép clip có chuyển cảnh")
    return Path(out)


# --------------------------------------------------------------------------- audio
def build_voiceover(audio_files: list[str | Path | None], slot_durations: list[float], out: str | Path) -> Path:
    """Nối giọng đọc từng cảnh, mỗi cảnh được đệm im lặng cho đủ độ dài slot."""
    args: list[str] = []
    parts = []
    idx = 0
    for i, (af, dur) in enumerate(zip(audio_files, slot_durations)):
        if af:
            args += ["-i", str(af)]
            src = f"[{idx}:a]"
            idx += 1
        else:
            args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
            src = f"[{idx}:a]"
            idx += 1
        parts.append(
            f"{src}aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f},asetpts=N/SR/TB[a{i}]"
        )
    n = len(slot_durations)
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    ff.run(args, desc="ghép giọng đọc")
    return Path(out)


def mix_music(
    voice: str | Path | None,
    music: str | Path | None,
    out: str | Path,
    total_duration: float,
    music_volume: float = 0.12,
    ducking: bool = True,
    loudnorm: bool = True,
) -> Path:
    """Trộn giọng đọc với nhạc nền (lặp nhạc, fade in/out, tự giảm nhạc khi có lời)."""
    D = max(0.5, float(total_duration))
    fade_out = min(2.5, D / 4)
    norm = ",loudnorm=I=-14:TP=-1.5:LRA=11" if loudnorm and ff.has_filter("loudnorm") else ""
    args: list[str] = []
    if voice:
        args += ["-i", str(voice)]
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
    if voice and music:
        m_idx = 1
        mchain = (
            f"[{m_idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume={music_volume},"
            f"atrim=0:{D:.3f},asetpts=N/SR/TB,afade=t=in:d=1,afade=t=out:st={D - fade_out:.3f}:d={fade_out:.3f}[m]"
        )
        vchain = f"[0:a]aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={D:.3f},atrim=0:{D:.3f}"
        if ducking and ff.has_filter("sidechaincompress"):
            fc = (
                f"{vchain},asplit=2[v1][v2];{mchain};"
                f"[m][v2]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[md];"
                f"[v1][md]amix=inputs=2:duration=first:normalize=0{norm}[out]"
            )
        else:
            fc = f"{vchain}[v1];{mchain};[v1][m]amix=inputs=2:duration=first:normalize=0{norm}[out]"
    elif voice:
        fc = f"[0:a]aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={D:.3f},atrim=0:{D:.3f}{norm}[out]"
    elif music:
        fc = (
            f"[0:a]aresample=44100,aformat=channel_layouts=stereo,volume={max(music_volume, 0.5)},"
            f"atrim=0:{D:.3f},afade=t=in:d=1,afade=t=out:st={D - fade_out:.3f}:d={fade_out:.3f}[out]"
        )
    else:
        args += ["-f", "lavfi", "-t", f"{D:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        fc = "[0:a]anull[out]"
    args += ["-filter_complex", fc, "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    ff.run(args, desc="trộn âm thanh")
    return Path(out)


# --------------------------------------------------------------------------- final
def render_final(
    video: str | Path,
    audio: str | Path | None,
    out: str | Path,
    ass_file: str | Path | None = None,
    fonts_dir: str | None = None,
    crf: int = 20,
    preset: str = "medium",
    fps: int = 30,
) -> Path:
    """Ghép hình + tiếng, in cứng phụ đề (ASS) nếu có -> MP4 sẵn sàng đăng YouTube."""
    video, out = Path(video).resolve(), Path(out).resolve()
    workdir = None
    args: list[str] = ["-i", str(video)]
    if audio:
        args += ["-i", str(Path(audio).resolve())]
    vf = None
    if ass_file and ff.has_filter("subtitles"):
        # chạy ffmpeg trong thư mục tạm, dùng tên file tương đối để tránh lỗi escape đường dẫn (Windows)
        workdir = Path(tempfile.mkdtemp(prefix="autotube_sub_"))
        shutil.copy(ass_file, workdir / "subs.ass")
        vf = "subtitles=subs.ass"
        if fonts_dir and Path(fonts_dir).exists():
            fdir = workdir / "fonts"
            fdir.mkdir()
            for f in Path(fonts_dir).glob("*.[ot]tf"):
                shutil.copy(f, fdir / f.name)
            vf += ":fontsdir=fonts"
    if vf:
        args += ["-vf", vf]
    args += ["-map", "0:v:0"]
    if audio:
        args += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    args += [*_venc(crf, preset, fps), "-movflags", "+faststart", str(out)]
    try:
        ff.run(args, desc="xuất video cuối", cwd=workdir)
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return out


# --------------------------------------------------------------------------- tools rời cho AI
def trim(src: str | Path, out: str | Path, start: float, end: float | None = None) -> Path:
    args = ["-ss", f"{start:.3f}", "-i", str(src)]
    if end is not None and end > start:
        args += ["-t", f"{end - start:.3f}"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)]
    ff.run(args, desc="cắt video")
    return Path(out)


def join_videos(inputs: list[str | Path], out: str | Path, fmt: str = "landscape", fps: int = 30) -> Path:
    """Ghép nhiều video bất kỳ (khác độ phân giải/có hoặc không có tiếng) thành một."""
    w, h = resolution(fmt)
    args: list[str] = []
    parts = []
    for i, src in enumerate(inputs):
        info = ff.probe(src)
        args += ["-i", str(src)]
        parts.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p[v{i}]"
        )
        if info["has_audio"]:
            parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
        else:
            d = info["duration"] or 1
            parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{d:.3f}[a{i}]")
    n = len(inputs)
    parts.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", *_venc(20, "veryfast", fps),
             "-c:a", "aac", str(out)]
    ff.run(args, desc="ghép video")
    return Path(out)


def change_speed(src: str | Path, out: str | Path, factor: float) -> Path:
    factor = max(0.25, min(4.0, float(factor)))
    atempo, f = [], factor
    while f > 2.0:
        atempo.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        atempo.append("atempo=0.5")
        f /= 0.5
    atempo.append(f"atempo={f:.4f}")
    has_audio = ff.probe(src)["has_audio"]
    args = ["-i", str(src), "-filter:v", f"setpts=PTS/{factor:.4f}"]
    args += ["-filter:a", ",".join(atempo)] if has_audio else ["-an"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    ff.run(args, desc="đổi tốc độ")
    return Path(out)


def add_text(src: str | Path, out: str | Path, text: str, start: float = 0, end: float | None = None,
             position: str = "top") -> Path:
    info = ff.probe(src)
    w, h = info["width"] or 1920, info["height"] or 1080
    png = Path(out).with_suffix(".overlay.png")
    text_overlay_png(text, (w, h), png, position=position)
    end = end if end is not None else info["duration"]
    fc = f"[0:v][1:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[v]"
    args = ["-i", str(src), "-loop", "1", "-i", str(png), "-filter_complex", fc, "-map", "[v]"]
    if info["has_audio"]:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += ["-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    ff.run(args, desc="chèn chữ")
    png.unlink(missing_ok=True)
    return Path(out)


def extract_audio(src: str | Path, out: str | Path) -> Path:
    ff.run(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(out)], desc="tách âm thanh")
    return Path(out)


def extract_frame(src: str | Path, out: str | Path, t: float = 1.0) -> Path:
    ff.run(["-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)], desc="trích khung hình")
    return Path(out)


def replace_audio(video: str | Path, audio: str | Path, out: str | Path, mix: bool = False, volume: float = 1.0) -> Path:
    has_audio = ff.probe(video)["has_audio"]
    if mix and has_audio:
        fc = f"[1:a]volume={volume}[b];[0:a][b]amix=inputs=2:duration=first:normalize=0[a]"
        args = ["-i", str(video), "-stream_loop", "-1", "-i", str(audio), "-filter_complex", fc,
                "-map", "0:v", "-map", "[a]"]
    else:
        args = ["-i", str(video), "-stream_loop", "-1", "-i", str(audio), "-filter_complex", f"[1:a]volume={volume}[a]",
                "-map", "0:v", "-map", "[a]", "-shortest"]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]
    ff.run(args, desc="thay/trộn âm thanh")
    return Path(out)


def reformat(src: str | Path, out: str | Path, fmt: str = "shorts", mode: str = "crop") -> Path:
    """Đổi khung hình (vd. 16:9 -> 9:16 cho Shorts). mode=crop (cắt giữa) hoặc blur (nền mờ)."""
    w, h = resolution(fmt)
    if mode == "blur":
        fc = (
            f"[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:2[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
    else:
        fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1[v]"
    args = ["-i", str(src), "-filter_complex", fc, "-map", "[v]"]
    if ff.probe(src)["has_audio"]:
        args += ["-map", "0:a", "-c:a", "aac"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)]
    ff.run(args, desc="đổi khung hình")
    return Path(out)
