#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式的结构不变量测试。    python3 tests/test_formulas.py

对**已逐字核到原文**的公式，再找一个数值算例的价值有限 —— 真正的检验是
**按原文的分支形式独立重写一遍，与我们的实现逐点对比**。系数抄错、指数符号
写反、分支条件写错，这几种错都会被这一招抓住。

外加一组"公式必然成立"的结构性质（单调性、连续性、性别系数），
它们不依赖任何外部数据。
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checkup.rules as R  # noqa: E402
from checkup.compose import egfr_ckdepi2021, fib4  # noqa: E402

UMOL_PER_MGDL = 88.4


# ============================================ CKD-EPI 2021：按原文重写

def _nkf_two_branch(scr_umol: float, age: int, male: bool) -> float:
    """**照 NKF 官方实现指南的 IF 分支表逐字重写**，不引用我们的实现。

    原文：
      Female ≤0.70 → 142 × (Scr/0.7)^-0.241 × 0.9938^Age × 1.012
      Female >0.70 → 142 × (Scr/0.7)^-1.200 × 0.9938^Age × 1.012
      Male   ≤0.90 → 142 × (Scr/0.9)^-0.302 × 0.9938^Age
      Male   >0.90 → 142 × (Scr/0.9)^-1.200 × 0.9938^Age
    """
    scr = scr_umol / UMOL_PER_MGDL
    if male:
        if scr <= 0.90:
            return 142 * (scr / 0.9) ** -0.302 * 0.9938 ** age
        return 142 * (scr / 0.9) ** -1.200 * 0.9938 ** age
    if scr <= 0.70:
        return 142 * (scr / 0.7) ** -0.241 * 0.9938 ** age * 1.012
    return 142 * (scr / 0.7) ** -1.200 * 0.9938 ** age * 1.012


def test_ckdepi_matches_nkf_two_branch_transcription():
    """我们的 min/max 单式必须与 NKF 的 IF 分支表**逐点一致**。

    覆盖分支边界两侧与两端极值。
    """
    worst = 0.0
    for male in (True, False):
        for age in (18, 35, 55, 75, 90):
            for scr in (40, 60, 70, 79, 80, 88, 100, 200, 400, 800):
                a = egfr_ckdepi2021(scr, age, male)
                b = _nkf_two_branch(scr, age, male)
                worst = max(worst, abs(a - b))
    assert worst < 1e-9, f"与 NKF 原文分支式最大偏差 {worst}"


def test_ckdepi_at_kappa_equals_base_times_age_factor():
    """结构性质：Scr/κ = 1 时 min=max=1 → eGFR = 142 × 0.9938^age × [1.012 若女]。"""
    for age in (20, 45, 70):
        assert abs(egfr_ckdepi2021(0.9 * UMOL_PER_MGDL, age, True)
                   - 142 * 0.9938 ** age) < 1e-9
        assert abs(egfr_ckdepi2021(0.7 * UMOL_PER_MGDL, age, False)
                   - 142 * 0.9938 ** age * 1.012) < 1e-9


def test_ckdepi_is_continuous_at_kappa_but_not_differentiable():
    """κ 点是两段幂函数的**连接点**：C0 连续，但**导数不连续**。

    这一点值得写下来：有人会以为它在 κ 处光滑，从而用一个统一的斜率
    去估"肌酐每升高一点 eGFR 掉多少"。实际两侧斜率不同
    （男 0.9 处：左支 α=-0.302，右支 -1.200）。
    """
    for male, k, a in ((True, 0.9, -0.302), (False, 0.7, -0.241)):
        age = 50
        fac = 1.0 if male else 1.012
        at = egfr_ckdepi2021(k * UMOL_PER_MGDL, age, male)
        left = 142 * 1.0 ** a * 0.9938 ** age * fac
        right = 142 * 1.0 ** -1.200 * 0.9938 ** age * fac
        assert abs(at - left) < 1e-12 and abs(left - right) < 1e-12
        # 两侧斜率确实不同
        d = 1e-4
        sl_l = (egfr_ckdepi2021((k - d) * UMOL_PER_MGDL, age, male)
                - egfr_ckdepi2021((k - 2 * d) * UMOL_PER_MGDL, age, male)) / d
        sl_r = (egfr_ckdepi2021((k + 2 * d) * UMOL_PER_MGDL, age, male)
                - egfr_ckdepi2021((k + d) * UMOL_PER_MGDL, age, male)) / d
        assert abs(sl_l - sl_r) > 10, (male, sl_l, sl_r)


