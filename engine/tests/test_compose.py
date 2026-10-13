#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L5 组合与分层层的回归测试。    python3 tests/test_compose.py

金标准：旧管线 ``体检分析/data/combo_signals.csv``，以及本会话中实跑
``ascvd.py`` 得到的输出（2026-10-01 记于下）。
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, china_par_male, egfr_ckdepi2021, evaluate, fib4  # noqa: E402
from checkup.compose import load_profile  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all, year_from_name  # noqa: E402
from checkup.rules import RULES, RULES_BY_ID  # noqa: E402
from checkup.schema import DocumentMeta  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_legacy()
PATHS.require_profile()
SRC = PATHS.real_reports
LEGACY_COMBO = PATHS.legacy("combo_signals.csv")
CVI = os.path.join(BASE, "catalog", "cvi.json")
PROFILE = PATHS.profile

#: 本会话实跑旧 ascvd.py 得到的结果（2026-10-01）
LEGACY_CHINAPAR = {"2024": 0.5, "2025": 2.6, "2026": 1.4}

_derived = None


def derived():
    global _derived
    if _derived is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        js = judge_all(obs, CVI, catalog=cat)
        metas = {}
        for fn in sorted(os.listdir(SRC)):
            if fn.lower().endswith(".pdf"):
                y = year_from_name(fn) or "?"
                m, _, _, _ = extract_report(os.path.join(SRC, fn), y, TPL)
                metas[y] = m
        _derived = evaluate(Composer(js, metas, load_profile(PROFILE)))
    return _derived


def rows(rule_id: str):
    return sorted((d for d in derived() if d.rule_id == rule_id),
                  key=lambda x: x.year)


def one(rule_id: str, year: str):
    return next(d for d in derived() if d.rule_id == rule_id and d.year == year)


# --------------------------------------------------------------- 算法校准

def test_china_par_paper_example():
    """用论文样例校准实现：60岁 未治疗SBP130 TC210mg/dL HDL55mg/dL 腰围80
    不吸烟 糖尿病 北方城镇 无家族史 → 论文报告 11.0%。"""
    r = china_par_male(60, 130, 210 * 0.0259, 55 * 0.0259, 80,
                       smoke=False, dm=True, north=True, urban=True, fh=False)
    assert abs(r * 100 - 11.0) < 1.0, f"实得 {r*100:.1f}%，论文 11.0%"


def test_egfr_and_fib4_formulas():
    assert abs(egfr_ckdepi2021(95.0, 32) - 94.1) < 0.2
    assert abs(fib4(32, 20.0, 245.0, 22.0) - 0.56) < 0.01


# ------------------------------------------ 公式文本 vs 实现常量
#
# `rules.py` 是**数据模块**（引文、URL、DOI 占七成），变异杀死率只有 25% ——
# 那是对的。但其中 `formula` 是**人类可读的公式文本**，而真正的计算在
# `compose.py`：**两者会漂移**，这正是 `non-HDL-C` 事故的形状
# （文档说的和代码做的不一致）。所以不逐字断言文案，
# 而是断言**文案里的关键常量与实现一致**。

def test_ldl_targets_match_the_table7_quote():
    """`LDL_TARGETS` 的数值必须能在表7 的逐字引文里找到。"""
    from checkup.compose import LDL_TARGETS
    from checkup.rules import P_LIPID_TABLE7
    q = P_LIPID_TABLE7.quote_verbatim
    for band, (target, need_drop) in LDL_TARGETS.items():
        assert f"<{target}" in q, f"{band} 的目标 {target} 不在表7 引文里"
        if need_drop:
            assert ">50%" in q or "降幅>50%" in q, band


def test_metsyn_cuts_match_the_rule_formulas():
    """三个代谢综合征标准的阈值必须出现在各自规则的 `formula` 文本里。"""
    from checkup.compose import METSYN_CUTS
    from checkup.rules import RULES
    formulas = {r.rule_id: r.formula for r in RULES}
    for std, cuts in METSYN_CUTS.items():
        f = formulas.get(f"METSYN.{std}")
        assert f, std
        assert str(cuts["fpg"]) in f, (std, "FPG", cuts["fpg"])
        assert str(cuts["tg"]) in f, (std, "TG", cuts["tg"])
        for sex in ("男", "女"):
            assert str(cuts["hdl"][sex]) in f, (std, "HDL", sex)
        if "wc" in cuts:
            for sex in ("男", "女"):
                assert str(cuts["wc"][sex]) in f, (std, "腰围", sex)


def test_ckd_g_stages_match_the_kdigo_quote():
    """KDIGO 的 G 分期切点必须与引文一致。"""
    from checkup.compose import G_STAGES
    from checkup.rules import P_KDIGO2012
    q = P_KDIGO2012.quote_verbatim
    for lo, name in G_STAGES:
        if name == "G5":
            continue                     # G5 是"<15"，下界 0 只用于区间判断
        assert name in q, name
        assert str(lo) in q, (name, lo)


