"""Danh sách công cụ (tools) mà AI local được phép tự gọi.

Mỗi tool = tên + mô tả + JSON schema tham số + hàm xử lý. Agent đọc mô tả rồi tự
quyết định dùng tool nào, theo thứ tự nào, với tham số gì.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..studio import Studio
from ..video import editor
from ..video import ffmpeg as ff


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def _obj(props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props or {}, "required": required or []}


S = {"type": "string"}
I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}


class FinishSignal(Exception):
    def __init__(self, summary: str):
        super().__init__(summary)
        self.summary = summary


def build_tools(studio: Studio, allow_upload: bool = True) -> dict[str, Tool]:
    st = studio
    tools: list[Tool] = []

    def add(name, desc, params, fn):
        tools.append(Tool(name, desc, params, fn))

    # ---------------------------------------------------------------- ideas & script
    add("get_trending_topics", "Get currently trending Google searches in the channel's country (for inspiration).",
        _obj(), lambda: {"trends": st.trends()})
    add("brainstorm_ideas",
        "Generate and rank fresh video ideas for the channel niche (avoids topics already published).",
        _obj({"count": I, "hint": {**S, "description": "optional extra direction"}}),
        lambda count=5, hint="": {"ideas": st.brainstorm(int(count), hint)})
    add("write_script",
        "Let the scriptwriter write a full scene-by-scene script for a topic and load it into a NEW project. "
        "Use this OR set_script.",
        _obj({"topic": S, "angle": S, "extra_instructions": S, "self_improve": {**B, "description": "run a critique pass"}},
             ["topic"]),
        lambda topic, angle="", extra_instructions="", self_improve=False: _script_view(
            st, st.create_script(topic, angle, extra_instructions, bool(self_improve))))
    add("set_script",
        "Load a script YOU wrote yourself into a NEW project. scenes = list of objects with keys: narration "
        "(spoken text), visual_query (2-5 English stock-footage keywords), on_screen_text (short caption or ''), "
        "effect (zoom_in|zoom_out|pan_left|pan_right|none), transition (fade|wipeleft|slideleft|circleopen|dissolve|none).",
        _obj({"title": S, "topic": S, "music_mood": S, "thumbnail_text": S,
              "scenes": {"type": "array", "items": _obj({"narration": S, "visual_query": S, "on_screen_text": S,
                                                         "effect": S, "transition": S}, ["narration"])}},
             ["title", "scenes"]),
        lambda title, scenes, topic="", music_mood="", thumbnail_text="": (
            st.set_script(title, scenes, music_mood, thumbnail_text, topic), _script_view(st, st.script_dict()))[1])
    add("view_script", "Show the current script (all scenes with their index, narration, visuals, duration).",
        _obj(), lambda: _script_view(st, st.script_dict(), with_status=True))
    add("improve_script", "Critique the current script and rewrite it to be more engaging (hook, pacing, accuracy).",
        _obj(), lambda: (st.improve_script(), _script_view(st, st.script_dict()))[1])
    add("edit_scene", "Change fields of one scene (index from 0). Only pass fields you want to change.",
        _obj({"index": I, "narration": S, "visual_query": S, "on_screen_text": S, "effect": S, "transition": S},
             ["index"]),
        lambda index, **kw: _scene_view(st.edit_scene(int(index), **kw)))
    add("add_scene", "Insert a new scene at a position.",
        _obj({"position": I, "narration": S, "visual_query": S, "on_screen_text": S}, ["position", "narration"]),
        lambda position, narration, visual_query="", on_screen_text="": (
            st.add_scene(int(position), narration, visual_query, on_screen_text), {"scenes": len(st.p.scenes)})[1])
    add("remove_scene", "Delete a scene by index.", _obj({"index": I}, ["index"]),
        lambda index: (st.remove_scene(int(index)), {"scenes": len(st.p.scenes)})[1])
    add("move_scene", "Move a scene to a new position.", _obj({"index": I, "new_position": I}, ["index", "new_position"]),
        lambda index, new_position: (st.move_scene(int(index), int(new_position)), {"ok": True})[1])

    # ---------------------------------------------------------------- visuals
    add("find_visuals_for_all_scenes",
        "Automatically download matching stock video/images for every scene that has no media yet.",
        _obj({"prefer_video": B}), lambda prefer_video=None: {"results": st.find_visuals(True, prefer_video)})
    add("search_media", "Search stock footage/images (local library, Pexels, Pixabay) and list candidates.",
        _obj({"query": {**S, "description": "English keywords"}, "kind": {**S, "enum": ["any", "video", "image"]}},
             ["query"]),
        lambda query, kind="any": {"candidates": st.search_media(query, kind)})
    add("set_scene_media",
        "Set the visual of one scene: either by a new search query (English keywords) or a file path. "
        "Optionally 'start' = second of the source video to start from.",
        _obj({"index": I, "query": S, "path": S, "prefer_video": B, "start": N}, ["index"]),
        lambda index, query=None, path=None, prefer_video=None, start=None: st.set_scene_media(
            int(index), query, path, prefer_video, start))
    add("generate_image", "Create an AI image for a scene with local Stable Diffusion (if the user runs it).",
        _obj({"index": I, "prompt": {**S, "description": "English image prompt"}}, ["index", "prompt"]),
        lambda index, prompt: st.generate_image(int(index), prompt))

    # ---------------------------------------------------------------- audio
    add("generate_voiceover",
        "Create the narration audio (text-to-speech) for all scenes. Sets each scene's duration.",
        _obj({"voice": {**S, "description": "optional voice id, e.g. vi-VN-HoaiMyNeural or vi-VN-NamMinhNeural"},
              "rate": {**S, "description": "speed like +10% or -5%"}}),
        lambda voice=None, rate=None: st.voiceover(voice, rate))
    add("list_voices", "List available TTS voices for a language code (e.g. vi, en).", _obj({"language": S}),
        lambda language="": {"voices": st.list_voices(language)[:40]})
    add("choose_background_music",
        "Pick background music by mood (calm, epic, happy, sad, mystery, tech, inspiring) from the local library, "
        "or generate=true to synthesize ambient music.",
        _obj({"mood": S, "path": S, "generate": B}),
        lambda mood=None, path=None, generate=False: st.choose_music(mood, path, bool(generate)))

    # ---------------------------------------------------------------- subtitles & render
    add("generate_subtitles", "Create subtitles (SRT + styled ASS) synced to the narration.",
        _obj({"karaoke": {**B, "description": "highlight words as they are spoken"},
              "position": {**S, "enum": ["bottom", "center"]}, "max_words_per_line": I}),
        lambda karaoke=True, position=None, max_words_per_line=None: st.make_subtitles(bool(karaoke), position,
                                                                                         max_words_per_line))
    add("render_video",
        "Edit everything together: scene clips with camera motion, transitions, on-screen text, voiceover, "
        "background music with ducking and burned-in subtitles. Produces final.mp4. quality: draft|normal|high.",
        _obj({"transition": {**S, "description": "override transition for all scenes"}, "burn_subtitles": B,
              "music": B, "quality": {**S, "enum": ["draft", "normal", "high"]}}),
        lambda transition=None, burn_subtitles=None, music=None, quality="normal": st.render(
            transition, burn_subtitles, music, quality))
    add("create_thumbnail", "Create the YouTube thumbnail with big bold text.",
        _obj({"text": {**S, "description": "2-5 punchy words"}, "scene_index": I,
              "style": {**S, "enum": ["bold_yellow", "white_red", "white_black", "cyan"]}}),
        lambda text=None, scene_index=None, style="bold_yellow": st.thumbnail(
            text, None if scene_index is None else int(scene_index), style))
    add("generate_seo", "Write the optimized YouTube title, description, tags and hashtags.", _obj(),
        lambda: st.seo())
    add("set_metadata", "Manually set/override title, description, tags or hashtags.",
        _obj({"title": S, "description": S, "tags": {"type": "array", "items": S},
              "hashtags": {"type": "array", "items": S}}),
        lambda title=None, description=None, tags=None, hashtags=None: st.set_metadata(title, description, tags, hashtags))
    add("review_project", "Quality-check the project and list remaining issues before publishing.", _obj(),
        lambda: st.review())

    # ---------------------------------------------------------------- free editing on files
    add("list_project_files", "List media files in the current project folder.", _obj(),
        lambda: {"files": st.list_files()})
    add("inspect_media", "Get duration/resolution/audio info of a media file.", _obj({"file": S}, ["file"]),
        lambda file: ff.probe(st.project_file(file)))
    add("trim_video", "Cut a part of a video file (start/end in seconds). Output saved in the project.",
        _obj({"file": S, "start": N, "end": N, "output": S}, ["file", "start"]),
        lambda file, start, end=None, output=None: _file_out(st, editor.trim(
            st.project_file(file), _out(st, output, file, "trim"), float(start), None if end is None else float(end))))
    add("join_videos", "Concatenate several video files into one.",
        _obj({"files": {"type": "array", "items": S}, "output": S}, ["files"]),
        lambda files, output=None: _file_out(st, editor.join_videos(
            [st.project_file(f) for f in files], _out(st, output, files[0], "joined"), st.p.format)))
    add("change_speed", "Speed up or slow down a video (factor 0.25-4).", _obj({"file": S, "factor": N, "output": S},
                                                                            ["file", "factor"]),
        lambda file, factor, output=None: _file_out(st, editor.change_speed(
            st.project_file(file), _out(st, output, file, "speed"), float(factor))))
    add("add_text_to_video", "Overlay text on a video between start and end seconds.",
        _obj({"file": S, "text": S, "start": N, "end": N, "position": {**S, "enum": ["top", "center", "bottom"]},
              "output": S}, ["file", "text"]),
        lambda file, text, start=0, end=None, position="top", output=None: _file_out(st, editor.add_text(
            st.project_file(file), _out(st, output, file, "text"), text, float(start),
            None if end is None else float(end), position)))
    add("replace_audio", "Replace (or mix) the audio track of a video with another audio file.",
        _obj({"video": S, "audio": S, "mix": B, "volume": N, "output": S}, ["video", "audio"]),
        lambda video, audio, mix=False, volume=1.0, output=None: _file_out(st, editor.replace_audio(
            st.project_file(video), st.project_file(audio), _out(st, output, video, "audio"), bool(mix), float(volume))))
    add("convert_format", "Convert a video to another aspect ratio: shorts (9:16), landscape (16:9) or square.",
        _obj({"file": S, "format": {**S, "enum": ["shorts", "landscape", "square"]},
              "mode": {**S, "enum": ["crop", "blur"]}, "output": S}, ["file", "format"]),
        lambda file, format, mode="blur", output=None: _file_out(st, editor.reformat(
            st.project_file(file), _out(st, output, file, format), format, mode)))
    add("make_shorts_version",
        "Create a vertical 9:16 Shorts cut (max 58s) from the final video, with blurred background.",
        _obj({"start": N, "duration": N}),
        lambda start=0, duration=58: _shorts(st, float(start), float(duration)))
    add("transcribe_media", "Transcribe speech in an audio/video file with local Whisper (if installed).",
        _obj({"file": S}, ["file"]), lambda file: _transcribe(st, file))

    # ---------------------------------------------------------------- publish & finish
    if allow_upload:
        add("upload_to_youtube",
            "Publish final.mp4 to YouTube with the SEO metadata, thumbnail and captions. "
            "privacy: private|unlisted|public. publish_at: optional ISO time (UTC) to schedule.",
            _obj({"privacy": {**S, "enum": ["private", "unlisted", "public"]}, "publish_at": S}),
            lambda privacy=None, publish_at=None: st.upload(privacy, publish_at))

    def _finish(summary: str = "") -> dict:
        raise FinishSignal(summary)

    add("finish", "Call when the whole job is complete (or impossible). Give a short summary.",
        _obj({"summary": S}, ["summary"]), _finish)
    return {t.name: t for t in tools}


# ------------------------------------------------------------------------ helpers
def _script_view(st: Studio, script: dict, with_status: bool = False) -> dict:
    scenes = []
    for i, s in enumerate(script.get("scenes", [])):
        item = {"i": i, "narration": s["narration"], "visual_query": s.get("visual_query", ""),
                "on_screen_text": s.get("on_screen_text", "")}
        if with_status and st.project and i < len(st.p.scenes):
            sc = st.p.scenes[i]
            item.update({"duration": sc.duration, "media": Path(sc.media_path).name if sc.media_path else None,
                         "media_source": sc.media_source or None, "voiced": bool(sc.audio_path)})
        scenes.append(item)
    return {"title": script.get("title"), "music_mood": script.get("music_mood"), "scenes": scenes}


def _scene_view(s) -> dict:
    return {"index": s.index, "narration": s.narration, "visual_query": s.visual_query,
            "on_screen_text": s.on_screen_text, "effect": s.effect, "transition": s.transition,
            "needs_voiceover": not s.audio_path, "needs_media": not s.media_path}


def _out(st: Studio, output: str | None, src: str, suffix: str) -> Path:
    if output:
        return st.project_file(output if output.endswith(".mp4") else output + ".mp4")
    return st.p.sub("edits", f"{Path(src).stem}_{suffix}.mp4")


def _file_out(st: Studio, path: Path) -> dict:
    info = ff.probe(path)
    rel = str(Path(path).relative_to(st.p.path)) if str(path).startswith(str(st.p.path)) else str(path)
    return {"output": rel, "duration": round(info["duration"], 2), "resolution": f"{info['width']}x{info['height']}"}


def _shorts(st: Studio, start: float, duration: float) -> dict:
    if not st.p.final_video:
        raise RuntimeError("Chưa có final.mp4 — hãy render_video trước")
    cut = editor.trim(st.p.final_video, st.p.sub("edits", "shorts_cut.mp4"), start, start + min(58.0, duration))
    out = editor.reformat(cut, st.p.sub("shorts.mp4"), "shorts", "blur")
    st.p.extra["shorts_video"] = str(out)
    st.save()
    return _file_out(st, out)


def _transcribe(st: Studio, file: str) -> dict:
    from ..subtitles.subs import whisper_words

    src = st.project_file(file)
    audio = editor.extract_audio(src, st.p.sub("work", Path(file).stem + "_16k.wav"))
    words = whisper_words(audio, st.cfg.get_path("subtitles.whisper_model", "small"), st.p.language)
    return {"text": " ".join(w["text"] for w in words)[:3000], "words": len(words)}


def result_to_text(result: Any, limit: int = 3500) -> str:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "... (truncated)"
