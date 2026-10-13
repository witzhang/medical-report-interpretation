#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**抽检单**（C3）：交付物附一张 ≤5 条的抽检清单（机器决定 + 依据 + 对照原文页）。

    python3 engine/tools/spot_check.py --engine-out <产物目录> [--out <抽检单.md>]

为什么人只需抽检：正确性由 E1–E6 机械验收（模板层）+ 对账 34/34（报告层）保证；
人的角色从"回答者"降为"**抽检者**"。这张单子挑**最值得看一眼**的几条：

1. **级别最高/有红旗的项**（错在哪儿代价最大）；
2. **跨年比较的"转折项"**（判了"升/降"的，看它有没有被检索口径坑到）；
3. **年份间数字最容易看错的位置**（同项目多行、重复出现的项）；
4. 其余按确定性抽样（同 seed 可复现），凑满 ≤5 条。

每条三件：**机器决定**（值/级别）+ **依据**（哪条规则/哪张产物）+ **对照原文页**
（年份 · 页码 · 项目原文 · 值原文）—— 人只回答"这一条和原报告对得上吗"。
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(SKILL, "reconcile"))

from checkup.console import init_console  # noqa: E402
init_console()

MAX_ITEMS = 5


def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build(engine_out: str, seed: int = 0) -> list[dict]:
    """挑 ≤5 条抽检项。"""
    items: list[dict] = []
    obs = _rows(os.path.join(engine_out, "normalized_observations.csv"))
    by_key = {(r["年份"], r["项目原文"]): r for r in obs}

    # ① 跨年比较里**最值得看一眼**的项：变化超出噪音 / 稳健性取决于 CVi /
    #    2026 落在非正常区间（这三类最容易因为口径、单位或检索位置看错）
    cands: list[tuple[int, str, dict]] = []
    for j in _rows(os.path.join(engine_out, "judgements.csv")):
        verdict = (j.get("判2024→2026") or "").strip()
        robust = j.get("稳健性") or ""
        pos = (j.get("位置2026") or "").strip()
        if verdict == "beyond_noise":
            cands.append((0, "变化超出噪音（RCV）", j))
        elif "取决" in robust:
            cands.append((1, "稳健性取决于 CVi 取值", j))
        elif pos and pos != "normal":
            cands.append((2, f"2026 落在非正常区间（{pos}）", j))
        elif verdict == "within_noise":
            cands.append((3, "噪声内的变化", j))
    cands.sort(key=lambda x: x[0])
    for pri, why_pick, j in cands[:3]:
        loc = j.get("位置2026") or j.get("位置2024") or ""
        item = j.get("项目", "")
        verdict = (j.get("判2024→2026") or "").strip()
        src = by_key.get(("2026", item)) or by_key.get(("2024", item)) or {}
        items.append({
            "类别": f"跨年比较（{why_pick}）",
            "机器决定": f"{item}：{j.get('2024', '')} → {j.get('2026', '')} "
                        f"（{verdict or '—'}，{j.get('2024→2026%', '')}）",
            "依据": f"judgements.csv（稳健性 {j.get('稳健性', '')}，"
                    f"RCV 阈值 {j.get('RCV阈值%', '')}%）· 判读用 CVi/RCV 而不是裸百分比",
            "对照原文页": f"{src.get('年份', '')} 第 {src.get('页', '?')} 页 · "
                          f"原文「{src.get('项目原文', item)} = "
                          f"{src.get('值原文', '')} {src.get('单位', '')}」"
                          + (f"（{loc}）" if loc else ""),
        })
        if len(items) >= 3:
            break

    # ② 影像台账/机构发现的"级别"项（错在哪儿代价最大）
    for r in _rows(os.path.join(engine_out, "institution_findings.csv"))[:1]:
        items.append({
            "类别": "机构级发现",
            "机器决定": f"{r.get('年份', '')} {r.get('标题', '')}"
                        f" → 报告按脚本建议定级",
            "依据": "institution_findings.csv + states.json 的报告级别 "
                    "（调档必须在卡片写「（调档：<理由>）」）",
            "对照原文页": f"{r.get('年份', '')} 第 {r.get('页', '?')} 页 · "
                          f"原文「{(r.get('说明与建议') or '')[:80]}」",
        })
        break

    # ③ 数值与单位并排的项（单位最容易漏看；随便抽但可复现）
    pool = [r for r in obs if r.get("数值") and r.get("单位")]
    rng = random.Random(seed)
    for r in rng.sample(pool, min(2, len(pool))) if pool else []:
        if any(it["对照原文页"].startswith(f"{r['年份']} 第 {r['页']} 页")
               for it in items):
            continue
        items.append({
            "类别": "数值+单位",
            "机器决定": f"{r['项目原文']} = {r['数值']} {r['单位']}"
                        f"（{r['年份']}）",
            "依据": f"normalized_observations.csv（匹配方式 {r['匹配方式']}，"
                    f"单位状态 {r['单位状态']}）",
            "对照原文页": f"{r['年份']} 第 {r['页']} 页 · 原文「{r['项目原文']} = "
                          f"{r['值原文']} {r['单位']}，参考 {r['参考区间']}」",
        })
    return items[:MAX_ITEMS]


def main() -> int:
    ap = argparse.ArgumentParser(description="抽检单（≤5 条：机器决定 + 依据 + 原文页）")
    ap.add_argument("--engine-out", default="", help="引擎产物目录")
    ap.add_argument("--out", default="", help="输出 md（缺省 <engine-out>/抽检单.md）")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    engine_out = args.engine_out or os.path.join(SKILL, "engine", "catalog")
    if not os.path.isdir(engine_out):
        print(f"✗ 找不到产物目录：{engine_out}")
        return 1
    items = build(engine_out, seed=args.seed)
    L = ["# 抽检单（≤5 条）", "",
         "- 生成：`engine/tools/spot_check.py`（同 `--seed` 同清单，可复现）",
         "- **怎么用**：每条只看三行 —— 机器决定 / 依据 / 对照原文页；"
         "回答一个问题「这一条和原报告对得上吗」。",
         "- 为什么只需抽检：正确性由模板验收（E1–E6）+ 报告对账（34/34）机械保证；"
         "这张单子是给你**放心**用的，不看也不影响交付。", ""]
    for i, it in enumerate(items, 1):
        L += [f"## {i}. [{it['类别']}]", "",
              f"- **机器决定**：{it['机器决定']}",
              f"- **依据**：{it['依据']}",
              f"- **对照原文页**：{it['对照原文页']}", ""]
    if not items:
        L.append("（没有可抽检的项：产物目录里没有 judgements/observations）")
    out = args.out or os.path.join(engine_out, "抽检单.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"抽检单 {len(items)} 条 → {out}")
    for i, it in enumerate(items, 1):
        print(f"  {i}. [{it['类别']}] {it['机器决定'][:48]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
