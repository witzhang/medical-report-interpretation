#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 PDF 自动起草版面模板。

拿到一家新机构的报告时，先跑这个：

    python3 draft_template.py <报告.pdf> [更多.pdf ...] \\
        --org YY-002 --name "XX市第一人民医院"

产出 ``catalog/templates/<org>.json``（**confirmed=False 的草案**）与一份
推断依据清单。人工确认几何假设后再投入使用。

**草案不能直接用。** 自动起草错了会静默抽错数据，所以每一项推断都记了
"从哪来"（``evidence``），请逐条核对。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checkup.autotemplate import draft_template  # noqa: E402
from checkup.tables import extract_lab_results, lab_page_audit  # noqa: E402
from checkup.validate import check_extraction_audit  # noqa: E402

import fitz  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "catalog", "templates")


from checkup.console import init_console  # noqa: E402
init_console()

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+", help="同机构的一至多份报告")
    ap.add_argument("--org", default="AUTO-001", help="机构 ID")
    ap.add_argument("--name", default="", help="机构名称")
    ap.add_argument("--out", default=None,
                    help="草案输出目录（缺省用户产物目录 templates-draft/；"
                         "**验收通过后**再由 accept_template.py --install 落盘进包）")
    ap.add_argument("--verify", action="store_true",
                    help="用草案回抽一遍，报告抽到多少项")
    args = ap.parse_args()

    paths: list[str] = []
    for p in args.pdfs:
        paths.extend(sorted(glob.glob(p)) or [p])
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"找不到: {missing}")

    d = draft_template(paths, org_id=args.org, org_name=args.name)
    print("=" * 88)
    print("推断依据（逐条核对）")
    print("=" * 88)
    print(d.render())

    print()
    print("=" * 88)
    print("草案摘要")
    print("=" * 88)
    t = d.template
    print(f"  page_marker      {t.lab.page_marker!r}")
    print(f"  header_cells     {t.lab.header_cells}")
    print(f"  column_groups    {t.lab.column_groups}")
    print(f"  section_x_max    {t.lab.section_x_max}")
    print(f"  anchor_strings   {t.header_anchor_strings}")
    print(f"  noise_labels     {t.narrative.noise_labels[:8]}…")

    if args.verify:
        print()
        print("=" * 88)
        print("回抽验证")
        print("=" * 88)
        for p in paths:
            doc = fitz.open(p)
            try:
                n = len(extract_lab_results(doc, "0000", t))
                audit = check_extraction_audit(lab_page_audit(doc, t))
                print(f"  {os.path.basename(p)}: 抽出 {n} 项 · 审计 {audit.level}"
                      f" —— {audit.detail}")
            finally:
                doc.close()

    from devpaths import default_out_dir, guard_out  # noqa: PLC0415
    out_dir = guard_out(args.out or os.path.join(default_out_dir(), "templates-draft"))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{args.org}.json")
    t.save(out)
    print()
    print(f"草案已写入 {out}")
    print("⚠ confirmed=False。**先人工核对上面的几何假设，再投入使用。**")
    print("  推荐路径：python3 engine/tools/accept_template.py --pdfs <样本…> "
          "--template " + out + " --install（验收链全绿才落盘进包）")


if __name__ == "__main__":
    main()
