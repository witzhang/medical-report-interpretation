#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""红旗（A 类危急值）的回归测试。    python3 tests/test_redflags.py

依据：《健康体检重要异常结果管理专家共识（试行版）》A 类 ——
「需要立即进行临床干预，否则将危及生命或导致严重不良后果的异常结果」。

**这个模块此前一项测试都没有。** 变异测试指出：12 条 A 类规则的阈值
（30 / 1000 / 1.0 / 0.5 / 2.8 / 3.9 / 16.7 …）改成别的数**没有任何测试失败** ——
因为真实数据里一个红旗都没有，"结果为空"这条断言对任何阈值都成立。

红旗是整条链里**临床后果最重**的输出，阈值边界必须逐个锁。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.redflags import (DEFAULT_A, MULTIPLE, RedFlagRule,  # noqa: E402
                              check, load_rules)
from checkup.schema import NormalizedObservation  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
SRC = PATHS.real_reports


def _obs(aid, value, ref_high=None, year="2026", item=None):
    return NormalizedObservation(
        year=year, page_no=1, section="生化检查", item_raw=item or aid,
        occurrence=1, analyte_id=aid, value=value, value_type="numeric",
        unit="", ref_low=None, ref_high=ref_high)


def _hit(aid, value, ref_high=None, **profile):
    h = check([_obs(aid, value, ref_high)], profile=profile)
    return h[0] if h else None


# ============================================ 每条规则的阈值边界

def test_every_le_rule_fires_at_its_threshold():
    """``le`` 类规则：**恰好等于**阈值必须命中，往上一点必须不命中。"""
    bad = []
    for r in DEFAULT_A:
        if r.op != "le":
            continue
        if r.condition == "diabetes":
            prof = {"diabetes": True}
        elif r.condition == "no_diabetes":
            prof = {"diabetes": False}
        else:
            prof = {}
        at = _hit(r.analyte_id, r.value, **prof)
        over = _hit(r.analyte_id, r.value + abs(r.value) * 0.01 + 1e-6, **prof)
        if not (at and at.rule_id == r.rule_id):
            bad.append(f"{r.rule_id}: 恰好 {r.value} 未命中")
        if over is not None and over.rule_id == r.rule_id:
            bad.append(f"{r.rule_id}: 略高于 {r.value} 仍命中")
    assert not bad, bad


def test_every_ge_rule_fires_at_its_threshold():
    """``ge`` 类规则：恰好等于阈值必须命中，往下一点必须不命中。"""
    bad = []
    for r in DEFAULT_A:
        if r.op != "ge":
            continue
        prof = {"diabetes": True} if r.condition == "diabetes" else {}
        at = _hit(r.analyte_id, r.value, **prof)
        below = _hit(r.analyte_id, r.value - abs(r.value) * 0.01 - 1e-6, **prof)
        if not (at and at.rule_id == r.rule_id):
            bad.append(f"{r.rule_id}: 恰好 {r.value} 未命中")
        if below is not None and below.rule_id == r.rule_id:
            bad.append(f"{r.rule_id}: 略低于 {r.value} 仍命中")
    assert not bad, bad


def test_multiple_rules_use_the_reports_own_uln():
    """``ge_uln_multiple`` 规则按**该年报告自己的**参考上限算倍数。

    共识自陈「受各实验室检测系统不同影响…仅提供参考」，所以倍数必须
    相对报告自带的上限，不能是全局常量。
    """
    alt = next(r for r in DEFAULT_A if r.rule_id == "RF.A.ALT")
    assert alt.op == MULTIPLE
    uln = 40.0
    at = _hit(alt.analyte_id, alt.value * uln, ref_high=uln)
    below = _hit(alt.analyte_id, alt.value * uln - 0.1, ref_high=uln)
    assert at and at.rule_id == alt.rule_id, at
    assert below is None or below.rule_id != alt.rule_id, below
    # 同一个数值，换一个更高的上限就不该命中 —— 证明用的是报告的上限
    assert _hit(alt.analyte_id, alt.value * uln, ref_high=uln * 10) is None


def test_multiple_rules_need_a_reference_upper_bound():
    """报告没给上限时，倍数规则**不得命中**（不能按某个默认上限猜）。"""
    alt = next(r for r in DEFAULT_A if r.rule_id == "RF.A.ALT")
    assert _hit(alt.analyte_id, alt.value * 40, ref_high=None) is None


# ============================================ 条件门控

