#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 CVi 表（个体生物学变异），供 L4 变化轴判定使用。

数据来自 EFLM Biological Variation Database 的合并估计值，经旧管线
``体检分析/ref/cvi_table.csv`` 落盘（该表带 EFLM 指标名与研究数，可回溯）。

**只挂到血液类段落**（血常规 / 生化检查 / 免疫检查）。这是旧管线踩过的坑：
把尿液沉渣的白细胞计数套用了血液白细胞的 CVi，得出「超出噪声」的错误结论。
本引擎里尿液与血液的 analyte_id 本就不同，这里再加一道段落白名单。

用法:
    python3 build_cvi.py
"""
from __future__ import annotations

import csv
import json
import os
import argparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checkup.normalize import AnalyteCatalog  # noqa: E402
from devpaths import default_out_dir, guard_out  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
LEGACY = os.path.join(os.path.dirname(BASE), "体检分析", "ref", "cvi_table.csv")
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
OUT = os.path.join(BASE, "catalog", "cvi.json")

#: 只允许血液类段落参与 CVi 判定（坑 8 的护栏）
ALLOWED_SECTIONS = {"血常规", "生化检查", "免疫检查"}


from checkup.console import init_console  # noqa: E402
init_console()

def main() -> None:
    ap = argparse.ArgumentParser(
        description="由旧管线 CVi 表生成 catalog/cvi.json。"
                    "默认**只写用户产物目录**；写回包内需显式 --install。")
    ap.add_argument("--out", default=None, help="产物目录（缺省用户产物目录）")
    ap.add_argument("--install", action="store_true",
                    help="写回**包内** catalog/cvi.json（默认不写）")
    args = ap.parse_args()
    out_path = OUT if args.install else os.path.join(
        guard_out(args.out or default_out_dir()), "cvi.json")
    cat = AnalyteCatalog.load(CATALOG)
    by_name: dict[tuple[str, str], tuple] = {}
    if not os.path.exists(LEGACY):
        print(f"✗ 缺少旧管线金标准：{LEGACY}\n"
              f"  处置：设 CHECKUP_FIXTURES 指向数据根（本工具是作者开发工具，需数据根）")
        raise SystemExit(2)

    with open(LEGACY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r["CVi(%)"]:
                continue
            lo, hi = (r["CVi参考范围"].split("-") + [""])[:2] if r["CVi参考范围"] else ("", "")
            by_name[(r["中文项目"], "")] = (
                float(r["CVi(%)"]), lo, hi,
                int(r["纳入研究数"]) if r["纳入研究数"] else None,
                r["EFLM指标"], r["EFLM_ID"], r["矩阵"])

    out, missing = {}, []
    for a in cat.analytes.values():
        if a.section not in ALLOWED_SECTIONS or a.value_type != "numeric":
            continue
        row = by_name.get((a.name, ""))
        if not row:
            missing.append(f"{a.section}/{a.name}")
            continue
        cvi, lo, hi, n, eflm, eid, matrix = row
        out[a.analyte_id] = {
            "cvi": cvi,
            "cvi_low": float(lo) if lo else None,
            "cvi_high": float(hi) if hi else None,
            "n_studies": n,
            "eflm_analyte": eflm,
            "eflm_id": int(eid) if eid else None,
            "matrix": matrix,
            "source": "EFLM Biological Variation Database（合并估计值 median）",
        }

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"version": "2026.10.01", "source_file": "体检分析/ref/cvi_table.csv",
                   "cva_assumed": 3.0, "entries": out},
                  f, ensure_ascii=False, indent=2)

    print(f"CVi 表已写入 {out_path}")
    if not args.install:
        print("（未加 --install：**包内 catalog/cvi.json 未被改动**）")
    print(f"  覆盖 {len(out)} 个血液类指标")
    print(f"  CVa 为假设值 {3.0}%（报告不提供仪器精密度，必须显式声明）")
    if missing:
        print(f"\n无 CVi 的血液类指标 {len(missing)} 个（不做变化轴判定）：")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
