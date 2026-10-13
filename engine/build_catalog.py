#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成规范指标目录（D6 的种子）。

这是「新机构冷启动」那一步的自动化版本：把三年观测里出现过的
(机构, 段落, 项目, 值类型) 全部登记成 catalog 条目，标 ``confirmed: false``，
并**自动记录**两类必须人工介入的情况：

* **歧义键**——同一 (段, 项目) 对应多个值类型（尿液检查里两项都叫「白细胞」）
  → 不放进种子表，改出显式 MatchRule
* **单位跨年不一致**——同一条目在不同年份单位不同 → 记入 conflicts 报告，
  不静默择一

用法:
    python3 build_catalog.py
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz  # noqa: E402

from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import (SECTION_PREFIX, MatchRule, RepairRule,  # noqa: E402
                               AnalyteCatalog, normalize_name)
from checkup.schema import AnalyteDefinition  # noqa: E402
from checkup.pipeline import require_routed_template, year_from_name  # noqa: E402
from checkup.tables import extract_lab_results  # noqa: E402
from checkup.templates import default_registry  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
from devpaths import FIXTURES, guard_out, default_out_dir  # noqa: E402

SRC = os.environ.get("CHECKUP_SRC") or FIXTURES.real_reports
CATALOG = os.path.join(BASE, "catalog", "analytes.json")

from checkup.console import init_console  # noqa: E402
init_console()

def _pdfs(src: str) -> dict[str, str]:
    """报告目录 → {年份: 文件名}。**不写死文件名**：按目录内容探测。

    年份键只用于本工具的"按年统计"（引擎判定层的年份取自内页）。
    年份从文件名取 4 位数字串（与引擎侧 ``pipeline.year_from_name`` 同一口径，
    不要求数字开头——``【姓名】体检报告2024.pdf`` 也要能认出来）。
    """
    out: dict[str, str] = {}
    if not os.path.isdir(src):
        raise SystemExit(f"✗ 报告目录不存在：{src}\n"
                         f"  用 --src / CHECKUP_SRC / CHECKUP_FIXTURES 指定报告目录。")
    for fn in sorted(os.listdir(src)):
        if fn.lower().endswith(".pdf"):
            y = year_from_name(fn)
            if y:
                out[y] = fn
    if not out:
        raise SystemExit(f"✗ 报告目录里没有可用的 PDF：{src}\n"
                         f"  文件名需含 4 位年份（如「2024 体检报告.pdf」）。")
    return out


def collect(src: str = ""):
    """报告目录 → 全部观测的 (段, 项目, 值类型, 单位) 统计 + 机构 id。"""
    src = src or SRC
    units: dict[tuple, Counter] = defaultdict(Counter)
    registry = default_registry(BASE)
    orgs: list[str] = []
    for year, fn in _pdfs(src).items():
        path = os.path.join(src, fn)
        tpl = require_routed_template(path, registry)
        if tpl.org_id not in orgs:
            orgs.append(tpl.org_id)
        doc = fitz.open(path)
        for r in extract_lab_results(doc, year, tpl):
            units[(r.section, r.item_raw, r.value_type)][r.unit_raw] += 1
        _, narratives, observations, _ = extract_report(path, year, tpl)
        for it in narratives:
            units[(it.modality or "影像", it.item, "narrative")][""] += 1
        for o in observations:
            vt = "numeric" if o.value is not None else "qualitative"
            units[(o.section or "体格", o.item_raw, vt)][o.unit or ""] += 1
        doc.close()
    global ORG
    ORG = orgs[0] if len(orgs) == 1 else "MULTI"
    return units


#: 机构 id（由 collect() 从路由到的模板推导；不写死）
ORG = ""


