"""Đăng video lên YouTube bằng YouTube Data API v3 (OAuth 2.0).

Chuẩn bị (làm 1 lần — xem README):
  1. Google Cloud Console -> tạo project -> bật "YouTube Data API v3".
  2. Tạo OAuth client ID loại "Desktop app" -> tải file JSON về credentials/client_secret.json
  3. Chạy: python -m autotube auth   (mở trình duyệt để đăng nhập kênh)
"""
from __future__ import annotations

import logging
import random
import time
from pathlib import Path

from ..config import Config

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",  # cần cho thumbnail & phụ đề
]
RETRIABLE_STATUS = {500, 502, 503, 504}


class YouTubeUploader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        yt = cfg.section("youtube")
        self.client_secrets = cfg.resolve(yt.get("client_secrets", "credentials/client_secret.json"))
        self.token_file = cfg.resolve(yt.get("token_file", "credentials/token.json"))
        self._service = None

    # ------------------------------------------------------------------ auth
    def credentials(self, interactive: bool = True):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                log.warning("Làm mới token thất bại (%s) — cần đăng nhập lại", exc)
                creds = None
        if not creds or not creds.valid:
            if not interactive:
                raise RuntimeError("Chưa đăng nhập YouTube. Chạy: python -m autotube auth")
            if not self.client_secrets.exists():
                raise FileNotFoundError(
                    f"Thiếu {self.client_secrets}. Tải OAuth client (Desktop app) từ Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets), SCOPES)
            creds = flow.run_local_server(port=0, prompt="consent")
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build("youtube", "v3", credentials=self.credentials(interactive=True),
                                  cache_discovery=False)
        return self._service

    def channel_info(self) -> dict:
        res = self.service.channels().list(part="snippet,statistics", mine=True).execute()
        items = res.get("items", [])
        if not items:
            return {}
        it = items[0]
        return {"id": it["id"], "title": it["snippet"]["title"], **it.get("statistics", {})}

    # ------------------------------------------------------------------ upload
    def upload(
        self,
        video: str | Path,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
        privacy: str | None = None,
        publish_at: str | None = None,
        thumbnail: str | Path | None = None,
        captions: str | Path | None = None,
        caption_language: str | None = None,
        category_id: str | None = None,
    ) -> dict:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        yt = self.cfg.section("youtube")
        privacy = privacy or yt.get("privacy", "private")
        publish_at = publish_at if publish_at is not None else (yt.get("publish_at") or None)
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(yt.get("made_for_kids", False))}
        if yt.get("contains_synthetic_media"):
            status["containsSyntheticMedia"] = True  # nhãn "Nội dung được thay đổi hoặc tổng hợp"
        if publish_at:
            status["privacyStatus"] = "private"  # bắt buộc private khi hẹn giờ
            status["publishAt"] = publish_at
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": _limit_tags(tags or []),
                "categoryId": str(category_id or yt.get("category_id", "22")),
                "defaultLanguage": yt.get("default_language", "vi"),
                "defaultAudioLanguage": yt.get("default_language", "vi"),
            },
            "status": status,
        }
        media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        request = self.service.videos().insert(part="snippet,status", body=body, media_body=media)
        response, retry = None, 0
        log.info("Đang tải video lên YouTube: %s", title)
        while response is None:
            try:
                progress, response = request.next_chunk()
                if progress:
                    log.info("  ... %d%%", int(progress.progress() * 100))
            except HttpError as exc:
                if exc.resp.status not in RETRIABLE_STATUS or retry >= 8:
                    raise
                retry += 1
                wait = min(64, 2 ** retry) + random.random()
                log.warning("Lỗi tạm thời %s, thử lại sau %.1fs", exc.resp.status, wait)
                time.sleep(wait)
            except (ConnectionError, TimeoutError, OSError) as exc:
                if retry >= 8:
                    raise
                retry += 1
                wait = min(64, 2 ** retry) + random.random()
                log.warning("Lỗi mạng %s, thử lại sau %.1fs", exc, wait)
                time.sleep(wait)
        video_id = response["id"]
        result = {"id": video_id, "url": f"https://youtu.be/{video_id}", "privacy": status["privacyStatus"],
                  "publish_at": publish_at}
        if thumbnail and Path(thumbnail).exists():
            try:
                self.service.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail))).execute()
                result["thumbnail"] = True
            except HttpError as exc:
                # Kênh chưa xác minh số điện thoại sẽ không đặt được thumbnail tuỳ chỉnh
                log.warning("Không đặt được thumbnail: %s", exc)
                result["thumbnail"] = False
        if captions and Path(captions).exists():
            try:
                lang = caption_language or yt.get("default_language", "vi")
                self.service.captions().insert(
                    part="snippet",
                    body={"snippet": {"videoId": video_id, "language": lang, "name": lang.upper(), "isDraft": False}},
                    media_body=MediaFileUpload(str(captions), mimetype="application/octet-stream"),
                ).execute()
                result["captions"] = True
            except HttpError as exc:
                log.warning("Không upload được phụ đề: %s", exc)
                result["captions"] = False
        log.info("Đã đăng: %s", result["url"])
        return result


def _limit_tags(tags: list[str], max_chars: int = 480) -> list[str]:
    """YouTube giới hạn tổng độ dài tag ~500 ký tự."""
    out, total = [], 0
    for t in tags:
        t = t.strip().replace("<", "").replace(">", "")
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if total + cost > max_chars:
            break
        out.append(t)
        total += cost
    return out
