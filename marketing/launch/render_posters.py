from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
OUT = ROOT / "final"
LANDING_URL = "https://omnidoer.github.io/handex/"

FONT_CJK = "/usr/share/fonts/google-droid-sans-fonts/DroidSansFallbackFull.ttf"
FONT_LATIN = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"
FONT_LATIN_BOLD = "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/adobe-source-code-pro/SourceCodePro-Semibold.otf"
FONT_AWESOME = "/usr/share/fonts/fontawesome/FontAwesome.otf"
REPO_LABEL = "github.com/OmniDoer/Handex"


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


def draw_chips(draw: ImageDraw.ImageDraw, x: int, y: int, chips: list[str], size: int, font_path: str = FONT_LATIN_BOLD) -> int:
    chip_font = font(font_path, size)
    cursor = x
    for chip in chips:
        box = draw.textbbox((0, 0), chip, font=chip_font)
        w = box[2] - box[0] + 34
        h = box[3] - box[1] + 18
        draw.rounded_rectangle((cursor, y, cursor + w, y + h), radius=18, fill=(8, 18, 30, 205), outline=(94, 234, 212, 115), width=2)
        draw.text((cursor + 17, y + 8), chip, font=chip_font, fill=(225, 252, 247, 255))
        cursor += w + 14
    return y + h


LOCALES = {
    "zh": {
        "badge_square": "产品发布",
        "badge_x": "HANDEX",
        "badge_story": "产品发布",
        "badge_font": FONT_CJK,
        "square_headline": "自动代理不可用时，继续交付。",
        "square_headline_size": 66,
        "square_body": "把网页模型接到本地工具：复制提示，审阅命令，执行，再把结果交回模型。",
        "square_body_size": 38,
        "x_title": "自动代理不可用时\n继续交付",
        "x_title_size": 66,
        "x_body": "网页模型提出一步命令，人审阅，本地执行，结果回传。",
        "x_body_size": 36,
        "story_title": "额度耗尽，\n也不断线。",
        "story_title_size": 92,
        "story_body": "把代理循环交还给人：网页模型提出下一步，人审阅命令，本地工具执行，结果回到模型。",
        "story_body_size": 42,
        "chips_square": ["技能", "保险柜", "代码仓库", "上下文"],
        "chips_x": ["技能", "保险柜", "本地工具"],
        "chips_font": FONT_CJK,
        "bridge": "技能 + 保险柜 + 代码仓库桥",
        "flow": "复制、审阅、执行、返回",
        "flow_font": FONT_CJK,
        "qr_label": "扫码了解",
    },
    "en": {
        "badge_square": "PRODUCT LAUNCH",
        "badge_x": "HANDEX",
        "badge_story": "HANDEX RELEASE",
        "badge_font": FONT_LATIN_BOLD,
        "square_headline": "Keep shipping when agents are unavailable.",
        "square_headline_size": 58,
        "square_body": "Connect any web LLM to local tools: copy a prompt, review the command, run it, and return the result.",
        "square_body_size": 35,
        "x_title": "Manual fallback\nfor Codex-style work",
        "x_title_size": 78,
        "x_body": "When quota runs out, keep the agent loop moving through reviewed copy-paste tool commands.",
        "x_body_size": 34,
        "story_title": "Quota gone,\nwork continues.",
        "story_title_size": 84,
        "story_body": "Hand the agent loop back to a human: the web model proposes one step, you review, local tools run it, and the result returns to the model.",
        "story_body_size": 34,
        "chips_square": ["Skills", "Vault", "GitHub", "Context"],
        "chips_x": ["Skills", "Vault", "Local tools"],
        "chips_font": FONT_LATIN_BOLD,
        "bridge": "Skills + Vault + GitHub Bridge",
        "flow": "Copy -> Review -> Run -> Return",
        "flow_font": FONT_MONO,
        "qr_label": "Learn more",
    },
    "ja": {
        "badge_square": "製品リリース",
        "badge_x": "HANDEX",
        "badge_story": "製品リリース",
        "badge_font": FONT_CJK,
        "square_headline": "自動エージェントが使えなくても、出荷を続ける。",
        "square_headline_size": 55,
        "square_body": "ウェブモデルをローカルツールへ接続。プロンプトをコピーし、命令を確認し、実行して、結果をモデルへ戻す。",
        "square_body_size": 36,
        "x_title": "自動エージェントの\n手動フォールバック",
        "x_title_size": 58,
        "x_body": "ウェブモデルが次の一手を出し、人が確認し、ローカルで実行し、結果を戻します。",
        "x_body_size": 34,
        "story_title": "上限に達しても、\n作業は止めない。",
        "story_title_size": 72,
        "story_body": "エージェントのループを人に戻す。ウェブモデルが次の一手を提案し、人が命令を確認し、ローカルツールが実行し、結果をモデルへ戻します。",
        "story_body_size": 36,
        "chips_square": ["スキル", "保管庫", "リポジトリ", "文脈"],
        "chips_x": ["スキル", "保管庫", "ローカルツール"],
        "chips_font": FONT_CJK,
        "bridge": "スキル、保管庫、リポジトリ橋",
        "flow": "コピー、確認、実行、返す",
        "flow_font": FONT_CJK,
        "qr_label": "詳細を見る",
    },
}


