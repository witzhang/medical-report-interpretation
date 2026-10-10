#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CKD 分期与代谢综合征的性别/病种分支。    python3 tests/test_ckd_metsyn.py

这一轮修的核心缺陷：**代谢综合征的三个标准原先对女性静默套用男性阈值**
（腰围写死 90cm、HDL 只在 `male` 为真时判定）。本案例是男性，所以这个错
一直没被数据暴露出来 —— 但它在"覆盖性别"这条要求下是硬伤。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import (Composer, G_STAGES, METSYN_CUTS,  # noqa: E402
                             _ckd_stage, evaluate, load_profile)
from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
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


def run(obs=None, metas=None, **profile_over):
    metas = metas or base()["metas"]
    pf = dict(load_profile(PATHS.profile))
    pf.update(profile_over)
    js = judge_all(obs or base()["obs"],
                   os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=metas)
    return evaluate(Composer(js, metas, pf))


def with_values(**overrides):
    out = []
    for o in base()["obs"]:
        if o.analyte_id in overrides:
            out.append(replace(o, value=overrides[o.analyte_id],
                               value_text=str(overrides[o.analyte_id])))
        else:
            out.append(o)
    return out


def one(res, rid, year="2026"):
    return next(x for x in res if x.rule_id == rid and x.year == year)


# =============================================================== CKD 分期

def test_ckd_g_stage_bands_match_kdigo():
    """KDIGO 2012 G 分期切点（下界含、上界不含）。"""
    assert G_STAGES == [(90, "G1"), (60, "G2"), (45, "G3a"),
                        (30, "G3b"), (15, "G4"), (0, "G5")]


def test_ckd_current_case_is_g1():
    r = run()
    for y in ("2024", "2025", "2026"):
        assert one(r, "RENAL.CKD_STAGE", y).inputs["G 分期"] == "G1", y


def test_ckd_a_stage_is_never_assumed():
    """**A 分期缺尿白蛋白/ACR → 不可评估**，绝不假定为 A1。

    这是本案例的真实缺口：报告有尿常规但无定量白蛋白。单凭 G 分期
    **不足以判定 CKD** —— 白蛋白尿是另一半。
    """
    for y in ("2024", "2025", "2026"):
        s = one(run(), "RENAL.CKD_STAGE", y)
        assert s.inputs["A 分期"].startswith("不可评估"), s.inputs
        assert "不足以判定 CKD" in " ".join(s.notes), s.notes


def test_ckd_stage_changes_with_creatinine():
    """肌酐升高 → G 分期下降，并提示与血脂指南「直接高危」联动。"""
    r = run(with_values(**{"CHEM.肌酐": 150.0}))
    s = one(r, "RENAL.CKD_STAGE")
    assert s.inputs["G 分期"] in ("G3a", "G3b"), s.inputs
    assert any("直接高危" in n for n in s.notes), s.notes


def test_ckd_missing_input_is_not_assessable():
    """缺肌酐 → ``not_assessable``，**不能默认正常**。"""
    s = one(run(with_values(**{"CHEM.肌酐": None})), "RENAL.CKD_STAGE")
    assert s.status == "not_assessable", s
    assert any("肌酐" in m or "年龄" in m for m in s.missing), s.missing


# =============================================================== 分性别阈值

def test_metsyn_thresholds_are_sex_specific():
    """三个标准的腰围/HDL 都必须有女值 —— 原先只有男值。"""
    for std in ("CDS2004", "GD2007", "IDF2005"):
        hdl = METSYN_CUTS[std]["hdl"]
        assert set(hdl) == {"男", "女"}, std
        assert hdl["女"] > hdl["男"], std          # 女性 HDL 切点更高
    assert METSYN_CUTS["IDF2005"]["wc"] == {"男": 90, "女": 80}
    assert METSYN_CUTS["GD2007"]["wc"] == {"男": 90, "女": 85}


def test_female_waist_80_triggers_idf_mandatory():
    """腰围 82cm：男性不达 IDF 必备条件，女性达（阈值 80）。"""
    metas = dict(base()["metas"])
    metas["2026"] = replace(metas["2026"], sex="女")
    obs = with_values(**{"VITAL.腰围": 82.0})
    r = run(obs=obs, metas=metas)
    s = one(r, "METSYN.IDF2005")
    assert s.inputs["腰围必备"] is True, s.text
    assert s.inputs["腰围阈值"] == 80

    r_m = run(obs=with_values(**{"VITAL.腰围": 82.0}))
    assert one(r_m, "METSYN.IDF2005").inputs["腰围必备"] is False


