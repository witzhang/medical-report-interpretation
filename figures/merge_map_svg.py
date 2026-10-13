#!/usr/bin/env python3
"""健康画像「归并信息图」生成器 —— 坐标与排版一律由脚本算，agent 不参与。

设计依据（已调研，见实施方案 §2.2）：
  · 不用雷达图：患者端可读性显著差于其他图形（Amsterdam UMC 混合方法研究），
    且结论依赖轴顺序、缺失与 0 分无法区分。
  · 不用分数：位置/长度编码虽最精确，但"26 分"对普通读者是抽象量；
    改用"用词说话"（词云的可取内核 —— Hearst：语义分区比随机云更易懂）。
  · 颜色只做细条强调、不做整块填充：PROTEUS 要求颜色信息在灰度下仍可读；
    CLEFT-Q 研究发现整块红黄绿会被读成"打分"，造成不必要的情绪伤害。
  · 归并是这张图的核心价值：把"六项异常"框在一起，直接传达"它们是一件事"。

用法：
    python3 merge_map_svg.py spec.json out.svg

spec.json：
{
  "title": "健康画像 · 2025-10-10",
  "sections": [
    {"label": "需要处理的",
     "groups": [
       {"heading": "...", "level": "intervene|watch|followup|normal|unknown",
        "items": ["...", "..."], "actions": ["...", "..."]}
     ]},
    {"label": "没问题的",   "plain": "血液 · 甲状腺功能 —— 三年都稳"},
    {"label": "不知道的（不是没问题）", "plain": "心肺功能 —— 三年没查"}
  ]
}
"""
import argparse
import json
import re
import sys

W = 880
PAD = 34
BOX_L = PAD
BOX_R = W - PAD
TITLE_H = 46
SEC_H = 30
GAP_SEC = 16
GAP_GROUP = 12
LINE_ITEM = 23
LINE_ACT = 25
GROUP_PAD_TOP = 26
GROUP_PAD_BOT = 16

# 级别 → (强调色, 强调条宽, 标题色)
LEVELS = {
    "intervene": ("#c0392b", 6, "#8e2a20"),
    "watch":     ("#d97706", 6, "#8a4b06"),
    "followup":  ("#4c9a2a", 6, "#2f6b18"),
    "normal":    ("#4c9a2a", 4, "#2f6b18"),
    "unknown":   ("#9aa0a6", 4, "#5f6368"),
}
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


try:
    from checkup.console import init_console
except ImportError:                     # 直跑：把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "engine"))
    from checkup.console import init_console
init_console()

def text_w(s, size):
    """粗略估宽：CJK 记 1 em，其余记 0.55 em。仅用于换行，不用于精确定位。"""
    return sum(size if CJK.match(c) else size * 0.55 for c in s)


def wrap(s, size, max_w):
    lines, cur = [], ""
    for ch in s:
        if text_w(cur + ch, size) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def group_height(g, inner_w):
    h = GROUP_PAD_TOP
    for it in g.get("items", []):
        h += LINE_ITEM * len(wrap(it, 14, inner_w - 24))
    if g.get("actions"):
        h += 6
        for a in g["actions"]:
            h += LINE_ACT * len(wrap(a, 14, inner_w - 60))
    return h + GROUP_PAD_BOT


def render(spec):
    inner_w = BOX_R - BOX_L
    # 先算总高
    H = TITLE_H
    for sec in spec["sections"]:
        H += SEC_H + 6
        if "groups" in sec:
            for g in sec["groups"]:
                H += group_height(g, inner_w) + GAP_GROUP
            H -= GAP_GROUP
        else:
            for ln in wrap(sec["plain"], 15, inner_w - 16):
                H += 24
        H += GAP_SEC
    H += 8

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="-apple-system, \'PingFang SC\', '
        f'\'Hiragino Sans GB\', \'Microsoft YaHei\', sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{PAD}" y="30" font-size="17" font-weight="700" '
        f'fill="#1a1a1a">{esc(spec.get("title", ""))}</text>',
    ]

    y = TITLE_H
    for sec in spec["sections"]:
        # 分节标题：贯线 + 白底遮断 + 文字（视觉上像"标签压在线上"）
        label_w = text_w(sec["label"], 13.5) + 16
        out.append(f'<line x1="{PAD}" y1="{y + 14}" x2="{BOX_R}" y2="{y + 14}" '
                   f'stroke="#e2e2e2" stroke-width="1"/>')
        out.append(f'<rect x="{PAD - 8}" y="{y + 2}" width="{label_w:.0f}" '
                   f'height="24" fill="#ffffff"/>')
        out.append(f'<text x="{PAD}" y="{y + 19}" font-size="13.5" font-weight="700" '
                   f'fill="#6b6b6b" letter-spacing="0.5">{esc(sec["label"])}</text>')
        y += SEC_H + 6

        if "groups" in sec:
            for g in sec["groups"]:
                gh = group_height(g, inner_w)
                color, bar_w, title_col = LEVELS.get(g.get("level", "watch"),
                                                     LEVELS["watch"])
                out.append(f'<rect x="{BOX_L}" y="{y}" width="{inner_w}" height="{gh}" '
                           f'rx="6" fill="#fbfbfb" stroke="#e6e6e6" stroke-width="1"/>')
                out.append(f'<rect x="{BOX_L}" y="{y}" width="{bar_w}" height="{gh}" '
                           f'rx="{bar_w / 2}" fill="{color}"/>')
                ty = y + 19
                for ln in wrap(g.get("heading", ""), 15, inner_w - 30):
                    out.append(f'<text x="{BOX_L + 20}" y="{ty}" font-size="15" '
                               f'font-weight="700" fill="{title_col}">{esc(ln)}</text>')
                    ty += 21
                for it in g.get("items", []):
                    for i, ln in enumerate(wrap(it, 14, inner_w - 24)):
                        prefix = "· " if i == 0 else "  "
                        out.append(f'<text x="{BOX_L + 26}" y="{ty + LINE_ITEM - 6}" '
                                   f'font-size="14" fill="#333333">'
                                   f'{esc(prefix + ln)}</text>')
                        ty += LINE_ITEM
                if g.get("actions"):
                    ty += 6
                    for a in g["actions"]:
                        for i, ln in enumerate(wrap(a, 14, inner_w - 60)):
                            prefix = "▸ " if i == 0 else "   "
                            out.append(f'<text x="{BOX_L + 26}" '
                                       f'y="{ty + LINE_ACT - 6}" font-size="14" '
                                       f'font-weight="600" fill="{title_col}">'
                                       f'{esc(prefix + ln)}</text>')
                            ty += LINE_ACT
                y += gh + GAP_GROUP
            y -= GAP_GROUP
        else:
            for ln in wrap(sec["plain"], 15, inner_w - 16):
                out.append(f'<text x="{BOX_L + 8}" y="{y + 16}" font-size="15" '
                           f'fill="#333333">{esc(ln)}</text>')
                y += 24
        y += GAP_SEC

    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="健康画像「归并信息图」生成器（坐标与排版一律由脚本算）")
    ap.add_argument("spec", help="spec.json 路径")
    ap.add_argument("out", help="输出 SVG 路径")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(spec))
    print(f"已写出 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
