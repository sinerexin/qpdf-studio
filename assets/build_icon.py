"""Build the Windows icon. One blue tile and a white page, readable at 16 px."""
from pathlib import Path

from PIL import Image, ImageDraw


def _icon(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(size * 0.06)
    radius = round(size * 0.22)
    draw.rounded_rectangle((margin, margin, size - margin - 1, size - margin - 1),
                           radius=radius, fill=(0, 113, 227, 255))
    left, top = round(size * 0.26), round(size * 0.18)
    right, bottom = round(size * 0.74), round(size * 0.82)
    draw.rounded_rectangle((left, top, right, bottom), radius=round(size * 0.045),
                           fill=(255, 255, 255, 255))
    fold = round(size * 0.16)
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
                 fill=(214, 230, 248, 255))
    bar_left = round(size * 0.33)
    bar_height = max(2, round(size * 0.028))
    for index, length in enumerate((0.22, 0.30, 0.24)):
        y = round(size * (0.40 + index * 0.08))
        draw.rounded_rectangle((bar_left, y, bar_left + round(size * length), y + bar_height),
                               radius=bar_height // 2, fill=(210, 210, 215, 255))
    mark_top = round(size * 0.66)
    draw.rounded_rectangle((bar_left, mark_top, round(size * 0.58), mark_top + bar_height),
                           radius=bar_height // 2, fill=(0, 113, 227, 255))
    return image


def make_icon(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    master = _icon(1024)
    master.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32),
                                              (48, 48), (64, 64), (128, 128), (256, 256)])
    return output


if __name__ == "__main__":
    print(make_icon(Path(__file__).with_name("qpdf-studio.ico")))
