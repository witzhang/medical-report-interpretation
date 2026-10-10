#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""血缘反查 + 抽样回读（N-4 / N-3 / P4）。

    # 单个数字：报告数字 → 底稿 → 引擎产物 → 页码/原文
    python3 scripts/trace_number.py --value 2.32 --item 甘油三酯 \
        --artifacts <四张底稿目录> --engine-out <引擎产物目录>

    # 交付前抽样回读：从报告里盲抽 N 个带单位数字，逐条回原 PDF 页核对
    python3 scripts/trace_number.py --report <报告.md> --artifacts <…> \
        --engine-out <…> --pdf-dir <原始报告目录> --sample 5 --out <产物根>/抽样回读_x.md

链条（与端到端文档 §11.1 的锚点一致）：

    报告里的数字 ──→ artifacts（数值表/派生/影像/数据质量/constants）
                 ──→ 引擎产物（judgements.csv 值 · normalized_observations.csv 值原文）
                 ──→ **原 PDF 第 N 页**（脚本回读该页文字层，核对值原文确实在那一页）

退出码：0 = 无断链（未完成回读的行如实计入『回读完成度』）；1 = 有断链。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "reconcile"))

from paths import (FIXTURES, guard_out, product_root,  # noqa: E402
                   resolve_constants)
from units import unit_compat  # noqa: E402

DATA_UNITS = ("mmol/L", "μmol/L", "umol/L", "cm", "mm", "kg", "mmHg", "U/L",
              "ng/ml", "ng/mL", "次/分", "g/L", "mIU/L", "pmol/L", "mL/min",
              "%", "10^9/L")
#: 单位按**长度降序**参与交替 —— 否则 "mmHg" 会被 "mm" 抢先截断
#: （"119/78 mmHg" 曾被读成 "78 mm"，进而指回腰围 78 cm 一类错源）
TOK = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(
    re.escape(u) for u in sorted(DATA_UNITS, key=len, reverse=True)) + ")")


from checkup.console import init_console  # noqa: E402
init_console()

def _load(art_dir: str, constants: str = "") -> dict:
    """载入底稿 + 常量登记簿（与对账层**同源**：--constants / CHECKUP_CONSTANTS /
    数据根 constants.user.json 附加登记 / 内置）。"""
    d = lambda n: json.load(open(os.path.join(art_dir, n), encoding="utf-8"))  # noqa: E731
    return {"facts": d("facts.json"), "states": d("states.json"),
            "coverage": d("coverage.json"), "advice": d("advice.json"),
            "constants": resolve_constants(constants)}