def test_egfr_report_bounds_match_the_nkf_quote():
    """NKF 规定的报告范围必须与引文一致。"""
    from checkup.compose import EGFR_REPORT_MAX, EGFR_REPORT_MIN
    from checkup.rules import P_CKDEPI
    q = P_CKDEPI.quote_verbatim
    assert str(EGFR_REPORT_MIN) in q and str(EGFR_REPORT_MAX) in q


def test_fib4_hcv_cutoffs_match_the_quote():
    """FIB-4 的 **HCV** 切点必须在逐字引文里；**NAFLD** 那套必须已被登记。

    这条测试第一次跑就抓到了真问题：代码用 NAFLD 切点（1.30/2.67），
    而已核实的出处只覆盖 HCV 队列（1.45/3.25）—— **那套没有引文支撑**。
    处理方式不是放宽测试，而是**把它登记为开放项**。
    """
    from checkup.compose import FIB4_CUTOFFS
    from checkup.openitems import OPEN_ITEMS
    from checkup.rules import P_FIB4_UNITS
    q = P_FIB4_UNITS.quote_verbatim
    lo, hi = FIB4_CUTOFFS["HCV"]
    assert str(lo) in q and str(hi) in q, (lo, hi)
    ids = {i.item_id for i in OPEN_ITEMS}
    assert "SRC.FIB4_NAFLD" in ids, \
        "NAFLD 切点没有引文支撑，必须登记为开放项（这条测试就是为此而写）"
    assert "NAFLD" in P_FIB4_UNITS.note, "出处说明应指出它不覆盖 NAFLD 那套"


def test_non_hdl_offset_matches_the_table7_note():
    """非 HDL-C 目标 = LDL-C 目标 + 0.8 —— 偏移量必须与引文一致。"""
    from checkup.rules import P_LIPID_TABLE7
    assert "0.8" in P_LIPID_TABLE7.quote_verbatim


# --------------------------------------------------------------- 复合指标

