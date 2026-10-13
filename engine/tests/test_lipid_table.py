#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""21 格危险分层表与余生风险判据。    python3 tests/test_lipid_table.py

原文来自《中国血脂管理指南（2023年）》图1。表在图**内部**，文字层字体编码损坏
（抽出为 `$6&9'` 这类乱码），由**渲染图逐字转录** —— 见
`体检分析/调研/src/lipid2023-fig1-21cell.md`。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.lipid_table import (CHOL_STRATA, HYPERTENSION,  # noqa: E402
                                 LIFETIME_CRITERIA, LIFETIME_MIN_HITS,
                                 NO_HYPERTENSION, _stratum_of, lifetime_risk,
                                 lookup, risk_factors)
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_legacy()
PATHS.require_profile()
SRC = PATHS.real_reports
REAL = {"2024": "20240612体检报告.pdf", "2025": "20250507体检报告.pdf",
        "2026": "20260609体检报告.pdf"}
_base = None


def base():
    global _base
    if _base is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        obs = [o for rows in build_all(SRC, cat).values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        _base = dict(obs=obs, metas=metas, cat=cat)
    return _base


def one(res, rid, year="2026"):
    return next(x for x in res if x.rule_id == rid and x.year == year)


def run_obs(mod):
    """用改造过的数值跑一遍完整评估，返回派生结果列表。"""
    from dataclasses import replace
    obs = [replace(o, value=mod[o.analyte_id]) if o.analyte_id in mod else o
           for o in base()["obs"]]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=base()["metas"])
    return evaluate(Composer(js, base()["metas"],
                             load_profile(PATHS.profile)))


def strat(mod=None, **pf_over):
    obs = [replace(o, value=mod[o.analyte_id]) if mod and o.analyte_id in mod
           else o for o in base()["obs"]]
    pf = dict(load_profile(PATHS.profile))
    pf.update(pf_over)
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=base()["metas"])
    res = evaluate(Composer(js, base()["metas"], pf))
    return next(x for x in res
                if x.rule_id == "LIPID.RISK_STRATIFICATION" and x.year == "2026")


# =================================================== 表本体

def test_table_has_exactly_21_cells():
    """21 格 = 3×3（无高血压）+ 4×3（有高血压）。"""
    n = sum(len(v) for v in NO_HYPERTENSION.values()) + \
        sum(len(v) for v in HYPERTENSION.values())
    assert n == 21, n


def test_every_cell_is_low_mid_or_high():
    for tbl in (NO_HYPERTENSION, HYPERTENSION):
        for row, cells in tbl.items():
            assert len(cells) == 3, (row, cells)
            for c in cells:
                assert c in ("低危", "中危", "高危"), (row, c)


def test_specific_cells_match_the_transcription():
    """抽查逐字转录的关键格。"""
    assert HYPERTENSION["2"] == ("中危", "高危", "高危")
    assert HYPERTENSION["3"] == ("高危", "高危", "高危")
    assert NO_HYPERTENSION["2"] == ("低危", "低危", "中危")
    assert NO_HYPERTENSION["3"] == ("低危", "中危", "中危")
    assert NO_HYPERTENSION["0~1"] == ("低危", "低危", "低危")


def test_three_cholesterol_strata_boundaries():
    assert [(s.key, s.tc_low, s.tc_high, s.ldl_low, s.ldl_high)
            for s in CHOL_STRATA] == [
        ("C1", 3.1, 4.1, 1.8, 2.6),
        ("C2", 4.1, 5.2, 2.6, 3.4),
        ("C3", 5.2, 7.2, 3.4, 4.9)]


# =================================================== 定列

def test_tc_and_ldl_agree_gives_that_column():
    r = lookup(False, 35, True, tc=4.5, ldl=3.0, hdl=1.2, smoke=False)
    assert r.stratum_key == "C2" and r.band == "低危"


def test_ldl_below_c1_lower_bound_is_extrapolated():
    """LDL 侧的外推提示 —— 变异测试发现只测了 TC 侧。"""
    idx, _w, notes = _stratum_of(tc=None, ldl=1.7)
    assert idx == 0
    assert any("外推" in n for n in notes), notes


