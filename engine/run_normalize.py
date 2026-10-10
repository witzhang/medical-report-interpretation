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
