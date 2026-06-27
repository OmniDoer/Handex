from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
OUT = ROOT / "final"

FONT_CJK = "/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf"
FONT_LATIN = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"
FONT_LATIN_BOLD = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/adobe-source-code-pro/SourceCodePro-Semibold.otf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def fit(path: Path, size: tuple[int, int]) -> Image.Image:
    image = Image.open(path).convert("RGB")
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)).convert("RGBA")


def rect_gradient(size: tuple[int, int], *, horizontal: bool = True, max_alpha: int = 210) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(height):
        for x in range(width):
            t = (1 - x / max(width - 1, 1)) if horizontal else (1 - y / max(height - 1, 1))
            alpha = int(max_alpha * max(0, min(1, t)))
            pixels[x, y] = (2, 6, 12, alpha)
    return overlay


def vertical_band(size: tuple[int, int], top_alpha: int = 175, bottom_alpha: int = 145) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, width, int(height * 0.25)), fill=(2, 6, 12, top_alpha))
    draw.rectangle((0, int(height * 0.68), width, height), fill=(2, 6, 12, bottom_alpha))
    return overlay.filter(Image.Resampling.BICUBIC)


def wrap(draw: ImageDraw.ImageDraw, text: str, typeface: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ") if " " in paragraph else list(paragraph)
        current = ""
        for word in words:
            separator = " " if " " in paragraph and current else ""
            candidate = f"{current}{separator}{word}".strip()
            if draw.textbbox((0, 0), candidate, font=typeface)[2] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def text_block(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    typeface: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    max_width: int,
    line_gap: int,
) -> int:
    x, y = xy
    for line in wrap(draw, text, typeface, max_width):
        draw.text((x, y), line, font=typeface, fill=fill)
        box = draw.textbbox((x, y), line, font=typeface)
        y = box[3] + line_gap
    return y


def rounded_label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    typeface: ImageFont.FreeTypeFont,
    *,
    fill: tuple[int, int, int, int] = (8, 145, 178, 210),
    stroke: tuple[int, int, int, int] = (155, 240, 230, 120),
) -> tuple[int, int]:
    x, y = xy
    box = draw.textbbox((0, 0), text, font=typeface)
    pad_x, pad_y = 22, 10
    w = box[2] - box[0] + pad_x * 2
    h = box[3] - box[1] + pad_y * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=22, fill=fill, outline=stroke, width=2)
    draw.text((x + pad_x, y + pad_y - 2), text, font=typeface, fill=(235, 255, 252, 255))
    return x + w, y + h


def draw_chips(draw: ImageDraw.ImageDraw, x: int, y: int, chips: list[str], size: int) -> int:
    chip_font = font(FONT_LATIN_BOLD, size)
    cursor = x
    for chip in chips:
        box = draw.textbbox((0, 0), chip, font=chip_font)
        w = box[2] - box[0] + 34
        h = box[3] - box[1] + 18
        draw.rounded_rectangle((cursor, y, cursor + w, y + h), radius=18, fill=(8, 18, 30, 205), outline=(94, 234, 212, 115), width=2)
        draw.text((cursor + 17, y + 8), chip, font=chip_font, fill=(225, 252, 247, 255))
        cursor += w + 14
    return y + h


def cover_generic_status_icon(draw: ImageDraw.ImageDraw) -> None:
    x1, y1, x2, y2 = 1218, 306, 1320, 424
    draw.rounded_rectangle((x1, y1, x2, y2), radius=22, fill=(7, 18, 24, 238), outline=(94, 234, 212, 120), width=2)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    draw.ellipse((cx - 23, cy - 23, cx + 23, cy + 23), outline=(144, 255, 225, 235), width=5)
    draw.line((cx - 6, cy, cx + 15, cy - 15), fill=(144, 255, 225, 235), width=5)
    draw.line((cx - 6, cy, cx + 15, cy + 15), fill=(144, 255, 225, 235), width=5)


