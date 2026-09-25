"""Trạng thái của một video đang được sản xuất (lưu ra project.json)."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def slugify(text: str, max_len: int = 40) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "video"


@dataclass
class Scene:
    index: int
    narration: str
    visual_query: str = ""
    on_screen_text: str = ""
    effect: str = "auto"            # auto | zoom_in | zoom_out | pan_left | pan_right | none
    transition: str = ""            # rỗng = theo cấu hình chung
    media_path: str = ""
    media_type: str = ""            # video | image
    media_source: str = ""          # local | pexels | pixabay | sd | placeholder | file
    media_start: float = 0.0        # cắt đoạn bắt đầu từ giây này của video nguồn
    audio_path: str = ""
    duration: float = 0.0
    words: list[dict] = field(default_factory=list)   # [{"text","start","end"}] tính theo từng cảnh

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class Project:
    id: str
    dir: str
    topic: str = ""
    angle: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    language: str = "vi"
    format: str = "landscape"
    scenes: list[Scene] = field(default_factory=list)
    music_mood: str = ""
    music_path: str = ""
    voiceover_path: str = ""
    srt_path: str = ""
    ass_path: str = ""
    final_video: str = ""
    thumbnail: str = ""
    thumbnail_text: str = ""
    youtube_id: str = ""
    youtube_url: str = ""
    created_at: float = field(default_factory=time.time)
    log: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------------- helpers
    @property
    def path(self) -> Path:
        return Path(self.dir)

    def sub(self, *parts: str) -> Path:
        p = self.path.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def note(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def save(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        out = self.path / "project.json"
        out.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        path = Path(path)
        if path.is_dir():
            path = path / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        scenes = [Scene.from_dict(s) for s in data.pop("scenes", [])]
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(scenes=scenes, **known)

    @classmethod
    def create(cls, output_dir: str | Path, topic: str = "", **kw) -> "Project":
        stamp = time.strftime("%Y%m%d-%H%M%S")
        pid = f"{stamp}-{slugify(topic or 'video')}"
        pdir = Path(output_dir) / pid
        pdir.mkdir(parents=True, exist_ok=True)
        proj = cls(id=pid, dir=str(pdir), topic=topic, **kw)
        proj.save()
        return proj

    def summary(self) -> dict:
        """Tóm tắt ngắn gọn để đưa cho AI xem tiến độ."""
        return {
            "topic": self.topic,
            "title": self.title,
            "scenes": len(self.scenes),
            "scenes_with_media": sum(1 for s in self.scenes if s.media_path),
            "scenes_with_audio": sum(1 for s in self.scenes if s.audio_path),
            "total_duration_sec": round(self.total_duration, 1),
            "music": Path(self.music_path).name if self.music_path else None,
            "subtitles": bool(self.srt_path),
            "final_video": self.final_video or None,
            "thumbnail": self.thumbnail or None,
            "has_seo": bool(self.title and self.description),
            "youtube_url": self.youtube_url or None,
        }
