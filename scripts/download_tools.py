#!/usr/bin/env python3
"""Tải các công cụ video/âm thanh mã nguồn mở từ GitHub về thư mục tools/.

    python scripts/download_tools.py            # tải tất cả những gì cần
    python scripts/download_tools.py --ffmpeg   # chỉ FFmpeg
    python scripts/download_tools.py --piper    # Piper TTS + giọng tiếng Việt (offline)
    python scripts/download_tools.py --fonts    # font Be Vietnam Pro (hiển thị tiếng Việt đẹp)
    python scripts/download_tools.py --ytdlp    # yt-dlp (tải video — chỉ dùng cho nội dung bạn có quyền)

Nguồn:
  FFmpeg    https://github.com/BtbN/FFmpeg-Builds   (Windows/Linux)
  Piper     https://github.com/rhasspy/piper         + giọng https://huggingface.co/rhasspy/piper-voices
  Font      https://github.com/google/fonts          (Be Vietnam Pro — OFL)
  yt-dlp    https://github.com/yt-dlp/yt-dlp
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
UA = {"User-Agent": "autotube-downloader"}

SYSTEM = platform.system().lower()          # windows | linux | darwin
MACHINE = platform.machine().lower()        # amd64 | x86_64 | arm64 | aarch64

PIPER_VOICES = {
    "vi_VN-vais1000-medium": "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium",
    "vi_VN-25hours_single-low": "vi/vi_VN/25hours_single/low/vi_VN-25hours_single-low",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium",
}
FONTS = {
    "BeVietnamPro-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bevietnampro/BeVietnamPro-Bold.ttf",
    "BeVietnamPro-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/bevietnampro/BeVietnamPro-Regular.ttf",
}


def log(msg: str) -> None:
    print(f"[download] {msg}", flush=True)


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"Tải {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r   {done / total * 100:5.1f}% ({done >> 20} MB)", end="", flush=True)
    if total:
        print()
    return dest


def github_asset(repo: str, patterns: list[str], tag: str = "latest") -> str:
    """Tìm link tải asset trong release GitHub khớp tất cả pattern."""
    api = f"https://api.github.com/repos/{repo}/releases/{'latest' if tag == 'latest' else 'tags/' + tag}"
    req = urllib.request.Request(api, headers={**UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rel = json.load(r)
    for a in rel.get("assets", []):
        name = a["name"].lower()
        if all(p in name for p in patterns):
            return a["browser_download_url"]
    raise RuntimeError(f"Không thấy file phù hợp {patterns} trong release {repo}@{tag}")


def extract_flat(archive: Path, target: Path) -> None:
    """Giải nén; nếu bên trong chỉ có 1 thư mục gốc thì bỏ cấp đó."""
    tmp = Path(tempfile.mkdtemp())
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(archive) as t:
            t.extractall(tmp)
    items = list(tmp.iterdir())
    src = items[0] if len(items) == 1 and items[0].is_dir() else tmp
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    shutil.rmtree(tmp, ignore_errors=True)


def make_executable(folder: Path) -> None:
    if os.name == "nt":
        return
    for p in folder.rglob("*"):
        if p.is_file() and (p.suffix == "" or p.suffix == ".so" or ".so." in p.name):
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------- tools
def get_ffmpeg() -> None:
    target = TOOLS / "ffmpeg"
    if (target / "bin").exists():
        log("FFmpeg đã có sẵn")
        return
    if SYSTEM == "darwin":
        log("macOS: cài FFmpeg bằng Homebrew:  brew install ffmpeg   (hoặc pip install imageio-ffmpeg)")
        return
    if SYSTEM == "windows":
        pats = ["win64", "gpl", ".zip"]
    else:
        pats = ["linuxarm64" if "arm" in MACHINE or "aarch" in MACHINE else "linux64", "gpl", ".tar.xz"]
    try:
        url = github_asset("BtbN/FFmpeg-Builds", ["master-latest", *pats])
    except Exception:
        url = github_asset("BtbN/FFmpeg-Builds", pats)
    arc = download(url, TOOLS / "_dl" / Path(url).name)
    extract_flat(arc, target)
    make_executable(target)
    log(f"FFmpeg -> {target / 'bin'}")


def get_piper(voices: list[str]) -> None:
    target = TOOLS / "piper"
    exe = target / ("piper.exe" if SYSTEM == "windows" else "piper")
    if not exe.exists():
        if SYSTEM == "windows":
            pats = ["windows", "amd64"]
        elif SYSTEM == "darwin":
            pats = ["macos", "aarch64" if "arm" in MACHINE else "x64"]
        else:
            pats = ["linux", "aarch64" if ("arm" in MACHINE or "aarch" in MACHINE) else "x86_64"]
        try:
            url = github_asset("rhasspy/piper", pats)
            arc = download(url, TOOLS / "_dl" / Path(url).name)
            voices_backup = None
            if (target / "voices").exists():
                voices_backup = Path(tempfile.mkdtemp()) / "voices"
                shutil.move(str(target / "voices"), voices_backup)
            extract_flat(arc, target)
            if voices_backup:
                shutil.move(str(voices_backup), target / "voices")
            make_executable(target)
            log(f"Piper -> {target}")
        except Exception as exc:
            log(f"Không tải được Piper binary ({exc}). Thay thế: pip install piper-tts")
    for v in voices:
        base = PIPER_VOICES.get(v)
        if not base:
            log(f"Không biết giọng {v}. Xem danh sách: https://huggingface.co/rhasspy/piper-voices")
            continue
        for ext in (".onnx", ".onnx.json"):
            dest = target / "voices" / f"{v}{ext}"
            if not dest.exists():
                download(f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{base}{ext}", dest)
    log("Giọng Piper sẵn sàng. Đặt tts.engine: piper trong config.yaml để dùng offline.")


def get_fonts() -> None:
    fdir = ROOT / "assets" / "fonts"
    for name, url in FONTS.items():
        if not (fdir / name).exists():
            download(url, fdir / name)
    log(f"Font -> {fdir}")


def get_ytdlp() -> None:
    name = "yt-dlp.exe" if SYSTEM == "windows" else ("yt-dlp_macos" if SYSTEM == "darwin" else "yt-dlp")
    url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{name}"
    dest = TOOLS / "yt-dlp" / ("yt-dlp.exe" if SYSTEM == "windows" else "yt-dlp")
    download(url, dest)
    make_executable(dest.parent)
    log(f"yt-dlp -> {dest}  (chỉ tải nội dung bạn sở hữu hoặc có giấy phép sử dụng)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffmpeg", action="store_true")
    ap.add_argument("--piper", action="store_true")
    ap.add_argument("--fonts", action="store_true")
    ap.add_argument("--ytdlp", action="store_true")
    ap.add_argument("--voices", nargs="*", default=["vi_VN-vais1000-medium"], help=f"giọng Piper: {list(PIPER_VOICES)}")
    ap.add_argument("--all", action="store_true", help="tải tất cả (kể cả Piper, yt-dlp)")
    args = ap.parse_args()
    nothing = not (args.ffmpeg or args.piper or args.fonts or args.ytdlp or args.all)
    TOOLS.mkdir(exist_ok=True)
    steps = []
    if args.ffmpeg or args.all or nothing:
        steps.append(("FFmpeg", get_ffmpeg))
    if args.fonts or args.all or nothing:
        steps.append(("Font", get_fonts))
    if args.piper or args.all:
        steps.append(("Piper", lambda: get_piper(args.voices)))
    if args.ytdlp or args.all:
        steps.append(("yt-dlp", get_ytdlp))
    failed = []
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:
            failed.append(name)
            log(f"LỖI khi tải {name}: {exc}")
    shutil.rmtree(TOOLS / "_dl", ignore_errors=True)
    if failed:
        log(f"Chưa tải được: {', '.join(failed)}. Kiểm tra mạng rồi chạy lại.")
        return 1
    log("Hoàn tất!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
