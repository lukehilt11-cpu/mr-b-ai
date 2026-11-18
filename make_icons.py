"""Generates assets/icon.png, icon.ico (Windows) and icon.icns (macOS). Needs Pillow."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "assets"
SIZE = 1024
ACCENT = (36, 87, 214)
WHITE = (255, 255, 255)
FONTS = [
    "arialbd.ttf", "Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc", "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
]


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size)          # Pillow >= 10.1


def master():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((64, 64, SIZE - 64, SIZE - 64), radius=210, fill=ACCENT)
    f = font(600)
    l, t, r, b = d.textbbox((0, 0), "B", font=f)
    d.text(((SIZE - (r - l)) / 2 - l, (SIZE - (b - t)) / 2 - t), "B", font=f, fill=WHITE)
    return img


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    img = master()
    img.save(OUT / "icon.png")
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    img.save(OUT / "icon.icns")
    print("Icons written to", OUT)
