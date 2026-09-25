"""Agent: AI local tự lên kế hoạch và tự gọi công cụ để làm video từ A tới Z.

Hỗ trợ 2 giao thức:
  * native  : tool-calling gốc của Ollama / OpenAI-compatible (qwen2.5, llama3.1, mistral-nemo...)
  * json    : model trả lời bằng JSON {"thought": "...", "tool": "...", "args": {...}} — dùng cho model
              không hỗ trợ tool-calling.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field

from ..content.generator import lang_name
from ..llm import LLMClient, LLMError, extract_json
from ..studio import Studio
from .tools import FinishSignal, Tool, build_tools, result_to_text

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are AutoTube, an autonomous AI video producer running on the owner's computer.
You own a YouTube channel and produce complete videos BY YOURSELF using the tools provided.

CHANNEL
- Niche: {niche}
- Audience: {audience}
- Style: {style}
- Content language: {language}
- Video format: {format} | target length ≈ {target}s

YOUR JOB
Create ONE original, high-quality video and {publish_goal}. You decide the topic, write the content,
choose visuals, voice, music, subtitles, edit and review it yourself.

TYPICAL WORKFLOW (adapt freely — you are the creative director):
1. Get inspiration: brainstorm_ideas (and optionally get_trending_topics). Pick the best idea.
2. Create the script: write_script (scriptwriter) OR set_script (write it yourself). view_script / edit_scene / improve_script to polish.
3. find_visuals_for_all_scenes, then fix weak scenes with set_scene_media (better English query) or generate_image.
4. generate_voiceover (you may pick a voice), choose_background_music (by mood), generate_subtitles.
5. render_video, then create_thumbnail and generate_seo (or set_metadata).
6. review_project. Fix any issue it reports, re-render if you changed scenes.
7. {publish_step}
8. finish with a short summary.

RULES
- Call exactly the tools you need; never invent tool names or file paths.
- Scene indexes start at 0.
- After changing narration you must generate_voiceover again, then render_video again.
- All spoken/visible text must be in {language}; stock search queries in English.
- Be concise in your own messages. Always make progress by calling a tool.
"""

JSON_PROTOCOL = """
TOOL PROTOCOL (IMPORTANT): you cannot call functions natively. To use a tool reply with ONLY one JSON object:
{{"thought": "short reasoning", "tool": "<tool_name>", "args": {{...}}}}
You will then receive the tool result. Available tools:
{tool_list}
"""


@dataclass
class AgentResult:
    finished: bool
    summary: str
    steps: int
    project_dir: str | None
    youtube_url: str | None
    transcript: list[dict] = field(default_factory=list)


