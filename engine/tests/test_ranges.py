#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D8 分层参考区间的回归测试。    python3 tests/test_ranges.py

设计借鉴 health-assistant 的 ``biomarker_reference_ranges``（Apache-2.0）。
它踩过的坑与我们**完全同源**，其模块原话：

    全局单一区间会让「60 岁女性的正常范围对 25 岁男性不正常」。

两条硬语义必须守住：**最具体者胜**、**未知维度不算命中**。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.ranges import RangeTable, ReferenceRange, flag_of  # noqa: E402
from checkup.schema import Finding, NormalizedObservation  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
SRC = PATHS.real_reports
AID = "CHEM.高密度脂蛋白胆固醇"
_obs = None


def obs():
    global _obs
    if _obs is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        _obs = [o for rows in per.values() for o in rows]
    return _obs


def _tbl(*rs) -> RangeTable:
    t = RangeTable()
    for r in rs:
        t.add(AID, r)
    return t


# --------------------------------------------------------------- 分层命中

def test_sex_specific_range_is_picked():
    t = _tbl(ReferenceRange(low=1.0, unit="mmol/L", sex="男", source="A"),
             ReferenceRange(low=1.3, unit="mmol/L", sex="女", source="A"))
    assert t.resolve(AID, "男", 40, "mmol/L").low == 1.0
    assert t.resolve(AID, "女", 40, "mmol/L").low == 1.3


def test_age_window_is_picked():
    t = _tbl(ReferenceRange(low=0.9, unit="mmol/L", age_min=18, age_max=44),
             ReferenceRange(low=1.1, unit="mmol/L", age_min=45, age_max=99))
    assert t.resolve(AID, "男", 30, "mmol/L").low == 0.9
    assert t.resolve(AID, "男", 60, "mmol/L").low == 1.1


def test_most_specific_wins():
    """最具体者胜：性别 + 单位 > 仅单位。"""
    t = _tbl(ReferenceRange(low=1.0, unit="mmol/L", source="通用"),
             ReferenceRange(low=1.2, unit="mmol/L", sex="男", source="男性"))
    r = t.resolve(AID, "男", 40, "mmol/L")
    assert r.low == 1.2 and r.source == "男性"
    assert "性别=男" in r.matched_by


# --------------------------------------------------- 未知维度不算命中

def test_unknown_sex_does_not_satisfy_sex_constrained_rows():
    """**性别未知 → 性别专属行不匹配**，回落到更粗的通用行。

    这就是「缺失 ≠ 阴性」下沉到数据层：不能假定未知性别落在任何一侧。
    """
    t = _tbl(ReferenceRange(low=1.0, unit="mmol/L", sex="男"),
             ReferenceRange(low=1.4, unit="mmol/L", sex="女"),
             ReferenceRange(low=0.8, unit="mmol/L", source="通用"))
    r = t.resolve(AID, None, 40, "mmol/L")
    assert r.low == 0.8, r
    assert r.origin == "stratified" and r.source == "通用"


def test_unknown_age_does_not_satisfy_age_constrained_rows():
    """年龄未知 → 年龄段专属行不匹配。"""
    t = _tbl(ReferenceRange(low=0.9, unit="mmol/L", age_min=18, age_max=44),
             ReferenceRange(low=0.7, unit="mmol/L", source="通用"))
    assert t.resolve(AID, "男", None, "mmol/L").low == 0.7


def test_age_outside_window_does_not_match():
    t = _tbl(ReferenceRange(low=0.9, unit="mmol/L", age_min=18, age_max=44))
    # 窗口外 → 没有分层命中 → 无报告区间 → empty
    assert t.resolve(AID, "男", 70, "mmol/L").shape == "empty"


def test_unit_must_match_or_be_equivalent():
    t = _tbl(ReferenceRange(low=10.0, unit="10^9/L"))
    t.add("X.WBC", ReferenceRange(low=1.0, unit="10^9/L"))
    assert t.resolve("X.WBC", "男", 40, "10^3/μL").low == 1.0     # 等价
    assert t.resolve("X.WBC", "男", 40, "g/L").origin == "none"   # 不同类


