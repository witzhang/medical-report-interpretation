#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「危险因素的水平均为干预前水平」—— 图1 脚注要求。    python3 tests/test_pretreatment.py

脚注逐字（`调研/src/lipid2023-fig1-21cell.md`）：

    危险因素的水平均为**干预前水平**。

这条不卡任何外部输入，但**很容易被忽略**，而忽略它的后果是**结论偏向乐观**：
他汀压低 TC/LDL-C，可能让人从 C3 掉到 C2、甚至让
「LDL-C≥4.9 或 TC≥7.2 → 直接高危」这条**根本不触发**。

判据沿用项目既有原则：**能用对的值就用对的值；用不上就把偏差说出来**，不静默。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
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

PRE = {"known": True, "tc": 6.5, "ldl": 4.95, "hdl": 0.9}


def base():
    global _base
    if _base is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        obs = [o for rows in build_all(SRC, cat).values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        _base = dict(obs=obs, metas=metas, cat=cat)
    return _base


def strat(year="2026", **pf_over):
    pf = dict(load_profile(PATHS.profile))
    pf.update(pf_over)
    js = judge_all(base()["obs"], os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=base()["metas"])
    res = evaluate(Composer(js, base()["metas"], pf))
    return next(x for x in res
                if x.rule_id == "LIPID.RISK_STRATIFICATION" and x.year == year)


def has(notes, *words):
    return any(all(w in n for w in words) for n in notes)


# =============================================== 三种情形

def test_not_on_treatment_uses_current_values():
    """未服降脂药 → 当次值即干预前值，不需要任何额外说明。"""
    s = strat()
    assert s.inputs["血脂取值来源"] == "current"
    assert not has(s.notes, "在服降脂药", "无治疗前血脂记录"), s.notes


def test_on_treatment_without_baseline_warns_of_optimistic_bias():
    """**核心**：在服药但无治疗前记录 → 必须说明结论偏乐观，且点出两个后果。"""
    s = strat(lipid_lowering=True)
    assert s.inputs["血脂取值来源"] == "current"
    assert has(s.notes, "在服降脂药", "无治疗前血脂记录"), s.notes
    assert has(s.notes, "掩盖"), "必须点出「直接高危」可能被掩盖"
    assert has(s.notes, "乐观"), "必须点明偏差方向"


def test_on_treatment_with_baseline_uses_pretreatment_values():
    s = strat(lipid_lowering=True, pretreatment_lipids=PRE)
    assert s.inputs["血脂取值来源"] == "pretreatment"
    assert s.inputs["TC"] == 6.5 and s.inputs["LDL-C"] == 4.95, s.inputs
    assert has(s.notes, "干预前水平"), s.notes


# =============================================== 掩盖效应

def test_treatment_can_mask_the_direct_high_risk_condition():
    """**最有说服力的一个案例**：同一个受检者，只看当次值会漏掉「直接高危」。

    当次 LDL-C 3.12（远低于 4.9）→ 低危
    治疗前 LDL-C 4.95（≥4.9）  → **直接高危**

    相差**两级**分层，而差别只在于"用哪个时间的值"。
    """
    on_tx = strat(lipid_lowering=True)
    with_base = strat(lipid_lowering=True, pretreatment_lipids=PRE)
    assert on_tx.band == "低危", on_tx.band
    assert with_base.band == "高危", with_base.band
    assert with_base.inputs["路径"] == "一级预防 · 直接高危", with_base.inputs
    assert "LDL-C≥4.9" in with_base.text, with_base.text


def test_high_cholesterol_baseline_alone_also_triggers():
    """TC≥7.2 同样能触发直接高危 —— 两个条件都要用干预前值判。"""
    pre = {"known": True, "tc": 7.5, "ldl": 3.0, "hdl": 1.2}
    s = strat(lipid_lowering=True, pretreatment_lipids=pre)
    assert s.band == "高危" and "TC≥7.2" in s.text, s.text


# =============================================== 部分缺失

def test_partial_baseline_falls_back_per_item_to_current():
    """只记得治疗前 LDL-C、没记 TC → LDL-C 用治疗前、TC 回落到当次，且说明。"""
    pre = {"known": True, "tc": None, "ldl": 4.95, "hdl": None}
    s = strat(lipid_lowering=True, pretreatment_lipids=pre)
    assert s.inputs["血脂取值来源"] == "pretreatment"
    assert s.inputs["LDL-C"] == 4.95
    assert s.inputs["TC"] == 5.09, s.inputs      # 回落到当次值
    assert has(s.notes, "缺失的项回落到当次值"), s.notes


def test_baseline_marked_unknown_is_treated_as_absent():
    """``known: false`` → 等同没有治疗前记录，走警告分支，不能误用空值。"""
    s = strat(lipid_lowering=True,
              pretreatment_lipids={"known": False, "tc": 6.5, "ldl": 4.95,
                                   "hdl": 0.9})
    assert s.inputs["血脂取值来源"] == "current", s.inputs
    assert s.inputs["LDL-C"] == 3.12, s.inputs
    assert has(s.notes, "无治疗前血脂记录"), s.notes


# =============================================== 与其余机制不冲突

def test_all_years_still_use_current_when_not_treated():
    for y in ("2024", "2025", "2026"):
        assert strat(y).inputs["血脂取值来源"] == "current", y


def test_pretreatment_also_feeds_the_21_cell_table():
    """干预前值必须一路作用到 21 格表的列判定，不只是用于直接高危。"""
    pre = {"known": True, "tc": 5.5, "ldl": 3.8, "hdl": 0.94}
    s = strat(lipid_lowering=True, pretreatment_lipids=pre)
    assert s.inputs["血脂取值来源"] == "pretreatment"
    assert s.inputs["21格列"] == "C3", s.inputs      # 当次 TC 5.09 只在 C2


def test_note_always_states_the_footnote_requirement():
    """无论是否服药，脚注这条要求本身都要出现在注记里（可复核）。"""
    for kw in ({}, {"lipid_lowering": True}):
        assert has(strat(**kw).notes, "干预前水平"), kw


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
