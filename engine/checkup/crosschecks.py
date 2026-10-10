# -*- coding: utf-8 -*-
"""独立算例交叉核对（框架 D13 的延伸）。

`Provenance.verified` 只能表达"原文核没核到"，表达不了"**算得对不对**"。
本模块补这一层：拿**独立实现的已知算例**去跑我们的规则，把偏差量出来、
锁进测试。这样"未核实的规则"就有了可追踪的误差上界，而不是一句空话。

判据不是"必须完全一致"——而是**偏差必须已知、有界、且写明它是否会改变结论**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CrossCheck:
    """一条交叉核对算例。"""

    rule_id: str
    source: str                     #: 独立实现/文献
    source_kind: str                #: primary | secondary | tertiary
    inputs: dict
    expected_text: str              #: 对方给出的读数（原文）
    expected_value: Optional[float] = None
    unit: str = ""
    tolerance: float = 0.5          #: 允许偏差（同 unit）
    note: str = ""
    #: 偏差是否会跨过该规则的**结论切点**
    crosses_decision_boundary: bool = False
    boundary_note: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


#: 已收集的算例。加条目时必须同时给出 ``source_kind``。
CROSS_CHECKS: list[CrossCheck] = [
    CrossCheck(
        rule_id="ASCVD.CHINAPAR",
        source="元医生 China-PAR 计算器 "
               "<https://www.yuandoctor.com/linchuang/china_par>",
        source_kind="tertiary",
        inputs={"age": 55, "sex": "男", "sbp": 130, "treated": False,
                "tc": 5.0, "hdl": 1.2, "wc": 85, "smoke": False, "dm": False,
                "north": False, "urban": False, "fh": False},
        expected_text="4.1% · 低危（<5%）",
        expected_value=4.1,
        unit="%",
        tolerance=0.5,
        note="对方标注系数来自 T/JPMA 024—2024 附录C，我们用的是 Circulation 2016 "
             "原文系数。两者可能不同版本，差异待定",
        crosses_decision_boundary=False,
        boundary_note="本例 4.1% 与 4.4% 同在 <5% 低危区，分层结论一致",
    ),
    CrossCheck(
        rule_id="ASCVD.CHINAPAR",
        source="元医生 China-PAR 计算器（女性算例）"
               "<https://www.yuandoctor.com/linchuang/china_par>",
        source_kind="tertiary",
        inputs={"age": 55, "sex": "女", "sbp": 130, "treated": True,
                "tc": 5.0, "hdl": 1.2, "wc": 85, "smoke": True, "dm": True,
                "north": True, "urban": True, "fh": True},
        expected_text="17.2% · 高危（≥10%）",
        expected_value=17.2,
        unit="%",
        tolerance=0.5,
        note="**我们只有男性方程** → 本例我们返回 not_applicable。"
             "这是覆盖缺口，不是数值偏差",
        crosses_decision_boundary=True,
        boundary_note="女性无法评估 → 分层不可用",
    ),
]


def by_rule(rule_id: str) -> list[CrossCheck]:
    return [c for c in CROSS_CHECKS if c.rule_id == rule_id]


def summary() -> str:
    """给技术版视图用的一行式摘要。"""
    from collections import Counter
    cnt = Counter(c.rule_id for c in CROSS_CHECKS)
    kinds = Counter(c.source_kind for c in CROSS_CHECKS)
    return (f"{len(CROSS_CHECKS)} 条算例覆盖 {len(cnt)} 条规则；"
            f"来源层级 {'/'.join(f'{k}×{v}' for k, v in kinds.items())}")


# ------------------------------------------------------- 偏差的后果量化

#: China-PAR 的风险分层切点（《中国血脂管理指南（2023年）》图1 说明给出的
#: 10 年发病平均风险定义；也是本引擎 ``_stratify`` 用的切点）
RISK_CUTOFFS = [5.0, 10.0]


def boundary_window(rule_inputs: dict, param: str, span: tuple,
                    tol: float, fn=None) -> list[dict]:
    """量化"偏差 tol 会不会改变结论"。

    做法：沿 ``param`` 扫一遍，找出风险落在每个切点 ±``tol`` 带内的取值区间。
    落在带内的人，其分层结论**在我们与对方之间可能相反**。

    ``rule_inputs`` 里除 ``param`` 外的键作为固定输入。
    返回 ``[{"cutoff":5.0, "lo":56, "hi":58, "n":3}, ...]``。
    """
    if fn is None:
        from .compose import china_par_male as fn
    lo_v, hi_v = span
    pts = []
    v = lo_v
    while v <= hi_v:
        kw = dict(rule_inputs)
        kw.pop("sex", None)
        kw.pop(param, None)          # 扫描变量不能同时当关键字参数
        sex = rule_inputs.get("sex", "男")
        if sex != "男":
            return []            # 只有男性方程，算不了
        val = fn(v, **kw)
        if val is not None:
            pts.append((v, val * 100))
        v += 1
    out = []
    for cut in RISK_CUTOFFS:
        band = [x for x, r in pts if abs(r - cut) <= tol]
        if band:
            out.append({"cutoff": cut, "lo": min(band), "hi": max(band),
                        "n": len(band)})
    return out


def describe_boundary(rule_id: str, tol: float = 0.5) -> str:
    """给审计视图用一句话说明偏差的实际后果。"""
    checks = [c for c in by_rule(rule_id) if c.inputs.get("sex") == "男"]
    if not checks:
        return "无可用算例"
    c = checks[0]
    win = boundary_window(c.inputs, "age", (40, 90), tol)
    if not win:
        return f"偏差 ±{tol}pp 不会触及分层切点（该输入下）"
    parts = [f"{w['cutoff']:.0f}% 切点附近 {w['lo']}–{w['hi']} 岁"
             f"（{w['n']} 个年龄点）" for w in win]
    return (f"偏差 ±{tol}pp 会翻转分层结论，范围：" + "；".join(parts))