def test_agreeing_tc_and_ldl_adds_no_disagreement_note():
    """**单侧断言的反面**：TC 与 LDL 同档时**不得**出现"取较重一列"的提示。

    变异测试发现 ``len(set(idxs)) > 1`` 改成 ``>= 1`` 没有任何测试失败 ——
    因为原先只测了"不一致时有提示"，没测"一致时没有"。
    """
    _idx, _w, notes = _stratum_of(tc=4.5, ldl=3.0)      # 都在 C2
    assert not any("取较重" in n for n in notes), notes


def test_disagreeing_tc_and_ldl_takes_the_heavier():
    """TC 在 C2、LDL-C 在 C1 → 取较重（C2），并写明这是**引擎的保守选择**。"""
    r = lookup(False, 35, True, tc=4.5, ldl=2.0, hdl=1.2, smoke=False)
    assert r.stratum_key == "C2", r.stratum_key
    assert any("取较重的一列" in n and "指南未规定" in n for n in r.notes), r.notes


def test_below_c1_is_extrapolated_and_flagged():
    """两个判据都够不到 C1 → 按 C1 处理，但必须标为**外推**。"""
    r = lookup(False, 35, True, tc=2.5, ldl=1.5, hdl=1.2, smoke=False)
    assert r.stratum_key == "C1"
    assert any("表未覆盖" in n and "外推" in n for n in r.notes), r.notes


# ---------------- 分层边界（变异测试指出这里原先没有直接覆盖）

def test_tc_stratum_lower_bounds_are_inclusive():
    """每档的**下界是闭**：``3.1`` 属 C1、``4.1`` 属 C2、``5.2`` 属 C3。

    变异测试发现：把 ``tc_low <= tc`` 的 ``<=`` 改成 ``<`` **没有任何测试失败**。
    代价是 TC 恰好 4.1 会掉进"超出上界"分支被判 C3 —— **跨一整档**。
    """
    for tc, want in ((3.1, 0), (4.1, 1), (5.2, 2)):
        idx, why, notes = _stratum_of(tc=tc, ldl=None)
        assert idx == want, (tc, idx, want, why, notes)


def test_tc_stratum_upper_bounds_are_exclusive():
    """每档的**上界是开**：``4.09`` 属 C1、``5.19`` 属 C2。"""
    for tc, want in ((4.099, 0), (5.199, 1), (7.199, 2)):
        idx, _w, _n = _stratum_of(tc=tc, ldl=None)
        assert idx == want, (tc, idx, want)


def test_ldl_stratum_lower_bounds_are_inclusive():
    for ldl, want in ((1.8, 0), (2.6, 1), (3.4, 2)):
        idx, _w, _n = _stratum_of(tc=None, ldl=ldl)
        assert idx == want, (ldl, idx, want)


def test_exact_c1_lower_bound_is_not_treated_as_extrapolation():
    """恰好等于下界 **不算外推** —— 变异测试发现这条也没覆盖。

    ``tc < 3.1`` 的 ``<`` 若改成 ``<=``，TC 恰好 3.1 会被当成"低于 C1 下界"，
    结论里会多出一条错误的外推说明。
    """
    idx, _w, notes = _stratum_of(tc=3.1, ldl=None)
    assert idx == 0
    assert not any("外推" in n for n in notes), notes


def test_just_below_c1_lower_bound_is_extrapolation():
    _idx, _w, notes = _stratum_of(tc=3.09, ldl=None)
    assert any("外推" in n for n in notes), notes


def test_chol_stratum_is_immutable():
    """表是 ``frozen=True`` 的 —— 变异测试发现改成 ``False`` 没人管。

    这张表是**原文转录**，被代码意外改写会让结论失去依据，值得锁住。
    """
    import dataclasses
    assert CHOL_STRATA[0].__dataclass_params__.frozen is True
    try:
        CHOL_STRATA[0].key = "X"        # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("CholStratum 竟然可以被修改")


# =================================================== 危险因素

