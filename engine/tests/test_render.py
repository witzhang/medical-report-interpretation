#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L7 输出层（红旗 / 措辞分层 / 三视图）的回归测试。

    python3 tests/test_render.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.conclude import synthesize  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all, year_from_name  # noqa: E402
from checkup.redflags import check as redflag_check, load_rules  # noqa: E402
from checkup.render import (classify_wording, lint, render_all,  # noqa: E402
                            render_clinical, render_patient)
from checkup.schema import Conclusion, NormalizedObservation  # noqa: E402
from checkup.validate import validate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_profile()
SRC = PATHS.real_reports
_ctx = None


def ctx():
    global _ctx
    if _ctx is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=cat)
        metas, find, narr = {}, [], []
        for fn in sorted(os.listdir(SRC)):
            if fn.lower().endswith(".pdf"):
                y = year_from_name(fn) or "?"
                m, n, _, f = extract_report(os.path.join(SRC, fn), y, TPL)
                metas[y], narr, find = m, narr + n, find + f
        prof = load_profile(PATHS.profile)
        derived = evaluate(Composer(js, metas, prof))
        cons = synthesize(js, derived, validation=validate(obs),
                          findings=find, narratives=narr, profile=prof)
        views = render_all(cons, js, derived, redflag_check(obs, profile=prof),
                           validation=validate(obs))
        _ctx = dict(obs=obs, js=js, derived=derived, cons=cons,
                    views=views, profile=prof)
    return _ctx


def _obs(aid: str, year: str, value: float, unit: str = "",
         low=None, high=None) -> NormalizedObservation:
    return NormalizedObservation(
        year=year, page_no=1, section="测试", item_raw=aid.split(".")[-1],
        occurrence=1, analyte_id=aid, value=value, value_type="numeric",
        unit=unit, ref_low=low, ref_high=high)


# --------------------------------------------------------------- 红旗

def test_no_red_flag_in_this_dataset():
    """本案例三年数据不该命中任何 A 类阈值。"""
    assert redflag_check(ctx()["obs"], profile=ctx()["profile"]) == []


def test_red_flag_thresholds_fire():
    """构造越界值，A 类阈值必须命中。"""
    cases = [
        ("BLD.血红蛋白浓度", 55, "g/L"),
        ("BLD.血小板", 25, "10^9/L"),
        ("BLD.血小板", 1200, "10^9/L"),
        ("BLD.白细胞", 0.8, "10^9/L"),
        ("BLD.中性粒细胞绝对值", 0.3, "10^9/L"),
        ("CHEM.肌酐", 800, "umol/L"),
        ("CHEM.葡萄糖", 2.0, "mmol/L"),
    ]
    for aid, v, u in cases:
        got = redflag_check([_obs(aid, "2026", v, u)], profile={})
        assert got, f"{aid}={v} 未命中红旗"


def test_red_flag_alt_is_relative_to_that_years_upper_limit():
    """ALT ≥15 倍 —— 相对**该年报告自己的**上限，不是全局常量。

    共识自陈「受各实验室检测系统不同影响，医学决定水平不同」。
    """
    # 上限 50 → 阈值 750
    assert not redflag_check([_obs("CHEM.丙氨酸氨基转移酶", "2026", 700, "U/L",
                                   high=50.0)], profile={})
    assert redflag_check([_obs("CHEM.丙氨酸氨基转移酶", "2026", 800, "U/L",
                               high=50.0)], profile={})
    # 换一家机构上限 30 → 阈值 450
    assert redflag_check([_obs("CHEM.丙氨酸氨基转移酶", "2026", 500, "U/L",
                               high=30.0)], profile={})


def test_red_flag_fpg_depends_on_diabetes_status():
    """FPG ≤2.8（无糖尿病史）/ ≤3.9（糖尿病史）——阈值随人群变。"""
    o = [_obs("CHEM.葡萄糖", "2026", 3.5, "mmol/L")]
    assert redflag_check(o, profile={"diabetes": False}) == []
    assert redflag_check(o, profile={"diabetes": True})


def test_blood_pressure_is_or_not_and():
    """共识原文是「收缩压 ≥180 和/或 舒张压 ≥110 伴急性症状」——是 **OR**。

    只看收缩压会漏掉「仅舒张压 ≥110」的情形。
    """
    both = [_obs("VITAL.收缩压", "2026", 185, "mmHg"),
            _obs("VITAL.舒张压", "2026", 115, "mmHg")]
    assert redflag_check(both, profile={})
    only_sys = [_obs("VITAL.收缩压", "2026", 185, "mmHg"),
                _obs("VITAL.舒张压", "2026", 95, "mmHg")]
    assert redflag_check(only_sys, profile={}), "仅收缩压达标也应命中"
    only_dia = [_obs("VITAL.收缩压", "2026", 150, "mmHg"),
                _obs("VITAL.舒张压", "2026", 115, "mmHg")]
    assert redflag_check(only_dia, profile={}), "仅舒张压达标也应命中"
    normal = [_obs("VITAL.收缩压", "2026", 139, "mmHg"),
              _obs("VITAL.舒张压", "2026", 84, "mmHg")]
    assert not redflag_check(normal, profile={})


