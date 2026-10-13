#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑 L2 归一：全部观测 → 规范指标 ID。

用法:
    python3 run_normalize.py
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz  # noqa: E402

from checkup import intake  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import require_routed_template  # noqa: E402
from checkup.tables import extract_lab_results  # noqa: E402
from checkup.templates import default_registry  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
from devpaths import guard_out, default_out_dir  # noqa: E402

CATALOG = os.path.join(BASE, "catalog", "analytes.json")


from checkup.console import init_console  # noqa: E402
init_console()


def write_cross_org_audit(out: str, hits: list[dict]) -> str:
    """把**跨机构种子命中**写成独立审计文件（B5）；无命中 → 不写、返回空串。

    为什么单独一个文件：``normalized_observations.csv`` 是既有产物，
    "命中来源"这件事不该让它变样（回归要求：产物不得变化）。审计单独成册，
    既不动产物，又让"这个项目凭什么映射上了"有据可查。
    """
    if not hits:
        return ""
    import json  # noqa: PLC0415
    audit = os.path.join(out, "audit_seed_cross_org.json")
    with open(audit, "w", encoding="utf-8") as f:
        json.dump(hits, f, ensure_ascii=False, indent=2)
    return audit


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None,
                    help="产物目录（缺省写数据根/用户工作目录；与 run_pipeline --out 用同一个）")
    ap.add_argument("--src", default=None,
                    help="报告目录（含 PDF）；缺省用 CHECKUP_SRC 或数据根的报告目录")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="接受『疑似 OCR 叠层』的输入（数据质量将标降级）")
    args = ap.parse_args()
    src = intake.pick_src(args.src)
    intake.preflight_or_exit(src, args.allow_degraded)
    out = guard_out(args.out or default_out_dir())
    os.makedirs(out, exist_ok=True)
    cat = AnalyteCatalog.load(CATALOG)
    print(f"目录 {os.path.basename(CATALOG)} 版本 {cat.version} · "
          f"{len(cat.analytes)} 个指标 · {len(cat.rules)} 条映射规则 · "
          f"{len(cat.repairs)} 条修复规则\n")

    rows, stats = [], Counter()
    cross_org: list[dict] = []          # 跨机构种子命中（B5：可审计，不改行为）
    registry = default_registry(BASE)
    for pdf in intake.list_pdfs(src):
        year = intake.require_year(os.path.basename(pdf))
        tpl = require_routed_template(pdf, registry)
        doc = fitz.open(pdf)

        items = []
        for r in extract_lab_results(doc, year, tpl):
            items.append(("化验", r.section, r.item_raw, r.value_type,
                          r.result_raw, r.value, r.unit_raw, r.ref_raw,
                          getattr(r, "page_no", "")))
        meta, narratives, observations, findings = extract_report(pdf, year, tpl)
        doc.close()
        for it in narratives:
            items.append(("叙述", it.modality or "影像", it.item, "narrative",
                          it.description_verbatim[:40], None, "", "",
                          getattr(it, "page_no", "")))
        for o in observations:
            vt = "numeric" if o.value is not None else "qualitative"
            items.append(("标量", o.section or "体格", o.item_raw, vt,
                          o.value_text, o.value, o.unit or "", "",
                          getattr(o, "page_no", "")))

        for src, section, name, vt, text, value, unit, ref, page in items:
            res = cat.resolve(tpl.org_id, section, name, vt, unit)
            # 名字与单位矛盾时，按显式修复规则重归属（可审计，非隐式启发式）
            if res.unit_status == "mismatch":
                stats["mismatch_raw"] += 1
                rep = cat.repair_by_unit(section, res.analyte_id, unit)
                if rep:
                    res = rep
            stats[f"by_{res.matched_by}"] += 1
            stats[f"unit_{res.unit_status}"] += 1
            if res.repaired_by:
                stats["repaired"] += 1
            if res.cross_org:
                # 命中来自别家机构登记的指标名（seed 键不含 org 维度）→
                # **行为不变**，但记进独立审计文件（B5）
                stats["seed_cross_org"] += 1
                cross_org.append({
                    "机构": tpl.org_id, "年份": year, "来源": src,
                    "段落": section, "项目原文": name,
                    "analyte_id": res.analyte_id,
                    "登记机构": cat.analytes[res.analyte_id].org_id
                    if res.analyte_id in cat.analytes else "",
                    "说明": "种子键是 (段, 名称)，不含 org → 跨机构命中；"
                            "命中结果与同机构命中同等对待（刻意保持行为）",
                })
            rows.append({
                "年份": year, "来源": src, "段落": section, "项目原文": name,
                "analyte_id": res.analyte_id, "匹配方式": res.matched_by,
                "值原文": text, "数值": value if value is not None else "",
                "单位": unit, "单位状态": res.unit_status,
                "修复规则": res.repaired_by, "参考区间": ref,
                "备注": res.note, "页": page,
            })

    with open(os.path.join(out, "normalized_observations.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    print(f"观测合计 {total} 条")
    print(f"  命中方式: 规则 {stats['by_rule']} · 种子 {stats['by_seed']} · "
          f"未映射 {stats['by_unmapped']}")
    print(f"  单位状态: 一致 {stats['unit_ok']} · 无规范单位 {stats['unit_unknown']}")
    print(f"  名字与单位矛盾 {stats['mismatch_raw']} 条 → "
          f"按显式修复规则重归属 {stats['repaired']} 条（可审计）")
    if cross_org:
        audit = write_cross_org_audit(out, cross_org)
        print(f"  跨机构种子命中 {len(cross_org)} 条（seed 键不含 org 维度 → "
              f"行为不变、可审计）：{audit}")
        for c in cross_org[:6]:
            print(f"    {c['机构']} {c['段落']}/{c['项目原文']} → {c['analyte_id']}"
                  f"（登记机构 {c['登记机构']}）")

    unmapped = sorted({(r["段落"], r["项目原文"]) for r in rows
                       if r["analyte_id"] == UNMAPPED})
    if unmapped:
        print(f"\n未映射 {len(unmapped)} 项（**保留原文，不猜**）：")
        for s, n in unmapped:
            print(f"  {s}/{n}")

    mism = [r for r in rows if r["单位状态"] == "mismatch"]
    if mism:
        print(f"\n单位不一致 {len(mism)} 条（**已拦下，不参与跨年比较**）：")
        for r in mism[:10]:
            print(f"  {r['年份']} {r['段落']}/{r['项目原文']} → {r['analyte_id']} "
                  f"单位={r['单位']!r} | {r['备注']}")

    print(f"\n已写入 {out}/normalized_observations.csv")


if __name__ == "__main__":
    main()
