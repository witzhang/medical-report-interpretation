#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告值规则与切点语境。    python3 tests/test_reporting.py

上一轮逐字核到两条规定，发现我们**写明了但没做**：

1. NKF 规定 eGFR 报告范围 5–150、结果取整数
2. FIB-4 的切点**按用途分两套**，原先只写了一套

这一轮做掉。做的时候发现第 1 条不是纯显示问题 —— **取整会跨过分期边界**，
而分期又喂给血脂指南的「CKD 3-4 期 → 直接高危」条件。一个四舍五入
能跨两层结论。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import (EGFR_REPORT_MAX, EGFR_REPORT_MIN,  # noqa: E402
                             FIB4_CONTEXT, FIB4_CUTOFFS, Composer,
                             egfr_ckdepi2021, egfr_reported, evaluate,
                             load_profile)
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


def run(obs=None):
    js = judge_all(obs or base()["obs"],
                   os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=base()["metas"])
    return evaluate(Composer(js, base()["metas"],
                             load_profile(PATHS.profile)))


def one(res, rid, year="2026"):
    return next(x for x in res if x.rule_id == rid and x.year == year)


def with_creatinine(v):
    return [replace(o, value=v) if o.analyte_id == "CHEM.肌酐" else o
            for o in base()["obs"]]


# =================================================== eGFR 报告值

def test_report_bounds_are_the_nkf_ones():
    assert (EGFR_REPORT_MIN, EGFR_REPORT_MAX) == (5, 150)


def test_reporting_rounds_to_whole_numbers():
    """NKF：Number of decimal places — zero, report in whole numbers。"""
    assert egfr_reported(94.1)[0] == 94
    assert egfr_reported(94.6)[0] == 95
    assert egfr_reported(60.0)[0] == 60
    assert egfr_reported(59.76)[0] == 60      # ← 关键：59.76 报告为 60


def test_reporting_clamps_out_of_range():
    """<5 报 ``<5``，>150 报 ``>150``，**不报一个数**。"""
    v, txt, clipped = egfr_reported(3.2)
    assert v is None and txt == "<5" and clipped is True
    v, txt, clipped = egfr_reported(180.0)
    assert v is None and txt == ">150" and clipped is True
    assert egfr_reported(5.0)[2] is False
    assert egfr_reported(150.0)[2] is False


def test_raw_value_is_never_rewritten():
    """**原始值永不改写**：结论里的 value 仍是计算值，报告值另存 inputs。"""
    s = one(run(), "RENAL.EGFR_CKDEPI2021", "2024")
    assert s.value == 94.1, s.value
    assert s.inputs["报告值"] == 94
    assert s.inputs["报告文本"] == "94"
    assert "94.1" in s.text and "94" in s.text


# =================================================== 取整跨边界

def test_rounding_across_stage_boundary_is_detected():
    """**核心案例**：肌酐 136.5–137.0 → eGFR 59.5–59.8 → 报告 60。

    计算值属 G3a（45-59），报告值属 G2（60-89）。这个跨越必须被说出。
    """
    s = one(run(with_creatinine(136.6)), "RENAL.EGFR_CKDEPI2021")
    assert 59.5 <= s.value <= 59.9, s.value
    assert s.inputs["报告值"] == 60
    assert any("取整跨过了分期边界" in n for n in s.notes), s.notes
    assert "G3a" in s.band and "G2" in s.band, s.band


def test_ckd_stage_uses_the_reported_value():
    """CKD 分期以**报告值**为准 —— 报告上印的是取整后的数。"""
    s = one(run(with_creatinine(136.6)), "RENAL.CKD_STAGE")
    assert s.inputs["eGFR 计算值"] < 60
    assert s.inputs["eGFR 报告值"] == 60
    assert s.inputs["G 分期"] == "G2", s.inputs   # 不是 G3a


def test_stage_is_g3a_when_rounding_stays_below_60():
    """反例：报告值仍 <60 → 该是 G3a。"""
    s = one(run(with_creatinine(137.5)), "RENAL.CKD_STAGE")
    assert s.inputs["eGFR 报告值"] == 59
    assert s.inputs["G 分期"] == "G3a", s.inputs


def test_rounding_flip_propagates_to_lipid_stratification():
    """**一个四舍五入跨两层结论**：分期 → 血脂指南「CKD 3-4 期 → 直接高危」。

    报告值 60 → 不构成 CKD 3-4 → 不列入直接高危；但必须显出计算值与准则的差异。
    """
    st = one(run(with_creatinine(136.6)), "LIPID.RISK_STRATIFICATION")
    assert st.inputs["路径"] == "一级预防 · 21 格表", st.inputs
    assert any("不列入直接高危" in n for n in st.notes), st.notes
    assert any("取整" in n for n in st.notes), st.notes


def test_ckd_condition_fires_when_reported_value_is_below_60():
    st = one(run(with_creatinine(137.5)), "LIPID.RISK_STRATIFICATION")
    assert st.inputs["路径"] == "一级预防 · 直接高危", st.inputs
    assert st.band == "高危"


def test_normal_case_is_unaffected():
    """本案例 eGFR ~116，取整不跨任何边界 —— 回归保护。"""
    s = one(run(), "RENAL.CKD_STAGE", "2026")
    assert s.inputs["G 分期"] == "G1"
    assert s.inputs["eGFR 报告值"] == 116
    assert not any("取整" in n for n in s.notes), s.notes


# =================================================== FIB-4 切点语境

def test_fib4_has_two_named_cutoff_sets():
    assert FIB4_CUTOFFS["HCV"] == (1.45, 3.25)
    assert FIB4_CUTOFFS["NAFLD"] == (1.30, 2.67)
    assert FIB4_CONTEXT in FIB4_CUTOFFS


def test_fib4_band_uses_the_declared_context():
    s = one(run(), "HEPATIC.FIB4")
    assert s.inputs["切点集"] == FIB4_CONTEXT
    lo, hi = FIB4_CUTOFFS[FIB4_CONTEXT]
    assert s.inputs["切点"] == f"{lo}/{hi}"
    assert FIB4_CONTEXT in s.text


def test_fib4_value_in_the_gap_between_cutoff_sets_differs():
    """同一个 FIB-4 值在两套切点下可以落进不同判读 —— 所以必须标明用途。

    FIB-4 = 1.35：NAFLD 判「灰区」（≥1.30），HCV 判「低」（<1.45）。
    """
    lo_n, hi_n = FIB4_CUTOFFS["NAFLD"]
    lo_h, hi_h = FIB4_CUTOFFS["HCV"]
    v = 1.35
    assert not (v < lo_n)                       # NAFLD：不是低
    assert v < lo_h                             # HCV：是低
    assert f"低（<{lo_h}）" != "灰区"


def test_fib4_notes_state_both_cutoff_sets():
    s = one(run(), "HEPATIC.FIB4")
    txt = " ".join(s.notes)
    assert "HCV" in txt and "NAFLD" in txt
    assert "混用" in txt


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
