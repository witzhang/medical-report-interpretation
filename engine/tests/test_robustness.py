#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鲁棒性扰动测试：把输入故意改坏，看守卫会不会响（框架 D21）。

判据不是"能不能跑通"，而是：

    **错误必须被拦下或降级并明说，绝不能静默接受。**

在没有第二家机构数据的情况下，这是对"覆盖各种情况"能做的最近似检验。
    python3 tests/test_robustness.py
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup import perturb as P  # noqa: E402
from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.judge import judge_all, judge_change, judge_position  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all, year_from_name  # noqa: E402
from checkup.schema import Change, Finding  # noqa: E402
from checkup.validate import (check_coverage, check_delta,  # noqa: E402
                              check_duplicates, check_internal_identity, validate)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_profile()
SRC = PATHS.real_reports
ORG = "NX-001"

_base = None


def base():
    """基线：未被扰动的三年观测 + 元信息 + 目录。"""
    global _base
    if _base is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        metas, narr, find = {}, [], []
        for fn in sorted(os.listdir(SRC)):
            if fn.lower().endswith(".pdf"):
                y = year_from_name(fn) or "?"
                m, n, _, f = extract_report(os.path.join(SRC, fn), y, TPL)
                metas[y], narr, find = m, narr + n, find + f
        _base = dict(obs=obs, metas=metas, narr=narr, find=find, cat=cat)
    return _base


def profile():
    return load_profile(PATHS.profile)


def jsons(obs):
    """观测 → 判定对象（Composer 要的是 Judgement，不是 Observation）。"""
    return list(judge_position(obs).values())


# ---------------------------------------------------------------- 单位

def test_equivalent_unit_change_is_not_incomparable():
    """``10^9/L`` → ``10^3/μL`` 是**等价**，不该被当成单位不可比。

    换机构时最常见的不是真换算，而是同一个单位的另一种写法。
    """
    obs = P.replace_unit(base()["obs"], "BLD.白细胞", "10^3/μL")
    j = judge_change(obs, {})["BLD.白细胞"]
    assert not any("拒绝判趋势" in n for n in j.notes), j.notes


def test_incomparable_unit_change_refuses_trend():
    """``10^9/L`` → ``10^12/L`` 真的不同类 → **必须拒绝比较**。

    独立佐证：Vital 的模块原话——*an absent delta is honest, a wrong one is not.*
    """
    obs = P.replace_unit(base()["obs"], "BLD.白细胞", "10^12/L", year="2026")
    j = judge_change(obs, {})["BLD.白细胞"]
    assert any("拒绝判趋势" in n for n in j.notes), j.notes
    assert not j.change, f"拒绝判趋势时不该给出判定: {j.change}"


# ---------------------------------------------------------------- 数值

def test_value_scale_error_caught_by_delta_check():
    """小数点错位（肌酐 76 → 760）必须被差值检查抓住。"""
    obs = P.scale_value(base()["obs"], "CHEM.肌酐", "2026", 10)
    c = check_delta(obs)
    assert c.level == "warn", c.detail
    assert any("肌酐" in i for i in c.items), c.items


def test_non_numeric_value_is_undetermined_not_normal():
    """值不可解析 → 该年判 ``undetermined``，**不能默认正常**。"""
    # 保留 value_type=numeric 但值解析不出来 —— 这才是"值不可解析"
    obs = [replace(o, value=None, value_text="<未检出>", flag=None)
           if (o.analyte_id == "CHEM.甘油三酯" and o.year == "2026") else o
           for o in base()["obs"]]
    pos = judge_position(obs)["CHEM.甘油三酯"]
    assert pos.position["2026"] == Finding.UNDETERMINED.value, pos.position
    assert pos.position["2024"] == Finding.ABNORMAL.value, "其他年份不该被牵连"


def test_missing_ref_range_is_not_assessable():
    """没有参考区间就判 ``not_assessable`` —— 报告不给区间 ≠ 正常。"""
    obs = P.drop_ref_range(base()["obs"], "CHEM.甘油三酯")
    pos = judge_position(obs)["CHEM.甘油三酯"]
    assert all(v == Finding.NOT_ASSESSABLE.value for v in pos.position.values()), \
        pos.position


# ---------------------------------------------------------------- 结构

def test_dropped_section_caught_by_coverage():
    """某年整段漏抽（血常规 24 项）必须被覆盖率检查发现。"""
    obs = P.drop_section(base()["obs"], "血常规", "2026")
    c = check_coverage(obs)
    assert c.level == "warn", c.detail
    assert "2026" in c.detail


def test_unknown_section_name_is_unmapped_not_merged():
    """未登记的段名不能"看着像就合并"。

    中文检验项目名高度相似（血液检查 / 血常规 / 血液细胞分析），
    模糊匹配会把不同机构的不同项目悄悄并成一条。
    """
    cat = base()["cat"]
    res = cat.resolve(ORG, "血液检查", "白细胞", "numeric", "10^9/L")
    assert res.analyte_id == UNMAPPED, f"未登记段名被猜成了 {res.analyte_id}"
    # 而登记过的段名仍然命中
    assert cat.resolve(ORG, "血常规", "白细胞", "numeric", "10^9/L").analyte_id \
        == "BLD.白细胞"