def main() -> None:
    ap = argparse.ArgumentParser(
        description="项目名目录种子（需数据根）：从报告收集 (段,项目,值类型,单位) 统计。"
                    "默认**只写用户产物目录**；写回包内需显式 --install。")
    ap.add_argument("--src", default=None,
                    help="报告目录（缺省 CHECKUP_SRC / 数据根的报告目录）")
    ap.add_argument("--out", default=None, help="产物目录（缺省用户产物目录）")
    ap.add_argument("--install", action="store_true",
                    help="把重建的目录写回**包内** catalog/analytes.json（默认不写！"
                         "该文件是随包数据，误覆盖会抹掉已登记的新机构规则）")
    args = ap.parse_args()
    out_dir = guard_out(args.out or default_out_dir())
    os.makedirs(out_dir, exist_ok=True)
    units = collect(args.src or "")

    analytes, rules, conflicts = [], [], []
    by_key: dict[tuple[str, str], set[str]] = defaultdict(set)

    for (section, name, vt), counter in sorted(units.items()):
        prefix = SECTION_PREFIX.get(section, "ETC")
        aid = f"{prefix}.{name}"
        if vt == "qualitative":
            aid += ".qual"
        elif vt == "narrative":
            aid += ".narr"
        canon, n = counter.most_common(1)[0]
        by_key[(section, name)].add(aid)

        if len(counter) > 1:
            conflicts.append({
                "段落": section, "项目": name, "值类型": vt,
                "各年单位": " / ".join(f"{u or '(空)'}×{c}" for u, c in counter.most_common()),
                "采用": canon or "(空)", "出现年份数": n,
            })

        analytes.append(AnalyteDefinition(
            analyte_id=aid, name=name, specimen="", value_type=vt,
            canonical_unit=canon, loinc_code=None, aliases=[],
            org_id=ORG, section=section, confirmed=False))

    # 歧义键 → 显式规则（种子表会自动剔除这些键）
    for (section, name), ids in sorted(by_key.items()):
        if len(ids) < 2:
            continue
        for aid in sorted(ids):
            vt = "qualitative" if aid.endswith(".qual") else (
                "narrative" if aid.endswith(".narr") else "numeric")
            rules.append(MatchRule(
                analyte_id=aid, org_id=ORG, section=section, source_name=name,
                value_type=vt,
                note="同名撞键：同段内同名但值类型不同，靠 value_type 区分"))

    # 已知的标签/单位错位：2024 把 RDW-CV 与 RDW-SD 的标签写反了，
    # 而单位列是对的（40.30 配 fL、13.20 配 ％）。按名字比会得出"下降 68%"。
    repairs = [
        RepairRule(
            rule_id="NX-001.RDW.SWAP",
            section="血常规",
            kind="swap_by_unit",
            members=["BLD.红细胞分布宽度CV", "BLD.红细胞分布宽度SD"],
            note="2024 报告两个标签写反；单位列正确，按单位重归属"),
    ]

    cat = AnalyteCatalog(analytes=analytes, rules=rules, repairs=repairs,
                         version="2026.10.01")
    # ⚠️ 默认**不写包内**：catalog/analytes.json 是随包数据（含已登记的新机构规则），
    # 一次误覆盖就会静默抹掉它们（事故：审计工具用 --help 试跑本脚本，把
    # SYN-002 的 20 条映射规则覆写没了）。要写回必须显式 --install。
    dst = CATALOG if args.install else os.path.join(out_dir, "analytes.json")
    cat.save(dst)

    with open(os.path.join(out_dir, "unit_conflicts.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["段落", "项目", "值类型", "各年单位",
                                          "采用", "出现年份数"])
        w.writeheader()
        w.writerows(conflicts)

    print(f"目录已写入 {dst}")
    if not args.install:
        print("（未加 --install：**包内 catalog/analytes.json 未被改动**；"
              "如确认要替换包内目录，请 diff 后加 --install）")
    print(f"  指标 {len(analytes)} 条 · 映射规则 {len(rules)} 条 · 修复规则 {len(repairs)} 条")
    print(f"  自动剔除的歧义键 {len(cat.ambiguous)} 个")
    for k in sorted(cat.ambiguous):
        print(f"    {k}")
    print(f"\n单位跨年不一致 {len(conflicts)} 项 → {out_dir}/unit_conflicts.csv")
    for c in conflicts:
        print(f"  {c['段落']}/{c['项目']}: {c['各年单位']}  → 采用 {c['采用']}")


if __name__ == "__main__":
    main()
