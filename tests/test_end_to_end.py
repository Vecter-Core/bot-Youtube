"""Chạy toàn bộ quy trình (không upload) với LLM giả lập + TTS dummy."""
from pathlib import Path

from fakes import SCRIPT, FakeLLM

from autotube.agent import VideoAgent
from autotube.pipeline import run_pipeline
from autotube.studio import Studio
from autotube.video import ffmpeg as ff


def test_pipeline_end_to_end(cfg):
    studio = Studio(cfg, llm=FakeLLM())
    res = run_pipeline(studio, upload=False)
    p = studio.p
    assert Path(res["video"]).exists()
    info = ff.probe(res["video"])
    assert info["has_audio"] and info["width"] == 1280
    # video phải khớp độ dài giọng đọc
    assert abs(info["duration"] - p.total_duration) < 0.5
    assert Path(p.thumbnail).exists() and Path(p.srt_path).exists()
    assert p.title == "Bạch tuộc có 3 trái tim?!"
    assert len(studio.history.items) == 1


def test_agent_drives_tools(cfg):
    calls = [
        ("brainstorm_ideas", {"count": 3}),
        ("set_script", {"title": SCRIPT["title"], "scenes": SCRIPT["scenes"], "music_mood": "calm"}),
        ("edit_scene", {"index": 1, "on_screen_text": "Máu xanh", "bogus_arg": 1}),
        ("find_visuals_for_all_scenes", {}),
        ("generate_voiceover", {}),
        ("choose_background_music", {"generate": True}),
        ("generate_subtitles", {"karaoke": True}),
        ("render_video", {"quality": "draft"}),
        ("not_a_tool", {}),
        ("create_thumbnail", {"text": "Bạch tuộc!"}),
        ("set_metadata", {"title": "Tiêu đề", "description": "Mô tả", "tags": ["a"]}),
        ("make_shorts_version", {"duration": 5}),
        ("review_project", {}),
        ("finish", {"summary": "Xong"}),
    ]
    llm = FakeLLM(calls)
    studio = Studio(cfg, llm=llm)
    agent = VideoAgent(studio, llm=llm, allow_upload=False)
    res = agent.run()
    assert res.finished and res.summary == "Xong"
    failed = [t for t in res.transcript if t.get("ok") is False]
    assert [t["tool"] for t in failed] == [], failed
    p = studio.p
    assert p.scenes[1].on_screen_text == "Máu xanh"
    assert Path(p.final_video).exists() and Path(p.extra["shorts_video"]).exists()
    assert ff.probe(p.extra["shorts_video"])["height"] == 1920
    unknown = [t for t in res.transcript if t["tool"] == "not_a_tool"][0]
    assert "Unknown tool" in unknown["result"]


def test_agent_json_protocol(cfg):
    calls = [("set_script", {"title": "T", "scenes": SCRIPT["scenes"][:1]}), ("view_script", {}),
             ("finish", {"summary": "ok"})]
    llm = FakeLLM(calls)
    llm.native_tools = False
    studio = Studio(cfg, llm=llm)
    res = VideoAgent(studio, llm=llm, allow_upload=False).run()
    assert res.finished and len(studio.p.scenes) == 1
