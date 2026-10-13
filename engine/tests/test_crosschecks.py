#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立算例交叉核对的回归测试。    python3 tests/test_crosschecks.py

`Provenance.verified` 只能表达"原文核没核到"，表达不了"**算得对不对**"。
本测试补这一层：偏差必须**已知、有界、且写明后果**。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import china_par_male  # noqa: E402
from checkup.crosschecks import (CROSS_CHECKS, RISK_CUTOFFS,  # noqa: E402
                                 boundary_window, by_rule,
                                 describe_boundary)
from checkup.rules import RULES  # noqa: E402


def _male_value(c) -> float:
    i = c.inputs
    return china_par_male(i["age"], i["sbp"], i["tc"], i["hdl"], i["wc"],
                          smoke=i["smoke"], dm=i["dm"], north=i["north"],
                          urban=i["urban"], fh=i["fh"],
                          treated=i["treated"]) * 100


# --------------------------------------------------------------- 元数据

def test_every_crosscheck_declares_source_kind_and_boundary():
    """每条算例必须标明来源层级与"偏差会不会改变结论"。"""
    for c in CROSS_CHECKS:
        assert c.source_kind in ("primary", "secondary", "tertiary"), c.source
        assert c.boundary_note, c.rule_id
        assert c.expected_text, c.rule_id
        assert c.tolerance > 0


def test_crosschecks_reference_existing_rules():
    """算例引用的规则必须真的存在 —— 否则核对是空转。"""
    ids = {r.rule_id for r in RULES}
    for c in CROSS_CHECKS:
        assert c.rule_id in ids, c.rule_id


# --------------------------------------------------------------- 数值

def test_male_worked_example_within_tolerance():
    """男性算例：偏差必须在声明容差内。"""
    c = next(x for x in CROSS_CHECKS if x.inputs.get("sex") == "男")
    got = _male_value(c)
    dev = abs(got - c.expected_value)
    assert dev <= c.tolerance, f"偏差 {dev:.2f}pp 超出容差 {c.tolerance}pp"


def test_male_deviation_is_actually_recorded_not_hidden():
    """偏差**确实存在**（0.3pp），不许悄悄当成 0。

    把实际偏差写进测试，是为了让"我们与他人不一致"这件事无法被忽略：
    哪天系数改了，这条会立刻变。
    """
    c = next(x for x in CROSS_CHECKS if x.inputs.get("sex") == "男")
    dev = round(abs(_male_value(c) - c.expected_value), 2)
    assert dev == 0.3, f"偏差变成 {dev}pp 了 —— 系数或被核对源变了，需重新评估"


def test_female_example_is_a_recorded_coverage_gap():
    """女性算例：我们**只有男性方程** → not_applicable。这是覆盖缺口，不是数值偏差。"""
    c = next(x for x in CROSS_CHECKS if x.inputs.get("sex") == "女")
    assert c.crosses_decision_boundary is True
    assert "not_applicable" in c.note
    # 引擎对女性返回 not_applicable（见 test_compose 的同类断言）
    assert boundary_window(c.inputs, "age", (40, 90), c.tolerance) == []


# --------------------------------------------------------------- 后果

def test_deviation_does_flip_stratification_near_cutoffs():
    """**核心结论**：0.3pp 的偏差足以翻转切点附近人群的分层。

    这不是"误差很小可以忽略"——它有一个可计算的后果范围。
    """
    c = next(x for x in CROSS_CHECKS if x.inputs.get("sex") == "男")
    win = boundary_window(c.inputs, "age", (40, 90), c.tolerance)
    assert win, "应当存在受影响的取值区间"
    cuts = {w["cutoff"] for w in win}
    assert cuts == set(RISK_CUTOFFS), cuts
    for w in win:
        assert w["lo"] <= w["hi"] and w["n"] >= 1


def test_boundary_window_grows_with_tolerance():
    """容差越大，受影响的区间只能越宽 —— 单调性是量化正确性的自检。"""
    c = next(x for x in CROSS_CHECKS if x.inputs.get("sex") == "男")
    w03 = {w["cutoff"]: w["n"] for w in
           boundary_window(c.inputs, "age", (40, 90), 0.3)}
    w05 = {w["cutoff"]: w["n"] for w in
           boundary_window(c.inputs, "age", (40, 90), 0.5)}
    w10 = {w["cutoff"]: w["n"] for w in
           boundary_window(c.inputs, "age", (40, 90), 1.0)}
    for cut in RISK_CUTOFFS:
        assert w03.get(cut, 0) <= w05.get(cut, 0) <= w10.get(cut, 0), cut


def test_deviation_is_reported_as_interpretable_text():
    """审计视图要能一句话说清后果，而不是只给一个数字。"""
    txt = describe_boundary("ASCVD.CHINAPAR", tol=0.5)
    assert "翻转分层结论" in txt
    assert "岁" in txt
    assert "切点" in txt


def test_unknown_rule_yields_no_checks():
    assert by_rule("NOPE.NOTHING") == []
    assert "无可用算例" in describe_boundary("NOPE.NOTHING")


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