# --------------------------------------------------------------- 三层回落

def test_falls_back_to_report_range():
    t = _tbl(ReferenceRange(low=1.3, unit="mmol/L", sex="女"))
    r = t.resolve(AID, "男", 40, "mmol/L", report_low=1.04,
                  report_high=None, report_raw=">1.04mmol/L")
    assert r.origin == "report" and r.low == 1.04
    assert r.shape == "low-only"


def test_empty_table_is_no_op():
    """空表等于不启用 —— 行为必须与原实现完全一致。"""
    t = RangeTable()
    assert len(t) == 0
    r = t.resolve(AID, "男", 40, "mmol/L", report_low=1.04, report_raw=">1.04")
    assert r.origin == "report" and r.low == 1.04


def test_no_range_anywhere_is_empty_not_normal():
    """哪一层都没有区间 → ``empty``，**不能默认正常**。"""
    r = RangeTable().resolve("Z.NONE", "男", 40, "")
    assert r.shape == "empty" and r.origin == "none"


def test_shape_is_modelled_not_assumed():
    """区间形态要显式建模（bounded / low-only / high-only / empty）。

    中文报告常写「＞1.04」「＜5.2」，假设 low+high 都在会判错。
    """
    assert RangeTable().resolve(AID, "男", 40, "", report_low=1.0,
                                report_high=2.0).shape == "bounded"
    assert RangeTable().resolve(AID, "男", 40, "", report_low=1.0).shape == "low-only"
    assert RangeTable().resolve(AID, "男", 40, "", report_high=2.0).shape == "high-only"
    assert RangeTable().resolve(AID, "男", 40, "",
                                report_raw="未见异常").shape == "text"


# --------------------------------------------------------------- 判定层

def test_flag_of_one_sided_ranges():
    assert flag_of(0.9, RangeTable().resolve(AID, "男", 40, "", report_low=1.04)
                   ) == Finding.ABNORMAL
    assert flag_of(1.2, RangeTable().resolve(AID, "男", 40, "", report_low=1.04)
                   ) == Finding.NORMAL
    assert flag_of(5.5, RangeTable().resolve(AID, "男", 40, "", report_high=5.2)
                   ) == Finding.ABNORMAL
    assert flag_of(None, RangeTable().resolve(AID, "男", 40, "", report_low=1.0)) is None


def test_judge_uses_stratified_when_present():
    """接进判定层：分层命中时区间与判定都跟着变，并记明依据。"""
    t = _tbl(ReferenceRange(low=1.2, unit="mmol/L", source="测试分层"))
    metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
             for y, f in {"2024": "20240612体检报告.pdf",
                          "2025": "20250507体检报告.pdf",
                          "2026": "20260609体检报告.pdf"}.items()}
    js = {j.analyte_id: j for j in judge_all(
        obs(), os.path.join(BASE, "catalog", "cvi.json"), metas=metas, ranges=t)}
    j = js[AID]
    assert j.ref_range["2026"] == ">1.2mmol/L", j.ref_range
    assert j.position["2026"] == Finding.ABNORMAL.value
    assert any("分层表" in n for n in j.notes), j.notes


def test_judge_falls_back_when_stratified_does_not_match():
    """分层表不匹配（性别不对）→ 回落报告自带，**行为不变**。"""
    t = _tbl(ReferenceRange(low=1.2, unit="mmol/L", sex="女"))
    metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
             for y, f in {"2026": "20260609体检报告.pdf"}.items()}
    js = {j.analyte_id: j for j in judge_all(
        [o for o in obs() if o.year == "2026"],
        os.path.join(BASE, "catalog", "cvi.json"), metas=metas, ranges=t)}
    assert js[AID].ref_range["2026"] == ">1.04mmol/L"


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