def cover_generic_status_icon(draw: ImageDraw.ImageDraw) -> None:
    x1, y1, x2, y2 = 1218, 306, 1320, 424
    draw.rounded_rectangle((x1, y1, x2, y2), radius=22, fill=(7, 18, 24, 238), outline=(94, 234, 212, 120), width=2)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    draw.ellipse((cx - 23, cy - 23, cx + 23, cy + 23), outline=(144, 255, 225, 235), width=5)
    draw.line((cx - 6, cy, cx + 15, cy - 15), fill=(144, 255, 225, 235), width=5)
    draw.line((cx - 6, cy, cx + 15, cy + 15), fill=(144, 255, 225, 235), width=5)


def qr_image(size: int) -> Image.Image:
    qr = qrcode.QRCode(version=3, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=2)
    qr.add_data(LANDING_URL)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#071218", back_color="#ffffff").convert("RGBA")
    return image.resize((size, size), Image.Resampling.NEAREST)


def draw_qr_badge(
    canvas: Image.Image,
    xy: tuple[int, int],
    *,
    size: int,
    label: str,
    label_font_path: str,
    label_size: int,
) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y = xy
    typeface = font(label_font_path, label_size)
    label_box = draw.textbbox((0, 0), label, font=typeface)
    label_w = label_box[2] - label_box[0]
    card_w = max(size + 32, label_w + 40)
    card_h = size + 62
    draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=24, fill=(3, 10, 16, 226), outline=(94, 234, 212, 150), width=2)
    qr_x = x + (card_w - size) // 2
    canvas.alpha_composite(qr_image(size), (qr_x, y + 16))
    draw.text((x + (card_w - label_w) // 2, y + size + 24), label, font=typeface, fill=(221, 255, 249, 255))


def draw_repo_badge(canvas: Image.Image, xy: tuple[int, int], *, text_size: int = 28) -> None:
    draw = ImageDraw.Draw(canvas)
    x, y = xy
    icon_font = font(FONT_AWESOME, text_size + 8)
    text_font = font(FONT_LATIN_BOLD, text_size)
    icon = "\uf09b"
    icon_box = draw.textbbox((0, 0), icon, font=icon_font)
    text_box = draw.textbbox((0, 0), REPO_LABEL, font=text_font)
    icon_w = icon_box[2] - icon_box[0]
    text_w = text_box[2] - text_box[0]
    height = max(icon_box[3] - icon_box[1], text_box[3] - text_box[1]) + 24
    width = icon_w + text_w + 58
    draw.rounded_rectangle((x, y, x + width, y + height), radius=20, fill=(3, 10, 16, 220), outline=(94, 234, 212, 135), width=2)
    center_y = y + height // 2
    draw.text((x + 18, center_y - (icon_box[3] - icon_box[1]) // 2 - 2), icon, font=icon_font, fill=(245, 250, 252, 255))
    draw.text((x + 18 + icon_w + 18, center_y - (text_box[3] - text_box[1]) // 2 - 2), REPO_LABEL, font=text_font, fill=(226, 255, 249, 255))


def locale_out(locale: str) -> Path:
    path = OUT / locale
    path.mkdir(parents=True, exist_ok=True)
    return path


def square_poster(locale: str, copy: dict[str, object]) -> None:
    canvas = fit(ASSETS / "handex-launch-bg-square.png", (1600, 1600))
    canvas.alpha_composite(rect_gradient(canvas.size, horizontal=True, max_alpha=205))
    draw = ImageDraw.Draw(canvas)
    rounded_label(draw, (96, 90), str(copy["badge_square"]), font(str(copy["badge_font"]), 34))
    draw.text((92, 168), "HANDEX", font=font(FONT_LATIN_BOLD, 156), fill=(255, 255, 255, 255))
    headline_font = FONT_LATIN_BOLD if locale == "en" else FONT_CJK
    body_font = FONT_LATIN if locale == "en" else FONT_CJK
    y = text_block(draw, (100, 342), str(copy["square_headline"]), font(headline_font, int(copy["square_headline_size"])), (204, 255, 247, 255), 980, 16)
    y = text_block(draw, (100, y + 30), str(copy["square_body"]), font(body_font, int(copy["square_body_size"])), (226, 238, 245, 245), 760, 14)
    draw_chips(draw, 100, 1272, list(copy["chips_square"]), 32, str(copy["chips_font"]))
    draw.text((100, 1388), str(copy["flow"]), font=font(str(copy["flow_font"]), 34), fill=(180, 243, 232, 235))
    draw_repo_badge(canvas, (100, 1450), text_size=27)
    draw_qr_badge(canvas, (1290, 1280), size=168, label=str(copy["qr_label"]), label_font_path=str(copy["chips_font"]), label_size=24)
    canvas.convert("RGB").save(locale_out(locale) / "handex-launch-poster-square.png", optimize=True, quality=94)


def x_card(locale: str, copy: dict[str, object]) -> None:
    canvas = fit(ASSETS / "handex-launch-bg-x-card.png", (1600, 900))
    canvas.alpha_composite(rect_gradient(canvas.size, horizontal=True, max_alpha=230))
    draw = ImageDraw.Draw(canvas)
    cover_generic_status_icon(draw)
    rounded_label(draw, (76, 76), str(copy["badge_x"]), font(FONT_LATIN_BOLD, 32), fill=(13, 148, 136, 220))
    title_font = FONT_LATIN_BOLD if locale == "en" else FONT_CJK
    body_font = FONT_LATIN if locale == "en" else FONT_CJK
    draw.text((72, 158), str(copy["x_title"]), font=font(title_font, int(copy["x_title_size"])), fill=(255, 255, 255, 255), spacing=10)
    text_block(draw, (78, 380), str(copy["x_body"]), font(body_font, int(copy["x_body_size"])), fill=(226, 238, 245, 245), max_width=610, line_gap=12)
    draw_repo_badge(canvas, (78, 622), text_size=25)
    draw.text((78, 720), str(copy["flow"]), font=font(str(copy["flow_font"]), 34), fill=(199, 255, 245, 250))
    draw_chips(draw, 78, 784, list(copy["chips_x"]), 27, str(copy["chips_font"]))
    draw_qr_badge(canvas, (1360, 642), size=136, label=str(copy["qr_label"]), label_font_path=str(copy["chips_font"]), label_size=21)
    canvas.convert("RGB").save(locale_out(locale) / "handex-launch-x-card.png", optimize=True, quality=94)


def story_poster(locale: str, copy: dict[str, object]) -> None:
    canvas = fit(ASSETS / "handex-launch-bg-story.png", (1080, 1920))
    band = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rectangle((0, 0, 1080, 470), fill=(2, 6, 12, 190))
    bd.rectangle((0, 1330, 1080, 1920), fill=(2, 6, 12, 185))
    canvas.alpha_composite(band)
    draw = ImageDraw.Draw(canvas)
    rounded_label(draw, (72, 70), str(copy["badge_story"]), font(str(copy["badge_font"]), 30), fill=(8, 145, 178, 220))
    title_font = FONT_LATIN_BOLD if locale == "en" else FONT_CJK
    body_font = FONT_LATIN if locale == "en" else FONT_CJK
    draw.text((68, 150), str(copy["story_title"]), font=font(title_font, int(copy["story_title_size"])), fill=(255, 255, 255, 255), spacing=8)
    text_block(draw, (72, 1375), str(copy["story_body"]), font(body_font, int(copy["story_body_size"])), fill=(230, 242, 248, 250), max_width=890, line_gap=16)
    draw.text((72, 1686), str(copy["bridge"]), font=font(str(copy["flow_font"]), 36), fill=(190, 255, 242, 245))
    draw.text((72, 1752), str(copy["flow"]), font=font(str(copy["flow_font"]), 32), fill=(245, 203, 133, 245))
    draw_repo_badge(canvas, (72, 1810), text_size=24)
    draw_qr_badge(canvas, (800, 1650), size=168, label=str(copy["qr_label"]), label_font_path=str(copy["chips_font"]), label_size=24)
    canvas.convert("RGB").save(locale_out(locale) / "handex-launch-story.png", optimize=True, quality=94)


def main() -> None:
    for locale, copy in LOCALES.items():
        square_poster(locale, copy)
        x_card(locale, copy)
        story_poster(locale, copy)


if __name__ == "__main__":
    main()
