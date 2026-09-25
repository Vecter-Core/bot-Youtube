"""Bao bọc FFmpeg: tìm file chạy, gọi lệnh, đọc thông tin media."""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..config import ROOT

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def _exe_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


@functools.lru_cache(maxsize=None)
def ffmpeg_bin() -> str:
    """Thứ tự tìm: biến môi trường -> tools/ffmpeg (tải từ GitHub) -> PATH -> imageio-ffmpeg."""
    env = os.environ.get("FFMPEG_BINARY")
    if env and Path(env).exists():
        return env
    for cand in (ROOT / "tools" / "ffmpeg" / "bin" / _exe_name("ffmpeg"), ROOT / "tools" / "ffmpeg" / _exe_name("ffmpeg")):
        if cand.exists():
            return str(cand)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise FFmpegError(
            "Không tìm thấy FFmpeg. Chạy: python scripts/download_tools.py  hoặc  pip install imageio-ffmpeg"
        ) from exc


@functools.lru_cache(maxsize=None)
def ffprobe_bin() -> str | None:
    ff = Path(ffmpeg_bin())
    sibling = ff.with_name(_exe_name("ffprobe"))
    if sibling.exists():
        return str(sibling)
    return shutil.which("ffprobe")


def run(args: list[str], desc: str = "ffmpeg", timeout: int | None = None, cwd: str | Path | None = None) -> str:
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-loglevel", "error", *map(str, args)]
    log.debug("RUN %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd
    )
    if proc.returncode != 0:
        raise FFmpegError(f"{desc} thất bại:\n{proc.stderr[-3000:]}")
    return proc.stderr


@functools.lru_cache(maxsize=None)
def has_filter(name: str) -> bool:
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace")
    return re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout, flags=re.M) is not None


def probe(path: str | Path) -> dict:
    """Trả về {duration, width, height, has_audio, has_video, fps}."""
    path = str(path)
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False, "has_video": False, "fps": 0.0}
    fp = ffprobe_bin()
    if fp:
        proc = subprocess.run(
            [fp, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            info["duration"] = float(data.get("format", {}).get("duration") or 0)
            for st in data.get("streams", []):
                if st.get("codec_type") == "video" and not info["has_video"]:
                    info["has_video"] = True
                    info["width"], info["height"] = int(st.get("width", 0)), int(st.get("height", 0))
                    num, _, den = (st.get("avg_frame_rate") or "0/1").partition("/")
                    info["fps"] = float(num) / float(den or 1) if float(den or 1) else 0.0
                elif st.get("codec_type") == "audio":
                    info["has_audio"] = True
            return info
    # Không có ffprobe (vd. imageio-ffmpeg) -> đọc stderr của ffmpeg -i
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace")
    err = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    for line in err.splitlines():
        if "Stream #" not in line:
            continue
        if "Video:" in line and not info["has_video"]:
            info["has_video"] = True
            dm = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
            if dm:
                info["width"], info["height"] = int(dm.group(1)), int(dm.group(2))
            fm = re.search(r"([\d.]+)\s*fps", line)
            if fm:
                info["fps"] = float(fm.group(1))
        elif "Audio:" in line:
            info["has_audio"] = True
    if info["duration"] == 0 and info["has_video"] and not info["has_audio"]:
        # ảnh tĩnh
        info["duration"] = 0.0
    return info


def duration(path: str | Path) -> float:
    return probe(path)["duration"]


def escape_filter_path(path: str | Path) -> str:
    """Escape đường dẫn để dùng trong filtergraph (subtitles=..., fontsdir=...)."""
    p = str(Path(path).resolve()).replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")
