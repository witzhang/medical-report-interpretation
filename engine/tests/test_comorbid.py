#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""病种分支的回归测试（框架 D12/D14）。    python3 tests/test_comorbid.py

《中国血脂管理指南（2023年）》图1 说明把分层分成**三条路径**，本测试逐条验：

1. **二级预防**（已诊断 ASCVD）：≥2 次严重事件，或 1 次事件且合并 ≥2 个
   高危险因素 → 超高危；其他 ASCVD 患者 → 极高危
2. **一级预防 · 直接高危**：①LDL-C≥4.9 或 TC≥7.2 ②年龄≥40 的糖尿病
   ③CKD 3-4 期 之一者 → 高危，**不需要再做 10 年风险评估**
3. **一级预防 · 10 年风险**：<5% / 5-9% / ≥10% → 低/中/高危

外加表7 的目标值（含"较基线降幅>50%"）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, LDL_TARGETS, evaluate, load_profile  # noqa: E402
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
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        _base = dict(obs=obs, metas=metas, cat=cat)
    return _base


def with_values(**overrides):
    """把指定指标的值改掉（可写成 ``AID=值`` 或 ``AID=(年份, 值)``）。"""
    out = []
    for o in base()["obs"]:
        if o.analyte_id in overrides:
            spec = overrides[o.analyte_id]
            if isinstance(spec, tuple):
                y, v = spec
                if o.year == y:
                    out.append(replace(o, value=v, value_text=str(v)))
                    continue
            else:
                out.append(replace(o, value=spec, value_text=str(spec)))
                continue
        out.append(o)
    return out


def run(obs=None, **profile_over):
    pf = dict(load_profile(PATHS.profile))
    pf.update(profile_over)
    js = judge_all(obs or base()["obs"],
                   os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=base()["metas"])
    return evaluate(Composer(js, base()["metas"], pf))


def one(res, rid, year="2026"):
    return next(x for x in res if x.rule_id == rid and x.year == year)


# --------------------------------------------------- 路径 1：二级预防

def test_secondary_prevention_other_ascvd_is_very_high():
    """已诊断 ASCVD、非多次事件 → **极高危** → LDL <1.8 且降幅>50%。"""
    r = run(ascvd_diagnosed=True, severe_ascvd_events=0)
    assert one(r, "LIPID.RISK_STRATIFICATION").band == "极高危"
    assert one(r, "LIPID.LDL_TARGET").inputs["目标值"] == 1.8
    assert one(r, "LIPID.LDL_TARGET").inputs["需降幅>50%"] is True


def test_secondary_prevention_two_events_is_ultra_high():
    """≥2 次严重 ASCVD 事件 → **超高危** → LDL <1.4。"""
    r = run(ascvd_diagnosed=True, severe_ascvd_events=2)
    assert one(r, "LIPID.RISK_STRATIFICATION").band == "超高危"
    assert one(r, "LIPID.LDL_TARGET").inputs["目标值"] == 1.4


def test_one_event_plus_two_risk_factors_is_ultra_high():
    """1 次严重事件 **且** 合并 ≥2 个高危险因素 → 超高危。"""
    r = run(ascvd_diagnosed=True, severe_ascvd_events=1,
            ascvd_high_risk_factors=2)
    assert one(r, "LIPID.RISK_STRATIFICATION").band == "超高危"


def test_one_event_without_enough_factors_is_only_very_high():
    """1 次事件但高危因素不足 2 个 → 只是极高危，不能升级。"""
    r = run(ascvd_diagnosed=True, severe_ascvd_events=1,
            ascvd_high_risk_factors=1)
    assert one(r, "LIPID.RISK_STRATIFICATION").band == "极高危"


# --------------------------------------------- 路径 2：一级预防·直接高危

def test_ldl_above_4_9_is_directly_high_risk():
    """LDL-C ≥4.9 → 直接高危，**不再做 10 年风险评估**。"""
    r = run(with_values(**{"CHEM.低密度脂蛋白胆固醇": 5.2}))
    s = one(r, "LIPID.RISK_STRATIFICATION")
    assert s.band == "高危", s.text
    assert s.inputs["路径"] == "一级预防 · 直接高危"
    assert "10年风险%" not in s.inputs, "直接高危不该再算 10 年风险"
    assert "LDL-C≥4.9" in s.text


def test_tc_above_7_2_is_directly_high_risk():
    """TC ≥7.2 → 直接高危。"""
    r = run(with_values(**{"CHEM.总胆固醇": 7.5}))
    s = one(r, "LIPID.RISK_STRATIFICATION")
    assert s.band == "高危" and "TC≥7.2" in s.text


