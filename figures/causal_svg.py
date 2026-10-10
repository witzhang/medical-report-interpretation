#!/usr/bin/env python3
"""因果归并图生成器 —— 分层有向图，坐标一律由脚本算。

设计依据（见实施方案 §2.2）：
  · Vo et al. (Diagrams 2020)：节点-连线图在理解「中介因果」（A→B→C 路径）上优于表格。
  · Welles & Xu (Social Networks)：认知匹配 —— 关系/模式类任务用节点图最准。
  · Kim et al. (J Health Communication 2013)：患者从"视觉因果模型"学到的显著多于纯口头讲解。
  · Gonzalez：因果图提升对因果序列的记忆，"因→果"形式对健康素养较低者尤其有用。
  ⚠️ 节点一多就会变"毛线球"，所以本图必须保持稀疏：层数 ≤4、节点 ≤20、边 ≤24。

用法：
    python3 causal_svg.py spec.json out.svg

spec.json：
{
  "title": "因果归并：这些发现是怎么连起来的",
  "note": "实线 = 有依据的关联；虚线 = 推断，尚待确认",
  "layers": [
     {"label": "① 报告里的发现", "nodes": [
        {"id": "bp", "text": "血压 145/92", "note": "已达高血压标准"}, ...]},
     ...
  ],
  "edges": [["bp","met"], ["met","mets"]],          # 实线
  "dashed": [["mets","waist"]]                       # 虚线（推断）
}
"""
import argparse
import json
import re
import sys

PAD = 30
TITLE_H = 48
COL_GAP = 62
NODE_GAP = 15
NODE_PAD_X = 14
NODE_PAD_Y = 11
LINE_MAIN = 19
LINE_NOTE = 15
MAX_COL_W = 300

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


def node_size(n):
    main = wrap(n["text"], 14, MAX_COL_W - 2 * NODE_PAD_X)
    note = wrap(n.get("note", ""), 11.5, MAX_COL_W - 2 * NODE_PAD_X) if n.get("note") else []
    w = max([text_w(l, 14) for l in main] + [text_w(l, 11.5) for l in note] or [0])
    w = min(w, MAX_COL_W - 2 * NODE_PAD_X) + 2 * NODE_PAD_X
    h = len(main) * LINE_MAIN + (len(note) * LINE_NOTE + 3 if note else 0) + 2 * NODE_PAD_Y
    return w, h, main, note


