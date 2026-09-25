from autotube.audio.tts import estimate_word_timings
from autotube.content.generator import normalize_script
from autotube.llm import extract_json
from autotube.project import slugify
from autotube.subtitles.subs import group_words, write_ass, write_srt
from autotube.video.editor import transition_overlaps


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": [1,2,]}\n```') == {"a": [1, 2]}
    assert extract_json('<think>hmm {"x":0}</think> Here: {"b": "c"} thanks') == {"b": "c"}
    assert extract_json('[{"t": 1}]') == [{"t": 1}]


def test_slugify_vietnamese():
    assert slugify("Đường đi của Bạch Tuộc!") == "duong-di-cua-bach-tuoc"


def test_normalize_script():
    data = normalize_script({"title": "x", "scenes": [{"text": "Xin chào", "keywords": ["a", "b"]}, {"narration": ""},
                                                       "Cảnh chuỗi"]})
    assert len(data["scenes"]) == 2
    assert data["scenes"][0]["visual_query"] == "a b"
    assert data["scenes"][0]["effect"] == "auto"


def test_word_timings_and_subtitles(tmp_path):
    words = estimate_word_timings("Xin chào các bạn. Hôm nay chúng ta sẽ học về vũ trụ bao la, rộng lớn.", 5.0)
    assert words[0]["start"] >= 0 and words[-1]["end"] <= 5.0
    cues = group_words(words, max_words=5)
    assert cues[0]["text"] == "Xin chào các bạn."
    srt = write_srt(cues, tmp_path / "a.srt").read_text(encoding="utf-8")
    assert "00:00:00," in srt and "-->" in srt
    ass = write_ass(cues, tmp_path / "a.ass", (1920, 1080)).read_text(encoding="utf-8")
    assert "\\k" in ass and "PlayResX: 1920" in ass


def test_transition_overlaps():
    assert transition_overlaps(["none", "none"], 0.5) == [0.0, 0.0]
    assert transition_overlaps(["fade", "none"], 0.5, 25) == [0.5, 0.04]
