"""Vẽ chữ (hỗ trợ tiếng Việt) bằng Pillow: chữ trên màn hình, ảnh nền, thumbnail."""
from __future__ import annotations

import functools
import hashlib
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import ROOT

FONT_CANDIDATES = [
    ROOT / "assets" / "fonts" / "Roboto-Bold.ttf",
    ROOT / "assets" / "fonts" / "BeVietnamPro-Bold.ttf",
    ROOT / "assets" / "fonts" / "NotoSans-Bold.ttf",
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"),
]


@functools.lru_cache(maxsize=None)
def find_font(preferred: str = "auto") -> str | None:
    if preferred and preferred != "auto":
        p = Path(preferred)
        if not p.is_absolute():
            p = ROOT / p
        if p.exists():
            return str(p)
    fonts_dir = ROOT / "assets" / "fonts"
    if fonts_dir.exists():
        for f in sorted(fonts_dir.glob("*.[ot]tf")):
            if "bold" in f.name.lower():
                return str(f)
    for cand in FONT_CANDIDATES:
        if cand.exists():
            return str(cand)
    if fonts_dir.exists():
        for f in sorted(fonts_dir.glob("*.[ot]tf")):
            return str(f)
    return None


@functools.lru_cache(maxsize=64)
def load_font(size: int, preferred: str = "auto") -> ImageFont.FreeTypeFont:
    path = find_font(preferred)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def hex_to_rgb(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textlength(trial, font=font) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


def draw_text_block(
    img: Image.Image,
    text: str,
    font_size: int,
    box: tuple[int, int, int, int],
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int | None = None,
    align: str = "center",
    valign: str = "center",
    font: str = "auto",
    background: str | None = None,
    line_spacing: float = 1.15,
    shrink_to_fit: bool = True,
) -> Image.Image:
    """Vẽ đoạn chữ tự xuống dòng vào trong box (x0,y0,x1,y1)."""
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    max_w, max_h = x1 - x0, y1 - y0
    size = font_size
    while True:
        fnt = load_font(size, font)
        lines = wrap_text(draw, text, fnt, max_w)
        line_h = int(size * line_spacing)
        total_h = line_h * len(lines)
        if not shrink_to_fit or total_h <= max_h or size <= 14:
            break
        size = int(size * 0.9)
    sw = stroke_width if stroke_width is not None else max(2, size // 14)
    if valign == "top":
        y = y0
    elif valign == "bottom":
        y = y1 - total_h
    else:
        y = y0 + (max_h - total_h) // 2
    if background:
        widest = max((draw.textlength(ln, font=fnt) for ln in lines), default=0)
        pad = size // 3
        bx0 = x0 + (max_w - widest) / 2 - pad if align == "center" else x0 - pad
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle(
            (bx0, y - pad, bx0 + widest + 2 * pad, y + total_h + pad // 2), radius=pad, fill=hex_to_rgb(background[:7], int(background[7:9], 16) if len(background) > 7 else 170)
        )
        img.alpha_composite(overlay) if img.mode == "RGBA" else img.paste(overlay, (0, 0), overlay)
        draw = ImageDraw.Draw(img)
    for ln in lines:
        w = draw.textlength(ln, font=fnt)
        x = x0 + (max_w - w) / 2 if align == "center" else (x1 - w if align == "right" else x0)
        draw.text((x, y), ln, font=fnt, fill=hex_to_rgb(color), stroke_width=sw, stroke_fill=hex_to_rgb(stroke_color))
        y += line_h
    return img


def text_overlay_png(text: str, size: tuple[int, int], out: str | Path, position: str = "top", font_size: int | None = None,
                     color: str = "#FFFFFF", background: str | None = "#000000A0") -> Path:
    """Ảnh PNG trong suốt chứa chữ, để chồng lên video."""
    w, h = size
    fs = font_size or int(min(w, h) * 0.07)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    margin = int(w * 0.07)
    if position == "center":
        box = (margin, int(h * 0.3), w - margin, int(h * 0.7))
        valign = "center"
    elif position == "bottom":
        box = (margin, int(h * 0.62), w - margin, int(h * 0.8))
        valign = "bottom"
    else:
        box = (margin, int(h * 0.08), w - margin, int(h * 0.3))
        valign = "top"
    draw_text_block(img, text, fs, box, color=color, background=background, valign=valign)
    out = Path(out)
    img.save(out)
    return out


PALETTES = [
    ("#0f2027", "#2c5364"), ("#1d2b64", "#f8cdda"), ("#232526", "#414345"), ("#3a1c71", "#d76d77"),
    ("#000428", "#004e92"), ("#134e5e", "#71b280"), ("#42275a", "#734b6d"), ("#141e30", "#243b55"),
]


def gradient_background(size: tuple[int, int], out: str | Path, seed: str = "", text: str = "") -> Path:
    """Ảnh nền gradient (dùng khi không tìm được hình ảnh phù hợp)."""
    w, h = size
    rnd = random.Random(int(hashlib.md5(seed.encode()).hexdigest(), 16))
    c1, c2 = (hex_to_rgb(c)[:3] for c in rnd.choice(PALETTES))
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    side = int(max(w, h) * 1.5)
    mask = Image.linear_gradient("L").resize((side, side)).rotate(rnd.choice([0, 45, 90, 135, 180]))
    mask = mask.crop(((side - w) // 2, (side - h) // 2, (side - w) // 2 + w, (side - h) // 2 + h))
    img = Image.composite(top, base, mask)
    # vài vòng sáng mờ cho đỡ đơn điệu
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for _ in range(6):
        r = rnd.randint(min(w, h) // 8, min(w, h) // 3)
        cx, cy = rnd.randint(0, w), rnd.randint(0, h)
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, rnd.randint(15, 40)))
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) // 20))
    img = img.convert("RGBA")
    img.alpha_composite(glow)
    if text:
        m = int(w * 0.1)
        draw_text_block(img, text, int(min(w, h) * 0.08), (m, m, w - m, h - m))
    out = Path(out)
    img.convert("RGB").save(out, quality=92)
    return out


def fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(1, int(img.width * scale + 0.5)), max(1, int(img.height * scale + 0.5))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def font_dir_for_ffmpeg() -> str | None:
    path = find_font()
    return os.path.dirname(path) if path else None