def render(spec):
    layers = spec["layers"]
    # 逐层算节点尺寸
    sized = []
    for L in layers:
        col_max_w = 0
        items = []
        for n in L["nodes"]:
            w, h, main, note = node_size(n)
            items.append({"n": n, "w": w, "h": h, "main": main, "note": note})
            col_max_w = max(col_max_w, w)
        sized.append({"label": L["label"], "col_w": col_max_w, "items": items})

    W = int(2 * PAD + sum(c["col_w"] for c in sized) + COL_GAP * (len(sized) - 1))
    # 各列起点
    xs = []
    x = PAD
    for c in sized:
        xs.append(x)
        x += c["col_w"] + COL_GAP

    # 列内纵向排布（居中）
    def layout_col(ci, top, avail_h):
        c = sized[ci]
        total = sum(it["h"] for it in c["items"]) + NODE_GAP * (len(c["items"]) - 1)
        y = top + max(0, (avail_h - total) / 2)
        pos = {}
        for it in c["items"]:
            pos[it["n"]["id"]] = (y, it)
            y += it["h"] + NODE_GAP
        return pos, total

    # 先试 top，算出最大列高
    BODY_TOP = TITLE_H + 26
    probe = {}
    maxh = 0
    for ci in range(len(sized)):
        p, t = layout_col(ci, BODY_TOP, 0)
        probe[ci] = p
        maxh = max(maxh, t)
    H = int(BODY_TOP + maxh + 30)
    # 重排（居中）
    pos_by_col = []
    for ci in range(len(sized)):
        p, _ = layout_col(ci, BODY_TOP, maxh)
        pos_by_col.append(p)

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="-apple-system, \'PingFang SC\', '
        f'\'Hiragino Sans GB\', \'Microsoft YaHei\', sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{PAD}" y="30" font-size="16.5" font-weight="700" '
        f'fill="#1a1a1a">{esc(spec.get("title", ""))}</text>',
    ]

    # 层标题
    for ci, c in enumerate(sized):
        out.append(f'<text x="{xs[ci]}" y="{TITLE_H + 6}" font-size="13" '
                   f'font-weight="700" fill="#6b6b6b" letter-spacing="0.5">'
                   f'{esc(c["label"])}</text>')

    # 边（画在节点下层）
    def anchor(nid, side):
        for ci, p in enumerate(pos_by_col):
            if nid in p:
                y, it = p[nid]
                x = xs[ci] + (it["w"] if side == "r" else 0)
                return x, y + it["h"] / 2
        raise KeyError(f"边引用了不存在的节点：{nid}")

    for group, dash in ((spec.get("edges", []), None), (spec.get("dashed", []), "5 4")):
        for a, b in group:
            x1, y1 = anchor(a, "r")
            x2, y2 = anchor(b, "l")
            mx = (x1 + x2) / 2
            d = f"M {x1:.1f},{y1:.1f} C {mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"
            da = f' stroke-dasharray="{dash}"' if dash else ""
            out.append(f'<path d="{d}" fill="none" stroke="#b9c2cc" '
                       f'stroke-width="1.4"{da}/>')
            # 箭头
            out.append(f'<path d="M {x2-6:.1f},{y2-4:.1f} L {x2:.1f},{y2:.1f} '
                       f'L {x2-6:.1f},{y2+4:.1f}" fill="none" stroke="#b9c2cc" '
                       f'stroke-width="1.4"/>')

    # 节点
    for ci, c in enumerate(sized):
        for it in c["items"]:
            y, _ = pos_by_col[ci][it["n"]["id"]]
            n = it["n"]
            lv = n.get("level", "mid")
            bar, fill, stroke = {
                "hot":  ("#c0392b", "#fdf4f3", "#e8c9c5"),
                "mid":  ("#d97706", "#fdf9f2", "#e8dcc4"),
                "ok":   ("#4c9a2a", "#f4faf1", "#cfe3c5"),
                "info": ("#9aa0a6", "#fafafa", "#e3e3e3"),
            }.get(lv, ("#9aa0a6", "#fafafa", "#e3e3e3"))
            out.append(f'<rect x="{xs[ci]}" y="{y:.1f}" width="{it["w"]}" '
                       f'height="{it["h"]}" rx="5" fill="{fill}" stroke="{stroke}" '
                       f'stroke-width="1"/>')
            out.append(f'<rect x="{xs[ci]}" y="{y:.1f}" width="4" '
                       f'height="{it["h"]}" rx="2" fill="{bar}"/>')
            ty = y + NODE_PAD_Y
            for ln in it["main"]:
                ty += LINE_MAIN - 5
                out.append(f'<text x="{xs[ci] + NODE_PAD_X}" y="{ty:.1f}" '
                           f'font-size="14" font-weight="600" fill="#242424">'
                           f'{esc(ln)}</text>')
            if it["note"]:
                ty += 6
                for ln in it["note"]:
                    ty += LINE_NOTE - 3
                    out.append(f'<text x="{xs[ci] + NODE_PAD_X}" y="{ty:.1f}" '
                               f'font-size="11.5" fill="#6f6f6f">{esc(ln)}</text>')

    note = spec.get("note")
    if note:
        out.append(f'<text x="{PAD}" y="{H - 12}" font-size="12" '
                   f'fill="#8a8a8a">{esc(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="因果归并图生成器（坐标与排版一律由脚本算）")
    ap.add_argument("spec", help="spec.json 路径")
    ap.add_argument("out", help="输出 SVG 路径")
    a = ap.parse_args()
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    nodes = sum(len(L["nodes"]) for L in spec["layers"])
    edges = len(spec.get("edges", [])) + len(spec.get("dashed", []))
    if nodes > 20 or edges > 24:
        print(f"⚠️ 节点 {nodes} / 边 {edges} 超出稀疏上限（20/24），"
              f"图会变毛线球，请先简化", file=sys.stderr)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(spec))
    print(f"已写出 {a.out}（节点 {nodes} · 边 {edges}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
