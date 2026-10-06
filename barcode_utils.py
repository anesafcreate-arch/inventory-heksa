from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


CODE128_PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213",
    "122312", "132212", "221213", "221312", "231212", "112232", "122132",
    "122231", "113222", "123122", "123221", "223211", "221132", "221231",
    "213212", "223112", "312131", "311222", "321122", "321221", "312212",
    "322112", "322211", "212123", "212321", "232121", "111323", "131123",
    "131321", "112313", "132113", "132311", "211313", "231113", "231311",
    "112133", "112331", "132131", "113123", "113321", "133121", "313121",
    "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111",
    "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212",
    "124112", "124211", "411212", "421112", "421211", "212141", "214121",
    "412121", "111143", "111341", "131141", "114113", "114311", "411113",
    "411311", "113141", "114131", "311141", "411131", "211412", "211214",
    "211232", "2331112",
)


def _encode_code128_b(value: str) -> list[int]:
    if not value:
        raise ValueError("Barcode tidak boleh kosong.")
    codes = [104]
    for char in value:
        ordinal = ord(char)
        if ordinal < 32 or ordinal > 126:
            raise ValueError("Barcode hanya boleh memakai karakter ASCII yang dapat dicetak.")
        codes.append(ordinal - 32)

    checksum = codes[0]
    for index, code in enumerate(codes[1:], start=1):
        checksum += code * index
    codes.append(checksum % 103)
    codes.append(106)
    return codes


def generate_barcode_png(value: str, *, scale: int = 3, height: int = 96) -> bytes:
    codes = _encode_code128_b(value)
    quiet_zone = 18
    text_height = 32
    pattern = "".join(CODE128_PATTERNS[code] for code in codes)
    total_modules = sum(int(width) for width in pattern)
    image_width = (total_modules * scale) + (quiet_zone * 2)
    image_height = height + text_height + 24

    image = Image.new("RGB", (image_width, image_height), "white")
    draw = ImageDraw.Draw(image)

    x = quiet_zone
    draw_bar = True
    for width_char in pattern:
        bar_width = int(width_char) * scale
        if draw_bar:
            draw.rectangle((x, 12, x + bar_width - 1, height + 12), fill="black")
        x += bar_width
        draw_bar = not draw_bar

    font = ImageFont.load_default()
    text_bbox = draw.textbbox((0, 0), value, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    draw.text(((image_width - text_width) / 2, height + 22), value, fill="#0b1f44", font=font)

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
