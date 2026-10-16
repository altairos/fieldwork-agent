#!/usr/bin/env python3
"""从 design-board.html 确定性生成配色变体，供并排视觉决策。

变体（见 docs/reviews/2026-10-16-前端设计评审.md 第四节）：
  A1 靛青  design-board-indigo.html    主色与数据色统一为靛青 #3A5C74，朱砂家族整体退场
  A2 青衿  design-board-bamboo.html    主色深青绿 #46683A，bamboo 提亮为 #6B8A57 拉开明度差
  A3 蓝白  design-board-wordblue.html  Word 风蓝白对照版（仅换配色，字体暂保留宋体）

规则：
  - token 类名不变，仅替换色值与少量说明文字；含 hex 的文字对排在裸 hex 对之前；
  - lg-mio（#A8412F，妙谧智能既有品牌资产）任何变体保留原色不改；
  - danger 为超支语义红：A1/A2 保留 #9E2F26，A3 换 Word 红 #A4262C；
  - 断言变体中不残留 #A8412E / 朱砂 / 纸感温润，#A8412F 恰好保留 1 处。

用法：python docs/frontend/make_variants.py（可重复再生，覆盖旧输出）
"""
import sys
from pathlib import Path

SRC = Path(__file__).with_name("design-board.html")

# ── 通用：朱砂家族退场后的文字位（各变体各自命名） ──────────────────────────

A1_TEXT = [
    ("#A8412E · 品牌/进行中", "#3A5C74 · 主色/进行中（与 indigo 同值）"),
    ("cinnabar 朱砂", "cinnabar 靛青"),
    ("学术档案 · 纸感温润", "学术档案 · 靛青一色"),
    ("（学术档案·纸感温润）", "（学术档案·靛青变体 A1）"),
    ("学术档案/纸感温润", "学术档案/靛青变体 A1"),
    ("朱砂点睛、靛蓝承载数据", "靛青一色承担交互与数据"),
    ("当前章朱砂高亮", "当前章靛青高亮"),
]

A2_TEXT = [
    ("#A8412E · 品牌/进行中", "#46683A · 主色/进行中"),
    ("#5E7A4E · 完成/确认", "#6B8A57 · 完成（提亮，与主青拉开明度）"),
    ("cinnabar 朱砂", "cinnabar 青衿"),
    ("学术档案 · 纸感温润", "学术档案 · 青青子衿"),
    ("（学术档案·纸感温润）", "（学术档案·青衿变体 A2）"),
    ("学术档案/纸感温润", "学术档案/青衿变体 A2"),
    ("朱砂点睛、靛蓝承载数据", "青衿点睛、靛蓝承载数据"),
    ("当前章朱砂高亮", "当前章青衿高亮"),
]

A3_TEXT = [
    ("#A8412E · 品牌/进行中", "#2B579A · 主色/进行中"),
    ("cinnabar 朱砂", "cinnabar Word 蓝"),
    ("indigo 靛蓝", "indigo 链接蓝"),
    ("bamboo 竹青", "bamboo 成功绿"),
    ("ochre 赭黄", "ochre 警示黄"),
    ("paper 纸底", "paper 纯白底"),
    ("学术档案 · 纸感温润", "Word 蓝白 · 工具对照"),
    ("（学术档案·纸感温润）", "（Word蓝白对照变体 A3）"),
    ("学术档案/纸感温润", "Word蓝白对照 A3（仅换配色、字体未换）"),
    (
        "视觉取「学术档案」气质：宣纸米白为底、墨黑为文、朱砂点睛、靛蓝承载数据，宋体立标题",
        "视觉取「Office 工具」气质：纯白为底、冷灰为文、Fluent 蓝一色承担交互与数据"
        "（本对照版仅换配色、暂保留宋体——若定此方向，UI 字体须一并换无衬线）",
    ),
    ("当前章朱砂高亮", "当前章蓝色高亮"),
    ("宣纸纤维暗纹", "底纹暗纹"),
]

# ── 色值映射（先长/具体，后短/裸 hex） ────────────────────────────────────

A1_HEX = [
    # cinnabar 家族 → 靛青家族（主色与 indigo 数据色统一）
    ("#A8412E", "#3A5C74"),   # cinnabar：config/:root/rp-tab/fab/logo色块
    ("#C9705C", "#7B95A9"),   # cinnabarsoft
    ("#F6EBE6", "#E9EEF1"),   # cinnabarwash（含 rp-tab 选中底）
    ("#96382a", "#2E4A5F"),   # 主按钮 hover
    ("#F7EFE6", "#EDF1F4"),   # 主按钮/圆钮文字
    ("#EBD5CD", "#D2DDE4"),   # rp-tab 选中描边
    ("rgba(168,65,46,.4)", "rgba(58,92,116,.35)"),    # fab 阴影
    ("rgba(168,65,46,.014)", "rgba(58,92,116,.012)"), # 纸纹朱砂暗纹 → 靛青
    # danger / indigo / bamboo / ochre / 纸墨系全部保留原值
]

