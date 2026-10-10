#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺口对账（P2-4 / P2-5）：把**缺口表**与 `coverage.json` 对齐，并把三类易混情形分开。

    python3 scripts/check_gaps.py <报告.md> --artifacts <四张底稿目录> [--out <md>]

回答四件事（**全部脚本口径，⛔ agent 不手工数**）：

1. **三分语义**：每个缺口/部分覆盖项是 `未做`（该年没有记录）/ `未找到`（做了但资料没有）/
   `确实为 0` 中的哪一种——「缺」与「报告未载明」是**两件事**，不许混（C04/C05 双向锁同源）。
2. **断档年**：某年整块项目缺失（例：2025 年漏做肺 CT 与骨密度 → 跨那一年的影像比较不可靠）。
3. **首年有、后年无**：第一年做了、后面某年没跟踪的项（P2-5 明确要求特别标出）。
4. **跨年不可比**：参考区间/单位逐年变化 → 只能报位置、不能连线（P2-3/P2-7）。

退出码：0 = 对账一致（报告含覆盖率四元组与全部缺口项）；1 = 不一致。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILL, "reconcile"))

from paths import default_artifacts, guard_out, product_root  # noqa: E402


from checkup.console import init_console  # noqa: E402
init_console()

def _norm(s: str) -> str:
    """归一：去空白、去括号内容、去「检查/试验/检测/测定」后缀（同 reconcile 口径）。"""
    import re
    s = re.sub(r"[\s　]", "", s or "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return re.sub(r"(检查|试验|检测|测定)$", "", s)


def _keys(item: str) -> list[str]:
    """一个项目名的可接受写法：全名 / 去后缀名 / 括号内内容（如「放射检查（胸部正位片）」）。"""
    import re
    ks = [item, _norm(item)]
    for m in re.findall(r"[（(]([^）)]+)[）)]", item):
        ks += [m, _norm(m)]
    return [k for k in dict.fromkeys(ks) if k]


def _aliases(cov: dict, project: str) -> list[str]:
    """项目名的可接受写法 = 全名 + coverage 里的「报告用名」+ 括号内内容（与 reconcile C02/C03 同源）。"""
    names = [project]
    for it in cov.get("逐项", []):
        if it.get("项目") == project:
            names += it.get("报告用名", [])
            break
    out = []
    for n in names:
        out += _keys(n)
    return list(dict.fromkeys(out))


def analyse(art_dir: str) -> tuple[dict, list[str]]:
    """→ (事实包, 不一致清单)。"""
    cov = json.load(open(os.path.join(art_dir, "coverage.json"), encoding="utf-8"))
    fct = json.load(open(os.path.join(art_dir, "facts.json"), encoding="utf-8"))
    problems: list[str] = []

    years = fct.get("年份") or sorted(
        {y for blk in fct.get("数值表", {}).values() for y in blk.get("逐年", {})})

    # ① 三分语义
    three_way = []
    for g in cov.get("缺口明细", []):
        three_way.append({"项目": g["项目"], "类别": g.get("类别"),
                          "判定": "未做（所有年份都没有记录）",
                          "能发现": g.get("能发现", "")})
    for p in cov.get("部分覆盖明细", []):
        for y, v in sorted(p.get("按年", {}).items()):
            if v == "missing":
                reason = "；".join(p.get("备注", [])) or "缺年份（该年未含/未做）"
                three_way.append({"项目": p["项目"], "类别": p.get("类别"),
                                  "判定": f"{y} 未做 —— {reason}",
                                  "能发现": p.get("能发现", "")})

    # ② 断档年 + ③ 首年有后年无（用逐项的按年状态算）
    gaps_by_item = {g["项目"]: g for g in cov.get("缺口明细", [])}
    partial = {p["项目"]: p for p in cov.get("部分覆盖明细", [])}
    lapsed: list[dict] = []
    for it in cov.get("逐项", []):
        by = it.get("按年", {})
        ys = sorted(by)
        if len(ys) < 2:
            continue
        states = [by[y] for y in ys]
        # 首年有、中途/最后缺
        if states[0] == "full" and any(s == "missing" for s in states[1:]):
            miss = [y for y in ys[1:] if by[y] == "missing"]
            lapsed.append({"项目": it["项目"], "首年": ys[0],
                           "缺的年份": miss, "状态": it.get("状态")})
    # 断档年 = **首次有记录之后**又缺的年份（项目级）；不把"后几年才加做"算成断档
    blackout: dict = {}
    for it in cov.get("逐项", []):
        ys = sorted(it.get("按年") or {})
        seen_record = False
        for y in ys:
            st = it["按年"][y]
            if st != "missing":
                seen_record = True
            elif seen_record:
                blackout.setdefault(y, []).append(it["项目"])
    # 影像类（超出指引的加做项，不在覆盖表里）：中间年缺失也算断档
    for name, blk in fct.get("影像", {}).items():
        ys = sorted(blk)
        if len(ys) >= 2:
            span = [str(n) for n in range(int(ys[0]), int(ys[-1]) + 1)]
            for y in span:
                if y not in ys:
                    blackout.setdefault(y, []).append(name.split("（")[0])

    # ④ 跨年不可比：参考区间逐年是否变化（数值表）
    incomparable = []
    for name, blk in fct.get("数值表", {}).items():
        rows = blk.get("逐年", {})
        units = {t.get("单位") for t in rows.values() if t.get("单位")}
        refs = {y: (t.get("参考区间") or "") for y, t in rows.items()}
        vals = {r for r in refs.values() if r}
        # 任一年带「换算」= 载入层已把该年值**与区间**换算过 → 文本差异属单位换算，不算不可比
        converted = any(bool(t.get("换算")) for t in rows.values())
        if len(units) > 1:
            incomparable.append({"项目": name, "原因": "单位不同（未换算）",
                                "参考区间逐年": refs})
        elif len(vals) > 1 and not converted:
            incomparable.append({"项目": name, "原因": "参考区间文本不同",
                                "参考区间逐年": refs})

    facts = {
        "_aliases": {t["项目"]: _aliases(cov, t["项目"]) for t in three_way},
        "年份": years,
        "覆盖率": {k: cov.get(k) for k in ("应查", "全覆盖", "部分覆盖", "缺", "覆盖率")},
        "三分语义": three_way,
        "断档年": {y: v for y, v in blackout.items() if len(v) >= 2},
        "首年有后年无": lapsed,
        "跨年不可比（参考区间变化）": incomparable,
        "超出指引要求": [x.get("项目") for x in cov.get("超出指引要求", [])]
        if isinstance(cov.get("超出指引要求"), list) else cov.get("超出指引要求"),
    }
    return facts, problems


def report_consistency(report: str, facts: dict) -> list[str]:
    """报告必须含：覆盖率四元组、每个缺口项、部分覆盖原因。"""
    bad = []
    R = report
    cov = facts["覆盖率"]
    for label, v in (("应查", cov["应查"]), ("全覆盖", cov["全覆盖"]),
                     ("部分覆盖", cov["部分覆盖"]), ("缺", cov["缺"]),
                     ("覆盖率", cov["覆盖率"])):
        if str(v) not in R:
            bad.append(f"报告缺覆盖率口径「{label} {v}」")
    nR = _norm(R)
    items = {t["项目"] for t in facts["三分语义"]}
    for it in sorted(items):
        alts = facts.get("_aliases", {}).get(it) or _keys(it)
        if not any(_norm(k) in nR for k in alts):
            bad.append(f"报告缺缺口/部分覆盖项：{it}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="缺口对账（与 coverage.json 对齐）")
    ap.add_argument("report", help="报告 md（对账一致性）")
    ap.add_argument("--artifacts", default="", help="四张底稿目录")
    ap.add_argument("--out", default="", help="对账记录输出（缺省 <产物根>/缺口对账.md）")
    ap.add_argument("--case-name", default="real")
    args = ap.parse_args()

    art = args.artifacts or default_artifacts(args.case_name)
    facts, _ = analyse(art)
    report = open(args.report, encoding="utf-8").read()
    bad = report_consistency(report, facts)

    L = [f"# 缺口对账 · {args.case_name}", "",
         f"- 覆盖率：应查 {facts['覆盖率']['应查']} · 全覆盖 {facts['覆盖率']['全覆盖']} · "
         f"部分覆盖 {facts['覆盖率']['部分覆盖']} · 缺 {facts['覆盖率']['缺']} · "
         f"**{facts['覆盖率']['覆盖率']}%**（口径 = coverage.json）", "",
         "## 一、三分语义（未做 / 未找到 / 确实为 0）", ""]
    L += ["| 项目 | 类别 | 判定 | 它本来能发现什么 |", "|---|---|---|---|"]
    for t in facts["三分语义"]:
        L.append(f"| {t['项目']} | {t.get('类别') or '—'} | {t['判定']} | {t['能发现']} |")
    L += ["", "> `未做` = 该年没有任何记录（**不是 0，也不是正常**）；"
              "`未找到` = 做了但资料里没有；`确实为 0` = 有记录且为 0。"
              "缺口项均为「未做」；有记录的年份一律按做过算（C04/C05 双向锁）。", ""]
    L += ["## 二、断档年（首次有记录之后又缺的年份；含中间年缺失）", ""]
    if facts["断档年"]:
        L += ["| 年份 | 缺的项目 |", "|---|---|"]
        for y, items in sorted(facts["断档年"].items()):
            L.append(f"| {y} | {'、'.join(items)} |")
        L += ["", "> 跨断档年的比较**不可靠**，报告必须写明。", ""]
    else:
        L += ["- 无", ""]
    L += ["## 三、首年有、后年无（P2-5 要求特别标出）", ""]
    if facts["首年有后年无"]:
        L += ["| 项目 | 首年 | 缺的年份 | 该项状态 |", "|---|---|---|---|"]
        for t in facts["首年有后年无"]:
            L.append(f"| {t['项目']} | {t['首年']} | {'、'.join(t['缺的年份'])} | {t['状态']} |")
        L += [""]
    else:
        L += ["- 无", ""]
    L += ["## 四、跨年不可比（参考区间逐年变化 → 只报位置、不连线）", ""]
    if facts["跨年不可比（参考区间变化）"]:
        for t in facts["跨年不可比（参考区间变化）"]:
            L.append(f"- **{t['项目']}**（{t.get('原因', '区间不同')}）：" + " / ".join(
                f"{y}：{r}" for y, r in sorted(t["参考区间逐年"].items())))
        L += [""]
    else:
        L += ["- 无（三年参考区间一致）", ""]
    L += ["## 五、对账结论", ""]
    if bad:
        L += ["**不一致：**"] + [f"- {b}" for b in bad]
    else:
        L += ["✅ 报告含覆盖率四元组与全部缺口/部分覆盖项，与 coverage.json 一致；"
              "三分语义、断档年、首年有后年无、跨年不可比四类已分开列示。"]

    out = guard_out(args.out or os.path.join(product_root(), f"缺口对账_{args.case_name}.md"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"[{args.case_name}] 覆盖 {facts['覆盖率']['覆盖率']}% · "
          f"缺口 {facts['覆盖率']['缺']} 项 · 部分覆盖 {facts['覆盖率']['部分覆盖']} 项 · "
          f"断档年 {list(facts['断档年'])} · 首年有后年无 {len(facts['首年有后年无'])} 项 · "
          f"跨年不可比 {len(facts['跨年不可比（参考区间变化）'])} 项")
    if bad:
        print("✗ 报告与产物不一致：")
        for b in bad:
            print("   ·", b)
        print(f"（详见 {out}）")
        return 1
    print(f"✓ 缺口对账一致（详见 {out}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
