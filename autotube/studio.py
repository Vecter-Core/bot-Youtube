"""Studio: tập hợp mọi tính năng sản xuất video, thao tác trên một Project.

Cả chế độ pipeline (chạy tuần tự) và chế độ agent (AI tự chọn công cụ) đều gọi vào đây.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from .audio import music as music_lib
from .audio.tts import TTSResult, estimate_word_timings, get_tts
from .config import Config
from .content import generator as gen
from .llm import LLMClient
from .media.sources import MediaFinder
from .project import Project, Scene
from .subtitles import subs
from .video import editor
from .video import ffmpeg as ff
from .video.text_render import find_font, font_dir_for_ffmpeg, text_overlay_png
from .video.thumbnail import make_thumbnail

log = logging.getLogger(__name__)

AUTO_EFFECTS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]


class Studio:
    def __init__(self, cfg: Config, llm: LLMClient | None = None, project: Project | None = None):
        self.cfg = cfg
        self.llm = llm or LLMClient(cfg.section("llm"))
        self.project = project
        self.history = gen.History(cfg.resolve(cfg.get_path("paths.history_file", "output/history.json")))
        self._tts = None
        self._finder: MediaFinder | None = None

    # ================================================================ project
    @property
    def p(self) -> Project:
        if self.project is None:
            raise RuntimeError("Chưa có project. Hãy tạo kịch bản trước (create_script).")
        return self.project

    def new_project(self, topic: str = "", angle: str = "") -> Project:
        ch = self.cfg.section("channel")
        self.project = Project.create(
            self.cfg.resolve(self.cfg.get_path("paths.output_dir", "output")), topic=topic, angle=angle,
            language=ch.get("language", "vi"), format=self.cfg.get_path("video.format", "landscape"),
        )
        self._finder = None
        log.info("Project mới: %s", self.project.dir)
        return self.project

    def save(self) -> None:
        if self.project:
            self.project.save()

    @property
    def size(self) -> tuple[int, int]:
        return editor.resolution(self.p.format)

    @property
    def tts(self):
        if self._tts is None:
            self._tts = get_tts(self.cfg.section("tts"))
        return self._tts

    @property
    def finder(self) -> MediaFinder:
        if self._finder is None:
            media_cfg = dict(self.cfg.section("media"))
            media_cfg["local_dir"] = str(self.cfg.resolve(media_cfg.get("local_dir", "assets/media")))
            self._finder = MediaFinder(media_cfg, self.p.format, self.size)
        return self._finder

    # ================================================================ content
    def trends(self) -> list[str]:
        ch = self.cfg.section("channel")
        return gen.fetch_trends(ch.get("trends_geo", "VN")) if ch.get("use_trends", True) else []

    def brainstorm(self, n: int = 5, hint: str = "", use_trends: bool = True) -> list[dict]:
        trends = self.trends() if use_trends else []
        return gen.generate_ideas(self.llm, self.cfg.section("channel"), n=n, past_topics=self.history.topics(),
                                  trends=trends, hint=hint)

    def create_script(self, topic: str, angle: str = "", extra: str = "", improve: bool = False) -> dict:
        """AI viết kịch bản đầy đủ cho chủ đề rồi nạp vào project."""
        if self.project is None or self.project.scenes:
            self.new_project(topic, angle)
        else:
            self.p.topic, self.p.angle = topic, angle
        script = gen.write_script(self.llm, topic, self.cfg.section("channel"), {
            **self.cfg.section("video"), "format": self.p.format}, angle=angle, extra=extra)
        if improve:
            script = gen.critique_script(self.llm, script, self.cfg.section("channel"))
        self.load_script(script)
        return script

    def load_script(self, script: dict) -> None:
        script = gen.normalize_script(script)
        p = self.p
        p.title = script.get("title") or p.title or p.topic
        p.music_mood = script.get("music_mood") or p.music_mood or "calm"
        p.thumbnail_text = script.get("thumbnail_text") or p.thumbnail_text or p.title
        p.scenes = [Scene(index=i, **s) for i, s in enumerate(script["scenes"])]
        p.note(f"Nạp kịch bản {len(p.scenes)} cảnh: {p.title}")
        self.save()

    def set_script(self, title: str, scenes: list[dict], music_mood: str = "", thumbnail_text: str = "",
                   topic: str = "") -> None:
        """Dùng khi AI tự viết kịch bản của riêng nó."""
        if self.project is None or self.project.scenes:
            self.new_project(topic or title)
        self.load_script({"title": title, "scenes": scenes, "music_mood": music_mood, "thumbnail_text": thumbnail_text})

    def improve_script(self) -> str:
        script = self.script_dict()
        improved = gen.critique_script(self.llm, script, self.cfg.section("channel"))
        self.load_script({**script, **improved})
        return "Đã cải thiện kịch bản"

    def script_dict(self) -> dict:
        p = self.p
        return {"title": p.title, "music_mood": p.music_mood, "thumbnail_text": p.thumbnail_text,
                "scenes": [{"narration": s.narration, "visual_query": s.visual_query, "on_screen_text": s.on_screen_text,
                            "effect": s.effect, "transition": s.transition} for s in p.scenes]}

    def edit_scene(self, index: int, **changes) -> Scene:
        s = self._scene(index)
        for k, v in changes.items():
            if v is None or k not in ("narration", "visual_query", "on_screen_text", "effect", "transition"):
                continue
            if k == "narration" and v != s.narration:
                s.audio_path, s.duration, s.words = "", 0.0, []  # cần lồng tiếng lại
            if k == "visual_query" and v != s.visual_query:
                s.media_path, s.media_type = "", ""
            setattr(s, k, v)
        self.p.note(f"Sửa cảnh {index}: {list(changes)}")
        self.save()
        return s

    def add_scene(self, position: int, narration: str, visual_query: str = "", on_screen_text: str = "") -> None:
        p = self.p
        position = max(0, min(position, len(p.scenes)))
        p.scenes.insert(position, Scene(index=position, narration=narration, visual_query=visual_query,
                                        on_screen_text=on_screen_text))
        self._reindex()

    def remove_scene(self, index: int) -> None:
        self.p.scenes.pop(self._scene(index).index)
        self._reindex()

    def move_scene(self, index: int, new_position: int) -> None:
        s = self.p.scenes.pop(self._scene(index).index)
        self.p.scenes.insert(max(0, min(new_position, len(self.p.scenes))), s)
        self._reindex()

    def _reindex(self) -> None:
        for i, s in enumerate(self.p.scenes):
            s.index = i
        self.save()

    def _scene(self, index: int) -> Scene:
        try:
            return self.p.scenes[int(index)]
        except (IndexError, ValueError, TypeError):
            raise ValueError(f"Cảnh {index} không tồn tại (có {len(self.p.scenes)} cảnh, đánh số từ 0)")

    # ================================================================ media
    def find_visuals(self, only_missing: bool = True, prefer_video: bool | None = None) -> list[dict]:
        out = []
        for s in self.p.scenes:
            if only_missing and s.media_path and Path(s.media_path).exists():
                continue
            out.append(self.set_scene_media(s.index, query=s.visual_query or s.narration[:60], prefer_video=prefer_video))
        return out

    def set_scene_media(self, index: int, query: str | None = None, path: str | None = None,
                        prefer_video: bool | None = None, start: float | None = None) -> dict:
        s = self._scene(index)
        if path:
            src = Path(path)
            if not src.is_absolute():
                cand = self.p.path / src
                src = cand if cand.exists() else self.cfg.resolve(src)
            if not src.exists():
                raise FileNotFoundError(f"Không thấy file {path}")
            s.media_path, s.media_type, source = str(src), editor.media_kind(src), "file"
            s.media_source = source
        else:
            q = query or s.visual_query or s.narration[:60]
            s.visual_query = q
            media, kind, source = self.finder.best_for(q, self.p.path / "media", f"scene{index:02d}",
                                                        prefer_video=prefer_video)
            s.media_path, s.media_type, s.media_source = str(media), kind, source
        if start is not None:
            s.media_start = float(start)
        self.save()
        return {"scene": index, "media": Path(s.media_path).name, "type": s.media_type, "source": source}

    def search_media(self, query: str, kind: str = "any", limit: int = 6) -> list[dict]:
        res = self.finder.search(query, kind=kind, limit=limit)
        return [{k: v for k, v in r.items() if k in ("source", "kind", "url", "path", "width", "height", "duration", "id")}
                for r in res]

    def generate_image(self, index: int, prompt: str) -> dict:
        s = self._scene(index)
        out = self.finder.generate_sd(prompt, self.p.path / "media" / f"scene{index:02d}_sd.png")
        s.media_path, s.media_type, s.media_source = str(out), "image", "sd"
        self.save()
        return {"scene": index, "media": out.name}

    # ================================================================ audio
    def voiceover(self, voice: str | None = None, rate: str | None = None, only_missing: bool = False) -> dict:
        if voice or rate:
            tcfg = dict(self.cfg.section("tts"))
            if voice:
                tcfg["voice"] = voice
            if rate:
                tcfg["rate"] = rate
            self.cfg["tts"] = tcfg
            self._tts = None
        pad = float(self.cfg.get_path("tts.scene_padding", 0.25))
        done = 0
        for s in self.p.scenes:
            if only_missing and s.audio_path and Path(s.audio_path).exists():
                continue
            out = self.p.sub("audio", f"scene{s.index:02d}")
            try:
                res: TTSResult = self.tts.synthesize(s.narration, out)
            except Exception as exc:
                log.error("Lồng tiếng cảnh %d lỗi: %s — dùng giọng im lặng", s.index, exc)
                from .audio.tts import DummyTTS

                res = DummyTTS().synthesize(s.narration, out)
            s.audio_path, s.words = str(res.path), res.words
            s.duration = round(res.duration + pad, 3)
            done += 1
        self.p.voiceover_path = ""
        self.p.note(f"Lồng tiếng {done} cảnh bằng {self.tts.name}")
        self.save()
        return {"scenes_voiced": done, "total_duration": round(self.p.total_duration, 1), "engine": self.tts.name}

    def list_voices(self, language: str = "") -> list[str]:
        return self.tts.list_voices(language or self.p.language if self.project else language)

    def choose_music(self, mood: str | None = None, path: str | None = None, generate: bool = False) -> dict:
        mcfg = self.cfg.section("music")
        mood = mood or self.p.music_mood or "calm"
        self.p.music_mood = mood
        if path:
            src = self.cfg.resolve(path)
            if not src.exists():
                raise FileNotFoundError(path)
            self.p.music_path = str(src)
        else:
            picked = None if generate else music_lib.pick_music(self.cfg.resolve(mcfg.get("dir", "assets/music")), mood,
                                                               seed=self.p.id)
            if picked is None:
                dur = max(10.0, (self.p.total_duration or float(self.cfg.get_path("video.target_duration", 60))) + 2)
                picked = music_lib.synth_ambient(self.p.sub("audio", "music_generated.wav"), dur, mood)
            self.p.music_path = str(picked)
        self.save()
        return {"music": Path(self.p.music_path).name, "mood": mood}

    # ================================================================ subtitles
    def _global_words(self) -> list[dict]:
        words, t = [], 0.0
        for s in self.p.scenes:
            ws = s.words or (estimate_word_timings(s.narration, s.duration - 0.25) if s.duration else [])
            for w in ws:
                words.append({"text": w["text"], "start": round(t + w["start"], 3), "end": round(t + w["end"], 3)})
            t += s.duration
        return words

    def make_subtitles(self, karaoke: bool = True, position: str | None = None, max_words: int | None = None) -> dict:
        scfg = self.cfg.section("subtitles")
        words = None
        if scfg.get("use_whisper"):
            try:
                voice = self._build_voice()
                words = subs.whisper_words(voice, scfg.get("whisper_model", "small"), self.p.language)
            except Exception as exc:
                log.warning("Whisper lỗi, dùng mốc thời gian TTS: %s", exc)
        if not words:
            words = self._global_words()
        if not words:
            raise RuntimeError("Chưa có lời thoại/giọng đọc — hãy chạy voiceover trước")
        vertical = self.p.format in ("shorts", "vertical")
        cues = subs.group_words(words, max_words=int(max_words or scfg.get("max_words_per_line", 7 if not vertical else 4)))
        srt = subs.write_srt(cues, self.p.sub("subtitles.srt"))
        font_path = find_font(scfg.get("font", "auto"))
        ass = subs.write_ass(
            cues, self.p.sub("subtitles.ass"), self.size, font_name=subs.font_family(font_path),
            font_size=int(scfg.get("font_size") or 0), color=scfg.get("color", "#FFFFFF"),
            highlight=scfg.get("highlight_color", "#FFD400"), outline=scfg.get("outline_color", "#000000"),
            position=position or scfg.get("position", "bottom"), karaoke=karaoke,
        )
        self.p.srt_path, self.p.ass_path = str(srt), str(ass)
        self.p.extra["cues"] = len(cues)
        # nhớ lựa chọn để render tự tạo lại phụ đề (khớp giọng mới) mà không mất kiểu dáng AI đã chọn
        self.p.extra["sub_opts"] = {"karaoke": karaoke, "position": position, "max_words": max_words}
        self.save()
        return {"cues": len(cues), "srt": srt.name, "karaoke": karaoke}

    # ================================================================ render
    def _resolve_effect(self, s: Scene) -> str:
        if s.effect in ("auto", "", None):
            return AUTO_EFFECTS[s.index % len(AUTO_EFFECTS)] if self.cfg.get_path("video.ken_burns", True) else "none"
        return s.effect

    def _overlay_for(self, s: Scene) -> Path | None:
        text = s.on_screen_text
        if s.index == 0 and self.cfg.get_path("video.intro_title", True) and not text:
            text = self.p.title
        if not text:
            return None
        key = hashlib.md5(f"{text}{self.size}".encode()).hexdigest()[:8]
        out = self.p.sub("overlays", f"scene{s.index:02d}_{key}.png")
        if not out.exists():
            text_overlay_png(text, self.size, out, position="center" if s.index == 0 and text == self.p.title else "top")
        return out

    def _build_voice(self) -> Path:
        out = self.p.sub("audio", "voiceover.wav")
        editor.build_voiceover([s.audio_path or None for s in self.p.scenes], [s.duration for s in self.p.scenes], out)
        self.p.voiceover_path = str(out)
        return out

    def render(self, transition: str | None = None, burn_subtitles: bool | None = None, music: bool | None = None,
               quality: str = "normal") -> dict:
        p, vcfg = self.p, self.cfg.section("video")
        if not p.scenes:
            raise RuntimeError("Project chưa có cảnh nào")
        if any(not s.duration for s in p.scenes):
            self.voiceover(only_missing=True)
        if any(not s.media_path or not Path(s.media_path).exists() for s in p.scenes):
            self.find_visuals(only_missing=True)
        fps = int(vcfg.get("fps", 30))
        crf = int(vcfg.get("crf", 20))
        preset = {"draft": "ultrafast", "normal": vcfg.get("preset", "medium"), "high": "slow"}.get(quality, "medium")
        if quality == "draft":
            crf = 28
        default_tr = transition or vcfg.get("transition", "fade")
        transitions = [(transition or s.transition or default_tr) for s in p.scenes[:-1]]
        overlaps = editor.transition_overlaps(transitions, float(vcfg.get("transition_duration", 0.5)), fps)

        clips = []
        for i, s in enumerate(p.scenes):
            clip_len = s.duration + (overlaps[i] if i < len(overlaps) else 0)
            effect = self._resolve_effect(s)
            ov = self._overlay_for(s)
            key = hashlib.md5(json.dumps([s.media_path, s.media_start, round(clip_len, 3), effect, str(ov),
                                          self.size, fps, crf, preset]).encode()).hexdigest()[:10]
            clip = p.sub("clips", f"scene{i:02d}_{key}.mp4")
            if not clip.exists():
                log.info("Dựng cảnh %d/%d (%.1fs, %s)", i + 1, len(p.scenes), clip_len, s.media_type)
                editor.make_scene_clip(s.media_path, clip_len, clip, size=self.size, fps=fps, effect=effect,
                                       media_start=s.media_start, overlay_png=ov, crf=crf, preset=preset)
            clips.append(clip)
        video_only = editor.concat_clips(clips, p.sub("work", "video_noaudio.mp4"), transitions,
                                         float(vcfg.get("transition_duration", 0.5)), fps, crf, preset)

        voice = self._build_voice()
        use_music = self.cfg.get_path("music.enabled", True) if music is None else music
        if use_music and not p.music_path:
            self.choose_music()
        mixed = editor.mix_music(voice, p.music_path if use_music else None, p.sub("audio", "final_mix.wav"),
                                 p.total_duration, float(self.cfg.get_path("music.volume", 0.12)),
                                 bool(self.cfg.get_path("music.ducking", True)))

        scfg = self.cfg.section("subtitles")
        burn = scfg.get("enabled", True) and (scfg.get("burn_in", True) if burn_subtitles is None else burn_subtitles)
        if scfg.get("enabled", True):
            self.make_subtitles(**p.extra.get("sub_opts", {}))  # luôn khớp với giọng đọc hiện tại
        final = p.path / "final.mp4"
        if burn and ff.has_filter("subtitles"):
            editor.render_final(video_only, mixed, final, ass_file=p.ass_path, fonts_dir=font_dir_for_ffmpeg(),
                                crf=crf, preset=preset, fps=fps)
        else:
            editor.render_final(video_only, mixed, final, crf=crf, preset=preset, fps=fps)
            if burn:
                tmp = final.with_name("final_nosub.mp4")
                shutil.move(final, tmp)
                cues = subs.group_words(self._global_words(), int(scfg.get("max_words_per_line", 7)))
                subs.burn_with_pillow(tmp, cues, final)
                tmp.unlink(missing_ok=True)
        p.final_video = str(final)
        info = ff.probe(final)
        p.note(f"Xuất video {info['duration']:.1f}s {info['width']}x{info['height']}")
        self.save()
        return {"video": str(final), "duration": round(info["duration"], 1),
                "resolution": f"{info['width']}x{info['height']}", "subtitles_burned": bool(burn)}

    # ================================================================ publish prep
    def thumbnail(self, text: str | None = None, scene_index: int | None = None, style: str = "bold_yellow") -> dict:
        p = self.p
        text = text or p.thumbnail_text or p.title
        bg = None
        candidates = [p.scenes[scene_index]] if scene_index is not None and p.scenes else p.scenes
        for s in candidates:
            if s.media_path and Path(s.media_path).exists():
                bg = s.media_path
                break
        out = make_thumbnail(bg, text, p.sub("thumbnail.jpg"), vertical=p.format in ("shorts", "vertical"), style=style)
        p.thumbnail, p.thumbnail_text = str(out), text
        self.save()
        return {"thumbnail": out.name, "text": text}

    def seo(self) -> dict:
        p = self.p
        script_text = "\n".join(s.narration for s in p.scenes)
        data = gen.generate_seo(self.llm, p.title or p.topic, script_text, self.cfg.section("channel"), p.format)
        p.title, p.description, p.tags, p.hashtags = data["title"], data["description"], data["tags"], data["hashtags"]
        self.save()
        return data

    def set_metadata(self, title: str | None = None, description: str | None = None, tags: list[str] | None = None,
                     hashtags: list[str] | None = None) -> dict:
        p = self.p
        if title:
            p.title = title[:100]
        if description:
            p.description = description
        if tags:
            p.tags = [str(t) for t in tags]
        if hashtags:
            p.hashtags = [str(h).lstrip("#") for h in hashtags]
        self.save()
        return {"title": p.title, "tags": len(p.tags)}

    def review(self) -> dict:
        """Kiểm tra chất lượng trước khi đăng — AI dùng để tự rà soát."""
        p, issues = self.p, []
        if not p.scenes:
            issues.append("Chưa có kịch bản")
        for s in p.scenes:
            if not s.audio_path:
                issues.append(f"Cảnh {s.index} chưa lồng tiếng")
            if not s.media_path:
                issues.append(f"Cảnh {s.index} chưa có hình ảnh")
        placeholders = [s.index for s in p.scenes if s.media_source == "placeholder"]
        target = float(self.cfg.get_path("video.target_duration", 60))
        dur = p.total_duration
        if dur and (dur < target * 0.5 or dur > target * 1.8):
            issues.append(f"Độ dài {dur:.0f}s lệch nhiều so với mục tiêu {target:.0f}s")
        if p.format in ("shorts", "vertical") and dur > 180:
            issues.append("Shorts phải dưới 3 phút")
        if not p.final_video or not Path(p.final_video).exists():
            issues.append("Chưa xuất video (render)")
        if not (p.title and p.description):
            issues.append("Thiếu tiêu đề/mô tả SEO")
        if not p.thumbnail:
            issues.append("Chưa có thumbnail")
        return {"ok": not issues, "issues": issues, "summary": p.summary(),
                "placeholder_scenes": placeholders,
                "hint": "placeholder_scenes use a plain generated background; try set_scene_media with another query"
                if placeholders else ""}

    def upload(self, privacy: str | None = None, publish_at: str | None = None) -> dict:
        from .youtube.uploader import YouTubeUploader

        p = self.p
        if not p.final_video or not Path(p.final_video).exists():
            raise RuntimeError("Chưa có video cuối. Hãy render trước.")
        if not p.title:
            raise RuntimeError("Chưa có tiêu đề. Hãy tạo SEO trước.")
        desc = p.description or p.title
        if p.hashtags:
            desc += "\n\n" + " ".join(f"#{h}" for h in p.hashtags)
        scfg = self.cfg.section("subtitles")
        res = YouTubeUploader(self.cfg).upload(
            p.final_video, p.title, desc, p.tags, privacy=privacy, publish_at=publish_at,
            thumbnail=p.thumbnail or None,
            captions=p.srt_path if scfg.get("upload_caption", True) and p.srt_path else None,
            caption_language=p.language,
        )
        p.youtube_id, p.youtube_url = res["id"], res["url"]
        p.note(f"Đã đăng YouTube {res['url']}")
        self.save()
        self.history.add(topic=p.topic, title=p.title, url=res["url"], project=p.id)
        return res

    def mark_done_without_upload(self) -> None:
        p = self.p
        self.history.add(topic=p.topic, title=p.title, url="", project=p.id)

    # ================================================================ free editing
    def project_file(self, name: str) -> Path:
        path = Path(name)
        if not path.is_absolute():
            path = self.p.path / name
        return path

    def list_files(self) -> list[str]:
        return [str(f.relative_to(self.p.path)) for f in sorted(self.p.path.rglob("*")) if f.is_file()
                and f.suffix.lower() in (".mp4", ".mp3", ".wav", ".jpg", ".png", ".srt", ".ass")]