def square_poster() -> None:
    canvas = fit(ASSETS / "handex-launch-bg-square.png", (1600, 1600))
    canvas.alpha_composite(rect_gradient(canvas.size, horizontal=True, max_alpha=205))
    draw = ImageDraw.Draw(canvas)
    rounded_label(draw, (96, 90), "PRODUCT LAUNCH", font(FONT_LATIN_BOLD, 34))
    draw.text((92, 168), "HANDEX", font=font(FONT_LATIN_BOLD, 156), fill=(255, 255, 255, 255))
    y = text_block(draw, (100, 342), "自动代理不可用时，继续交付。", font(FONT_CJK, 66), (204, 255, 247, 255), 980, 16)
    y = text_block(draw, (100, y + 30), "把网页模型接到本地工具：复制提示，审阅命令，执行，再把结果交回模型。", font(FONT_CJK, 38), (226, 238, 245, 245), 760, 14)
    draw_chips(draw, 100, 1272, ["Skills", "Vault", "GitHub", "Context"], 32)
    draw.text((100, 1388), "Copy -> Review -> Run -> Return", font=font(FONT_MONO, 34), fill=(180, 243, 232, 235))
    canvas.convert("RGB").save(OUT / "handex-launch-poster-square.png", optimize=True, quality=94)


def x_card() -> None:
    canvas = fit(ASSETS / "handex-launch-bg-x-card.png", (1600, 900))
    canvas.alpha_composite(rect_gradient(canvas.size, horizontal=True, max_alpha=230))
    draw = ImageDraw.Draw(canvas)
    cover_generic_status_icon(draw)
    rounded_label(draw, (76, 76), "HANDEX", font(FONT_LATIN_BOLD, 32), fill=(13, 148, 136, 220))
    draw.text((72, 158), "Manual fallback\nfor Codex-style work", font=font(FONT_LATIN_BOLD, 78), fill=(255, 255, 255, 255), spacing=10)
    text_block(draw, (78, 362), "When quota runs out, keep the agent loop moving through reviewed copy-paste tool commands.", font(FONT_LATIN, 34), fill=(226, 238, 245, 245), max_width=600, line_gap=12)
    draw.text((78, 720), "Copy -> Review -> Run -> Return", font=font(FONT_MONO, 34), fill=(199, 255, 245, 250))
    draw_chips(draw, 78, 784, ["Skills", "Vault", "Local tools"], 27)
    canvas.convert("RGB").save(OUT / "handex-launch-x-card.png", optimize=True, quality=94)


def story_poster() -> None:
    canvas = fit(ASSETS / "handex-launch-bg-story.png", (1080, 1920))
    band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rectangle((0, 0, 1080, 470), fill=(2, 6, 12, 190))
    bd.rectangle((0, 1330, 1080, 1920), fill=(2, 6, 12, 185))
    canvas.alpha_composite(band)
    draw = ImageDraw.Draw(canvas)
    rounded_label(draw, (72, 70), "HANDEX RELEASE", font(FONT_LATIN_BOLD, 30), fill=(8, 145, 178, 220))
    draw.text((68, 150), "额度耗尽，\n也不断线。", font=font(FONT_CJK, 92), fill=(255, 255, 255, 255), spacing=8)
    text_block(draw, (72, 1375), "把代理循环交还给人：网页模型提出下一步，人审阅命令，本地工具执行，结果回到模型。", font(FONT_CJK, 42), fill=(230, 242, 248, 250), max_width=890, line_gap=16)
    draw.text((72, 1686), "Skills + Vault + GitHub Bridge", font=font(FONT_MONO, 36), fill=(190, 255, 242, 245))
    draw.text((72, 1752), "Copy -> Review -> Run -> Return", font=font(FONT_MONO, 32), fill=(245, 203, 133, 245))
    canvas.convert("RGB").save(OUT / "handex-launch-story.png", optimize=True, quality=94)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    square_poster()
    x_card()
    story_poster()


if __name__ == "__main__":
    main()