def test_female_hdl_threshold_differs():
    """HDL 1.15：男性**不低于** 1.03（不计入），女性低于 1.29（计入）。

    同一个数值在两种性别下落到不同结论 —— 这正是"对女性套用男性阈值"
    会出错的地方。
    """
    metas = dict(base()["metas"])
    metas["2026"] = replace(metas["2026"], sex="女")
    obs = with_values(**{"CHEM.高密度脂蛋白胆固醇": 1.15})
    m = one(run(obs=obs), "METSYN.GD2007")
    f = one(run(obs=obs, metas=metas), "METSYN.GD2007")
    assert "HDL男<1.03" not in m.inputs["已满足"], m.inputs
    assert "HDL女<1.29" in f.inputs["已满足"], f.inputs


def test_unknown_sex_is_not_assessable_not_male_default():
    """**性别未知 → 不可评估**，不默认取男性阈值（未知维度不算命中）。"""
    metas = dict(base()["metas"])
    metas["2026"] = replace(metas["2026"], sex=None)
    r = run(metas=metas)
    for rid in ("METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"):
        s = one(r, rid)
        assert s.status == "not_assessable", (rid, s.status)
        assert any("性别未知" in m for m in s.missing), (rid, s.missing)


# =============================================================== 治疗状态

def test_diagnosed_hypertension_counts_as_bp_component():
    """**已确诊高血压即计入血压组件**，即使当次血压正常（各标准原文含"或已诊断"）。"""
    obs = with_values(**{"VITAL.收缩压": 110.0, "VITAL.舒张压": 70.0})
    before = one(run(obs=obs), "METSYN.GD2007")
    assert "BP≥130/85" not in before.inputs["已满足"]
    after = one(run(obs=obs, hypertension_diagnosed=True), "METSYN.GD2007")
    assert "BP≥130/85" in after.inputs["已满足"], after.inputs


def test_diagnosed_diabetes_counts_as_glucose_component():
    obs = with_values(**{"CHEM.葡萄糖": 4.5})
    assert "FPG≥6.1" not in one(run(obs=obs), "METSYN.CDS2004").inputs["已满足"]
    s = one(run(obs=obs, diabetes=True), "METSYN.CDS2004")
    assert "FPG≥6.1" in s.inputs["已满足"], s.inputs


# =============================================================== 回归

def test_current_male_case_unchanged():
    """本案例（男性）三个标准的结果必须与改动前**逐条一致**。"""
    r = run()
    assert one(r, "METSYN.CDS2004", "2024").inputs["共满足"] == 1
    assert one(r, "METSYN.CDS2004", "2026").inputs["共满足"] == 1
    assert one(r, "METSYN.GD2007", "2024").inputs["共满足"] == 2
    assert one(r, "METSYN.GD2007", "2025").band == "满足"      # 2025 满足
    assert one(r, "METSYN.GD2007", "2026").inputs["共满足"] == 2
    for y in ("2024", "2025", "2026"):
        assert one(r, "METSYN.IDF2005", y).band == "不满足", y


def test_metsyn_records_sex_in_inputs():
    """性别必须留在 inputs 里，否则结论无法复核。"""
    for rid in ("METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"):
        assert one(run(), rid).inputs["性别"] == "男", rid


# ------------------------------------------------------------------ 主程序

# ---------------------------- 阈值边界与三标准全覆盖（由变异测试指出）
#
# `_metsyn` 是整个仓库存活变异体最多的一处（30 个），主题只有两个：
#   ① **阈值边界从没构造过** —— ``>=`` 改成 ``>`` 无人管
#   ② **确诊计入逻辑只测了 CDS 2004** —— 另两个标准的 ``or`` 没测
#
# 下面按标准逐条把边界补齐。

def _sat(rid, **vals):
    """返回某标准下「已满足」的条目集合。"""
    return one(run(with_values(**vals)), rid, "2026").inputs["已满足"]


