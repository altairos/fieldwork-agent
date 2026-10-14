"""田野罗盘 logo 生产资产构建器 — 概念 B（田·罗盘）。

纯几何标识（圆环 + 田字方框 + 十字 + 东北朱砂象限 + 中心点），无文字依赖。
产出：
  - 4 态独立 SVG（full / mono / reverse / cinnabar）+ favicon.svg
  - PNG：favicon-16 / favicon-32 / apple-touch-icon-180 / logo-512
    （8x 超采样绘制后 LANCZOS 降采样，保证小尺寸抗锯齿）
用法： python build_logo.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent

# ---- 品牌色（与 design-board.html / Plan 4 token 同源）----
INK = "#2A2724"
CINNABAR = "#A8412E"
PAPER_CARD = "#FDFCF9"
PAPER_BG = "#F9F6F0"
DARK = "#201D1A"

# 四态：main=墨线, accent=朱砂/指向, paper=中心镂空
VARIANTS = {
    "full":     dict(main=INK,      accent=CINNABAR, paper=PAPER_CARD),
    "mono":     dict(main=INK,      accent=INK,      paper=PAPER_CARD),
    "reverse":  dict(main=PAPER_BG, accent=PAPER_BG, paper=DARK),       # 深底反白
    "cinnabar": dict(main=PAPER_BG, accent=PAPER_BG, paper=CINNABAR),   # 朱砂底
}


def _body(main: str, accent: str, paper: str) -> str:
    return (
        f'  <circle cx="32" cy="32" r="26" fill="none" stroke="{main}" stroke-width="3"/>\n'
        f'  <rect x="14" y="14" width="36" height="36" fill="none" stroke="{main}" stroke-width="2.4"/>\n'
        f'  <rect x="32" y="14" width="18" height="18" fill="{accent}"/>\n'
        f'  <path d="M32 14V50M14 32H50" stroke="{main}" stroke-width="2.4"/>\n'
        f'  <circle cx="32" cy="32" r="2.7" fill="{paper}" stroke="{main}" stroke-width="1.6"/>\n'
    )


def write_svgs() -> None:
    for name, v in VARIANTS.items():
        (OUT / f"fieldwork-logo-{name}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" '
            f'role="img" aria-label="田野罗盘">\n{_body(v["main"], v["accent"], v["paper"])}</svg>\n',
            encoding="utf-8",
        )
    v = VARIANTS["full"]
    (OUT / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        + _body(v["main"], v["accent"], v["paper"]) + "</svg>\n",
        encoding="utf-8",
    )


def render_png(size: int, v: dict, fname: str, bg: str | None = None, pad: float = 0.0) -> None:
    SS, S = 8, size * 8
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if bg:
        d.rectangle([0, 0, S, S], fill=bg)
    main, accent, paper = v["main"], v["accent"], v["paper"]
    s = 1 - 2 * pad          # 内容缩放（留白）
    u = S / 64.0

    def m(x: float, y: float):                 # viewBox 坐标 → 像素（居中缩放）
        return ((32 + (x - 32) * s) * u, (32 + (y - 32) * s) * u)

    def w(px: float) -> int:                   # 线宽缩放
        return max(1, round(px * u * s))

    # 圆环 r26 stroke3（Pillow 描边内缩，bbox 外扩半线宽使描边居中）
    d.ellipse([*m(32 - 27.5, 32 - 27.5), *m(32 + 27.5, 32 + 27.5)], outline=main, width=w(3))
    # 田字外框
    d.rectangle([*m(12.8, 12.8), *m(51.2, 51.2)], outline=main, width=w(2.4))
    # 东北朱砂象限
    d.rectangle([*m(32, 14), *m(50, 32)], fill=accent)
    # 十字
    d.line([m(32, 14), m(32, 50)], fill=main, width=w(2.4))
    d.line([m(14, 32), m(50, 32)], fill=main, width=w(2.4))
    # 中心点
    d.ellipse([*m(32 - 3.5, 32 - 3.5), *m(32 + 3.5, 32 + 3.5)], fill=paper, outline=main, width=w(1.6))
    img.resize((size, size), Image.LANCZOS).save(OUT / f"{fname}.png")


def main() -> None:
    write_svgs()
    full = VARIANTS["full"]
    render_png(16, full, "favicon-16")
    render_png(32, full, "favicon-32")
    render_png(512, full, "logo-512")
    render_png(180, full, "apple-touch-icon", bg=PAPER_BG, pad=0.09)
    print("done ->", OUT)


if __name__ == "__main__":
    main()
