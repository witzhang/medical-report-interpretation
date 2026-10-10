#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L4 判定层的回归测试。    python3 tests/test_judge.py"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.judge import judge_all, load_cvi, rcv  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.schema import Change, Finding, NormalizedObservation  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
PATHS.require_legacy()
SRC = PATHS.real_reports
CVI = os.path.join(BASE, "catalog", "cvi.json")
LEGACY = PATHS.legacy("rcv_result.csv")
_js = None


def js():
    global _js
    if _js is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        _js = {j.analyte_id: j for j in judge_all(
            [o for rows in per.values() for o in rows], CVI, catalog=cat)}
    return _js


def by_item(name: str):
    return next(j for j in js().values() if j.canonical_name == name)


# --------------------------------------------------------------- 公式

def test_rcv_formula():
    """RCV% = √2 · 1.96 · √(CVi² + CVa²)。"""
    got = rcv(10.0, 3.0)
    want = 1.96 * (2 ** 0.5) * ((100 + 9) ** 0.5)
    assert abs(got - want) < 1e-9, (got, want)
    assert abs(rcv(4.3864, 3.0) - 14.7) < 0.1      # 肌酐，与旧管线一致


# --------------------------------------------------------------- 三次比较

def test_three_pairwise_comparisons():
    """必须做**三次**两两比较：24vs25、25vs26、24vs26。

    只做相邻比较会漏掉跨度最大的那一对——CK 的 +58.5% 就在 24→26 上。
    """
    ck = by_item("肌酸激酶")
    for k in ("2024→2025", "2025→2026", "2024→2026"):
        assert k in ck.change, f"缺 {k}: {sorted(ck.change)}"
    assert ck.change["2024→2026"] == Change.BEYOND_NOISE.value
    assert abs(ck.change_pct["2024→2026"] - 58.5) < 0.1


# --------------------------------------------------------------- 与旧管线一致

