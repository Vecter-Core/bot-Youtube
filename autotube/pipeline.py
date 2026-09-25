"""Pipeline cố định: chạy lần lượt từng bước, AI chỉ lo phần sáng tạo.

Ổn định hơn chế độ agent với model nhỏ (3B-7B). Cũng được dùng để "vá" những bước
mà agent bỏ sót (complete_missing).
"""
from __future__ import annotations

import logging
from pathlib import Path

from .studio import Studio

log = logging.getLogger(__name__)


def run_pipeline(studio: Studio, topic: str | None = None, angle: str = "", upload: bool = True,
                 improve: bool = True, hint: str = "") -> dict:
    if not topic:
        log.info("① AI đang nghĩ ý tưởng...")
        ideas = studio.brainstorm(n=5, hint=hint)
        if not ideas:
            raise RuntimeError("AI không đưa ra được ý tưởng nào")
        best = ideas[0]
        topic, angle = best["topic"], best.get("angle", "")
        log.info("   → Chọn: %s", topic)
    log.info("② AI viết kịch bản: %s", topic)
    studio.create_script(topic, angle, improve=improve)
    log.info("   → %s (%d cảnh)", studio.p.title, len(studio.p.scenes))
    return complete_missing(studio, upload=upload)


def complete_missing(studio: Studio, upload: bool = True) -> dict:
    """Làm nốt mọi bước còn thiếu của project hiện tại."""
    p = studio.p
    if any(not s.media_path or not Path(s.media_path).exists() for s in p.scenes):
        log.info("③ Tìm hình ảnh/video minh hoạ...")
        for r in studio.find_visuals(only_missing=True):
            log.info("   cảnh %s: %s (%s)", r["scene"], r["media"], r["source"])
    if any(not s.audio_path or not Path(s.audio_path).exists() for s in p.scenes):
        log.info("④ Lồng tiếng...")
        studio.voiceover(only_missing=True)
    if studio.cfg.get_path("music.enabled", True) and not p.music_path:
        log.info("⑤ Chọn nhạc nền...")
        studio.choose_music()
    if studio.cfg.get_path("subtitles.enabled", True) and not p.ass_path:
        log.info("⑥ Tạo phụ đề...")
        studio.make_subtitles()
    if not p.final_video or not Path(p.final_video).exists():
        log.info("⑦ Dựng video (có thể mất vài phút)...")
        info = studio.render()
        log.info("   → %s (%ss, %s)", info["video"], info["duration"], info["resolution"])
    if not p.description:
        log.info("⑧ Viết tiêu đề/mô tả/tags SEO...")
        studio.seo()
    if not p.thumbnail:
        log.info("⑨ Tạo thumbnail...")
        studio.thumbnail()
    result = {"project": p.dir, "video": p.final_video, "title": p.title, "thumbnail": p.thumbnail}
    if upload and not p.youtube_url:
        log.info("⑩ Đăng lên YouTube...")
        result.update(studio.upload())
    elif not upload and not p.youtube_url:
        studio.mark_done_without_upload()
    result["youtube_url"] = p.youtube_url or None
    return result
