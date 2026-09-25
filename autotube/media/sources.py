"""Tìm hình ảnh/video minh hoạ cho từng cảnh.

Nguồn: thư mục local của bạn, Pexels, Pixabay (miễn phí, cần API key),
Stable Diffusion local (AUTOMATIC1111/Forge --api), hoặc ảnh nền tự tạo (placeholder).
"""
from __future__ import annotations

import base64
import logging
import random
import re
from pathlib import Path

import requests

from ..project import slugify
from ..video.editor import IMAGE_EXT, VIDEO_EXT
from ..video.text_render import gradient_background

log = logging.getLogger(__name__)
UA = {"User-Agent": "autotube/1.0"}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", slugify(text, 200).replace("-", " ")) if len(w) > 2}


def _download(url: str, out: Path, headers: dict | None = None) -> Path:
    with requests.get(url, stream=True, timeout=120, headers={**UA, **(headers or {})}) as r:
        r.raise_for_status()
        with open(out, "wb") as fh:
            for chunk in r.iter_content(1 << 16):
                fh.write(chunk)
    return out


class MediaFinder:
    def __init__(self, cfg: dict, orientation: str = "landscape", size: tuple[int, int] = (1920, 1080)):
        self.cfg = cfg
        self.orientation = "portrait" if orientation in ("shorts", "vertical") else (
            "square" if orientation == "square" else "landscape")
        self.size = size
        self.used: set[str] = set()

    # ------------------------------------------------------------------ search
    def search(self, query: str, source: str = "all", kind: str = "any", limit: int = 6) -> list[dict]:
        """Trả về danh sách ứng viên [{source, kind, url|path, preview, width, height, duration}]."""
        sources = self.cfg.get("sources") or ["local", "pexels", "pixabay"]
        if source != "all":
            sources = [source]
        results: list[dict] = []
        for src in sources:
            try:
                if src == "local":
                    results += self._search_local(query, kind, limit)
                elif src == "pexels" and self.cfg.get("pexels_api_key"):
                    results += self._search_pexels(query, kind, limit)
                elif src == "pixabay" and self.cfg.get("pixabay_api_key"):
                    results += self._search_pixabay(query, kind, limit)
            except Exception as exc:
                log.warning("Tìm media trên %s lỗi: %s", src, exc)
        return results

    def _search_local(self, query: str, kind: str, limit: int) -> list[dict]:
        root = Path(self.cfg.get("local_dir") or "assets/media")
        if not root.is_absolute():
            from ..config import ROOT

            root = ROOT / root
        if not root.exists():
            return []
        q = _keywords(query)
        scored = []
        for p in root.rglob("*"):
            ext = p.suffix.lower()
            k = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None
            if not k or (kind != "any" and k != kind):
                continue
            score = len(q & _keywords(str(p.relative_to(root))))
            if score:
                scored.append((score, p, k))
        scored.sort(key=lambda x: -x[0])
        return [{"source": "local", "kind": k, "path": str(p), "score": s} for s, p, k in scored[:limit]]

    def _search_pexels(self, query: str, kind: str, limit: int) -> list[dict]:
        headers = {"Authorization": self.cfg["pexels_api_key"]}
        out = []
        if kind in ("any", "video"):
            r = requests.get("https://api.pexels.com/videos/search", headers=headers, timeout=30,
                             params={"query": query, "per_page": limit, "orientation": self.orientation})
            r.raise_for_status()
            for v in r.json().get("videos", []):
                files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
                if not files:
                    continue
                target = max(self.size)
                files.sort(key=lambda f: (abs(max(f["width"], f["height"]) - target), -f["width"]))
                best = files[0]
                out.append({"source": "pexels", "kind": "video", "url": best["link"], "id": f"pexels-v{v['id']}",
                            "width": best["width"], "height": best["height"], "duration": v.get("duration"),
                            "preview": v.get("image"), "credit": v.get("user", {}).get("name", "")})
        if kind in ("any", "image"):
            r = requests.get("https://api.pexels.com/v1/search", headers=headers, timeout=30,
                             params={"query": query, "per_page": limit, "orientation": self.orientation})
            r.raise_for_status()
            for p in r.json().get("photos", []):
                out.append({"source": "pexels", "kind": "image", "url": p["src"].get("large2x") or p["src"]["original"],
                            "id": f"pexels-p{p['id']}", "width": p["width"], "height": p["height"],
                            "preview": p["src"].get("medium"), "credit": p.get("photographer", "")})
        return out

    def _search_pixabay(self, query: str, kind: str, limit: int) -> list[dict]:
        key = self.cfg["pixabay_api_key"]
        out = []
        q = query[:100]
        if kind in ("any", "video"):
            r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                             params={"key": key, "q": q, "per_page": max(3, limit), "safesearch": "true"})
            r.raise_for_status()
            for h in r.json().get("hits", []):
                vids = h.get("videos", {})
                best = vids.get("large") if vids.get("large", {}).get("url") else vids.get("medium")
                if best and best.get("url"):
                    out.append({"source": "pixabay", "kind": "video", "url": best["url"], "id": f"pixabay-v{h['id']}",
                                "width": best.get("width"), "height": best.get("height"), "duration": h.get("duration"),
                                "credit": h.get("user", "")})
        if kind in ("any", "image"):
            orient = {"portrait": "vertical", "landscape": "horizontal"}.get(self.orientation, "all")
            r = requests.get("https://pixabay.com/api/", timeout=30,
                             params={"key": key, "q": q, "per_page": max(3, limit), "image_type": "photo",
                                     "orientation": orient, "safesearch": "true"})
            r.raise_for_status()
            for h in r.json().get("hits", []):
                out.append({"source": "pixabay", "kind": "image", "url": h["largeImageURL"], "id": f"pixabay-p{h['id']}",
                            "width": h.get("imageWidth"), "height": h.get("imageHeight"), "credit": h.get("user", "")})
        return out

    # ------------------------------------------------------------------ fetch
    def fetch(self, candidate: dict, out_dir: Path, name: str) -> Path:
        """Tải/copy một ứng viên về thư mục dự án."""
        out_dir.mkdir(parents=True, exist_ok=True)
        if candidate.get("path"):
            p = Path(candidate["path"])
            self.used.add(str(p))
            return p
        ext = ".mp4" if candidate["kind"] == "video" else Path(candidate["url"].split("?")[0]).suffix or ".jpg"
        out = out_dir / f"{name}{ext}"
        _download(candidate["url"], out)
        self.used.add(candidate.get("id") or candidate["url"])
        return out

    def best_for(self, query: str, out_dir: Path, name: str, prefer_video: bool | None = None,
                 fallback_text: str = "") -> tuple[Path, str, str]:
        """Tự chọn media tốt nhất cho câu truy vấn. Trả (path, kind, source)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        prefer_video = self.cfg.get("prefer_video", True) if prefer_video is None else prefer_video
        order = self.cfg.get("sources") or ["local", "pexels", "pixabay", "sd", "placeholder"]
        queries = [query] + ([" ".join(query.split()[:2])] if len(query.split()) > 2 else [])
        for src in order:
            if src == "sd":
                try:
                    return self.generate_sd(query, out_dir / f"{name}.png"), "image", "sd"
                except Exception as exc:
                    log.debug("Stable Diffusion không dùng được: %s", exc)
                continue
            if src == "placeholder":
                break
            for q in queries:
                cands = [c for c in self.search(q, source=src) if (c.get("id") or c.get("path") or c.get("url")) not in self.used]
                if not cands:
                    continue
                if prefer_video:
                    cands.sort(key=lambda c: 0 if c["kind"] == "video" else 1)
                else:
                    cands.sort(key=lambda c: 0 if c["kind"] == "image" else 1)
                top = cands[: 3 if src != "local" else 1]
                choice = random.choice(top)
                try:
                    return self.fetch(choice, out_dir, name), choice["kind"], src
                except Exception as exc:
                    log.warning("Tải media lỗi: %s", exc)
        out = gradient_background(self.size, out_dir / f"{name}.jpg", seed=query + name, text=fallback_text)
        return out, "image", "placeholder"

    def generate_sd(self, prompt: str, out: Path, negative: str = "text, watermark, blurry, lowres, deformed") -> Path:
        """Tạo ảnh bằng Stable Diffusion WebUI (AUTOMATIC1111/Forge chạy với tham số --api)."""
        url = (self.cfg.get("sd_url") or "http://127.0.0.1:7860").rstrip("/")
        w, h = self.size
        scale = 1024 / max(w, h)
        payload = {"prompt": f"{prompt}, highly detailed, cinematic lighting, 4k", "negative_prompt": negative,
                   "width": int(w * scale) // 64 * 64, "height": int(h * scale) // 64 * 64, "steps": 25, "cfg_scale": 6}
        r = requests.post(f"{url}/sdapi/v1/txt2img", json=payload, timeout=600)
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(r.json()["images"][0]))
        return out