def test_beyond_noise_matches_legacy_pipeline():
    """超出噪声的项目必须与旧管线 rcv_result.csv **完全一致**（4 项）。"""
    got = {j.canonical_name for j in js().values()
           if j.change.get("2024→2026") == Change.BEYOND_NOISE.value}
    want = set()
    with open(LEGACY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["判24→26"] == "超出":
                want.add(r["项目"])
    assert got == want, f"新 {sorted(got)}\n旧 {sorted(want)}"
    assert got == {"肌酸激酶", "谷氨酰转肽酶", "总前列腺特异性抗原", "肌酐"}


def test_numeric_fields_match_legacy():
    """CVi / RCV 阈值 / Δ24→26 / 稳健性 逐项比对。"""
    old = {}
    with open(LEGACY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            old[r["项目"]] = r
    bad = []
    for j in js().values():
        o = old.get(j.canonical_name)
        if not o or j.cv_i is None or j.rcv_threshold is None:
            continue
        if abs(j.cv_i - float(o["CVi"])) > 1e-3:
            bad.append((j.canonical_name, "CVi", j.cv_i, o["CVi"]))
        if abs(j.rcv_threshold - float(o["RCV阈值"])) > 0.05:
            bad.append((j.canonical_name, "RCV", j.rcv_threshold, o["RCV阈值"]))
        if abs((j.change_pct.get("2024→2026") or 0) - float(o["24→26"])) > 0.05:
            bad.append((j.canonical_name, "Δ24→26", j.change_pct.get("2024→2026"), o["24→26"]))
        if j.robustness != o["稳健性"]:
            bad.append((j.canonical_name, "稳健性", j.robustness, o["稳健性"]))
    assert not bad, bad[:6]


def test_robustness_uses_cvi_confidence_interval():
    """CVi 的置信区间要传导到结论：用上下界各算一次阈值。"""
    ck = by_item("肌酸激酶")
    assert ck.robustness == "取决于CVi取值"        # CVi 范围宽 → 不稳健
    psa = by_item("总前列腺特异性抗原")
    assert psa.robustness == "稳健超出"


def test_weak_cvi_is_flagged():
    """研究数 <3 的 CVi 估计不稳，必须标注。"""
    psa = by_item("总前列腺特异性抗原")
    assert psa.n_studies == 1
    assert any("研究" in n for n in psa.notes), psa.notes


def test_rcv_overstrict_caveat_is_attached():
    """文献自陈的局限必须随结论输出：前分析未标准化时 RCV 可能过严。"""
    assert any("前分析" in n for n in by_item("肌酐").notes), by_item("肌酐").notes


# --------------------------------------------------------------- 位置轴

def test_position_uses_each_years_own_range():
    """位置轴用**当年各自的**参考区间，不覆盖、不混用。"""
    tg = by_item("甘油三酯")
    assert tg.position["2024"] == Finding.ABNORMAL.value
    assert tg.position["2026"] == Finding.ABNORMAL.value
    assert tg.ref_range["2026"] == "0-1.7mmol/L"


def test_position_axis_and_change_axis_are_separate():
    """两条轴必须分开：位置正常但变化超出噪声（或反之）都要能表达。"""
    hdl = by_item("高密度脂蛋白胆固醇")
    # 位置：三年都 abnormal（0.94 < 1.04）
    assert {hdl.position[y] for y in ("2024", "2025", "2026")} == {Finding.ABNORMAL.value}
    # 变化：三年同值 → 无法与波动区分
    assert hdl.change.get("2024→2026") == Change.WITHIN_NOISE.value


def test_missing_year_is_undetermined_not_normal():
    """缺失 ≠ 阴性。缺值的年份判 undetermined，不按其他年份推断。"""
    for j in js().values():
        for y, p in j.position.items():
            assert p != "", (j.analyte_id, y)
    # 骨密度只在 2024/2026 有：不在判定对象里也不该被补成正常
    bmd = [j for j in js().values() if "骨密度" in j.canonical_name]
    assert not bmd or all(
        j.position.get("2025") in (None, Finding.UNDETERMINED.value) for j in bmd)


# --------------------------------------------------------------- 拒绝比较

def test_incomparable_units_refuse_trend():
    """单位不可比时**拒绝判趋势**——an absent delta is honest, a wrong one is not."""
    o = [
        NormalizedObservation(year="2024", page_no=1, section="X", item_raw="T",
                              occurrence=1, analyte_id="X.T", value=1.0, unit="mg/dL",
                              ref_low=0, ref_high=2),
        NormalizedObservation(year="2025", page_no=1, section="X", item_raw="T",
                              occurrence=1, analyte_id="X.T", value=2.0, unit="mmol/L",
                              ref_low=0, ref_high=2),
    ]
    j = judge_all(o, CVI)[0]
    assert not j.change, j.change
    assert any("拒绝判趋势" in n for n in j.notes), j.notes


def test_equivalent_units_are_comparable():
    """10^9/L 与 10^3/μL 是**等价**，不该被当成不可比。"""
    o = [
        NormalizedObservation(year="2024", page_no=1, section="血常规", item_raw="白细胞",
                              occurrence=1, analyte_id="BLD.白细胞", value=7.5,
                              unit="10^9/L", ref_low=3.5, ref_high=9.5),
        NormalizedObservation(year="2025", page_no=1, section="血常规", item_raw="白细胞",
                              occurrence=1, analyte_id="BLD.白细胞", value=8.6,
                              unit="10^3/μL", ref_low=3.5, ref_high=9.5),
    ]
    j = judge_all(o, CVI)[0]
    assert not any("拒绝判趋势" in n for n in j.notes), j.notes


# --------------------------------------------------------------- 不做回归

def test_no_regression_is_computed_anywhere():
    """3 个数据点**不做回归**——只用 RCV 做两两比较。

    6 个开源项目全量 grep 无一人对化验趋势跑回归；3–5 个点的 R² 几乎由
    点数决定，且易被单个离群点主导。
    """
    import checkup.judge as m
    src = open(m.__file__, encoding="utf-8").read()
    for bad in ("polyfit", "linregress", "r_squared", "r-squared", "sklearn",
                "statsmodels", "corrcoef"):
        assert bad not in src.lower(), f"判定层出现了回归相关调用: {bad}"


# --------------------------------------------------------------- CVi 表

def test_cvi_table_has_provenance():
    """每条 CVi 必须带 EFLM 指标名与研究数，可回溯。"""
    e = load_cvi(CVI)
    assert len(e) >= 40, len(e)
    for aid, v in e.items():
        assert v["cvi"] > 0
        assert v["eflm_analyte"] and v["source"]


def test_cvi_only_attached_to_blood_sections():
    """CVi 只挂血液类段落——把尿液沉渣套用血液细胞 CVi 是踩过的坑（坑 8）。"""
    e = load_cvi(CVI)
    bad = [k for k in e if k.split(".")[0] in ("URI", "STOOL")]
    assert not bad, f"尿/便项目被挂了 CVi: {bad}"


# --------------------------------------------------------------- 边界（由变异测试指出）
#
# 以下几条都是「改动源码后测试不失败」的地方 —— 说明边界值从没构造过。
# 主题：**只测了区间之内，没测区间之上**。

def _obs(aid, year, value, unit="U/L", section="生化检查", kind="numeric"):
    return NormalizedObservation(
        year=year, page_no=1, section=section, item_raw=aid, occurrence=1,
        analyte_id=aid, value=value, value_type=kind, unit=unit,
        ref_low=None, ref_high=None)


def _judge(rows, cvi_path=None):
    """注意：``judge_all`` 收的是 CVi **路径**（内部会 load），
    传错就会静默用默认表 —— 这里踩过一次。"""
    out = judge_all(rows, cvi_path or CVI, catalog=None)
    return next(j for j in out if j.analyte_id == rows[0].analyte_id)


def _with_cvi(aid, cvi, lo, hi, n):
    """往一张临时 CVi 表里塞一个受控条目。"""
    import json as _json
    import tempfile
    d = _json.load(open(CVI, encoding="utf-8"))
    d["entries"][aid] = {"cvi": cvi, "cvi_low": lo, "cvi_high": hi,
                         "n_studies": n, "eflm_analyte": "test"}
    p = os.path.join(tempfile.mkdtemp(), "cvi.json")
    _json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def test_within_noise_boundary_just_below_and_above():
    """阈值**两侧**都要测：略低于 → within、略高于 → beyond。

    为什么不用"严格等于"：``ch = (vb-va)/va*100`` 与 ``rcv(cvi, cva)``
    是两条不同的浮点运算链，**不可能精确相等**，所以 ``abs(ch) > thr`` 改成
    ``>=`` 在实际上不可杀（等价变异体）。这里只锁住两侧。
    """
    aid = "TEST.BOUND"
    p = _with_cvi(aid, 10.0, 10.0, 10.0, 5)
    thr = rcv(10.0, 3.0)
    va = 100.0
    below = _judge([_obs(aid, "2024", va),
                    _obs(aid, "2025", va * (1 + (thr - 0.5) / 100))], p)
    above = _judge([_obs(aid, "2024", va),
                    _obs(aid, "2025", va * (1 + (thr + 0.5) / 100))], p)
    assert below.change["2024→2025"] == Change.WITHIN_NOISE.value, below.change_pct
    assert above.change["2024→2025"] == Change.BEYOND_NOISE.value, above.change_pct
    assert abs(below.change_pct["2024→2025"]) < thr < abs(above.change_pct["2024→2025"])


def test_two_year_series_still_gets_robustness():
    """**只有两年**时也要算稳健性。

    变异测试发现 ``len(years) >= 2`` 改成 ``>`` 无人管 —— 因为所有
    稳健性测试用的都是三年数据。
    """
    aid = "TEST.TWO"
    p = _with_cvi(aid, 5.0, 4.5, 5.5, 5)
    j = _judge([_obs(aid, "2024", 100.0), _obs(aid, "2025", 130.0)], p)
    assert j.robustness, "两年数据没算稳健性"
    assert j.change_pct.get("2024→2025") is not None
    assert "2025→2026" not in j.change


def test_single_unit_series_reports_no_conflict():
    """**只有一种单位**时不报单位冲突。

    变异测试发现 ``len(units) <= 1`` 改成 ``<`` 无人管 —— 从没构造过
    "只有一个单位值"的两点序列（现有测试都是多单位或不可比单位）。
    """
    aid = "TEST.ONEUNIT"
    p = _with_cvi(aid, 5.0, 4.5, 5.5, 5)
    j = _judge([_obs(aid, "2024", 100.0, unit="U/L"),
                _obs(aid, "2025", 120.0, unit="U/L")], p)
    assert j.change.get("2024→2025") == Change.BEYOND_NOISE.value, j.change
    assert not any("单位" in n and "可比" in n for n in j.notes), j.notes


def test_three_studies_is_enough_no_caveat():
    """``n_studies`` **恰好 3** 时不该再标"估计不稳"（判定用的是严格小于）。"""
    aid = "TEST.N3"
    p2 = _with_cvi(aid, 5.0, 4.5, 5.5, 3)
    j2 = _judge([_obs(aid, "2024", 100.0), _obs(aid, "2025", 130.0)], p2)
    assert j2.n_studies == 3
    assert not any("估计不稳" in n for n in j2.notes), j2.notes
    p1 = _with_cvi(aid, 5.0, 4.5, 5.5, 2)
    j1 = _judge([_obs(aid, "2024", 100.0), _obs(aid, "2025", 130.0)], p1)
    assert any("估计不稳" in n for n in j1.notes), j1.notes


def test_robustness_three_way_boundary():
    """稳健性的三段划分按 **CVi 上下界**算，边界上的取法要固定。"""
    aid = "TEST.ROB"
    # CVi 区间很窄 → hi ≈ lo，故只有"稳健超出/稳健在内"两种可能
    p = _with_cvi(aid, 5.0, 5.0, 5.0, 5)
    j = _judge([_obs(aid, "2024", 100.0), _obs(aid, "2025", 200.0)], p)
    assert j.robustness == "稳健超出", j.robustness
    j2 = _judge([_obs(aid, "2024", 100.0), _obs(aid, "2025", 101.0)], p)
    assert j2.robustness == "稳健在内", j2.robustness


def test_qualitative_observation_is_excluded_from_change_axis():
    """**值类型不是 numeric 的观测不进变化轴**，哪怕它带着一个数值。

    变异测试发现 ``o.value_type == "numeric" and o.value is not None``
    改成 ``or`` 无人管 —— 因为从没构造过"定性项却带着数值"的记录。
    """
    aid = "TEST.QUAL"
    p = _with_cvi(aid, 5.0, 4.5, 5.5, 5)
    rows = [_obs(aid, "2024", 100.0, kind="qualitative"),
            _obs(aid, "2025", 130.0, kind="qualitative")]
    out = judge_all(rows, p, catalog=None)
    # 该条目会经**位置轴**出现（定性 → not_applicable），但**变化轴必须为空**
    j = next((x for x in out if x.analyte_id == aid), None)
    assert j is not None, "定性项连位置轴都没进"
    assert not j.change and not j.change_pct, \
        f"定性项的数值被算进了变化轴：{j.change}"
    assert j.rcv_threshold is None


def test_resolved_range_empty_yields_not_assessable():
    """分层区间解析为 ``empty``（既无分层命中、报告也没给区间）→ 不可评估。

    变异测试发现 ``rr.shape == "empty"`` 改成 ``!=`` 无人管 —— 说明
    这条分支从没在判定层被走到过。
    """
    from checkup.ranges import RangeTable
    aid = "TEST.EMPTY"
    p = _with_cvi(aid, 5.0, 4.5, 5.5, 5)
    rows = [_obs(aid, "2024", 100.0), _obs(aid, "2025", 130.0)]
    tbl = RangeTable()                     # 空表 → 只能回落报告自带
    out = judge_all(rows, p, catalog=None, ranges=tbl)
    j = next(x for x in out if x.analyte_id == aid)
    assert j.position["2024"] == Finding.NOT_ASSESSABLE.value, j.position
    assert j.position["2025"] == Finding.NOT_ASSESSABLE.value, j.position


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