def test_risk_factors_match_the_footnote():
    """脚注逐字：危险因素 = 吸烟、低 HDL-C、年龄≥45/55 岁（男性/女性）。"""
    n, hits, miss = risk_factors(50, True, 0.9, True)
    assert n == 3 and not miss, (n, hits, miss)      # 吸烟 + 低HDL + 年龄≥45
    assert risk_factors(35, True, 1.5, False)[0] == 0  # 三项都不命中
    # 年龄切点分性别
    assert risk_factors(45, True, 1.5, False)[0] == 1
    assert risk_factors(45, False, 1.5, False)[0] == 0
    assert risk_factors(55, False, 1.5, False)[0] == 1


def test_hdl_threshold_boundary_is_exact():
    """低 HDL-C 的切点是 1.04：``1.03`` 算低、``1.04`` 不算。"""
    assert risk_factors(35, True, 1.03, False)[0] == 1
    assert risk_factors(35, True, 1.04, False)[0] == 0


def test_risk_factor_count_never_exceeds_three():
    """脚注只给三项，故个数上限是 3 —— ``lookup`` 里 ``n_rf > 3`` 是防御性代码。"""
    n, hits, _m = risk_factors(60, True, 0.5, True)
    assert n == 3, (n, hits)


def test_one_risk_factor_uses_the_combined_row():
    """无高血压块的 ``0~1`` 是原文的**合并行** —— 1 个因素要与 0 个同判。

    变异测试发现 ``n_rf <= 1`` 改成 ``< 1`` 没人管 —— 因为**从没测过
    ``n_rf == 1`` 且无高血压**的情形（那会 KeyError）。
    """
    r = lookup(False, 30, True, tc=4.5, ldl=3.0, hdl=1.5, smoke=True)
    assert r.row_key == "0~1", r.row_key
    assert r.band == "低危"
    # 有高血压时 0 与 1 是**分开的两行**，结果相同但行键不同
    r1 = lookup(True, 30, True, tc=4.5, ldl=3.0, hdl=1.5, smoke=True)
    assert r1.row_key == "1", r1.row_key


def test_missing_risk_factor_input_is_flagged_as_optimistic():
    """缺 HDL-C 或年龄 → 个数可能更多，结果**偏乐观**，必须说。"""
    r = lookup(False, None, True, tc=4.5, ldl=3.0, hdl=None, smoke=False)
    assert any("偏乐观" in n for n in r.notes), r.notes


# =================================================== 未知维度

def test_unknown_hypertension_is_not_assessable_not_assumed():
    """**高血压未知 → 不可评估**。

    两个块在多数格子上结果不同（如 2 个危险因素 + C3：无高血压=中危、
    有高血压=高危），默认取"无高血压"块会**低估风险**。
    """
    r = lookup(None, 35, True, tc=5.25, ldl=3.5, hdl=0.94, smoke=True)
    assert r.status == "not_assessable" and r.band is None
    assert any("不能查表" in n for n in r.notes), r.notes
    # 同一输入、高血压已知 → 有结果，且两块确实不同
    lo = lookup(False, 35, True, tc=5.25, ldl=3.5, hdl=0.94, smoke=True)
    hi = lookup(True, 35, True, tc=5.25, ldl=3.5, hdl=0.94, smoke=True)
    assert (lo.band, hi.band) == ("中危", "高危")


# =================================================== 余生风险

def test_lifetime_criteria_are_the_five_from_the_figure():
    assert len(LIFETIME_CRITERIA) == 5
    assert LIFETIME_MIN_HITS == 2
    labels = " ".join(l for l, _k in LIFETIME_CRITERIA)
    for k in ("收缩压≥160", "非 HDL-C≥5.2", "HDL-C<1.0", "BMI≥28", "吸烟"):
        assert k in labels, k


def test_lifetime_two_hits_is_high_risk():
    """任意 **2 项及以上** → ASCVD 高危人群。"""
    r = lifetime_risk(sbp=120, dbp=80, non_hdl=4.0, hdl=0.9, bmi=25, smoke=True)
    assert r.band == "高危人群" and len(r.hits) == 2, (r.band, r.hits)
    r1 = lifetime_risk(sbp=120, dbp=80, non_hdl=4.0, hdl=0.9, bmi=25,
                       smoke=False)
    assert r1.band == "非高危人群" and len(r1.hits) == 1


