#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给新机构起草归一映射规则。

    python3 draft_catalog.py <新机构报告.pdf> --org YY-002

流程：用模板（必要时先跑 ``draft_template.py``）抽出化验项 → 挑出未映射的
→ 起草映射规则 → 落到 ``catalog/drafts/<org>-rules.json``。

**产出是草案。** 自动合并会把不同机构的同名不同物悄悄并成一条，
所以文件里写死 ``confirmed: false``，必须人工确认。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fitz  # noqa: E402

from checkup.autotemplate import draft_template  # noqa: E402
from checkup.catalog_draft import propose, save_draft, unmapped_items  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.tables import extract_lab_results  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
DRAFTS = os.path.join(BASE, "catalog", "drafts")


from checkup.console import init_console  # noqa: E402
init_console()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--org", required=True)
    ap.add_argument("--catalog", default=CATALOG)
    ap.add_argument("--out", default=None,
                    help="草案输出目录（缺省用户产物目录；显式指向包内 catalog/drafts "
                         "时才写包内——草案属运行期产物，原则上不进包）")
    args = ap.parse_args()

    cat = AnalyteCatalog.load(args.catalog)
    paths: list[str] = []
    for p in args.pdfs:
        paths.extend(sorted(glob.glob(p)) or [p])

    # 没有已登记模板时，**现场起草一个**——否则新机构第一份报告连
    # "抽出来看看有哪些项目"都做不到，词表草案无从谈起。
    fallback = None
    if all(route_document(p).template is None for p in paths):
        print("⚠ 没有已登记模板 → 现场自动起草一份（仅用于本次词表草案）")
        fallback = draft_template(paths, org_id=args.org).template
        print(f"  草案：栏数 {fallback.lab.column_groups}，"
              f"section_x_max {fallback.lab.section_x_max}")

    obs: list[NormalizedObservation] = []
    for p in paths:
        r = route_document(p)
        tpl = r.template or fallback
        if tpl is None:
            print(f"⚠ {os.path.basename(p)}: {r.reason}")
            continue
        doc = fitz.open(p)
        try:
            for lab in extract_lab_results(doc, "0000", tpl):
                res = cat.resolve(args.org, lab.section, lab.item_raw,
                                  lab.value_type, lab.unit_raw)
                obs.append(NormalizedObservation(
                    year="0000", page_no=lab.page_no, section=lab.section,
                    item_raw=lab.item_raw, occurrence=lab.occurrence,
                    analyte_id=res.analyte_id, value_type=lab.value_type,
                    unit=lab.unit_raw, value=lab.value, ref_low=lab.ref_low,
                    ref_high=lab.ref_high))
        finally:
            doc.close()

    items = unmapped_items(obs)
    print(f"观测 {len(obs)} 条 · 未映射 {len(items)} 项")
    if not items:
        print("✓ 全部已映射，无需起草规则")
        return

    props = propose(items, cat, args.org)
    print()
    print("=" * 92)
    print("映射规则草案（人工确认后才可并入 catalog）")
    print("=" * 92)
    for pr in props:
        target = pr.analyte_id or f"[新建] {pr.new_analyte['name']}"
        flag = "⚠" if pr.confidence < 0.8 else " "
        print(f"{flag} {pr.section}/{pr.source_name}  →  {target}   "
              f"置信度 {pr.confidence}")
        for r in pr.reasons:
            print(f"      · {r}")

    low = [p for p in props if p.confidence < 0.8]
    print()
    print(f"合计 {len(props)} 条：高置信 {len(props) - len(low)} · "
          f"**待人工确认 {len(low)}**")
    from devpaths import default_out_dir, guard_out  # noqa: PLC0415
    out_dir = guard_out(args.out or os.path.join(default_out_dir(), "catalog-drafts"))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{args.org}-rules.json")
    save_draft(props, out)
    print(f"草案已写入 {out}")
    print("⚠ confirmed=false。绝不自动合并——不同机构的同名不同物必须先看再并。")


if __name__ == "__main__":
    main()