def test_cds2004_threshold_boundaries():
    """CDS 2004 每个阈值：**恰好等于算命中**，差一点不算。"""
    # BMI≥25（默认 24 左右 → 先归零再看边界）
    assert "BMI≥25.0" in _sat("METSYN.CDS2004", **{"VITAL.体重指数": 25.0})
    assert "BMI≥25.0" not in _sat("METSYN.CDS2004", **{"VITAL.体重指数": 24.9})
    # FPG≥6.1
    assert "FPG≥6.1" in _sat("METSYN.CDS2004", **{"CHEM.葡萄糖": 6.1})
    assert "FPG≥6.1" not in _sat("METSYN.CDS2004", **{"CHEM.葡萄糖": 6.09})
    # BP≥140/90：收缩压与舒张压**各自**单独达标即可
    assert "BP≥140/90" in _sat("METSYN.CDS2004", **{"VITAL.收缩压": 140.0})
    assert "BP≥140/90" not in _sat("METSYN.CDS2004", **{"VITAL.收缩压": 139.9})
    assert "BP≥140/90" in _sat("METSYN.CDS2004", **{"VITAL.舒张压": 90.0})
    assert "BP≥140/90" not in _sat("METSYN.CDS2004", **{"VITAL.舒张压": 89.9})
    # HDL 男<0.9（严格小于）。
    # ⚠ CDS 2004 把 TG 与 HDL 合并成**一个**标签「TG≥1.7 或 HDL男<0.9」，
    # 所以只要 TG 达标，这个标签就恒在 —— **结论文字看不出是哪一项异常**。
    # 要隔离 HDL 边界，必须把 TG 压到正常。
    normal_tg = {"CHEM.甘油三酯": 1.0}
    assert "HDL男<0.9" in _sat("METSYN.CDS2004",
                              **{**normal_tg,
                                 "CHEM.高密度脂蛋白胆固醇": 0.89})
    assert "HDL男<0.9" not in _sat("METSYN.CDS2004",
                                  **{**normal_tg,
                                     "CHEM.高密度脂蛋白胆固醇": 0.9})
    # TG 边界同理：把 HDL 压到不低
    normal_hdl = {"CHEM.高密度脂蛋白胆固醇": 1.5}
    assert "TG≥1.7" in _sat("METSYN.CDS2004",
                           **{**normal_hdl, "CHEM.甘油三酯": 1.7})
    assert "TG≥1.7" not in _sat("METSYN.CDS2004",
                               **{**normal_hdl, "CHEM.甘油三酯": 1.69})


def test_gd2007_threshold_boundaries():
    """2007 指南版每个阈值：腰围/TG/HDL/BP/FPG。"""
    assert _sat("METSYN.GD2007", **{"VITAL.腰围": 90.1}).find("腰围男>90") >= 0
    assert "腰围男>90cm" not in _sat("METSYN.GD2007", **{"VITAL.腰围": 90.0})
    assert "TG≥1.7" in _sat("METSYN.GD2007", **{"CHEM.甘油三酯": 1.7})
    assert "HDL男<1.03" in _sat("METSYN.GD2007",
                               **{"CHEM.高密度脂蛋白胆固醇": 1.02})
    assert "HDL男<1.03" not in _sat("METSYN.GD2007",
                                   **{"CHEM.高密度脂蛋白胆固醇": 1.03})
    assert "BP≥130/85" in _sat("METSYN.GD2007", **{"VITAL.收缩压": 130.0})
    assert "BP≥130/85" not in _sat("METSYN.GD2007", **{"VITAL.收缩压": 129.9})
    assert "FPG≥5.6" in _sat("METSYN.GD2007", **{"CHEM.葡萄糖": 5.6})
    assert "FPG≥5.6" not in _sat("METSYN.GD2007", **{"CHEM.葡萄糖": 5.59})


def test_idf2005_threshold_boundaries():
    """IDF 2005：腰围是**必备条件**，且是 ``≥``（与 2007 版的 ``>`` 不同）。"""
    at = one(run(with_values(**{"VITAL.腰围": 90.0})), "METSYN.IDF2005", "2026")
    assert at.inputs["腰围必备"] is True, "腰围 90 应满足 IDF 必备条件（≥）"
    below = one(run(with_values(**{"VITAL.腰围": 89.9})), "METSYN.IDF2005", "2026")
    assert below.inputs["腰围必备"] is False


def test_treatment_aware_logic_in_all_three_standards():
    """**确诊计入**必须在三个标准里都成立 —— 原先只测了 CDS 2004。

    变异测试发现 ``hx_htn or sbp >= ...`` / ``hx_dm or fpg >= ...`` 那些
    ``or`` 改成 ``and`` 无人管：把血压与血糖都压到正常，再逐个打开确诊标记，
    三个标准都应当把对应条目算作满足。
    """
    normal_bp = {"VITAL.收缩压": 110.0, "VITAL.舒张压": 70.0}
    normal_fpg = {"CHEM.葡萄糖": 4.5}
    def _count(rid, obs):
        """三个标准各自的「满足项数」字段名不同（IDF 用「另满足」）。"""
        ins = one(run(obs), rid, "2026").inputs
        return ins.get("共满足", ins.get("另满足")), ins

    for obs, delta in ((None, 0),):
        pass
    # 血压：三个标准都应当因"已确诊"而**多算一项**
    for rid in ("METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"):
        before = one(run(with_values(**normal_bp)), rid, "2026").inputs
        after = one(run(with_values(**normal_bp), hypertension_diagnosed=True),
                    rid, "2026").inputs
        b = before.get("共满足", before.get("另满足"))
        a = after.get("共满足", after.get("另满足"))
        assert a == b + 1, (rid, "血压", b, a)
    # 血糖：同理
    for rid in ("METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"):
        before = one(run(with_values(**normal_fpg)), rid, "2026").inputs
        after = one(run(with_values(**normal_fpg), diabetes=True),
                    rid, "2026").inputs
        b = before.get("共满足", before.get("另满足"))
        a = after.get("共满足", after.get("另满足"))
        assert a == b + 1, (rid, "血糖", b, a)