def test_diabetes_over_40_is_directly_high_risk():
    """年龄≥40 的糖尿病 → 直接高危。本案例 2026 年 35 岁，须 ≥40 才成立。"""
    young = run(diabetes=True)
    assert one(young, "LIPID.RISK_STRATIFICATION").inputs.get("路径") \
        != "一级预防 · 直接高危", "35 岁的糖尿病不该直接判高危"
    old = run(with_values(**{"VITAL.收缩压": (None, None)}) if False else None,
              diabetes=True)
    # 把年龄改成 45 来触发：直接改 meta
    from dataclasses import replace as _r
    metas = dict(base()["metas"])
    metas["2026"] = _r(metas["2026"], age=45)
    js = judge_all(base()["obs"], os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=metas)
    pf = dict(load_profile(PATHS.profile))
    pf["diabetes"] = True
    res = evaluate(Composer(js, metas, pf))
    s = one(res, "LIPID.RISK_STRATIFICATION")
    assert s.band == "高危" and "糖尿病" in s.text, s.text


def test_ckd_3_4_is_directly_high_risk():
    """CKD 3-4 期（eGFR<60）→ 直接高危。"""
    r = run(with_values(**{"CHEM.肌酐": 150.0}))     # eGFR 会掉到 60 以下
    s = one(r, "LIPID.RISK_STRATIFICATION")
    assert s.inputs["eGFR"] < 60, s.inputs
    assert s.band == "高危" and "CKD" in s.text
    assert any("尿白蛋白" in n for n in s.notes), s.notes


# -------------------------------------------- 路径 3：10 年风险（近似）

def test_third_path_uses_the_21_cell_table():
    """第三条路径已从「China-PAR 近似」改为**查 21 格表**（表已于 2026-10-01 取得）。

    China-PAR 个体风险降级为**旁证**，并须写明它与人群平均风险表**不等价**。
    """
    s = one(run(), "LIPID.RISK_STRATIFICATION")
    assert s.inputs["路径"] == "一级预防 · 21 格表", s.inputs
    assert "21 格表" in s.text, s.text
    assert any("不等价" in n and "以查表为准" in n for n in s.notes), s.notes


def test_ten_year_risk_matches_the_dedicated_chinapar_rule():
    """分层用的 10 年风险必须与 ASCVD.CHINAPAR 规则**一致**。

    这里曾经错把 LDL 当 HDL 传进去（3.0 vs 0.94），风险从 2.6% 掉到 1.1% ——
    会直接改变分层结论。
    """
    r = run()
    for y in ("2024", "2025", "2026"):
        assert (one(r, "LIPID.RISK_STRATIFICATION", y).inputs.get("ChinaPAR 10年风险%")
                == one(r, "ASCVD.CHINAPAR", y).value), y


def test_moderate_risk_under_55_notes_lifetime_risk():
    """中危且年龄<55 → 指南要求评估**余生风险**，必须提示。"""
    # 抬高风险：收缩压 179 且保留吸烟（不触发直接高危）
    r = run(with_values(**{"VITAL.收缩压": 175.0}))
    s = one(r, "LIPID.RISK_STRATIFICATION")
    if s.band == "中危":
        assert any("余生风险" in n for n in s.notes), s.notes


# --------------------------------------------------- 表7 目标值

def test_lipid_targets_table_matches_guideline():
    """表7 逐条对照（逐字原文见 rules.P_LIPID_TABLE7）。"""
    assert LDL_TARGETS["低危"] == (3.4, False)
    assert LDL_TARGETS["中危"] == (2.6, False)
    assert LDL_TARGETS["高危"] == (2.6, False)
    assert LDL_TARGETS["极高危"] == (1.8, True)
    assert LDL_TARGETS["超高危"] == (1.4, True)


def test_drop_over_50_percent_only_required_for_very_high_and_above():
    r = run()
    assert one(r, "LIPID.LDL_TARGET").inputs["需降幅>50%"] is False
    r2 = run(ascvd_diagnosed=True)
    assert one(r2, "LIPID.LDL_TARGET").inputs["需降幅>50%"] is True


def test_non_hdl_target_is_ldl_target_plus_point_8():
    """表7 注：非 HDL-C 目标 = LDL-C 目标 + 0.8。"""
    r = run()
    t = one(r, "LIPID.LDL_TARGET")
    assert any("+ 0.8" in n for n in t.notes), t.notes


def test_low_risk_current_case_is_unchanged():
    """本案例（一级预防、10 年风险 ~1.4%）仍应判低危、达标 —— 回归保护。"""
    r = run()
    assert one(r, "LIPID.RISK_STRATIFICATION").band == "低危"
    assert "达标" in one(r, "LIPID.LDL_TARGET").band


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
