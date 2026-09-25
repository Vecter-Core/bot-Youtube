"""Kiểm tra LLMClient với server HTTP giả lập định dạng Ollama và OpenAI."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from autotube.llm import LLMClient, LLMError


class Handler(BaseHTTPRequestHandler):
    requests: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.requests.append((self.path, body))
        if self.path == "/api/chat":
            if body.get("tools"):
                out = {"message": {"role": "assistant", "content": "",
                                   "tool_calls": [{"function": {"name": "render_video", "arguments": {"quality": "draft"}}}]}}
            else:
                out = {"message": {"role": "assistant", "content": '{"ok": true}'}}
        elif self.path == "/v1/chat/completions":
            out = {"choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "finish", "arguments": '{"summary": "x"}'}}]}}]}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"models": [{"name": "qwen2.5:7b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_ollama_tool_calls(server):
    llm = LLMClient({"provider": "ollama", "base_url": server, "model": "qwen2.5:7b"})
    assert llm.list_models() == ["qwen2.5:7b"]
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "a", "arguments": {"x": 1}}]},
            {"role": "tool", "name": "a", "tool_call_id": "1", "content": "ok"}]
    res = llm.chat(msgs, tools=[{"type": "function", "function": {"name": "render_video"}}])
    assert res["tool_calls"][0]["name"] == "render_video"
    assert res["tool_calls"][0]["arguments"] == {"quality": "draft"}
    sent = Handler.requests[-1][1]
    assert sent["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"x": 1}
    assert sent["messages"][2]["tool_name"] == "a"
    assert llm.complete_json("give json") == {"ok": True}
    assert Handler.requests[-1][1]["format"] == "json"


def test_openai_compat(server):
    llm = LLMClient({"provider": "openai_compat", "base_url": server + "/v1", "model": "m"})
    res = llm.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "finish"}}])
    assert res["tool_calls"][0]["arguments"] == {"summary": "x"}


def test_connection_error():
    llm = LLMClient({"provider": "ollama", "base_url": "http://127.0.0.1:9", "timeout": 2})
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert llm.ping() is False