def test_diagnosis_flag_makes_the_three_standards_differ():
    """确诊标记打开后，三个标准的满足**项数**应当各自增加 1。"""
    normal = {"VITAL.收缩压": 110.0, "VITAL.舒张压": 70.0}
    for rid in ("METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"):
        before = one(run(with_values(**normal)), rid, "2026").inputs
        after = one(run(with_values(**normal), hypertension_diagnosed=True),
                    rid, "2026").inputs
        b = before.get("共满足", before.get("另满足"))
        a = after.get("共满足", after.get("另满足"))
        assert a == b + 1, (rid, b, a)


# -------------------------- 由「输出快照分类」工具指出的真缺口
#
# `tools/triage_mutants.py` 对 compose.py 的 197 个变异体逐个注入、跑 8 组输入
# 比对输出：**65 个改变输出，其中 53 个存活 → 10 个是测试没覆盖的真缺口。**
# 下面补其中最要紧的几处。

def test_egfr_rounding_across_stage_boundary_is_flagged():
    """**取整跨过 CKD 分期边界时必须说明。**

    肌酐 136.5 → 计算 eGFR 59.76 → 属 **G3a**；按 NKF 规定取整报告为 60，
    属 **G2**。这个差别会改变降脂指南的"是否直接高危"判断，所以必须写出来。

    变异测试把 ``if rep is not None and g_rep != g_raw`` 的 ``and`` 改成 ``or``
    后输出变了却没人管 —— 这条注记从没被构造过（真实数据的 eGFR 是 94~116，
    离边界很远）。
    """
    scr = 136.5
    got = one(run(with_values(**{"CHEM.肌酐": scr})), "RENAL.EGFR_CKDEPI2021",
              "2026")
    assert got.inputs["报告值"] is not None, got.inputs
    note = " ".join(got.notes)
    assert "取整跨过了分期边界" in note, note
    assert "G3a" in note and "G2" in note, note
    # band 必须同时给出计算与报告两个分期
    assert "G3a" in got.band and "G2" in got.band, got.band


def test_no_stage_crossing_means_no_noise():
    """没跨边界时**不得**出现那条注记（反面用例）。

    只测"该出现时出现了"是不够的 —— 一个恒真的注记同样能通过那种测试。
    """
    got = one(run(), "RENAL.EGFR_CKDEPI2021", "2026")
    assert "取整跨过了分期边界" not in " ".join(got.notes), got.notes


def test_metsyn_empty_hit_list_shows_a_placeholder():
    """一项都没满足时，文案里必须显示「无」而不是空白。

    ``'、'.join(met) or '无'`` 的 ``or`` 改成 ``and`` 后：空列表 → ``'' and '无'``
    → ``''``，文案里就成了「（已满足：）」—— 读起来像**程序出错了**。
    """
    # 把五项全部压到正常
    clean = {"VITAL.体重指数": 22.0, "CHEM.葡萄糖": 4.5,
             "VITAL.收缩压": 110.0, "VITAL.舒张压": 70.0,
             "CHEM.甘油三酯": 1.0, "CHEM.高密度脂蛋白胆固醇": 1.6,
             "VITAL.腰围": 80.0}
    for rid, key in (("METSYN.CDS2004", "已满足"),
                     ("METSYN.GD2007", "已满足"),
                     ("METSYN.IDF2005", "另满足")):
        ins = one(run(with_values(**clean)), rid, "2026").inputs
        val = str(ins.get(key, ""))
        assert val not in ("", "None"), (rid, ins)
        if val.strip() == "":
            raise AssertionError(f"{rid} 的 {key} 是空的 —— 应显示「无」")


def test_chinapar_agreement_label_is_not_inverted():
    """「与查表结果**不一致**」的判定不能反。

    ``'' if risk_band == strat else '**不一致**'`` 改成 ``!=`` 后，
    **一致时说成不一致、不一致时说成一致** —— 而这是给读者判断
    「两个口径要不要互相印证」的关键一句。
    """
    import re
    got = one(run(), "LIPID.RISK_STRATIFICATION", "2026")
    note = " ".join(got.notes)
    if "旁证" not in note:
        return                      # 本案例没跑 China-PAR 旁证就跳过
    assert "表是人群平均风险" in note, note
    # 从注记里取出「模型给的档」与「查表给的档」，自己比一遍，
    # 再看文案的说法是否与之一致 —— 这样断言才可能失败。
    m = re.search(r"10 年风险 ([\d.]+)% → (\S+?)[；;]", note)
    assert m, note
    model_band = m.group(2)
    strat = got.band
    agree = (model_band == strat)
    says_disagree = "**不一致**" in note
    assert says_disagree == (not agree), (model_band, strat, note)


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