A2_HEX = [
    ("#A8412E", "#46683A"),   # cinnabar → 青衿深绿
    ("#C9705C", "#7A9468"),
    ("#F6EBE6", "#E8EFE3"),
    ("#96382a", "#38522E"),
    ("#F7EFE6", "#F1F5EC"),
    ("#EBD5CD", "#D4E0CC"),
    ("#5E7A4E", "#6B8A57"),   # bamboo 提亮，与主青拉开明度差
    ("#EBF0E5", "#EEF3E8"),   # bamboowash
    ("rgba(168,65,46,.4)", "rgba(70,104,58,.35)"),
    ("rgba(168,65,46,.014)", "rgba(94,122,78,.012)"),
    # indigo 数据色保留，与青绿主色形成低对比邻近关系
]

A3_HEX = [
    # 纸墨系 → 白底冷灰（Fluent 中性色）
    ("#F9F6F0", "#FFFFFF"),   # paper
    ("#F1ECE2", "#F4F5F9"),   # paperdeep
    ("#FDFCF9", "#FFFFFF"),   # card（含 lg-b 罗盘中心点）
    ("#F4F0E7", "#F2F3F7"),   # cardsunk
    ("#2A2724", "#323130"),   # ink（含 lg-b 描边）
    ("#5F584C", "#605E5C"),   # inksoft
    ("#948B7A", "#A19F9D"),   # inkfaint
    ("#E2D9C8", "#E1DFDD"),   # hairline
    ("#EBE5D9", "#EDEBE9"),   # hairsoft
    # 主色家族 → Fluent 蓝
    ("#A8412E", "#2B579A"),   # cinnabar 位 = Word 蓝
    ("#C9705C", "#6D8FC4"),
    ("#F6EBE6", "#EAF0F9"),
    ("#96382a", "#20437A"),
    ("#F7EFE6", "#FFFFFF"),   # 主按钮文字
    ("#EBD5CD", "#C7D6EC"),   # rp-tab 选中描边
    # 辅助语义色 → Office 习惯色
    ("#3A5C74", "#0563C1"),   # indigo → 链接蓝
    ("#E9EEF1", "#E8F0FB"),   # indigowash
    ("#5E7A4E", "#107C10"),   # bamboo → 成功绿
    ("#EBF0E5", "#EAF3EA"),   # bamboowash
    ("#A8802C", "#986F0B"),   # ochre → 警示黄
    ("#F5EFDF", "#FBF4DF"),   # ochrewash
    ("#9E2F26", "#A4262C"),   # danger → Word 语义红（仍为语义红）
    ("#EDF1F4", "#FFFFFF"),   # 用户气泡文字
    # 阴影/遮罩/壳体 → 中性黑
    ("rgba(60,48,30,", "rgba(0,0,0,"),          # 全部暖褐阴影前缀
    ("rgba(38,35,32,.34)", "rgba(0,0,0,.32)"),  # scrim
    ("#201D1A", "#1B1A19"),   # 手机壳 + notch
    ("#D8CDB8", "#C8C6C4"),   # 滚动条 thumb
    # 纸纹暗纹 → 极淡蓝
    ("rgba(168,65,46,.014)", "rgba(43,87,154,.018)"),
    ("rgba(58,92,116,.014)", "rgba(43,87,154,.014)"),
    ("rgba(168,65,46,.4)", "rgba(43,87,154,.4)"),
    # 宿舍卡渐变 → 冷灰蓝
    ("#E7DCC7", "#E9EDF4"),
    ("#D8CBB2", "#D9DFEA"),
    ("#E3DACB", "#E6EAF2"),
    ("#D2C6B0", "#D5DCE8"),
    ("#E7DFD0", "#EBEEF4"),
    ("#D9CDB6", "#DEE3ED"),
]

VARIANTS = [
    ("design-board-indigo.html", "A1 靛青", A1_TEXT + A1_HEX),
    ("design-board-bamboo.html", "A2 青衿", A2_TEXT + A2_HEX),
    ("design-board-wordblue.html", "A3 Word蓝白对照", A3_TEXT + A3_HEX),
]

FORBIDDEN = ["#A8412E", "朱砂", "纸感温润", "rgba(168,65,46"]


def main() -> int:
    with open(SRC, encoding="utf-8", newline="") as f:
        src = f.read()
    print(f"源文件 {SRC.name}：{len(src)} 字符，{src.count(chr(10)) + 1} 行\n")

    ok = True
    for dst_name, label, pairs in VARIANTS:
        out, total, missed = src, 0, []
        for old, new in pairs:
            n = out.count(old)
            if n == 0:
                missed.append(old)
            total += n
            out = out.replace(old, new)

        # 断言：旧主色家族与旧文案零残留；lg-mio 品牌资产恰好保留 1 处
        for s in FORBIDDEN:
            if s in out:
                print(f"[FAIL] {label} 残留 {s!r} × {out.count(s)}")
                ok = False
        mio = out.count("#A8412F")
        if mio != 1:
            print(f"[FAIL] {label} lg-mio #A8412F 计数 = {mio}（应为 1）")
            ok = False

        dst = SRC.with_name(dst_name)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        print(f"[OK] {label:<18} → {dst_name:<28} 替换 {total} 处")
        if missed:
            for m in missed:
                print(f"     [WARN] 未命中：{m}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
