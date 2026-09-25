"""AI sáng tạo nội dung: ý tưởng, kịch bản, SEO, tự phê bình & cải thiện."""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from ..llm import LLMClient
from ..video.editor import EFFECTS, XFADE_TRANSITIONS

log = logging.getLogger(__name__)

LANG_NAMES = {"vi": "Vietnamese (tiếng Việt có dấu)", "en": "English", "ja": "Japanese", "ko": "Korean",
              "zh": "Chinese", "es": "Spanish", "fr": "French", "th": "Thai", "id": "Indonesian"}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


SYSTEM_CREATOR = (
    "You are a top YouTube content strategist and scriptwriter. You create original, accurate, "
    "engaging videos that keep viewers watching. You never invent fake statistics; if unsure, keep "
    "claims general. You always answer with valid JSON when asked for JSON."
)


# --------------------------------------------------------------------------- trends & history
def fetch_trends(geo: str = "VN", limit: int = 15) -> list[str]:
    """Từ khoá đang thịnh hành trên Google Trends (RSS, không cần API key)."""
    for url in (f"https://trends.google.com/trending/rss?geo={geo}",
                f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"):
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            titles = [i.findtext("title") for i in root.iter("item")]
            return [t for t in titles if t][:limit]
        except Exception as exc:
            log.debug("Không lấy được Google Trends: %s", exc)
    return []


class History:
    """Lưu các chủ đề đã làm để AI không lặp lại."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.items: list[dict] = []
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.items = []

    def topics(self, n: int = 50) -> list[str]:
        return [i.get("topic", "") for i in self.items[-n:]]

    def add(self, **item) -> None:
        self.items.append(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- generation
def generate_ideas(llm: LLMClient, channel: dict, n: int = 5, past_topics: list[str] | None = None,
                   trends: list[str] | None = None, hint: str = "") -> list[dict]:
    prompt = f"""Brainstorm {n} fresh YouTube video ideas for this channel.

Channel niche: {channel.get('niche')}
Target audience: {channel.get('audience')}
Style: {channel.get('style')}
Language of the content: {lang_name(channel.get('language', 'vi'))}
{f'Extra direction from the owner: {hint}' if hint else ''}
{('Currently trending searches (use only if relevant to the niche): ' + ', '.join(trends)) if trends else ''}
{('Already published topics (DO NOT repeat): ' + '; '.join(past_topics[-30:])) if past_topics else ''}

Return JSON: {{"ideas": [{{"topic": "...", "angle": "unique angle / why it is interesting", "hook": "first sentence to grab attention", "score": 1-10 estimated viral potential}}]}}
Write topic, angle and hook in {lang_name(channel.get('language', 'vi'))}."""
    data = llm.complete_json(prompt, system=SYSTEM_CREATOR)
    ideas = data.get("ideas", data) if isinstance(data, dict) else data
    ideas = [i for i in ideas if isinstance(i, dict) and i.get("topic")]
    ideas.sort(key=lambda i: -float(i.get("score") or 0))
    return ideas


def write_script(llm: LLMClient, topic: str, channel: dict, video_cfg: dict, angle: str = "",
                 extra: str = "") -> dict:
    lang = channel.get("language", "vi")
    fmt = video_cfg.get("format", "landscape")
    target = int(video_cfg.get("target_duration", 60))
    words_per_sec = 2.6 if lang == "vi" else 2.4
    n_words = int(target * words_per_sec)
    n_scenes = max(3, min(30, round(target / (6 if fmt != "shorts" else 4))))
    prompt = f"""Write a complete YouTube video script.

Topic: {topic}
{f'Angle: {angle}' if angle else ''}
Channel niche: {channel.get('niche')} | Audience: {channel.get('audience')} | Style: {channel.get('style')}
Format: {"YouTube Shorts, vertical 9:16, very fast pacing" if fmt == "shorts" else "standard landscape 16:9 video"}
Target length: about {target} seconds of narration (~{n_words} words total), around {n_scenes} scenes.
Narration language: {lang_name(lang)}.
{extra}

Rules:
- Scene 1 must open with a strong hook (question, surprising fact, or bold promise) in the first 5 seconds.
- Each scene: 1-3 short spoken sentences, natural and conversational, no emojis, no markdown, no stage directions.
- End with a short call to action (like/subscribe/comment question).
- "visual_query": 2-5 ENGLISH keywords to search stock footage that literally shows the scene (e.g. "astronaut floating space station").
- "on_screen_text": optional very short caption (max 6 words) in {lang_name(lang)} or "".
- "effect": one of {EFFECTS} (camera motion when the visual is a still image).
- "transition": transition INTO the next scene, one of {XFADE_TRANSITIONS[:10] + ['none']}.
- "music_mood": one of calm, epic, happy, sad, mystery, tech, inspiring.

Return JSON:
{{"title": "catchy title (max 80 chars)", "hook": "...", "music_mood": "...",
  "thumbnail_text": "2-5 punchy words for the thumbnail",
  "scenes": [{{"narration": "...", "visual_query": "...", "on_screen_text": "...", "effect": "zoom_in", "transition": "fade"}}]}}"""
    data = llm.complete_json(prompt, system=SYSTEM_CREATOR)
    return normalize_script(data)


def _as_text(value) -> str:
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value).strip()
    return str(value or "").strip()


def normalize_script(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Kịch bản không đúng định dạng")
    scenes = data.get("scenes") or []
    clean = []
    for s in scenes:
        if isinstance(s, str):
            s = {"narration": s}
        narration = (s.get("narration") or s.get("text") or s.get("voiceover") or "").strip()
        if not narration:
            continue
        effect = s.get("effect") if s.get("effect") in EFFECTS else "auto"
        tr = s.get("transition") if s.get("transition") in XFADE_TRANSITIONS + ["none"] else ""
        clean.append({
            "narration": narration,
            "visual_query": _as_text(s.get("visual_query") or s.get("visual") or s.get("keywords")),
            "on_screen_text": _as_text(s.get("on_screen_text")),
            "effect": effect,
            "transition": tr,
        })
    if not clean:
        raise ValueError("Kịch bản không có cảnh nào")
    data["scenes"] = clean
    data["title"] = (data.get("title") or "").strip()[:100]
    return data


def critique_script(llm: LLMClient, script: dict, channel: dict) -> dict:
    """AI tự đọc lại kịch bản, chấm điểm và viết lại cho hay hơn."""
    prompt = f"""You are reviewing a YouTube script before production. Improve it:
- stronger hook in scene 1, tighter sentences, better retention, correct facts, natural {lang_name(channel.get('language', 'vi'))}.
- keep the same JSON structure and roughly the same length. Keep visual_query in English.

Script JSON:
{json.dumps(script, ensure_ascii=False)}

Return JSON: {{"score_before": 1-10, "changes": "short summary", "script": {{...improved script with same keys...}}}}"""
    data = llm.complete_json(prompt, system=SYSTEM_CREATOR, temperature=0.5)
    improved = data.get("script") if isinstance(data, dict) else None
    try:
        return normalize_script(improved) if improved else script
    except ValueError:
        return script


def generate_seo(llm: LLMClient, title: str, script_text: str, channel: dict, fmt: str = "landscape") -> dict:
    lang = channel.get("language", "vi")
    prompt = f"""Create YouTube SEO metadata for this video.
Working title: {title}
Language: {lang_name(lang)}
Format: {"Shorts" if fmt == "shorts" else "regular video"}
Script:
{script_text[:4000]}

Return JSON:
{{"title": "click-worthy but honest title, max 90 characters",
  "description": "3 short paragraphs: summary with keywords, key points as bullet lines, call to action. 600-1200 characters.",
  "tags": ["15-25 search tags, mix of broad and long-tail, no # symbol"],
  "hashtags": ["3-5 hashtags without #"]}}
All text in {lang_name(lang)} (tags may include English)."""
    data = llm.complete_json(prompt, system=SYSTEM_CREATOR, temperature=0.6)
    title_out = (data.get("title") or title).strip().strip('"')[:100]
    tags = [re.sub(r"^#", "", str(t)).strip() for t in data.get("tags", []) if str(t).strip()]
    hashtags = [re.sub(r"[^\w]", "", str(t)) for t in data.get("hashtags", []) if str(t).strip()]
    if fmt == "shorts" and "Shorts" not in hashtags:
        hashtags.append("Shorts")
    return {"title": title_out, "description": (data.get("description") or "").strip(), "tags": tags[:30],
            "hashtags": hashtags[:5]}
