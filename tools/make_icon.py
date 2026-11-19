"""프로그램 아이콘 그리기: 주황 둥근 사각형(대각선 그러데이션) + 흰 편지 봉투 + 체크 표시가 든 초록 점.

  uv run python tools/make_icon.py

src/mailbrief/assets/ 에 icon.ico (작업표시줄/exe) 와 icon.png (창 안 머리글/알림) 를 만든다.
색은 브리핑 HTML 의 강조색(#d9480f)에 맞춘다.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "src" / "mailbrief" / "assets"
S = 1024  # 크게 그린 뒤 줄여서 가장자리를 매끄럽게


def lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def draw() -> Image.Image:
    # 왼쪽 위가 밝고 오른쪽 아래가 진한 대각선 그러데이션
    top, bottom = (255, 146, 84), (210, 64, 10)
    gradient = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(gradient)
    for k in range(2 * S):
        gd.line([(k, 0), (0, k)], fill=lerp(top, bottom, k / (2 * S)) + (255,), width=2)
    # 위쪽 은은한 빛
    shine = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shine).ellipse([-200, -620, S + 200, 380], fill=(255, 255, 255, 34))
    gradient.alpha_composite(shine.filter(ImageFilter.GaussianBlur(40)))

    mask = Image.new("L", (S, S), 0)
    margin = 56
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, S - margin, S - margin], radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(gradient, mask=mask)

    # 봉투 그림자
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    x0, y0, x1, y1 = 230, 330, 794, 720
    ImageDraw.Draw(shadow).rounded_rectangle([x0, y0 + 24, x1, y1 + 24], radius=56, fill=(120, 30, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))

    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x1, y1], radius=56, fill=(255, 255, 255, 255))
    # 봉투 덮개 (V)
    flap = (217, 72, 15, 255)
    d.line([(x0 + 40, y0 + 50), ((x0 + x1) // 2, y0 + 230), (x1 - 40, y0 + 50)], fill=flap, width=44, joint="curve")
    for x, y in ((x0 + 40, y0 + 50), (x1 - 40, y0 + 50)):
        d.ellipse([x - 22, y - 22, x + 22, y + 22], fill=flap)
    d.ellipse([(x0 + x1) // 2 - 22, y0 + 208, (x0 + x1) // 2 + 22, y0 + 252], fill=flap)

    # 알림 점 (오른쪽 위)
    cx, cy, r = 780, 300, 110
    d.ellipse([cx - r - 22, cy - r - 22, cx + r + 22, cy + r + 22], fill=(255, 255, 255, 255))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(34, 160, 90, 255))
    # 점 안의 체크 표시 ('정리해 뒀어요')
    check = [(cx - 50, cy + 4), (cx - 14, cy + 40), (cx + 52, cy - 34)]
    d.line(check, fill=(255, 255, 255, 255), width=30, joint="curve")
    for x, y in (check[0], check[2]):
        d.ellipse([x - 15, y - 15, x + 15, y + 15], fill=(255, 255, 255, 255))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((128, 128), Image.LANCZOS).save(OUT / "icon.png")
    big.resize((64, 64), Image.LANCZOS).save(OUT / "icon-64.png")
    print("완료:", OUT)


if __name__ == "__main__":
    main()
