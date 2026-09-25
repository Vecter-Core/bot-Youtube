"""Kết nối tới AI local.

Hỗ trợ:
  * Ollama  (/api/chat, có tool-calling gốc)
  * Server tương thích OpenAI: LM Studio, llama.cpp, vLLM, Jan, KoboldCpp... (/v1/chat/completions)

Mọi phản hồi được chuẩn hoá về dạng:
    {"content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import requests

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> Any:
    """Lấy JSON từ câu trả lời của LLM (chịu được ```json ...``` và chữ thừa)."""
    if text is None:
        raise ValueError("empty response")
    text = text.strip()
    # Bỏ phần suy nghĩ của các model "reasoning" (deepseek-r1, qwen3...)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Tìm khối {...} hoặc [...] cân bằng đầu tiên
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            # thử sửa dấu phẩy thừa
                            fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                            try:
                                return json.loads(fixed)
                            except json.JSONDecodeError:
                                break
            start = text.find(opener, start + 1)
    raise ValueError(f"Không tìm thấy JSON hợp lệ trong phản hồi: {text[:300]}")


class LLMClient:
    def __init__(self, cfg: dict):
        self.provider = (cfg.get("provider") or "ollama").lower()
        self.base_url = (cfg.get("base_url") or "http://localhost:11434").rstrip("/")
        self.model = cfg.get("model") or "qwen2.5:7b"
        self.api_key = cfg.get("api_key") or ""
        self.temperature = float(cfg.get("temperature", 0.8))
        self.num_ctx = int(cfg.get("num_ctx", 8192))
        self.timeout = int(cfg.get("timeout", 600))
        self.native_tools = bool(cfg.get("native_tools", True))

    # ------------------------------------------------------------------ public
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> dict:
        temp = self.temperature if temperature is None else temperature
        if self.provider == "ollama":
            return self._chat_ollama(messages, tools, json_mode, temp)
        if self.provider in ("openai", "openai_compat", "lmstudio", "llamacpp", "vllm"):
            return self._chat_openai(messages, tools, json_mode, temp)
        raise LLMError(f"provider không hỗ trợ: {self.provider}")

    def complete(self, prompt: str, system: str | None = None, **kw) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, **kw)["content"]

    def complete_json(self, prompt: str, system: str | None = None, retries: int = 3, **kw) -> Any:
        """Yêu cầu LLM trả JSON; tự thử lại và nhắc sửa nếu JSON lỗi."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        last_err: Exception | None = None
        for attempt in range(retries):
            reply = self.chat(messages, json_mode=True, **kw)["content"]
            try:
                return extract_json(reply)
            except ValueError as exc:
                last_err = exc
                log.warning("LLM trả JSON lỗi (lần %d): %s", attempt + 1, exc)
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {"role": "user", "content": "Your previous answer was not valid JSON. Reply again with ONLY valid JSON, no extra text."}
                )
        raise LLMError(f"LLM không trả về JSON hợp lệ: {last_err}")

    def ping(self) -> bool:
        try:
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            else:
                r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=5)
            return r.ok
        except requests.RequestException:
            return False

    def list_models(self) -> list[str]:
        try:
            if self.provider == "ollama":
                r = requests.get(f"{self.base_url}/api/tags", timeout=10)
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except requests.RequestException as exc:
            raise LLMError(f"Không kết nối được AI local tại {self.base_url}: {exc}") from exc

    # ---------------------------------------------------------------- ollama
    def _chat_ollama(self, messages, tools, json_mode, temperature) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._to_ollama_msg(m) for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx},
        }
        if tools and self.native_tools:
            payload["tools"] = tools
        if json_mode and not tools:
            payload["format"] = "json"
        data = self._post(f"{self.base_url}/api/chat", payload)
        msg = data.get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append({"id": tc.get("id") or uuid.uuid4().hex[:8], "name": fn.get("name"), "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}

    @staticmethod
    def _to_ollama_msg(m: dict) -> dict:
        out = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("tool_calls"):
            out["tool_calls"] = [
                {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in m["tool_calls"]
            ]
        if m["role"] == "tool" and m.get("name"):
            out["tool_name"] = m["name"]
        return out

    # ---------------------------------------------------------------- openai
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _chat_openai(self, messages, tools, json_mode, temperature) -> dict:
        conv = []
        for m in messages:
            item: dict[str, Any] = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
                    }
                    for c in m["tool_calls"]
                ]
            if m["role"] == "tool":
                item["tool_call_id"] = m.get("tool_call_id", "")
            conv.append(item)
        payload: dict[str, Any] = {"model": self.model, "messages": conv, "temperature": temperature}
        if tools and self.native_tools:
            payload["tools"] = tools
        if json_mode and not tools:
            payload["response_format"] = {"type": "json_object"}
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
        try:
            data = self._post(url, payload, headers=self._headers())
        except LLMError:
            if "response_format" not in payload:
                raise
            payload.pop("response_format")  # một số server không hỗ trợ
            data = self._post(url, payload, headers=self._headers())
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = {"_raw": raw}
            calls.append({"id": tc.get("id") or uuid.uuid4().hex[:8], "name": fn.get("name"), "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}

    # ---------------------------------------------------------------- http
    def _post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise LLMError(
                f"Không kết nối được AI local tại {url}. Hãy chắc chắn Ollama/LM Studio đang chạy. ({exc})"
            ) from exc
        if not r.ok:
            raise LLMError(f"AI local trả lỗi {r.status_code}: {r.text[:500]}")
        return r.json()
