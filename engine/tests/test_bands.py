#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方分带的边界值回归（P1-A0）。    python3 tests/test_bands.py

三层锁：

1. **边界值**：每段分带的两侧都测（左闭右开 `[low, high)`），错一个数就被抓住；
2. **诚实纪律**：``verified=True`` 必须有逐字原文；``verified=False`` 必须写明原因
   （不许伪装已核实，也不许"悄悄不标"）；
3. **关键更正**：吸烟包年数门槛 = **30**（中国肺癌筛查指南原文），不是 20。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.bands import (BAND_SETS, EGFR_SLOPE, TG胰腺炎阈值, band_label, band_of,
                           band_set, verify_consistency, 临界判据, 包年数, 早发家族史,
                           非HDL_C)  # noqa: E402


# ------------------------------------------------------------ 自检

def test_band_tables_are_continuous_and_honest():
    assert verify_consistency() == []


def test_verified_true_requires_verbatim_quote():
    """``verified=True`` 必须带逐字原文；``False`` 必须写明原因（不许伪装）。"""
    n_true = 0
    for bs in BAND_SETS:
        for p in bs.provenance:
            if p.verified:
                assert p.quote_verbatim.strip(), f"{bs.name}：verified=True 但无原文"
                n_true += 1
            else:
                assert p.note.strip(), f"{bs.name}：verified=False 但没写原因"
    # 本轮实际核到原文的条目：非 HDL-C（表7 注）+ 2025 慢病文件（交叉验证）
    assert n_true >= 2


# ------------------------------------------------------------ 边界值（左闭右开）

def test_systolic_boundaries():
    assert band_label("收缩压", 119.9) == "正常"
    assert band_label("收缩压", 120) == "正常高值"      # 下界含
    assert band_label("收缩压", 129.9) == "正常高值"
    assert band_label("收缩压", 130) == "重点关注段"
    assert band_label("收缩压", 139.9) == "重点关注段"
    assert band_label("收缩压", 140) == "高血压"        # 上界不含 → 归下一段
    assert band_label("收缩压", 200) == "高血压"


def test_diastolic_boundaries():
    assert band_label("舒张压", 80) == "正常高值"
    assert band_label("舒张压", 85) == "重点关注段"
    assert band_label("舒张压", 90) == "高血压"


def test_glucose_boundaries():
    assert band_label("空腹血糖", 6.09) == "正常"
    assert band_label("空腹血糖", 6.1) == "糖尿病前期"
    assert band_label("空腹血糖", 6.99) == "糖尿病前期"
    assert band_label("空腹血糖", 7.0) == "糖尿病"


def test_hba1c_boundaries_and_version_note():
    assert band_label("糖化血红蛋白", 5.69) == "正常"
    assert band_label("糖化血红蛋白", 5.7) == "糖尿病前期"
    assert band_label("糖化血红蛋白", 6.5) == "糖尿病"
    hit = band_of("糖化血红蛋白", 5.8)
    assert "版本" in hit["备注"], "HbA1c 必须带版本差异说明"


def test_lipid_boundaries():
    assert band_label("总胆固醇", 5.19) == "合适"
    assert band_label("总胆固醇", 5.2) == "边缘升高"
    assert band_label("总胆固醇", 6.2) == "升高"
    assert band_label("坏胆固醇", 3.4) == "边缘升高"
    assert band_label("坏胆固醇", 4.1) == "升高"
    assert band_label("甘油三酯", 1.7) == "边缘升高"
    assert band_label("甘油三酯", 2.3) == "升高"


def test_hdl_sex_specific():
    assert band_label("好胆固醇", 0.94, "男") == "降低"
    assert band_label("好胆固醇", 1.0, "男") == "正常"
    assert band_of("好胆固醇", 0.94, "女") is None, "女性界值不同 → 该表不适用"


def test_uric_acid_boundaries():
    assert band_label("尿酸", 419.9) == "正常"
    assert band_label("尿酸", 420) == "高尿酸血症"


def test_unit_and_analyte_id_present():
    for bs in BAND_SETS:
        assert bs.unit and bs.analyte_id and bs.strength, bs.name


# ------------------------------------------------------------ 判据与常量

def test_critical_thresholds():
    assert 临界判据["贴线余量"] == 5
    assert 临界判据["单调余量"] == 10
    assert 临界判据["强度"] == "弱"
    assert EGFR_SLOPE["自然下降"] == (0.4, 1.0)
    assert EGFR_SLOPE["快速进展"] == 5.0
    assert TG胰腺炎阈值["值"] == 5.6
    assert 非HDL_C["治疗目标基数"] == 4.2
    assert 非HDL_C["分档界线"] == 4.9


def test_pack_year_threshold_is_30_not_20():
    """**更正锁**：中国肺癌筛查指南的吸烟门槛是 ≥30 包年（20 是 NCCN 口径；
    指南里的 ≥20 说的是「被动吸烟 ≥20 年」）。"""
    assert 包年数["高危门槛"] == 30
    assert "30" in 包年数["provenance"].quote_verbatim
    assert 包年数["provenance"].verified is True
    assert "每天吸烟的包数" in 包年数["定义"]


def test_early_family_history_definition():
    assert 早发家族史["男性"] == 55
    assert 早发家族史["女性"] == 65
    assert 早发家族史["provenance"].verified is False
    assert 早发家族史["provenance"].note


def test_band_label_none_for_unknown_item():
    assert band_of("某个不在表里的项目", 1.0) is None
    assert band_set("坏胆固醇").name == "坏胆固醇"


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
            failed.append((name, e))
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