def test_lifetime_all_five_hits():
    r = lifetime_risk(sbp=170, dbp=105, non_hdl=5.5, hdl=0.8, bmi=30, smoke=True)
    assert len(r.hits) == 5 and r.band == "高危人群"


def test_lifetime_bp_missing_is_not_assessable():
    """血压缺失也要判不可评估 —— 变异测试发现只测了 non-HDL 缺失。"""
    r = lifetime_risk(sbp=None, dbp=80, non_hdl=4.0, hdl=0.9, bmi=25,
                      smoke=True)
    assert r.status == "not_assessable" and r.band == "不可评估", r
    r2 = lifetime_risk(sbp=120, dbp=None, non_hdl=4.0, hdl=0.9, bmi=25,
                       smoke=True)
    assert r2.status == "not_assessable", r2


def test_lifetime_bp_thresholds_are_or_not_and():
    """收缩压 ≥160 **或**舒张压 ≥100 —— 单侧达标即可。

    变异测试发现 ``or`` 改成 ``and`` 没人管 —— 因为原先只测了两侧都达标。
    """
    only_sys = lifetime_risk(sbp=165, dbp=80, non_hdl=4.0, hdl=1.5, bmi=25,
                             smoke=False)
    assert len(only_sys.hits) == 1, only_sys.hits
    only_dia = lifetime_risk(sbp=120, dbp=105, non_hdl=4.0, hdl=1.5, bmi=25,
                             smoke=False)
    assert len(only_dia.hits) == 1, only_dia.hits
    neither = lifetime_risk(sbp=159, dbp=99, non_hdl=4.0, hdl=1.5, bmi=25,
                            smoke=False)
    assert not neither.hits, neither.hits


def test_lifetime_each_threshold_boundary():
    """五条判据的边界值逐条验：恰好达标算命中，差一点不算。"""
    def hits(**kw):
        base_kw = dict(sbp=120, dbp=80, non_hdl=4.0, hdl=1.5, bmi=25,
                       smoke=False)
        base_kw.update(kw)
        return lifetime_risk(**base_kw).hits

    assert len(hits(non_hdl=5.2)) == 1 and not hits(non_hdl=5.19)
    assert len(hits(hdl=0.99)) == 1 and not hits(hdl=1.0)
    assert len(hits(bmi=28.0)) == 1 and not hits(bmi=27.9)
    assert len(hits(sbp=160)) == 1 and not hits(sbp=159.9)
    assert len(hits(dbp=100)) == 1 and not hits(dbp=99.9)
    assert len(hits(smoke=True)) == 1


def test_lifetime_missing_input_is_not_assessable():
    """缺项 → 不可评估，**不能默认不算命中**（那会低估）。"""
    r = lifetime_risk(sbp=120, dbp=80, non_hdl=None, hdl=0.9, bmi=25, smoke=True)
    assert r.status == "not_assessable" and r.band == "不可评估", r


# =================================================== 接入判定层

def test_stratification_uses_the_table_not_the_approximation():
    s = strat()
    assert s.inputs["路径"] == "一级预防 · 21 格表"
    assert "21格行" in s.inputs and "21格列" in s.inputs
    assert "21 格表" in s.text


def test_table_and_chinapar_are_reported_as_different_things():
    """表是人群平均风险、模型是个体风险，**两者不等价** —— 必须写明以谁为准。"""
    s = strat()
    assert "ChinaPAR 10年风险%" in s.inputs
    assert any("不等价" in n and "以查表为准" in n for n in s.notes), s.notes


def test_current_case_reaches_mid_risk_and_evaluates_lifetime():
    """TC 5.25 落在 C3、危险因素 2 个、无高血压 → 中危 → 触发余生风险评估。"""
    s = strat({"CHEM.总胆固醇": 5.25})
    assert s.band == "中危"
    assert s.inputs["余生风险"] == "高危人群"
    assert any("余生风险" in n and "命中" in n for n in s.notes), s.notes


def test_no_lifetime_evaluation_when_not_mid_risk():
    s = strat()
    assert s.band == "低危"
    assert "余生风险" not in s.inputs


def test_hypertension_flag_switches_the_table_block():
    lo = strat({"CHEM.总胆固醇": 5.25})
    hi = strat({"CHEM.总胆固醇": 5.25}, hypertension_diagnosed=True)
    assert (lo.band, hi.band) == ("中危", "高危")