class VideoAgent:
    def __init__(self, studio: Studio, llm: LLMClient | None = None, max_steps: int | None = None,
                 allow_upload: bool | None = None, max_history: int = 24, on_event=None):
        self.studio = studio
        self.llm = llm or studio.llm
        acfg = studio.cfg.section("agent")
        self.max_steps = int(max_steps or acfg.get("max_steps", 40))
        self.allow_upload = acfg.get("allow_upload", True) if allow_upload is None else allow_upload
        self.tools: dict[str, Tool] = build_tools(studio, self.allow_upload)
        self.max_history = max_history
        self.on_event = on_event or (lambda kind, data: None)
        self.native = self.llm.native_tools

    # ------------------------------------------------------------------ prompt
    def system_prompt(self) -> str:
        cfg = self.studio.cfg
        ch, v = cfg.section("channel"), cfg.section("video")
        prompt = SYSTEM_PROMPT.format(
            niche=ch.get("niche"), audience=ch.get("audience"), style=ch.get("style"),
            language=lang_name(ch.get("language", "vi")), format=v.get("format", "landscape"),
            target=v.get("target_duration", 60),
            publish_goal="publish it on YouTube" if self.allow_upload else "prepare it for publishing (do NOT upload)",
            publish_step="upload_to_youtube" if self.allow_upload else "(uploading is disabled — skip it)",
        )
        if not self.native:
            lines = []
            for t in self.tools.values():
                params = ", ".join(
                    f"{k}{'' if k in t.parameters.get('required', []) else '?'}:{p.get('type', 'any')}"
                    for k, p in t.parameters.get("properties", {}).items())
                lines.append(f"- {t.name}({params}): {t.description}")
            prompt += JSON_PROTOCOL.format(tool_list="\n".join(lines))
        return prompt

    # ------------------------------------------------------------------ run
    def run(self, task: str = "") -> AgentResult:
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": task or "Start now. Produce today's video end-to-end."},
        ]
        schemas = [t.schema() for t in self.tools.values()] if self.native else None
        transcript: list[dict] = []
        idle = 0
        summary, finished = "", False
        for step in range(1, self.max_steps + 1):
            self._trim(messages)
            try:
                reply = self.llm.chat(messages, tools=schemas, json_mode=not self.native)
            except LLMError as exc:
                if self.native and "does not support tools" in str(exc):
                    log.warning("Model không hỗ trợ tool-calling gốc → chuyển sang giao thức JSON")
                    self.native, schemas = False, None
                    self.llm.native_tools = False
                    messages[0]["content"] = self.system_prompt()
                    continue
                raise
            calls = reply["tool_calls"]
            content = reply["content"] or ""
            if not calls and content:
                calls = self._parse_json_call(content)
            if content.strip():
                self.on_event("thought", content.strip()[:500])

            if not calls:
                idle += 1
                messages.append({"role": "assistant", "content": content})
                if idle >= 3:
                    summary = "Agent dừng vì không gọi công cụ nào."
                    break
                messages.append({"role": "user", "content": self._nudge()})
                continue
            idle = 0

            if self.native:
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            else:
                messages.append({"role": "assistant", "content": content})

            for call in calls:
                name, args = call.get("name"), call.get("arguments") or {}
                self.on_event("tool_call", {"step": step, "tool": name, "args": args})
                try:
                    result = self._execute(name, args)
                    ok = True
                except FinishSignal as fin:
                    summary, finished = fin.summary, True
                    transcript.append({"step": step, "tool": name, "args": args, "result": "finish"})
                    break
                except Exception as exc:
                    ok = False
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                    log.debug(traceback.format_exc())
                text = result_to_text(result)
                self.on_event("tool_result", {"tool": name, "ok": ok, "result": text[:400]})
                transcript.append({"step": step, "tool": name, "args": args, "ok": ok, "result": text[:1000]})
                if self.native:
                    messages.append({"role": "tool", "name": name, "tool_call_id": call.get("id", ""), "content": text})
                else:
                    messages.append({"role": "user", "content": f"Result of {name}: {text}\nWhat next? Reply with one JSON tool call."})
            if finished:
                break
        else:
            summary = summary or f"Hết số bước tối đa ({self.max_steps})."

        p = self.studio.project
        return AgentResult(finished, summary, len(transcript), p.dir if p else None,
                           p.youtube_url if p else None, transcript)

    # ------------------------------------------------------------------ helpers
    def _execute(self, name: str, args: dict):
        tool = self.tools.get(name or "")
        if tool is None:
            return {"error": f"Unknown tool '{name}'. Available: {', '.join(self.tools)}"}
        if not isinstance(args, dict):
            args = {}
        allowed = set(tool.parameters.get("properties", {}))
        clean = {k: v for k, v in args.items() if k in allowed and v is not None}
        missing = [r for r in tool.parameters.get("required", []) if r not in clean]
        if missing:
            return {"error": f"Missing required arguments: {missing}"}
        t0 = time.time()
        result = tool.handler(**clean)
        log.info("  ✓ %s (%.1fs)", name, time.time() - t0)
        return result

    def _parse_json_call(self, content: str) -> list[dict]:
        try:
            data = extract_json(content)
        except ValueError:
            return []
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return []
        name = data.get("tool") or data.get("name") or data.get("action") or data.get("function")
        if isinstance(name, dict):  # {"function": {"name":..., "arguments":...}}
            data, name = name, name.get("name")
        args = data.get("args") or data.get("arguments") or data.get("parameters") or data.get("input") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not name:
            return []
        return [{"id": f"json{int(time.time() * 1000)}", "name": name, "arguments": args}]

    def _nudge(self) -> str:
        status = self.studio.project.summary() if self.studio.project else {"project": "none yet"}
        base = f"Progress so far: {json.dumps(status, ensure_ascii=False)}\n"
        if self.native:
            return base + "Continue by calling the next tool. When everything is done call finish."
        return base + 'Continue: reply with ONLY a JSON object {"thought": ..., "tool": ..., "args": {...}}.'

    def _trim(self, messages: list[dict]) -> None:
        """Giữ lịch sử hội thoại gọn để vừa context của model nhỏ."""
        if len(messages) <= self.max_history + 2:
            return
        head, tail = messages[:2], messages[2:]
        tail = tail[-self.max_history:]
        while tail and tail[0]["role"] in ("tool",):
            tail.pop(0)
        while tail and tail[0]["role"] == "user" and tail[0]["content"].startswith("Result of"):
            tail.pop(0)
        status = self.studio.project.summary() if self.studio.project else {}
        note = {"role": "user", "content": "(Older steps were removed to save memory.) Current project status: "
                + json.dumps(status, ensure_ascii=False)}
        messages[:] = head + [note] + tail
