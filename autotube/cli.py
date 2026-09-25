"""Giao diện dòng lệnh:  python -m autotube <lệnh> [tuỳ chọn]

Lệnh chính:
  init        tạo config.yaml và các thư mục
  doctor      kiểm tra FFmpeg, AI local, TTS, font, YouTube
  auth        đăng nhập kênh YouTube (1 lần)
  ideas       AI gợi ý ý tưởng video
  make        làm 1 video từ A-Z (mặc định: agent tự quyết định)
  edit        yêu cầu AI chỉnh sửa một project có sẵn bằng lời
  resume      làm nốt các bước còn thiếu của một project
  upload      đăng một project đã làm xong
  schedule    chạy tự động theo lịch (vd. mỗi 24 giờ 1 video)
  voices      liệt kê giọng đọc
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from .config import EXAMPLE_CONFIG, ROOT, Config, load_config

log = logging.getLogger("autotube")


def setup_logging(verbose: bool = False, logfile: Path | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows console
        except Exception:
            pass
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S", handlers=handlers,
                        force=True)
    for noisy in ("urllib3", "googleapiclient", "asyncio", "PIL", "google_auth_oauthlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ============================================================================ commands
def cmd_init(args, cfg: Config) -> int:
    target = ROOT / "config.yaml"
    if target.exists() and not args.force:
        print(f"Đã có {target} (dùng --force để ghi đè)")
    else:
        shutil.copy(EXAMPLE_CONFIG, target)
        print(f"Đã tạo {target} — hãy mở file và chỉnh chủ đề kênh, model AI, API key...")
    for d in ("assets/music", "assets/media", "assets/fonts", "credentials", "output", "tools"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    from .llm import LLMClient
    from .video import ffmpeg as ff
    from .video.text_render import find_font

    ok = True

    def line(status: bool | None, name: str, detail: str = ""):
        icon = {True: "[OK]  ", False: "[LỖI]", None: "[--]  "}[status]
        print(f"{icon} {name}: {detail}")

    try:
        line(True, "FFmpeg", ff.ffmpeg_bin())
        line(ff.has_filter("subtitles"), "  libass (in phụ đề)", "có" if ff.has_filter("subtitles") else
             "không có → dùng Pillow thay thế")
        line(ff.has_filter("xfade"), "  xfade (chuyển cảnh)", "có" if ff.has_filter("xfade") else "không có")
    except Exception as exc:
        ok = False
        line(False, "FFmpeg", str(exc))

    llm = LLMClient(cfg.section("llm"))
    try:
        models = llm.list_models()
        has = any(m == llm.model or m.split(":")[0] == llm.model.split(":")[0] for m in models)
        line(True, f"AI local ({llm.provider})", f"{llm.base_url} — {len(models)} model")
        line(has, f"  model '{llm.model}'", "sẵn sàng" if has else
             f"chưa có! Chạy: ollama pull {llm.model}   (đang có: {', '.join(models[:8])})")
        ok &= has
    except Exception as exc:
        ok = False
        line(False, "AI local", str(exc))

    tcfg = cfg.section("tts")
    engine = tcfg.get("engine", "edge")
    if engine == "edge":
        try:
            import edge_tts  # noqa: F401

            line(True, "TTS", f"edge-tts, giọng {tcfg.get('voice')}")
        except ImportError:
            ok = False
            line(False, "TTS", "chưa cài edge-tts (pip install edge-tts)")
    elif engine == "piper":
        p = cfg.resolve(tcfg.get("piper_model", ""))
        line(p.exists(), "TTS", f"piper {p}")
        ok &= p.exists()
    else:
        line(None, "TTS", engine)

    font = find_font(cfg.get_path("subtitles.font", "auto"))
    line(bool(font), "Font tiếng Việt", font or "không thấy — chạy scripts/download_tools.py --fonts")

    mcfg = cfg.section("media")
    line(bool(mcfg.get("pexels_api_key")) or None, "Pexels API", "có key" if mcfg.get("pexels_api_key") else
         "chưa có (khuyên dùng — miễn phí)")
    line(bool(mcfg.get("pixabay_api_key")) or None, "Pixabay API", "có key" if mcfg.get("pixabay_api_key") else "chưa có")
    from .audio.music import list_music

    n_music = len(list_music(cfg.resolve(cfg.get_path("music.dir", "assets/music"))))
    line(True if n_music else None, "Nhạc nền", f"{n_music} bài" if n_music else "thư mục trống → sẽ tự tổng hợp nhạc ambient")

    yt = cfg.section("youtube")
    cs, tk = cfg.resolve(yt.get("client_secrets")), cfg.resolve(yt.get("token_file"))
    line(cs.exists(), "YouTube client_secret", str(cs) if cs.exists() else f"thiếu {cs} (xem README mục YouTube)")
    line(tk.exists() or None, "YouTube đăng nhập", "đã đăng nhập" if tk.exists() else "chưa — chạy: python -m autotube auth")
    print("\nKết luận:", "Sẵn sàng!" if ok else "Còn lỗi cần xử lý ở trên.")
    return 0 if ok else 1


def cmd_auth(args, cfg: Config) -> int:
    from .youtube.uploader import YouTubeUploader

    up = YouTubeUploader(cfg)
    up.credentials(interactive=True)
    info = up.channel_info()
    print("Đăng nhập thành công:", json.dumps(info, ensure_ascii=False))
    return 0


def cmd_ideas(args, cfg: Config) -> int:
    from .studio import Studio

    ideas = Studio(cfg).brainstorm(n=args.count, hint=args.hint or "")
    for i, idea in enumerate(ideas, 1):
        print(f"{i}. [{idea.get('score', '?')}] {idea['topic']}\n   ↳ {idea.get('angle', '')}")
    return 0


def _apply_overrides(args, cfg: Config) -> None:
    if getattr(args, "format", None):
        cfg["video"]["format"] = args.format
    if getattr(args, "duration", None):
        cfg["video"]["target_duration"] = args.duration
    if getattr(args, "model", None):
        cfg["llm"]["model"] = args.model
    if getattr(args, "tts", None):
        cfg["tts"]["engine"] = args.tts
    if getattr(args, "privacy", None):
        cfg["youtube"]["privacy"] = args.privacy


def _print_event(kind: str, data) -> None:
    if kind == "tool_call":
        args = json.dumps(data["args"], ensure_ascii=False)
        print(f"\n🤖 [{data['step']}] {data['tool']} {args[:300]}")
    elif kind == "tool_result":
        print(f"   {'✓' if data['ok'] else '✗'} {data['result'][:300]}")
    elif kind == "thought":
        print(f"💭 {data}")


def make_one(cfg: Config, mode: str = "agent", topic: str | None = None, upload: bool = True,
             task: str = "", hint: str = "") -> dict:
    from .agent import VideoAgent
    from .pipeline import complete_missing, run_pipeline
    from .studio import Studio

    studio = Studio(cfg)
    if mode == "pipeline":
        return run_pipeline(studio, topic=topic, upload=upload, improve=True, hint=hint)

    if not task:
        task = "Start now. Produce today's video end-to-end."
        if topic:
            task += f" The owner wants this topic: {topic}"
        if hint:
            task += f" Owner's direction: {hint}"
    agent = VideoAgent(studio, allow_upload=upload, on_event=_print_event)
    res = agent.run(task)
    log.info("Agent kết thúc (%d bước): %s", res.steps, res.summary)
    if studio.project is None or not studio.project.scenes:
        log.warning("Agent không tạo được kịch bản → chuyển sang pipeline cố định")
        return run_pipeline(studio, topic=topic, upload=upload, hint=hint)
    if cfg.get_path("agent.ensure_complete", True):
        return complete_missing(studio, upload=upload)
    return {"project": studio.p.dir, "video": studio.p.final_video, "youtube_url": studio.p.youtube_url}


def cmd_make(args, cfg: Config) -> int:
    _apply_overrides(args, cfg)
    res = make_one(cfg, args.mode, args.topic, upload=not args.no_upload, hint=args.hint or "")
    print("\n=== XONG ===")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def _load_studio(cfg: Config, project: str):
    from .project import Project
    from .studio import Studio

    path = Path(project)
    if not path.exists():
        path = cfg.resolve(cfg.get_path("paths.output_dir", "output")) / project
    return Studio(cfg, project=Project.load(path))


def cmd_edit(args, cfg: Config) -> int:
    from .agent import VideoAgent

    studio = _load_studio(cfg, args.project)
    agent = VideoAgent(studio, allow_upload=args.upload, on_event=_print_event)
    task = (f"A project already exists (title: {studio.p.title}). Do NOT create a new script. "
            f"Use view_script first, then apply this request from the owner, re-render, and finish:\n{args.instruction}")
    res = agent.run(task)
    print("\n=== XONG ===\n", res.summary, "\n", studio.p.final_video)
    return 0


def cmd_resume(args, cfg: Config) -> int:
    from .pipeline import complete_missing

    studio = _load_studio(cfg, args.project)
    if args.rerender:
        studio.p.final_video = ""
    res = complete_missing(studio, upload=args.upload)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_upload(args, cfg: Config) -> int:
    studio = _load_studio(cfg, args.project)
    res = studio.upload(privacy=args.privacy, publish_at=args.publish_at)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_schedule(args, cfg: Config) -> int:
    _apply_overrides(args, cfg)
    interval = float(args.interval_hours or cfg.get_path("schedule.interval_hours", 24))
    per_run = int(args.count or cfg.get_path("schedule.videos_per_run", 1))
    log.info("Chạy tự động: %d video mỗi %.1f giờ (Ctrl+C để dừng)", per_run, interval)
    while True:
        for i in range(per_run):
            try:
                res = make_one(cfg, args.mode, None, upload=not args.no_upload, hint=args.hint or "")
                log.info("Video %d xong: %s", i + 1, res.get("youtube_url") or res.get("video"))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.exception("Lỗi khi làm video: %s", exc)
        if args.once:
            return 0
        log.info("Nghỉ %.1f giờ tới lượt tiếp theo...", interval)
        time.sleep(interval * 3600)


def cmd_voices(args, cfg: Config) -> int:
    from .audio.tts import get_tts

    for v in get_tts(cfg.section("tts")).list_voices(args.lang):
        print(v)
    return 0


# ============================================================================ main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="autotube", description="AI local tự làm video và đăng YouTube")
    ap.add_argument("-c", "--config", help="đường dẫn config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="tạo config.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)
    sub.add_parser("doctor", help="kiểm tra cài đặt").set_defaults(fn=cmd_doctor)
    sub.add_parser("auth", help="đăng nhập YouTube").set_defaults(fn=cmd_auth)

    p = sub.add_parser("ideas", help="AI gợi ý ý tưởng")
    p.add_argument("-n", "--count", type=int, default=8)
    p.add_argument("--hint")
    p.set_defaults(fn=cmd_ideas)

    def common(p):
        p.add_argument("--mode", choices=["agent", "pipeline"], default="agent",
                       help="agent = AI tự quyết định mọi thứ; pipeline = chạy theo quy trình cố định")
        p.add_argument("--no-upload", action="store_true", help="chỉ làm video, không đăng")
        p.add_argument("--hint", help="định hướng thêm cho AI")
        p.add_argument("--format", choices=["landscape", "shorts", "square"])
        p.add_argument("--duration", type=int, help="độ dài mục tiêu (giây)")
        p.add_argument("--model", help="model AI local (ghi đè config)")
        p.add_argument("--tts", choices=["edge", "piper", "dummy"])
        p.add_argument("--privacy", choices=["private", "unlisted", "public"])

    p = sub.add_parser("make", help="làm 1 video")
    p.add_argument("--topic", help="chủ đề cụ thể (bỏ trống để AI tự nghĩ)")
    common(p)
    p.set_defaults(fn=cmd_make)

    p = sub.add_parser("edit", help="AI chỉnh sửa project theo yêu cầu")
    p.add_argument("project")
    p.add_argument("instruction")
    p.add_argument("--upload", action="store_true")
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("resume", help="làm nốt project dang dở")
    p.add_argument("project")
    p.add_argument("--upload", action="store_true")
    p.add_argument("--rerender", action="store_true")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("upload", help="đăng project lên YouTube")
    p.add_argument("project")
    p.add_argument("--privacy", choices=["private", "unlisted", "public"])
    p.add_argument("--publish-at", help="hẹn giờ ISO 8601 UTC, vd 2026-10-01T12:00:00Z")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("schedule", help="chạy tự động theo lịch")
    p.add_argument("--interval-hours", type=float)
    p.add_argument("--count", type=int, help="số video mỗi lượt")
    p.add_argument("--once", action="store_true")
    common(p)
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("voices", help="liệt kê giọng đọc")
    p.add_argument("--lang", default="vi")
    p.set_defaults(fn=cmd_voices)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(args.verbose, cfg.resolve(cfg.get_path("paths.output_dir", "output")) / "autotube.log")
    try:
        return args.fn(args, cfg) or 0
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        return 130
    except Exception as exc:
        log.error("%s", exc, exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())