def test_red_flag_needs_human_when_context_unavailable():
    """读不到症状/复测值时必须标 needs_human，不能装作已经判准。"""
    hits = redflag_check([_obs("VITAL.收缩压", "2026", 185, "mmHg"),
                          _obs("VITAL.舒张压", "2026", 115, "mmHg")], profile={})
    assert hits and hits[0].needs_human


def test_red_flag_rules_are_overridable():
    """A 类阈值不能是硬编码全局常量 —— 必须支持机构覆盖。"""
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump({"a_class": [{"rule_id": "X", "analyte_id": "CHEM.肌酐",
                                "op": "ge", "value": 200, "unit": "umol/L",
                                "label": "机构自定义"}]}, f)
        path = f.name
    try:
        rules = load_rules(path)
        assert len(rules) == 1 and rules[0].value == 200
        assert redflag_check([_obs("CHEM.肌酐", "2026", 250, "umol/L")],
                             rules=rules, profile={})
    finally:
        os.unlink(path)


# --------------------------------------------------------------- 措辞分层

def test_wording_classification():
    assert classify_wording("本次检测 LDL-C 3.12 mmol/L，报告参考区间 <3.4") == "L1"
    assert classify_wording("依据指南，建议携带本报告至心内科评估") == "L2"
    assert classify_wording("您患有高脂血症") == "L3"
    assert classify_wording("确诊为糖尿病") == "L3"
    assert classify_wording("建议服用阿托伐他汀 20mg") == "L3"


def test_generated_conclusions_have_no_diagnostic_wording():
    """本引擎产出的结论**不得**出现诊断性措辞（L3）。"""
    bad = lint(ctx()["cons"])
    assert not bad, bad


def test_lint_catches_injected_diagnostic():
    """注入一条越界结论，lint 必须抓到。"""
    c = Conclusion(conclusion_id="X", cls="confirmed", text="您患有高脂血症")
    assert lint([c])


def test_no_conclusion_is_diagnostic_assertion():
    for c in ctx()["cons"]:
        assert c.assertion_type != "diagnostic"


# --------------------------------------------------------------- 三视图

def test_three_views_generated():
    v = ctx()["views"]
    assert set(v) == {"clinical", "patient", "audit"}
    for name, text in v.items():
        assert text.strip(), name


def test_patient_view_carries_disclaimer():
    """患者版必须带免责声明：不是诊断，治疗由医师判断。"""
    t = ctx()["views"]["patient"]
    assert "不是诊断" in t
    assert "临床医师" in t


def test_patient_view_has_no_diagnostic_wording():
    assert classify_wording(ctx()["views"]["patient"]) in ("L1", "L2")


def test_clinical_view_lists_abnormal_and_beyond_noise():
    t = ctx()["views"]["clinical"]
    assert "超出参考区间" in t
    assert "变化超出噪声" in t
    assert "甘油三酯" in t and "肌酸激酶" in t


def test_audit_view_marks_verified_vs_unverified_provenance():
    """技术版必须区分「逐字」与「仅题录·未核到原文」——不得伪装成逐字引用。"""
    t = ctx()["views"]["audit"]
    assert "逐字" in t
    assert "未核到原文" in t


def test_audit_view_has_rule_and_evidence():
    t = ctx()["views"]["audit"]
    assert "CHINAPAR" in t or "CHINA_PAR" in t or "ASCVD.CHINAPAR" in t
    assert "证据" in t


def test_patient_view_prioritizes_red_flag():
    """命中红旗时，患者版必须把它放在最前，且不得被软化。"""
    red = redflag_check([_obs("CHEM.肌酐", "2026", 900, "umol/L")], profile={})
    t = render_patient([], red)
    assert "请立即就医" in t
    assert t.index("请立即就医") < (t.index("已确认") if "已确认" in t else len(t))
    assert "不要等待" in t
    assert "仅供参考" not in t


def test_clinical_view_shows_red_flag_section_when_hit():
    red = redflag_check([_obs("CHEM.肌酐", "2026", 900, "umol/L")], profile={})
    t = render_clinical([], [], red)
    assert "危急值" in t and "需立即处理" in t


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