def _flat(o, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        for v in o.values():
            _flat(v, out)
    elif isinstance(o, list):
        for v in o:
            _flat(v, out)
    else:
        out.append(o)
    return out


def _match(v, value: float, dec: int = 2) -> bool:
    """与 reconcile 数字追踪同口径：绝对值相等、或**按报告自己的显示精度**四舍五入后一致。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    if abs(v - value) < 1e-6:
        return True
    for d in {dec, 1, 2, 3}:
        if f"%.{d}f" % v == f"%.{d}f" % value:
            return True
    return False


def _rank(v, value: float, dec: int = 2):
    """匹配强度：0=精确 · 1=显示精度四舍五入相等 · 2=0.5% 容差；不匹配→None。

    用于在多行候选里挑**最强**的一行（此前取文件首行：3.12 会指到 2025 的 3.13）。
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if abs(v - value) < 1e-6:
        return 0
    for d in {dec, 1, 2, 3}:
        if f"%.{d}f" % v == f"%.{d}f" % value:
            return 1
    if abs(v - value) / max(abs(v), 1e-9) < 0.005:
        return 2
    return None


def trace_one(A: dict, engine_out: str, value: float, item: str = "",
              dec: int = 2, unit: str = "") -> dict:
    """→ {底稿, 引擎, 页码, 原文}（能指到多少给多少）。"""
    hit = {"底稿": [], "引擎": "", "页码": "", "原文": "", "页候选": []}
    fct = A["facts"]
    for name, blk in fct.get("数值表", {}).items():
        if item and item not in name:
            continue
        for y, t in blk.get("逐年", {}).items():
            v = t.get("值")
            if v is not None and _match(v, value, dec) and unit_compat(unit, t.get("单位")):
                hit["底稿"].append(f"数值表.{name}.{y}={v}{t.get('单位') or ''}"
                                   f"（区间 {t.get('参考区间') or '—'}）")
                hit["页码"] = hit["页码"] or str(t.get("页") or "")
                if t.get("页"):
                    hit["页候选"].append((_rank(v, value, dec), y, str(t["页"])))
                hit["引擎"] = blk.get("analyte_id") or ""
    # 派生/数据质量/constants/覆盖率：无单位元数据 → 不做单位过滤（保持召回；
    # 命中标签本身写明来源，供人工核读）
    for name, blk in fct.get("派生", {}).items():
        for v in _flat(blk):
            if isinstance(v, (int, float)) and _match(v, value, dec):
                hit["底稿"].append(f"派生.{name}={v}")
    for name, blk in fct.get("影像台账", {}).items():
        for y, t in (blk.get("逐年") or {}).items():
            if not isinstance(t, dict):
                continue
            sz = t.get("尺寸")
            if sz not in (None, "") and _match(sz, value, dec) and unit_compat(unit, t.get("单位")):
                hit["底稿"].append(f"影像台账.{name}.{y}={sz}{t.get('单位') or ''}")
    # 参考区间里的数字（机构标注；单位随该年）——与对账层通道对称
    for name, blk in fct.get("数值表", {}).items():
        if item and item not in name:
            continue
        for y, t in blk.get("逐年", {}).items():
            if not unit_compat(unit, t.get("单位")):
                continue
            for x in re.findall(r"\d+(?:\.\d+)?", str(t.get("参考区间") or "")):
                if _match(float(x), value, dec):
                    hit["底稿"].append(f"参考区间.{name}.{y}={x}{t.get('单位') or ''}")
    # 影像「测量原文」里的数字（单位随条目）
    for name, blk in fct.get("影像台账", {}).items():
        if item and item not in name:
            continue
        for y, t in blk.get("逐年", {}).items():
            if not isinstance(t, dict) or not unit_compat(unit, t.get("单位")):
                continue
            for x in re.findall(r"\d+(?:\.\d+)?", str(t.get("测量原文") or "")):
                if _match(float(x), value, dec):
                    hit["底稿"].append(f"影像测量原文.{name}.{y}={x}{t.get('单位') or ''}")
    for k, blk in fct.get("数据质量", {}).items():
        for v in _flat(blk):
            if isinstance(v, (int, float)) and _match(v, value, dec):
                hit["底稿"].append(f"数据质量.{k}={v}")
    # 状态分类里的变化量与噪声区间（与 reconcile 数字追踪同口径；缺这段会漏
    # 「幅度 +36.4%」这类只写在 states 里的数字）
    for r in A.get("states", {}).get("项目状态", []):
        ch = r.get("变化") or {}
        for label, raw in (("首末变化", r.get("首末变化%")), ("变化", ch.get("变化%"))):
            if raw is None:
                continue
            try:
                v = abs(float(raw))
            except (TypeError, ValueError):
                continue
            if _match(v, value, dec) and unit_compat(unit, "%"):
                hit["底稿"].append(f"states.{label}.{r['项目']}={raw}%")
        for b in ch.get("RCV区间%", []) or []:
            try:
                v = float(b)
            except (TypeError, ValueError):
                continue
            if _match(v, value, dec) and unit_compat(unit, "%"):
                hit["底稿"].append(f"states.RCV区间.{r['项目']}={b}%")
    # 逐年余量%（距参考上限的余量；% 类）——"距上限仅 0.2%"应有出处。
    # 放在变化/区间之后：同值下让"首末变化/RCV"等正源排在前面（展示与首条提示优先）
    for r in A.get("states", {}).get("项目状态", []):
        if item and item not in r.get("项目", ""):
            continue
        if not unit_compat(unit, "%"):
            continue
        for y, t in (r.get("逐年") or {}).items():
            mv = t.get("余量%") if isinstance(t, dict) else None
            if mv is None:
                continue
            if _match(abs(float(mv)), value, dec):
                hit["底稿"].append(f"states.余量%.{r['项目']}.{y}={mv}%")
    for k, blk in (A.get("constants") or {}).items():
        if k.startswith("_"):
            continue
        for v in _flat(blk):
            if isinstance(v, (int, float)) and _match(v, value, dec):
                hit["底稿"].append(f"constants.{k}={v}（登记制，带出处）")
                break
    for v in _flat(A["coverage"]):
        if isinstance(v, (int, float)) and abs(float(v) - value) < 1e-6:
            hit["底稿"].append(f"覆盖率口径={v}")

    # 引擎产物：归一表（值原文 + 页）+ judgements
    npath = os.path.join(engine_out, "normalized_observations.csv")
    if os.path.exists(npath):
        best = None       # (rank, 行)
        with open(npath, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not r.get("数值"):
                    continue
                try:
                    v = float(r["数值"])
                except ValueError:
                    continue
                # 报告侧带单位 → 该行单位必须**存在且可比**：空单位的观测不让指回，
                # 否则 "0.2%" 会撞上"上皮细胞=0.2（无单位）"这类值巧合。
                if not (unit_compat(unit, r.get("单位")) and (r.get("单位") or not unit)):
                    continue
                if not (not item or item in r["项目原文"]
                        or (hit["引擎"] and hit["引擎"] == r["analyte_id"])):
                    continue
                rk = _rank(v, value, dec)
                if rk is None:
                    continue
                if best is None or rk < best[0]:
                    best = (rk, r)
        if best is not None:
            r = best[1]
            hit["原文"] = f'{r["年份"]} 项目「{r["项目原文"]}」值原文 {r["值原文"]} ' \
                          f'区间 {r["参考区间"]}'
            hit["页码"] = hit["页码"] or str(r.get("页") or "")
            if r.get("页"):
                hit["页候选"].append((best[0], r["年份"], str(r["页"])))
            hit["引擎"] = hit["引擎"] or r["analyte_id"]
    # 页候选按匹配强度排序（最强的一年在前）
    hit["页候选"] = [(y, p) for _, y, p in sorted(hit["页候选"])]
    return hit


def verify_in_pdf(pdf_dir: str, year: str, page: str, value_text: str) -> str:
    """回读原 PDF：该年那份报告的第 page 页文字层里有没有这个值。"""
    if not (pdf_dir and os.path.isdir(pdf_dir) and page):
        return "（未提供原始报告目录或缺页码 → 跳过回读）"
    if not year:
        return "（未能确定年份 → 跳过回读）"
    import fitz
    cands = [f for f in sorted(os.listdir(pdf_dir))
             if f.lower().endswith(".pdf") and year in f]
    if not cands:
        return f"（{year} 年的 PDF 未找到）"
    doc = fitz.open(os.path.join(pdf_dir, cands[0]))
    try:
        i = int(page) - 1
        if i < 0 or i >= doc.page_count:
            return f"（页码 {page} 超范围）"
        t = doc[i].get_text()
    finally:
        doc.close()
    vt = (value_text or "").split("，")[-1].split(" ")[-1]
    key = re.sub(r"[\s,]", "", value_text or "")
    on_page = (key in re.sub(r"[\s,]", "", t)) or (vt and vt in t)
    return (f"✓ 第 {page} 页文字层核到「{value_text}」" if on_page
            else f"✗ 第 {page} 页文字层**未**核到「{value_text}」")


def main() -> int:
    ap = argparse.ArgumentParser(description="血缘反查 / 抽样回读")
    ap.add_argument("--value", type=float, default=None)
    ap.add_argument("--unit", default="",
                    help="与 --value 配套的单位（用于拒绝跨单位数值巧合；缺省不过滤）")
    ap.add_argument("--item", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--artifacts", default="")
    ap.add_argument("--engine-out", default="")
    ap.add_argument("--pdf-dir", default="")
    ap.add_argument("--case-name", default="real")
    ap.add_argument("--constants", default="",
                    help="常量登记簿（与对账层同源；缺省 CHECKUP_CONSTANTS 或数据根 "
                         "constants.user.json，再退内置）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    art = args.artifacts or os.path.join(product_root(), "artifacts", args.case_name)
    eng = args.engine_out or FIXTURES.engine_out
    A = _load(art, args.constants)

    if args.value is not None:
        _vs = f"{args.value:g}"
        h = trace_one(A, eng, args.value, args.item, unit=args.unit,
                      dec=len(_vs.split(".")[1]) if "." in _vs else 0)
        print(f"数字 {args.value}" + (f"（{args.item}）" if args.item else ""))
        print(f"  底稿：{'；'.join(h['底稿']) or '未指回'}")
        print(f"  引擎：{h['引擎'] or '—'}")
        print(f"  原文：{h['原文'] or '—'}")
        print(f"  页码：{h['页码'] or '—'}")
        return 0 if h["底稿"] else 1

    if not args.report:
        print("要么给 --value <数字>，要么给 --report <报告.md> 做抽样回读")
        return 2
    report = open(args.report, encoding="utf-8").read()
    # 先收候选（最多 60 个不同值），再**优先选能回原页的数**（数值表类）：
    # 回读的意义在"原页核到"；纯派生/百分比（states/数据质量）天然没有页码，
    # 全部混入会让"回读完成度"失真。组内保持报告出现顺序。
    seen, cands = set(), []
    for m in TOK.finditer(report):
        val, unit = float(m.group(1)), m.group(2)
        dec = len(m.group(1).split(".")[1]) if "." in m.group(1) else 0
        if val in seen:
            continue
        seen.add(val)
        ctx = report[max(0, m.start() - 30):m.end() + 8].replace("\n", " ")
        cands.append((val, unit, ctx, dec))
        if len(cands) >= 60:
            break
    with_page, no_page = [], []
    for c in cands:
        h = trace_one(A, eng, c[0], dec=c[3], unit=c[1])
        (with_page if (h["页码"] and h["原文"]) else no_page).append((c, h))
    picks = (with_page + no_page)[:args.sample]

    L = [f"# 抽样回读 · {args.case_name}", "",
         f"- 抽样 {len(picks)} 个带单位数字（优先取'可回原页'的数值表读数，"
         f"不足时补充其余；组内按报告出现顺序）",
         "- 链条：报告数字 → 底稿 → 引擎产物（值原文）→ **回原 PDF 页核对**", "",
         "| # | 报告数字 | 上下文 | 指回底稿 | 值原文 | 页码 | 回原页核对 |",
         "|---|---|---|---|---|---|---|"]
    bad, soft = [], []
    for i, ((val, unit, ctx, dec), h) in enumerate(picks, 1):
        vt = ""
        if h["原文"]:
            m = re.search(r"值原文 (.+?) 区间", h["原文"])
            vt = m.group(1) if m else ""
        if not h["页码"]:
            # 产物没有页码（如合成案例来源为 .txt）→ 回读这一步明确跳过，不算断链
            vfy = "（产物无页码 → 跳过回读）"
        elif vt:
            # 候选页**逐一尝试**：同一数值可能只在某一年命中——避免"拿错年份 →
            # 误报页码超范围"这类同一文件内判词矛盾。命中即记该页。
            pdf_dir = args.pdf_dir or FIXTURES.real_reports
            tried, vfy = [], "（无候选页）"
            for (yy, pp) in (h.get("页候选") or [])[:4]:
                v = verify_in_pdf(pdf_dir, yy, pp, vt)
                tried.append(f"{yy or '?'}·p{pp}: {v}")
                if v.startswith("✓"):
                    vfy = v
                    h["页码"] = pp
                    break
            else:
                # 全未核到 → **以 ✗ 开头**（计入断链），不得让"核不到"伪装成"没回读"
                vfy = ("✗ 候选页都未核到：" + "；".join(tried)[:120]) if tried \
                    else "（无候选页 → 跳过）"
        else:
            vfy = "（无引擎原文记录 → 跳过）"
        if vfy.startswith("（"):
            soft.append(val)          # 跳过/未找到/超范围：未完成回读，如实计数
        if not h["底稿"] or vfy.startswith("✗"):
            bad.append(val)
        L.append(f"| {i} | {val} {unit} | …{ctx.strip()[:36]}… | "
                 f"{'；'.join(h['底稿'])[:60] or '**未指回**'} | {vt or '—'} | "
                 f"{h['页码'] or '—'} | {vfy} |")
    done = len(picks) - len(soft)
    if not bad and not soft:
        verdict = "✅ 全部可反查、且全部在原报告页文字层核到。"
    elif not bad:
        verdict = (f"◐ 全部可反查；回读完成度 {done}/{len(picks)} —— "
                   f"{len(soft)} 行未完成回读（原因见上表：无页码/年份未定/页码超范围等），"
                   f"『全部回原页核到』不成立。")
    else:
        verdict = f"**有断链**：{bad}（另有 {len(soft)} 行未完成回读，见上表）"
    L += ["", "## 结论", "", verdict]
    out = guard_out(args.out or os.path.join(product_root(), f"抽样回读_{args.case_name}.md"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    if not bad and not soft:
        msg = "全部可反查并回原页核到"
    elif not bad:
        msg = f"全部可反查；回读完成度 {done}/{len(picks)}"
    else:
        msg = f"断链 {bad}"
    print(f"[{args.case_name}] 抽样 {len(picks)} 个数字 · {msg}（详见 {out}）")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