def test_unknown_hypertension_makes_stratification_not_assessable():
    s = strat(hypertension_diagnosed=None)
    assert s.status == "not_assessable"
    assert not s.band
    assert any("不能查表" in m for m in s.missing), s.missing


def test_regression_current_case_still_low_risk_all_years():
    """本案例三年经查表仍判低危 —— 与改用查表前**一致**。"""
    for y in ("2024", "2025", "2026"):
        obs = base()["obs"]
        js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                       catalog=base()["cat"], metas=base()["metas"])
        res = evaluate(Composer(js, base()["metas"],
                                load_profile(PATHS.profile)))
        got = next(x for x in res
                   if x.rule_id == "LIPID.RISK_STRATIFICATION" and x.year == y)
        assert got.band == "低危", (y, got.band)


def test_pretreatment_caveat_is_always_stated():
    """脚注：「危险因素的水平均为**干预前水平**」。"""
    s = strat()
    assert any("干预前水平" in n for n in s.notes), s.notes


# ------------------------------------------------------------------ 主程序

# ------------------------------------------ 分层路径的阈值边界（变异测试指出）
#
# `_stratify` 是全仓库存活最多的另一处（26 个），主题同为**边界没构造**：
# 低 HDL 的 1.04、危险因素的年龄 45/55、直接高危的 TC 7.2、糖尿病的 40 岁、
# LDL 到靶、非 HDL 到靶 —— 这些 ``>=``/``<`` 改了都没人管。

def _rf(**mod):
    """返回 (危险因素个数, 命中项) —— 从分层结论的 inputs 里读。"""
    s = strat(mod or None)
    return s.inputs.get("危险因素个数"), s.inputs.get("危险因素")


def test_low_hdl_risk_factor_boundary():
    """危险因素ᵃ 里的「低 HDL-C」切点是 **1.04**：1.03 算、1.04 不算。"""
    assert _rf(**{"CHEM.高密度脂蛋白胆固醇": 1.03})[0] == 2, _rf(
        **{"CHEM.高密度脂蛋白胆固醇": 1.03})
    assert _rf(**{"CHEM.高密度脂蛋白胆固醇": 1.04})[0] == 1, _rf(
        **{"CHEM.高密度脂蛋白胆固醇": 1.04})


def test_age_risk_factor_boundaries_differ_by_sex():
    """「年龄」这条危险因素：**男 ≥45、女 ≥55**（脚注逐字）。"""
    from dataclasses import replace
    for age, male, want in ((44, True, 1), (45, True, 2),
                            (54, False, 1), (55, False, 2)):
        metas = dict(base()["metas"])
        metas["2026"] = replace(metas["2026"], age=age,
                                sex="男" if male else "女")
        obs = base()["obs"]
        pf = dict(load_profile(PATHS.profile))
        pf.update({"smoking": {"current": False},
                   "hypertension_diagnosed": False})
        js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                       catalog=base()["cat"], metas=metas)
        res = evaluate(Composer(js, metas, pf))
        got = next(x for x in res if x.rule_id == "LIPID.RISK_STRATIFICATION"
                   and x.year == "2026")
        assert got.inputs["危险因素个数"] == want, (age, male, got.inputs)


def test_direct_high_risk_tc_boundary():
    """「TC≥7.2 → 直接高危」的边界：7.2 算、7.19 不算。"""
    assert strat({"CHEM.总胆固醇": 7.2}).band == "高危"
    assert strat({"CHEM.总胆固醇": 7.19}).band != "高危"


def test_direct_high_risk_diabetes_age_boundary():
    """「年龄≥40 的糖尿病 → 直接高危」：40 岁算、39 岁不算。"""
    from dataclasses import replace
    for age, want in ((40, "高危"), (39, None)):
        metas = dict(base()["metas"])
        metas["2026"] = replace(metas["2026"], age=age)
        pf = dict(load_profile(PATHS.profile))
        pf["diabetes"] = True
        js = judge_all(base()["obs"], os.path.join(BASE, "catalog", "cvi.json"),
                       catalog=base()["cat"], metas=metas)
        res = evaluate(Composer(js, metas, pf))
        got = next(x for x in res if x.rule_id == "LIPID.RISK_STRATIFICATION"
                   and x.year == "2026")
        if want:
            assert got.band == want, (age, got.band, got.text)
        else:
            assert got.inputs.get("路径") != "一级预防 · 直接高危", got.inputs


