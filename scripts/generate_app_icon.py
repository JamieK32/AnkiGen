from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "images"
SVG_PATH = IMAGES_DIR / "app_icon.svg"
PNG_PATH = IMAGES_DIR / "app_icon.png"
ICO_PATH = IMAGES_DIR / "app_icon.ico"
CANVAS_SIZE = 1024


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _rounded_gradient_background(size: int) -> Image.Image:
    top = ImageColor.getrgb("#0B1119")
    bottom = ImageColor.getrgb("#1A2432")
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        blend = y / max(1, size - 1)
        color = tuple(int(top[i] * (1 - blend) + bottom[i] * blend) for i in range(3)) + (255,)
        draw.line((0, y, size, y), fill=color)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((36, 36, size - 36, size - 36), radius=220, fill=255)
    image.putalpha(mask)
    return image


def _card_shadow(size: int, bounds: tuple[int, int, int, int], offset: tuple[int, int], opacity: int) -> Image.Image:
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x1, y1, x2, y2 = bounds
    ox, oy = offset
    ImageDraw.Draw(layer).rounded_rectangle(
        (x1 + ox, y1 + oy, x2 + ox, y2 + oy),
        radius=86,
        fill=(0, 0, 0, opacity),
    )
    return layer.filter(ImageFilter.GaussianBlur(28))


def _draw_spark(draw: ImageDraw.ImageDraw, cx: int, cy: int, outer: int, inner: int, fill: str) -> None:
    points: list[tuple[float, float]] = []
    for idx in range(8):
        radius = outer if idx % 2 == 0 else inner
        angle = math.radians(idx * 45 - 90)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=fill)


def build_png() -> Image.Image:
    image = _rounded_gradient_background(CANVAS_SIZE)
    shadow_back = _card_shadow(CANVAS_SIZE, (212, 240, 750, 770), (-26, 28), 60)
    shadow_front = _card_shadow(CANVAS_SIZE, (250, 188, 820, 792), (0, 22), 90)
    image.alpha_composite(shadow_back)
    image.alpha_composite(shadow_front)

    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((182, 262, 720, 792), radius=86, fill="#243040", outline="#324256", width=8)
    draw.rounded_rectangle((250, 188, 820, 792), radius=86, fill="#F7F5EF", outline="#D4D1C7", width=8)
    draw.rounded_rectangle((250, 188, 820, 324), radius=86, fill="#16A3A0")
    draw.rectangle((250, 270, 820, 324), fill="#16A3A0")

    fold = [(710, 188), (820, 188), (820, 296)]
    draw.polygon(fold, fill="#8CE2DE")
    draw.line((710, 188, 820, 296), fill="#B7F3EE", width=8)

    for top in (404, 478, 552):
        draw.rounded_rectangle((340, top, 614, top + 24), radius=12, fill="#CAD1DB")
    draw.rounded_rectangle((340, 626, 550, 650), radius=12, fill="#D9DEE6")

    font = _load_font(340)
    draw.text((604, 420), "A", anchor="mm", font=font, fill="#0F1720")

    _draw_spark(draw, 760, 380, 70, 28, "#F59E0B")
    draw.ellipse((714, 664, 798, 748), fill="#F59E0B")
    draw.polygon([(730, 702), (756, 728), (784, 678)], fill="#FFF7E6")

    return image


def build_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="AnkiGen icon">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#0B1119" />
      <stop offset="100%" stop-color="#1A2432" />
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="22" stdDeviation="22" flood-color="#000000" flood-opacity="0.24" />
    </filter>
  </defs>
  <rect x="36" y="36" width="952" height="952" rx="220" fill="url(#bg)" />
  <g filter="url(#shadow)">
    <rect x="182" y="262" width="538" height="530" rx="86" fill="#243040" stroke="#324256" stroke-width="8" />
    <rect x="250" y="188" width="570" height="604" rx="86" fill="#F7F5EF" stroke="#D4D1C7" stroke-width="8" />
  </g>
  <path d="M250 274c0-47.5 38.5-86 86-86h398c47.5 0 86 38.5 86 86v50H250z" fill="#16A3A0" />
  <path d="M710 188h110v108z" fill="#8CE2DE" />
  <path d="M710 188l110 108" stroke="#B7F3EE" stroke-width="8" />
  <rect x="340" y="404" width="274" height="24" rx="12" fill="#CAD1DB" />
  <rect x="340" y="478" width="274" height="24" rx="12" fill="#CAD1DB" />
  <rect x="340" y="552" width="274" height="24" rx="12" fill="#CAD1DB" />
  <rect x="340" y="626" width="210" height="24" rx="12" fill="#D9DEE6" />
  <text x="604" y="532" text-anchor="middle" dominant-baseline="middle" font-size="340" font-weight="700" font-family="'Segoe UI', Arial, sans-serif" fill="#0F1720">A</text>
  <path d="M760 310l19.8 41.2 45.5 6.6-32.9 32 7.8 45.3-40.2-21.1-40.2 21.1 7.8-45.3-32.9-32 45.5-6.6z" fill="#F59E0B" />
  <circle cx="756" cy="706" r="42" fill="#F59E0B" />
  <path d="M730 702l26 26 28-50z" fill="#FFF7E6" />
</svg>
"""


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    image = build_png()
    image.save(PNG_PATH)
    image.save(
        ICO_PATH,
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)],
    )
    SVG_PATH.write_text(build_svg(), encoding="utf-8")

    print(f"Generated {PNG_PATH}")
    print(f"Generated {ICO_PATH}")
    print(f"Generated {SVG_PATH}")


if __name__ == "__main__":
    main()