def test_unknown_analyte_name_is_unmapped_not_guessed():
    """未登记的项目名同样不猜。"""
    cat = base()["cat"]
    assert cat.resolve(ORG, "血常规", "白细胞计数（新）", "numeric", "").analyte_id \
        == UNMAPPED


# ---------------------------------------------------------------- 恒等式

def test_swapped_values_caught_by_identity():
    """直接胆红素与间接胆红素数值对调 —— 只有内部恒等式能抓住。

    这是**唯一能证明抽取准确**的确定性手段：三个值来自同一页不同列。
    """
    clean = check_internal_identity(base()["obs"])
    assert clean.level == "pass", clean.detail

    # 直接胆红素 ↔ 间接胆红素互换**和不变**，等于没扰动；
    # 必须拿总胆红素去换其中一项才会破坏恒等式。
    obs = P.swap_values(base()["obs"], "CHEM.总胆红素",
                        "CHEM.直接胆红素", "2026")
    c = check_internal_identity(obs)
    assert c.level == "warn", c.detail
    assert any("2026" in i for i in c.items), c.items


# ---------------------------------------------------------------- 组合层

def test_missing_age_makes_rules_not_assessable():
    """抹掉年龄 → eGFR / FIB-4 / China-PAR 全部落到 ``not_assessable``。

    **不得**按其他项正常推断为正常（框架 §三 G6）。
    """
    metas = P.drop_age(base()["metas"])
    got = evaluate(Composer(jsons(base()["obs"]), metas, profile()))
    for rid in ("RENAL.EGFR_CKDEPI2021", "HEPATIC.FIB4", "ASCVD.CHINAPAR"):
        rows = [d for d in got if d.rule_id == rid]
        assert rows and all(d.status == "not_assessable" for d in rows), \
            (rid, [(d.year, d.status) for d in rows])


def test_female_makes_china_par_not_applicable_not_assessable():
    """性别改为女 → China-PAR 判 ``not_applicable``，不是 ``not_assessable``。

    人群不适用 ≠ 缺输入。只实现了男性方程，不能硬套。
    """
    metas = P.flip_sex(base()["metas"])
    got = evaluate(Composer(jsons(base()["obs"]), metas, profile()))
    rows = [d for d in got if d.rule_id == "ASCVD.CHINAPAR"]
    assert rows and all(d.status == "not_applicable" for d in rows), \
        [(d.year, d.status) for d in rows]


def test_female_still_gets_the_other_rules():
    """女性受检者不该因为 China-PAR 不适用就丢掉其他规则。"""
    metas = P.flip_sex(base()["metas"])
    got = evaluate(Composer(jsons(base()["obs"]), metas, profile()))
    assert any(d.rule_id == "RENAL.EGFR_CKDEPI2021" and d.status == "ok" for d in got)


# ---------------------------------------------------------------- 重复

def test_duplicate_observation_is_flagged():
    """同一 (指标, 年份) 出现两条且**值不同** → 必须拦下。

    判定层是 ``series[year] = value`` 的写法，重复会让后一条**静默覆盖**前一条。
    这是写这套扰动测试时才发现的缺陷。
    """
    obs = P.duplicate_item(base()["obs"], "CHEM.肌酐", "2026", "肌酐", 200.0)
    c = check_duplicates(obs)
    assert c.level == "warn", c.detail
    assert any("静默覆盖" in i for i in c.items), c.items


def test_clean_data_has_no_duplicates():
    assert check_duplicates(base()["obs"]).level == "pass"


# ---------------------------------------------------------------- 整体

def test_clean_data_validation_level_is_warn_not_fatal():
    """干净数据不该被误判成 fatal —— 守卫过严会把正常报告拒之门外。"""
    assert validate(base()["obs"]).level == "warn"


def test_perturbations_do_not_crash_the_pipeline():
    """任何一类扰动都不该让流水线抛异常 —— 只能降级，不能崩。"""
    obs = base()["obs"]
    perturbations = [
        P.replace_unit(obs, "BLD.白细胞", "10^12/L"),
        P.scale_value(obs, "CHEM.肌酐", "2026", 10),
        P.blank_value(obs, "CHEM.甘油三酯", "2026"),
        P.drop_section(obs, "血常规", "2026"),
        P.drop_ref_range(obs, "CHEM.甘油三酯"),
        P.swap_values(obs, "CHEM.总胆红素", "CHEM.直接胆红素", "2026"),
        P.duplicate_item(obs, "CHEM.肌酐", "2026", "肌酐", 200.0),
    ]
    for i, o in enumerate(perturbations):
        rep = validate(o)
        assert rep.level in ("pass", "warn", "fatal"), i
        judge_position(o)
        judge_change(o, {})


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
