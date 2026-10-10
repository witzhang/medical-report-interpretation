#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 0 · 摄入预检：**先报事实与出路，再决定要不要往下走**。

    python3 scripts/scan_archive.py <报告目录> [--allow-degraded] [--json]

对目录里的**每一份文件**给出：

* **类型判定**（`checkup/intake.py`）：文字层 PDF / 疑似 OCR 叠层 / 扫描件 /
  加密 PDF / OFD / Word / Excel / 图片 / 未知；
* **能不能用 + 为什么 + 下一步**（"不支持"必须带可执行的出路，不是一句报错）；
* 可读的 PDF 再补：内页体检日期（⛔ 不用文件名）、文件名与内页不一致告警、
  模板路由结果、化验页抽取审计（判为化验页却 0 项 = 致命）。

退出码：0 = 都可用；1 = 有文件不可用（**停下，不要往下写报告**）；2 = 用法错误。
加 `--allow-degraded`：接受"疑似 OCR 叠层"（数据质量将标降级）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ENGINE = os.path.join(SKILL, "engine")
sys.path.insert(0, ENGINE)

import fitz  # noqa: E402

from checkup import intake  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402
from checkup.tables import lab_page_audit  # noqa: E402
from checkup.templates import default_registry  # noqa: E402


from checkup.console import init_console  # noqa: E402
init_console()

def _inner_date(text: str):
    m = re.search(r"(?:体检日期|检查日期|报告日期|采样日期)[:：]?\s*"
                  r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _pdf_details(path: str, registry) -> tuple[list[str], list[str]]:
    """可读 PDF 的补充检查 → (信息行, 问题)。"""
    info: list[str] = []
    problems: list[str] = []
    fn = os.path.basename(path)
    doc = fitz.open(path)
    try:
        pages = doc.page_count
        text = "\n".join(doc[i].get_text() for i in range(pages))
        date = _inner_date(text)
        fm = re.search(r"(\d{8})", fn)
        fn_date = (f"{fm.group(1)[:4]}-{fm.group(1)[4:6]}-{fm.group(1)[6:]}"
                   if fm else None)
        line = f"    页数 {pages} · 内页日期 {date or '未读到'}"
        if fn_date and date and fn_date != date:
            line += f"  ⚠ 文件名日期 {fn_date} ≠ 内页（一律采信内页）"
        info.append(line)
        res = route_document(path, registry)
        org = res.template.org_id if res.template else "UNKNOWN_LAYOUT"
        info.append(f"    机构路由 → {org}（{res.score:.2f}：{res.reason}）")
        if res.template is None:
            problems.append(
                f"{fn}：版式未注册（**不是格式问题**）→ 用 "
                f"`engine/tools/accept_template.py --pdfs {fn} --org NEW-001` "
                f"一键起草+验收；⛔ 不要硬跑")
        elif date is None:
            problems.append(f"{fn}：内页读不到体检日期 → 要求人工补，⛔ 不用导入日期代替")
        if res.template is not None:
            try:
                audit = lab_page_audit(doc, res.template)
                items = sum(a["items"] for a in audit)
                info.append(f"    化验页 {len(audit)} 页 · 抽到 {items} 项")
                if audit and items == 0:
                    problems.append(f"{fn}：判为化验页却 0 项（抽取审计 fatal）")
            except Exception as e:  # noqa: BLE001
                info.append(f"    ⚠ 化验页审计失败：{type(e).__name__}: {e}")
    finally:
        doc.close()
    return info, problems


def scan(src: str, registry, allow_degraded: bool = False) -> tuple[dict, int]:
    verdicts = intake.scan_dir(src)
    if not verdicts:
        print(f"✗ 目录里没有可检查的文件：{src}")
        return {}, 2
    summary = intake.summarize(verdicts)
    print(f"目录：{src}（{summary['total']} 份文件）")
    print("=" * 88)
    problems: list[str] = []
    for v in verdicts:
        fn = os.path.basename(v.path)
        if v.ignored:
            print(f"· 忽略 · {fn}（{v.reason}）")
            continue
        if not v.usable:
            mark = "✗ 不可用"
        elif v.degraded:
            mark = "⚠ 降级（疑似 OCR 叠层）"
        else:
            mark = "✓ 可用"
        print(f"\n{mark} · {fn} —— {v.label}")
        print(f"    原因：{v.reason}")
        if v.next_step:
            print(f"    下一步：{v.next_step}")
        if v.usable:
            if v.degraded and not allow_degraded:
                problems.append(
                    f"{fn}：疑似 OCR 叠层（数字可能来自图像识别）→ 建议换电子原件；"
                    f"确要跑加 --allow-degraded（产物须标注『数据质量：降级』）")
                continue
            info, probs = _pdf_details(v.path, registry)
            for line in info:
                print(line)
            problems.extend(probs)
            if v.degraded:
                print("    （已按 --allow-degraded 继续：产物须标注『数据质量：降级』）")
    print()
    print("=" * 88)
    if problems:
        print("✗ 有需要先处理的问题（**停下，不要往下写报告**）：")
        for p in problems:
            print("   ·", p)
        return summary, 1
    print(f"✓ 全部可用（{summary['usable']} 份文字层 PDF"
          + (f" + {summary['degraded']} 份降级" if summary["degraded"] else "")
          + "）：继续跑引擎三件套 → gen_artifacts → 写报告 → reconcile")
    return summary, 0


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段0 摄入预检（只读，不产出底稿）")
    ap.add_argument("src", help="报告目录")
    ap.add_argument("--engine", default=ENGINE, help="引擎目录（默认 skill/engine）")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="接受『疑似 OCR 叠层』的输入（数据质量将标降级）")
    ap.add_argument("--json", action="store_true",
                    help="额外输出机器可读汇总（stdout 末尾一行 JSON）")
    args = ap.parse_args()
    if not os.path.isdir(args.src):
        print(f"✗ 目录不存在：{args.src}")
        return 2
    reg = default_registry(args.engine)
    summary, code = scan(args.src, reg, allow_degraded=args.allow_degraded)
    if args.json and summary:
        print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
