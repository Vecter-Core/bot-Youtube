"""Kiểm tra logic upload YouTube với service giả (không gọi Google thật)."""
from autotube.youtube import uploader as up


class _Req:
    def __init__(self, result):
        self.result = result

    def next_chunk(self):
        return None, self.result

    def execute(self):
        return self.result


class FakeService:
    def __init__(self):
        self.calls = []

    def videos(self):
        svc = self

        class V:
            def insert(self, part, body, media_body):
                svc.calls.append(("videos.insert", body))
                return _Req({"id": "VID123"})
        return V()

    def thumbnails(self):
        svc = self

        class T:
            def set(self, videoId, media_body):
                svc.calls.append(("thumbnails.set", videoId))
                return _Req({})
        return T()

    def captions(self):
        svc = self

        class C:
            def insert(self, part, body, media_body):
                svc.calls.append(("captions.insert", body))
                return _Req({})
        return C()


def test_upload_builds_request(cfg, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 1000)
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8\xff")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    fake = FakeService()
    u = up.YouTubeUploader(cfg)
    u._service = fake
    res = u.upload(video, "Tiêu đề" * 30, "Mô tả", ["tag1", "tag hai"] * 100, publish_at="2030-01-01T00:00:00Z",
                   thumbnail=thumb, captions=srt)
    assert res["url"] == "https://youtu.be/VID123" and res["thumbnail"] and res["captions"]
    body = fake.calls[0][1]
    assert len(body["snippet"]["title"]) <= 100
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"]
    assert sum(len(t) + (2 if " " in t else 0) + 1 for t in body["snippet"]["tags"]) <= 500
    assert [c[0] for c in fake.calls] == ["videos.insert", "thumbnails.set", "captions.insert"]
