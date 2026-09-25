"""LLM giả lập để test mà không cần chạy Ollama."""
from __future__ import annotations

import json

SCRIPT = {
    "title": "5 sự thật thú vị về bạch tuộc",
    "hook": "Bạn có biết bạch tuộc có ba trái tim?",
    "music_mood": "mystery",
    "thumbnail_text": "Bạch tuộc 3 tim",
    "scenes": [
        {"narration": "Bạn có biết bạch tuộc có tới ba trái tim không?", "visual_query": "octopus underwater",
         "on_screen_text": "3 trái tim", "effect": "zoom_in", "transition": "fade"},
        {"narration": "Máu của chúng có màu xanh lam, vì chứa đồng thay vì sắt.", "visual_query": "blue blood cells",
         "on_screen_text": "", "effect": "pan_left", "transition": "wipeleft"},
        {"narration": "Hãy bấm theo dõi để xem thêm nhiều điều thú vị nhé!", "visual_query": "subscribe button",
         "on_screen_text": "", "effect": "auto", "transition": "none"},
    ],
}


class FakeLLM:
    native_tools = True
    model = "fake"

    def __init__(self, agent_calls: list[tuple[str, dict]] | None = None):
        self.agent_calls = list(agent_calls or [])
        self.prompts: list[str] = []

    def chat(self, messages, tools=None, json_mode=False, temperature=None):
        if tools is not None or (messages and "AutoTube" in messages[0].get("content", "")):
            if self.agent_calls:
                name, args = self.agent_calls.pop(0)
                if self.native_tools:
                    return {"content": "", "tool_calls": [{"id": name, "name": name, "arguments": args}]}
                return {"content": json.dumps({"thought": "ok", "tool": name, "args": args}), "tool_calls": []}
            return {"content": "done", "tool_calls": []}
        return {"content": self.complete(messages[-1]["content"]), "tool_calls": []}

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Brainstorm" in prompt:
            return json.dumps({"ideas": [{"topic": "Bạch tuộc", "angle": "sự thật lạ", "hook": "?", "score": 9}]})
        if "reviewing a YouTube script" in prompt:
            return "```json\n" + json.dumps({"score_before": 7, "changes": "ok", "script": SCRIPT}) + "\n```"
        if "complete YouTube video script" in prompt:
            return json.dumps(SCRIPT, ensure_ascii=False)
        if "SEO metadata" in prompt:
            return json.dumps({"title": "Bạch tuộc có 3 trái tim?!", "description": "Mô tả video.",
                               "tags": ["bạch tuộc", "khoa học"], "hashtags": ["khoahoc"]})
        return "{}"

    def complete_json(self, prompt, system=None, retries=3, **kw):
        return json.loads(self.complete(prompt).strip("`").removeprefix("json\n"))
