"""Tạo thumbnail 1280x720 (hoặc dọc cho Shorts) với chữ lớn nổi bật."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from . import editor
from .text_render import draw_text_block, fit_cover, gradient_background

STYLES = {
    "bold_yellow": {"color": "#FFE600", "stroke": "#000000"},
    "white_red": {"color": "#FFFFFF", "stroke": "#C00000"},
    "white_black": {"color": "#FFFFFF", "stroke": "#000000"},
    "cyan": {"color": "#00F0FF", "stroke": "#001018"},
}


def make_thumbnail(
    background: str | Path | None,
    text: str,
    out: str | Path,
    vertical: bool = False,
    style: str = "bold_yellow",
    at_time: float = 1.0,
) -> Path:
    size = (720, 1280) if vertical else (1280, 720)
    out = Path(out)
    bg_img: Image.Image | None = None
    if background:
        bg = Path(background)
        if editor.media_kind(bg) == "video":
            frame = out.with_name(out.stem + "_frame.jpg")
            editor.extract_frame(bg, frame, t=at_time)
            bg = frame
        try:
            bg_img = Image.open(bg).convert("RGB")
        except Exception:
            bg_img = None
    if bg_img is None:
        gradient_background(size, out.with_suffix(".bg.jpg"), seed=text)
        bg_img = Image.open(out.with_suffix(".bg.jpg")).convert("RGB")

    img = fit_cover(bg_img, size)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.25)
    # làm tối nhẹ nửa dưới để chữ nổi
    shade = Image.linear_gradient("L").resize(size).point(lambda v: int(v * 0.75))
    dark = Image.new("RGB", size, (0, 0, 0))
    img = Image.composite(dark, img, shade).filter(ImageFilter.UnsharpMask(2, 80, 3))
    img = img.convert("RGBA")
    st = STYLES.get(style, STYLES["bold_yellow"])
    w, h = size
    m = int(w * 0.05)
    box = (m, int(h * 0.45), w - m, h - m) if not vertical else (m, int(h * 0.55), w - m, h - m)
    draw_text_block(img, text.upper(), int(h * (0.16 if not vertical else 0.09)), box, color=st["color"],
                    stroke_color=st["stroke"], stroke_width=max(4, int(h * 0.012)), valign="bottom", line_spacing=1.05)
    out = out.with_suffix(".jpg")
    img.convert("RGB").save(out, quality=90)
    # YouTube giới hạn 2MB
    q = 85
    while out.stat().st_size > 2_000_000 and q > 40:
        img.convert("RGB").save(out, quality=q)
        q -= 10
    return out