def test_diabetes_condition_gates_the_glucose_rules():
    """``condition`` 必须真的门控：同一血糖值，有无糖尿病史结论不同。"""
    low_dm = next(r for r in DEFAULT_A if r.rule_id == "RF.A.FPG.LOW.DM")
    low_nodm = next(r for r in DEFAULT_A if r.rule_id == "RF.A.FPG.LOW.NODM")
    v = 3.0            # ≤3.9（糖尿病）但 >2.8（非糖尿病）
    assert _hit("CHEM.葡萄糖", v, diabetes=True).rule_id == low_dm.rule_id
    got = _hit("CHEM.葡萄糖", v, diabetes=False)
    assert got is None or got.rule_id != low_dm.rule_id


def test_high_glucose_rule_only_with_diabetes():
    hi = next(r for r in DEFAULT_A if r.rule_id == "RF.A.FPG.HIGH.DM")
    assert _hit("CHEM.葡萄糖", 20.0, diabetes=True).rule_id == hi.rule_id
    got = _hit("CHEM.葡萄糖", 20.0, diabetes=False)
    assert got is None or got.rule_id != hi.rule_id


# ============================================ needs_human 的语义

def test_needs_human_means_missing_context_not_severity():
    """``needs_human`` 的含义是「**判据包含我读不到的上下文**」。

    血压那条需要「伴急性症状，或休息后复测仍达此标准」—— 两者本工具都读不到，
    所以必须标。而化验阈值是自足的（报告上的数就是判据），不该标。

    这个字段与 `render.py` 的患者版联动：标了才会附上
    「本工具读不到症状与复测值，请立即携报告就医确认」。
    """
    bp = _hit("VITAL.收缩压", 185.0)
    assert bp is not None and bp.needs_human is True, bp
    hgb = next(r for r in DEFAULT_A if r.rule_id == "RF.A.HGB")
    got = _hit(hgb.analyte_id, hgb.value)
    assert got is not None, "血红蛋白恰好 60 应命中"
    assert got.needs_human is False, \
        "化验阈值是自足的，不该标 needs_human —— 那会让患者版附上无关的说明"


def test_needs_human_is_not_derived_from_the_documentation_field():
    """``needs_human`` 不能由 ``note`` 推导 —— 那是**文档字段**。

    原先写的是 ``needs_human=bool(r.note)``，而默认 12 条规则**全都没有 note**，
    于是这个机制**永远不生效**（死代码）。这里锁住"它是显式字段"。
    """
    import dataclasses
    names = {f.name for f in dataclasses.fields(RedFlagRule)}
    assert "needs_human" in names, \
        "needs_human 应是 RedFlagRule 的显式字段，而不是从 note 推导"
    # 文档字段与行为字段互不影响
    r = RedFlagRule("X", "A", "le", 1, "", "标签", note="只是给文档看的")
    assert r.needs_human is False


def test_a_rule_can_explicitly_require_human_context():
    """显式标了 needs_human 的规则，命中时必须把它传出去。"""
    r = RedFlagRule("X.CTX", "TEST.A", "le", 5, "", "需要上下文的判据",
                    needs_human=True)
    h = check([_obs("TEST.A", 4.0)], rules=[r])
    assert h and h[0].needs_human is True


# ============================================ 覆盖与去重

def test_institution_override_replaces_the_defaults():
    """A 类阈值**不能是硬编码全局常量** —— 共识自陈仅供参考，机构可替换。"""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                    encoding="utf-8") as f:
        json.dump({"a_class": [
            {"rule_id": "RF.ORG.HGB", "analyte_id": "BLD.血红蛋白浓度",
             "op": "le", "value": 50, "unit": "g/L",
             "label": "本机构：血红蛋白 ≤50"}]}, f, ensure_ascii=False)
        path = f.name
    try:
        rules = load_rules(path)
        assert len(rules) == 1 and rules[0].rule_id == "RF.ORG.HGB"
        # 用机构阈值：60 不再命中，50 才命中
        assert check([_obs("BLD.血红蛋白浓度", 60.0)], rules=rules) == []
        assert check([_obs("BLD.血红蛋白浓度", 50.0)], rules=rules)
    finally:
        os.unlink(path)


def test_same_rule_same_year_reports_once():
    """同一条规则同一年只报一次（哪怕有重复观测）。"""
    r = RedFlagRule("X.DUP", "TEST.A", "le", 5, "", "重复项")
    hits = check([_obs("TEST.A", 1.0), _obs("TEST.A", 2.0)], rules=[r])
    assert len(hits) == 1, [h.rule_id for h in hits]


def test_missing_value_is_skipped():
    r = RedFlagRule("X.NULL", "TEST.A", "le", 5, "", "空值")
    assert check([_obs("TEST.A", None)], rules=[r]) == []


# ============================================ 与真实数据的对照

def test_real_data_has_no_red_flags():
    """本案例三年一份红旗都没有 —— 这是**已知事实**，改了要立刻知道。"""
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    obs = [o for rows in build_all(SRC, cat).values() for o in rows]
    assert check(obs, profile={"diabetes": False}) == []


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
