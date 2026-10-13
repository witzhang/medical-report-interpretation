#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""化验单双栏表格抽取（L1 · 通道 A）的回归测试。

金标准是旧管线 ``体检分析/data/lab_<年>.csv``（已人工核对过）。
    python3 tests/test_tables.py
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.pipeline import year_from_name  # noqa: E402
from checkup.schema import Finding  # noqa: E402
from checkup.tables import (_calc_flag,  # noqa: E402
                            _group_of, _is_section_row, _split_ref,
                            extract_lab_results, lab_page_audit)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
LEGACY_T = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_legacy()
SRC = PATHS.real_reports
LEGACY = PATHS.legacy_dir
PDFS = {"2024": "20240612体检报告.pdf", "2025": "20250507体检报告.pdf",
        "2026": "20260609体检报告.pdf"}

_cache: dict = {}


def got(year: str):
    if year not in _cache:
        doc = fitz.open(os.path.join(SRC, PDFS[year]))
        _cache[year] = extract_lab_results(doc, year, TPL)
        doc.close()
    return _cache[year]


def legacy(year: str) -> dict:
    out, seen = {}, defaultdict(int)
    with open(os.path.join(LEGACY, f"lab_{year}.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            k = (r["section"], r["item"])
            seen[k] += 1
            out[(r["section"], r["item"], seen[k])] = r
    return out


# --------------------------------------------------- 与旧管线逐条一致

def test_item_count_and_keys_match_legacy():
    """三年各 81 项，(段, 项目, 段内第几次) 的键集合必须与旧管线完全一致。"""
    for y in ("2024", "2025", "2026"):
        old = legacy(y)
        new = {(r.section, r.item_raw, r.occurrence): r for r in got(y)}
        assert len(new) == len(old) == 81, (y, len(new), len(old))
        assert set(new) == set(old), \
            f"{y} 键不一致\n 缺: {sorted(set(old) - set(new))[:5]}\n 多: {sorted(set(new) - set(old))[:5]}"


def test_result_values_match_legacy():
    """结果字符串逐条一致。"""
    for y in ("2024", "2025", "2026"):
        old, new = legacy(y), {(r.section, r.item_raw, r.occurrence): r for r in got(y)}
        bad = [(k, old[k]["result"], new[k].result_raw)
               for k in old if old[k]["result"] != new[k].result_raw]
        assert not bad, f"{y} 结果不一致: {bad[:5]}"


def test_occurrence_index_for_duplicate_name():
    """尿液检查里两项都叫「白细胞」，段内出现次序必须正确编号。"""
    ws = [r for r in got("2024") if r.section == "尿液检查" and r.item_raw == "白细胞"]
    assert sorted(r.occurrence for r in ws) == [1, 2]
    assert any(r.result_raw == "阴性" for r in ws), "定性那项丢了"


# --------------------------------------------------- 已踩过的坑

def test_counting_unit_not_swallowed_into_range():
    """``3.5-9.510^9/L`` 不能被切成区间 ``3.5-9.510``。"""
    rng, unit, lo, hi, _ = _split_ref("3.5-9.510^9/L")
    assert (lo, hi) == (3.5, 9.5), (rng, lo, hi)
    assert unit == "10^9/L", unit


def test_one_sided_ranges():
    """单边区间的比较符与上下界。"""
    rng, unit, lo, hi, op = _split_ref(">1.04mmol/L")
    assert (lo, hi, op, unit) == (1.04, None, ">", "mmol/L")
    rng, unit, lo, hi, op = _split_ref("<5.2mmol/L")
    assert (lo, hi, op) == (None, 5.2, "<")


def test_arrows_do_not_become_section_names():
    """箭头与项目名可能被合成一个词（``↑甘油三酯``），不得被当成段标题。

    误判的后果：整个生化段的后半截项目全部丢失。
    """
    for y in ("2024", "2025", "2026"):
        secs = {r.section for r in got(y)}
        bad = {s for s in secs if "↑" in s or "↓" in s}
        assert not bad, f"{y} 出现了箭头段名: {bad}"
        assert "生化检查" in secs, f"{y} 生化段丢失: {secs}"


def test_signature_rows_excluded():
    """审核医生/检验者/审核日期不得进入指标。"""
    for y in ("2024", "2025", "2026"):
        names = {r.item_raw for r in got(y)}
        for bad in ("审核医生", "检验者", "审核日期", "操作员"):
            assert not any(bad in n for n in names), f"{y} 混入签名行 {bad}"


def test_section_carries_across_pages():
    """化验单第二页不重复段标题，段落状态必须跨页继承。"""
    # 2024 的生化检查起于第 10 页、续到第 11 页
    p11 = [r for r in got("2024") if r.page_no == 11]
    assert p11, "第 11 页没抽到任何项"
    assert all(r.section for r in p11), "续页出现空段名，段落继承失效"


def test_all_five_sections_present():
    want = {"血常规", "生化检查", "免疫检查", "尿液检查", "粪便检查"}
    for y in ("2024", "2025", "2026"):
        assert {r.section for r in got(y)} == want, (y, {r.section for r in got(y)})


# --------------------------------------------------- 位置轴判定

def test_arrows_are_attributed_per_column_not_per_row():
    """箭头必须**按栏归属**，不能按整行归属。

    2024 那一行的原始内容是::

        ↑甘油三酯(x0=51，左栏) | 1.82 | 0-1.7mmol/L | ↓(x0=318，右栏) | 高密度脂蛋白胆固醇 | 0.94

    甘油三酯的 ↑ 是**并进项目名**的；那个独立的 ↓ 属于右栏的 HDL。

    旧管线把整行的箭头套给该行**每一条**记录，于是甘油三酯也被记成 ↓，
    进而得出「报告把甘油三酯标错了」的结论（项目回顾 坑 17）——**那是我们
    自己抽取器的 bug，不是机构的错误**。本测试锁死正确行为。
    """
    for y in ("2024", "2025", "2026"):
        rs = {r.item_raw: r for r in got(y)}
        assert rs["甘油三酯"].pdf_flag == "↑", (y, rs["甘油三酯"].pdf_flag)
        assert rs["高密度脂蛋白胆固醇"].pdf_flag == "↓", \
            (y, rs["高密度脂蛋白胆固醇"].pdf_flag)
        assert rs["甘油三酯"].pdf_flag != rs["高密度脂蛋白胆固醇"].pdf_flag, \
            f"{y} 两项箭头相同，说明又按整行套用了"


def test_flag_is_computed_from_value_and_range():
    """自算标记 = 数值 vs 参考区间，与 PDF 箭头无关。

    甘油三酯 1.82/2.13/2.32 均超上限 1.7 → 三年都判 abnormal。
    """
    for y in ("2024", "2025", "2026"):
        tg = [r for r in got(y) if r.item_raw == "甘油三酯"][0]
        assert tg.flag == Finding.ABNORMAL, (y, tg.flag)
        hdl = [r for r in got(y) if "高密度" in r.item_raw][0]
        assert hdl.flag == Finding.ABNORMAL, (y, hdl.flag)


def test_computed_flag_agrees_with_pdf_arrow_after_fix():
    """抽对了之后，自算与 PDF 箭头**三年全部一致**——报告本身没有标错。"""
    mism = []
    for y in ("2024", "2025", "2026"):
        for r in got(y):
            if not r.pdf_flag or r.flag is None:
                continue
            want = {"↑": Finding.ABNORMAL, "↓": Finding.ABNORMAL}[r.pdf_flag]
            if r.flag != want:
                mism.append((y, r.item_raw, r.pdf_flag, r.flag))
    assert not mism, f"自算与 PDF 箭头仍不一致: {mism}"


def test_qualitative_results_have_no_flag():
    """定性项（阴性/未见异常）没有数值，不参与区间判定。"""
    q = [r for r in got("2024") if r.value is None]
    assert q, "应当存在定性项"
    assert all(r.flag is None for r in q)
    assert all(r.value_type == "qualitative" for r in q)


# ---------------------------------------- 边界（由变异测试指出）

def test_flag_boundaries_are_inclusive():
    """参考区间的**上下界都算正常** —— 恰好等于边界不是异常。

    变异测试发现 ``low <= value <= high``、``value >= low``、``value <= high``
    三处的比较符改成严格不等号**都无人管** —— 边界值从没构造过。
    """
    assert _calc_flag(1.7, 0.0, 1.7, "") == Finding.NORMAL      # 恰好上界
    assert _calc_flag(1.71, 0.0, 1.7, "") == Finding.ABNORMAL
    assert _calc_flag(0.0, 0.0, 1.7, "") == Finding.NORMAL      # 恰好下界
    assert _calc_flag(-0.01, 0.0, 1.7, "") == Finding.ABNORMAL
    # 单边
    assert _calc_flag(1.04, 1.04, None, ">") == Finding.NORMAL
    assert _calc_flag(1.03, 1.04, None, ">") == Finding.ABNORMAL
    assert _calc_flag(5.2, None, 5.2, "<") == Finding.NORMAL
    assert _calc_flag(5.21, None, 5.2, "<") == Finding.ABNORMAL
    # 无区间 → 无法判
    assert _calc_flag(1.0, None, None, "") is None
    assert _calc_flag(None, 0.0, 1.7, "") is None


def test_group_boundary_goes_to_the_right_group():
    """栏边界 ``x < b`` 的取法：**恰好等于边界归右栏**。

    变异测试发现 ``if x < b`` 改成 ``<=`` 无人管。
    """
    groups = [[0.0, 100.0, 200.0], [300.0, 400.0, 500.0]]
    bounds = [250.0]
    assert _group_of(249.9, groups, bounds) == 0
    assert _group_of(250.0, groups, bounds) == 1      # 恰好边界 → 右栏
    assert _group_of(0.0, groups, bounds) == 0


def test_section_column_boundary_is_inclusive():
    """段标题列判定 ``x0 <= section_x_max``：**恰好等于上限仍算段标题列**。

    变异测试发现改成 ``<`` 无人管。这条决定了项目名会不会被误当成段标题
    而**整行丢弃**，是本引擎最脆的一处几何假设。
    """
    lim = LEGACY_T.lab.section_x_max
    assert _is_section_row("血常规", lim, LEGACY_T,
                           _noise()) is True
    assert _is_section_row("血常规", lim + 0.1, LEGACY_T,
                           _noise()) is False


def _noise():
    from checkup.templates import compile_patterns
    return compile_patterns(LEGACY_T)["lab_noise"]


def test_audit_item_count_matches_extraction():
    """审计数必须**精确等于**实际抽出的项数 —— 多一个或少一个都说明计数有偏。

    变异测试发现 ``pno + 1`` 之类的页码运算改动无人管；这里锁住更本质的关系。
    """
    for fn in sorted(os.listdir(SRC))[:1]:
        doc = fitz.open(os.path.join(SRC, fn))
        try:
            audit = sum(a["items"] for a in lab_page_audit(doc, TPL))
            got = len(extract_lab_results(doc, year_from_name(fn), TPL))
            assert audit == got == 81, (audit, got)
        finally:
            doc.close()


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