def test_ldl_target_boundary_is_strict():
    """LDL-C **恰好等于**目标值不算达标（条件是严格小于）。"""
    s = one(run_obs({"CHEM.低密度脂蛋白胆固醇": 3.4}), "LIPID.LDL_TARGET")
    assert "未达标" in s.band, s.band          # 目标 <3.4，恰好 3.4 不达标
    s2 = one(run_obs({"CHEM.低密度脂蛋白胆固醇": 3.39}), "LIPID.LDL_TARGET")
    assert "达标" in s2.band and "未达标" not in s2.band, s2.band


def test_non_hdl_target_boundary_is_strict():
    """非 HDL-C 目标 = LDL 目标 + 0.8，**恰好等于**也不算达标。

    注意**不能动 TC 来构造**：TC 越过 5.2 会把分层从低危推到中危，
    目标随之从 3.4 变成 2.6，测的就不是同一个目标了。
    这里保持 TC 不变、只调 HDL，让 non-HDL = TC − HDL 落在目标两侧。
    """
    tc = next(o.value for o in base()["obs"]
              if o.analyte_id == "CHEM.总胆固醇" and o.year == "2026")
    assert tc is not None
    # 低危 → LDL 目标 3.4 → 非 HDL 目标 4.2
    at = one(run_obs({"CHEM.高密度脂蛋白胆固醇": round(tc - 4.2, 2)}),
             "LIPID.LDL_TARGET")
    below = one(run_obs({"CHEM.高密度脂蛋白胆固醇": round(tc - 4.19, 2)}),
                "LIPID.LDL_TARGET")
    assert "4.2" in " ".join(at.notes), at.notes
    assert "未达标" in " ".join(at.notes), at.notes
    assert "未达标" not in " ".join(below.notes), below.notes


def test_non_hdl_is_a_first_class_value_not_just_text():
    """non-HDL-C 必须出现在 ``inputs`` 里，**不能只活在文案里**。

    这条是那次事故的直接产物：值只在 f-string 里算，公式被改成加法后
    **416 项测试全过**。进了 inputs 之后，它与其它派生量一样可断言。
    """
    tc = next(o.value for o in base()["obs"]
              if o.analyte_id == "CHEM.总胆固醇" and o.year == "2026")
    hdl = next(o.value for o in base()["obs"]
               if o.analyte_id == "CHEM.高密度脂蛋白胆固醇" and o.year == "2026")
    s = one(run_obs({}), "LIPID.LDL_TARGET")
    assert s.inputs["non-HDL-C"] == round(tc - hdl, 2), s.inputs
    assert s.inputs["non-HDL-C 目标"] == round(s.inputs["目标值"] + 0.8, 2), s.inputs
    # 文案必须与 inputs 一致
    assert f"= {s.inputs['non-HDL-C']}" in " ".join(s.notes)


def test_non_hdl_note_value_is_tc_minus_hdl():
    """非 HDL-C 的**数值**必须是 ``TC − HDL-C``。

    这条是从一次事故里长出来的：一次被打断的变异测试把 ``-`` 改成了 ``+``
    并留在文件里，而**全部 416 项测试照样通过**（受损的只是 note 文案，
    真正的复合指标 `COMPOSITE.NON_HDL_C` 是另一处算的）。
    这个断言把 note 与公式绑在一起，同类改动再也藏不住。
    """
    tc = next(o.value for o in base()["obs"]
              if o.analyte_id == "CHEM.总胆固醇" and o.year == "2026")
    hdl = next(o.value for o in base()["obs"]
               if o.analyte_id == "CHEM.高密度脂蛋白胆固醇" and o.year == "2026")
    s = one(run_obs({}), "LIPID.LDL_TARGET")
    note = " ".join(s.notes)
    assert f"= {round(tc - hdl, 2)}" in note, note
    assert f"= {round(tc + hdl, 2)}" not in note, note


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