def test_composites_match_legacy():
    """四个复合指标必须与旧 combo_signals.csv 一致。"""
    want = {}
    with open(LEGACY_COMBO, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            want[r["指标"]] = [float(r["2024"]), float(r["2025"]), float(r["2026"])]
    cases = [("COMPOSITE.NON_HDL_C", "non-HDL-C(mmol/L)", 0.01),
             ("COMPOSITE.AIP", "AIP", 0.001),
             ("RENAL.EGFR_CKDEPI2021", "eGFR(CKD-EPI 2021, 自算)", 0.1),
             ("HEPATIC.FIB4", "FIB-4", 0.01)]
    for rid, label, tol in cases:
        got = [one(rid, y).value for y in ("2024", "2025", "2026")]
        exp = want[label]
        assert all(abs(g - e) < tol for g, e in zip(got, exp)), \
            f"{label}: 实得 {got}，旧管线 {exp}"


def test_egfr_notes_missing_albumin():
    """eGFR 正常也要说明 CKD 无法完整分期（缺尿微量白蛋白/ACR）。"""
    assert any("ACR" in n for n in one("RENAL.EGFR_CKDEPI2021", "2026").notes)


def test_aip_has_no_cutoff_caveat():
    """AIP 无公认切点——只看方向，不得据此下风险结论。"""
    assert any("无公认切点" in n for n in one("COMPOSITE.AIP", "2026").notes)


# --------------------------------------------------------------- 多版本并存

def test_metabolic_syndrome_three_versions_all_present():
    """三个版本的代谢综合征标准必须**并列输出**，禁止静默择一。

    本案例 2025 年在 2007 血脂指南版下满足、在 CDS 2004 版下不满足——
    版本不同结论就不同，必须让用户看见。
    """
    ids = {"METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005"}
    assert ids <= {d.rule_id for d in derived()}
    for rid in ids:
        assert len(rows(rid)) == 3, rid


def test_version_disagreement_is_visible():
    """2025：2007 版满足 / CDS 2004 不满足 —— 同一个人的同一年。"""
    assert one("METSYN.GD2007", "2025").band == "满足"
    assert one("METSYN.CDS2004", "2025").band == "不满足"
    assert one("METSYN.IDF2005", "2025").band == "不满足"   # 腰围未达必备


def test_idf_waist_is_mandatory():
    """IDF 2005 的腰围是**必备条件**，即使另满足 3 项也不成立。"""
    d = one("METSYN.IDF2005", "2025")
    assert d.inputs["腰围必备"] is False
    assert d.inputs["另满足"] >= 2
    assert d.band == "不满足"


# --------------------------------------------------------------- 风险分层

def test_china_par_matches_legacy():
    for y, exp in LEGACY_CHINAPAR.items():
        assert abs(one("ASCVD.CHINAPAR", y).value - exp) < 0.1, y


def test_china_par_age_extrapolation_warned():
    """年龄 <35 超出模型验证范围 35–74，必须显式警告。"""
    assert any("超出模型验证范围" in n for n in one("ASCVD.CHINAPAR", "2024").notes)
    assert any("超出模型验证范围" in n for n in one("ASCVD.CHINAPAR", "2025").notes)
    assert not any("超出模型验证范围" in n
                   for n in one("ASCVD.CHINAPAR", "2026").notes)


def test_family_history_unknown_is_declared():
    """家族史未知要写明按『无』计算，不能当已知。"""
    assert any("家族史未知" in n for n in one("ASCVD.CHINAPAR", "2026").notes)


def test_stratification_and_ldl_target():
    """危险因素 2 个（吸烟 + 低HDL-C）· 无 ASCVD · 10 年风险低 → 低危，目标 <3.4。"""
    for y in ("2024", "2025", "2026"):
        s = one("LIPID.RISK_STRATIFICATION", y)
        assert s.band == "低危", (y, s.text)
        assert s.inputs["危险因素个数"] == 2, s.inputs
        assert s.inputs["危险因素"] == ["吸烟", "低HDL-C（0.94）"], s.inputs
        t = one("LIPID.LDL_TARGET", y)
        assert "达标" in t.band and "未达标" not in t.band, (y, t.band)


def test_stratification_records_the_hypertension_column_used():
    """**确诊高血压要换一列** —— 21 格表已取得并照表查，故须记明用的是哪一格。

    高血压状态未知时会直接判不可评估（见 test_lipid_table），这里只看记录完整。
    """
    d = one("LIPID.RISK_STRATIFICATION", "2026")
    assert d.inputs["高血压"] is False
    assert d.inputs["21格行"] == "2" and d.inputs["21格列"] == "C2", d.inputs
    assert any("高血压状态" in n for n in d.notes), d.notes


# --------------------------------------------------------------- 缺项不推断

def test_missing_input_yields_not_assessable():
    """依赖项缺失时落到 not_assessable，**严禁**按其他项正常推断为正常。"""
    metas = {"2026": DocumentMeta(doc_id="x", org_id="NX-001",
                                  exam_date="2026-08-20", page_count=1,
                                  age=None, sex="男")}
    got = evaluate(Composer([], metas, {}))
    for d in got:
        assert d.status in ("not_assessable", "not_applicable"), (d.rule_id, d.status)
        if d.status == "not_assessable":
            assert d.missing, d.rule_id


def test_female_is_not_applicable_for_china_par_male():
    """本引擎只实现了男性方程 —— 女性应判 not_applicable 而不是硬套。"""
    metas = {"2026": DocumentMeta(doc_id="x", org_id="NX-001",
                                  exam_date="2026-08-20", page_count=1,
                                  age=50, sex="女")}
    d = next(x for x in evaluate(Composer([], metas, {}))
             if x.rule_id == "ASCVD.CHINAPAR")
    assert d.status == "not_applicable", d.status


# --------------------------------------------------------------- 规则库

def test_every_rule_has_provenance():
    """每条规则必须带出处；出处必须有类型、标题、定位。"""
    for r in RULES:
        assert r.provenance, f"{r.rule_id} 无出处"
        for p in r.provenance:
            assert p.source_type and p.source_title, r.rule_id
            assert p.accessed_at, f"{r.rule_id} 缺抓取日期"
            if not p.verified:
                assert p.note, f"{r.rule_id} 未验证却无说明"


def test_unverified_provenance_declares_why():
    """没核到原文的出处必须标 verified=False 并说明——不得伪装成逐字引用。"""
    for r in RULES:
        for p in r.provenance:
            if not p.verified:
                assert p.quote_verbatim == "", \
                    f"{r.rule_id}: 未验证却填了逐字引文"
                assert p.note, r.rule_id


def test_verified_provenance_has_verbatim_quote():
    """标了 verified 的必须真有逐字引文。"""
    for r in RULES:
        for p in r.provenance:
            if p.verified:
                assert len(p.quote_verbatim) > 20, f"{r.rule_id} 逐字引文过短"


def test_rules_declare_missing_policy():
    """每条规则必须声明缺项策略（block / downgrade / partial）。"""
    for r in RULES:
        assert r.missing_policy in ("block", "downgrade", "partial"), r.rule_id
        assert r.inputs, f"{r.rule_id} 未声明依赖输入"


def test_no_rule_is_diagnostic():
    """输出层只允许信息性/建议性/红旗，**严禁 diagnostic**（框架 L3 措辞分层）。"""
    for r in RULES:
        assert r.assertion_type in ("informational", "advisory", "red_flag"), \
            f"{r.rule_id} 断言类型越界: {r.assertion_type}"


# --------------------------------------------------------------- 无手工字典

def test_no_hand_maintained_value_dict_in_compose():
    """L5 **不得**再有 combo.py/ascvd.py 那种手工维护的数值字典。

    旧实现把年龄/血压/TC/HDL/腰围手抄成 dict，换年换人都要改代码。
    这里断言 compose.py 里不出现成对的年份→数值字面量。
    """
    import re as _re
    import checkup.compose as m
    src = open(m.__file__, encoding="utf-8").read()
    # 形如 {"2024": 32, "2025": 33, ...} 的手抄字典
    bad = _re.findall(r'["\']20\d\d["\']\s*:\s*\d', src)
    assert not bad, f"compose.py 里仍有年份→数值的手抄字典: {bad[:5]}"
    # 人的属性只能来自 profile
    assert "profile" in src


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
