#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑 D3：三份报告 → 叙述层 + 标量层 + 机构结论层。

用法:
    python3 run_narrative.py             # 处理全部年份并打印
    python3 run_narrative.py --quiet     # 只落盘
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checkup import intake  # noqa: E402
from checkup.narrative import extract_dept_summaries, extract_report  # noqa: E402
from checkup.pipeline import require_routed_template  # noqa: E402
from checkup.templates import default_registry  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
from devpaths import guard_out, default_out_dir  # noqa: E402

#: 体格检查里驱动组合层的关键项（框架 D12b 去手工字典要用的）
KEY_SCALARS = ["身高", "体重", "体重指数", "收缩压", "舒张压", "腰围", "臀围", "脉搏"]



from checkup.console import init_console  # noqa: E402
init_console()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
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

    all_n, all_o, all_f, all_d = [], [], [], []
    registry = default_registry(BASE)
    for pdf in intake.list_pdfs(src):
        year = intake.require_year(os.path.basename(pdf))
        tpl = require_routed_template(pdf, registry)
        meta, narratives, observations, findings = extract_report(pdf, year, tpl)
        dept = extract_dept_summaries(pdf, year, tpl)
        all_n.extend(narratives)
        all_o.extend(observations)
        all_f.extend(findings)
        all_d.extend(dept)

        with open(os.path.join(out, f"report_{year}.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"document": meta.__dict__,
                       "narratives": [i.to_dict() for i in narratives],
                       "observations": [o.__dict__ for o in observations],
                       "institution_findings": [v.__dict__ for v in findings],
                       "dept_summaries": [d.__dict__ for d in dept]},
                      f, ensure_ascii=False, indent=2)

        if not args.quiet:
            print("=" * 104)
            print(f"【{year}】{meta.doc_id}")
            print(f"  机构 {meta.org_id} · 体检日期 {meta.exam_date}（取自内页）"
                  f" · {meta.page_count} 页")
            print(f"  叙述项 {len(narratives)} · 标量读数 {len(observations)}"
                  f" · 机构异常项 {len(findings)} · 分科小结 {len(dept)} 段")
            print("-" * 104)
            print("  [通道 B · 叙述]")
            for it in narratives:
                meas = " ".join(
                    f"[{m.side or '—'}{('/' + m.descriptor) if m.descriptor else ''}"
                    f" {m.raw}]" for m in it.measurements) or "—"
                grade = f" {it.grade_system} {it.grade_value}" if it.grade_system else ""
                print(f"    p{it.page_no:<3} {str(it.modality or '—'):<6}"
                      f"{it.item:<12}{meas}{grade}")
            print("  [通道 A · 标量（关键项）]")
            for o in observations:
                if o.item_raw in KEY_SCALARS:
                    print(f"    p{o.page_no:<3} {str(o.section or '—'):<6}"
                          f"{o.item_raw:<10}{o.value_text!r}")
            print("  [机构结论]")
            for v in findings:
                print(f"    p{v.page_no:<3} {v.section} {v.no}.{v.title}")
            print()

    with open(os.path.join(out, "narrative_all.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["年份", "页", "检查方式", "检查项目", "侧别", "描述词",
                    "测量原文", "数值", "单位", "分级体系", "分级", "小结", "描述原文"])
        for it in all_n:
            if not it.measurements:
                w.writerow([it.year, it.page_no, it.modality or "", it.item, "", "",
                            "", "", "", it.grade_system or "", it.grade_value or "",
                            " / ".join(it.summary_lines), it.description_verbatim])
            for m in it.measurements:
                w.writerow([it.year, it.page_no, it.modality or "", it.item,
                            m.side or "", m.descriptor or "", m.raw, m.value, m.unit,
                            it.grade_system or "", it.grade_value or "",
                            " / ".join(it.summary_lines), it.description_verbatim])

    with open(os.path.join(out, "observations_all.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["年份", "页", "段落", "项目", "值原文", "数值", "单位", "定性结论"])
        for o in all_o:
            w.writerow([o.year, o.page_no, o.section or "", o.item_raw,
                        o.value_text, o.value, o.unit or "", o.qualifier or ""])

    with open(os.path.join(out, "institution_findings.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["年份", "页", "分类", "序号", "标题", "说明与建议"])
        for v in all_f:
            w.writerow([v.year, v.page_no, v.section, v.no, v.title, v.detail])

    with open(os.path.join(out, "dept_summaries.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["年份", "页", "科室", "行序", "小结原文"])
        for d in all_d:
            for k, line in enumerate(d.lines, start=1):
                w.writerow([d.year, d.page_no, d.section, k, line])

    print("=" * 104)
    print(f"叙述项 {len(all_n)} · 标量读数 {len(all_o)} · 机构异常项 {len(all_f)}"
          f" · 分科小结 {len(all_d)} 段")
    print(f"已写入 {out}/：report_<年>.json · narrative_all.csv · "
          f"observations_all.csv · institution_findings.csv · dept_summaries.csv")


if __name__ == "__main__":
    main()