def test_ckdepi_monotonic_in_creatinine_and_age():
    """肌酐越高 eGFR 越低；年龄越大 eGFR 越低。"""
    vals = [egfr_ckdepi2021(s, 50, True) for s in (60, 80, 100, 150, 300)]
    assert all(a > b for a, b in zip(vals, vals[1:])), vals
    ages = [egfr_ckdepi2021(80, a, True) for a in (20, 40, 60, 80)]
    assert all(a > b for a, b in zip(ages, ages[1:])), ages


def test_ckdepi_female_factor_is_exactly_1_012():
    """性别只体现在 κ、α 与 1.012 三处；比值必须恰好等于三项之积。"""
    scr, age = 70.0, 50
    m, f = egfr_ckdepi2021(scr, age, True), egfr_ckdepi2021(scr, age, False)
    # 必须用**未取整**的 mg/dL（70/88.4 = 0.791855…）；写 0.792 会引入 1e-4 误差
    mgdl = scr / UMOL_PER_MGDL
    assert abs(mgdl - 0.792) < 1e-3, mgdl
    # 男 r=0.88≤1 走 α 支；女 r=1.13>1 走 -1.200 支
    expect = ((mgdl / 0.7) ** -1.200 * 1.012) / ((mgdl / 0.9) ** -0.302)
    assert abs(f / m - expect) < 1e-12, (f / m, expect)


# ============================================ FIB-4

def test_fib4_matches_uw_formula_transcription():
    """照 UW 计算器页面逐字重写：年龄 × AST / (血小板 × √ALT)，单位 U/L、10^9/L。"""
    def uw(age, ast, plt, alt):
        return age * ast / (plt * math.sqrt(alt))

    for age in (25, 40, 60, 80):
        for ast, plt, alt in ((20, 250, 20), (40, 200, 40), (120, 90, 80),
                              (300, 50, 120)):
            assert abs(fib4(age, ast, plt, alt) - uw(age, ast, plt, alt)) < 1e-12


def test_fib4_hand_computed_anchor():
    """手算锚点：50 岁、AST 40、血小板 200、ALT 40 → 2000 / (200×√40) ≈ 1.5811。"""
    got = fib4(50, 40, 200, 40)
    assert abs(got - 2000 / (200 * math.sqrt(40))) < 1e-12
    assert abs(got - 1.5811) < 1e-4


def test_fib4_scaling_properties():
    """对年龄与 AST 线性；对血小板反比；对 √ALT 反比。"""
    assert abs(fib4(60, 40, 200, 40) / fib4(30, 40, 200, 40) - 2) < 1e-12
    assert abs(fib4(50, 80, 200, 40) / fib4(50, 40, 200, 40) - 2) < 1e-12
    assert abs(fib4(50, 40, 100, 40) / fib4(50, 40, 200, 40) - 2) < 1e-12
    assert abs(fib4(50, 40, 200, 40) / fib4(50, 40, 200, 160) - 2) < 1e-12


# ============================================ 出处卫生

def test_all_module_level_provenance_constants_are_provenance_objects():
    """**守卫**：`P_XXX = Provenance(...)` 后面多一个逗号就变成单元素元组。

    这个错本轮与上一轮各犯了一次（`P_KDIGO2012`、`P_FIB4_UNITS`）。
    已有的「每条规则必须有出处」测试只在元组被规则引用时才报，这里补一条直接的。
    """
    from checkup.schema import Provenance
    bad = [n for n, v in vars(R).items()
           if n.startswith("P_") and not isinstance(v, Provenance)]
    assert not bad, f"这些常量不是 Provenance：{bad}"


def test_ckdepi_and_fib4_are_verified_now():
    """本轮把这两条从 verified=False 推到 True —— 回归保护。"""
    assert R.P_CKDEPI.verified is True
    assert R.P_FIB4_UNITS.verified is True
    assert "NKF" in R.P_CKDEPI.quote_verbatim
    assert "10^9/L" in R.P_FIB4_UNITS.quote_verbatim


def test_ckdepi_reporting_rules_are_implemented_and_note_says_so():
    """NKF 的报告范围与取整**已实现**，注记必须跟着更新。

    这条原先断言 ``"尚未实施" in note`` —— 实现之后它就变成在**验证一句假话**。
    注记与实现必须同步，否则下一步读代码的人会被误导。
    """
    assert "尚未实施" not in R.P_CKDEPI.note
    assert "已于 2026-10-01 实现" in R.P_CKDEPI.note
    assert "取整会跨过 CKD 分期边界" in R.P_CKDEPI.note


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
