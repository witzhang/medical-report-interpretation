# -*- coding: utf-8 -*-
"""L2/L4 · 分层参考区间解析（框架 D8）。

借 health-assistant 的 ``biomarker_reference_ranges`` 设计（Apache-2.0，思路借鉴），
它踩过的坑与我们**完全同源**——其模块文档原话：

    全局单一区间会让「60 岁女性的正常范围对 25 岁男性不正常」。

三层回落：**分层命中 → 报告自带 → 不可评估**。

两条硬语义（照搬它的 ``_matches``）：

1. **最具体者胜**：在满足的候选里，约束维度最多的那条赢
2. **未知维度不算命中**：某行约束了 sex/age/unit，而患者该维度**未知**时，
   该行**不匹配** —— 不能假定未知年龄落在某个年龄段里

第 2 条就是「缺失 ≠ 阴性」下沉到数据层，比在文案里补一句可靠得多。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .normalize import _same_unit


@dataclass
class ReferenceRange:
    """一条参考区间，可带性别/年龄/单位分层。"""

    low: Optional[float] = None
    high: Optional[float] = None
    op: str = ""                    #: "" | ">" | "<" | ">=" | "<="
    unit: str = ""
    sex: Optional[str] = None       #: 男 / 女；None = 不限
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    text: str = ""
    source: str = ""                #: 出处（机构/标准/指南）
    note: str = ""


@dataclass
class ResolvedRange:
    low: Optional[float]
    high: Optional[float]
    op: str
    unit: str
    shape: str                      #: bounded | low-only | high-only | text | empty
    origin: str                     #: stratified | report | none
    source: str = ""
    matched_by: str = ""


def _shape(low, high, text) -> str:
    if low is not None and high is not None:
        return "bounded"
    if low is not None:
        return "low-only"
    if high is not None:
        return "high-only"
    return "text" if text else "empty"


def _matches(r: ReferenceRange, sex: Optional[str], age: Optional[float],
             unit: str) -> bool:
    """该行是否适用于这个人。

    **约束了的维度必须已知且满足** —— 未知一律不算命中。
    """
    if r.sex is not None:
        if sex is None or r.sex != sex:
            return False
    if r.age_min is not None or r.age_max is not None:
        if age is None:
            return False
        if r.age_min is not None and age < r.age_min:
            return False
        if r.age_max is not None and age > r.age_max:
            return False
    if r.unit:
        if not unit or not _same_unit(unit, r.unit):
            return False
    return True


def _specificity(r: ReferenceRange) -> int:
    """更高 = 更具体。性别 1 分、单位 1 分、年龄每界 1 分。"""
    return ((1 if r.sex is not None else 0)
            + (1 if r.unit else 0)
            + (1 if r.age_min is not None else 0)
            + (1 if r.age_max is not None else 0))


@dataclass
class RangeTable:
    """分层参考区间表。空表是合法状态 —— 此时一律回落到报告自带区间。"""

    entries: dict[str, list[ReferenceRange]] = field(default_factory=dict)

    def add(self, analyte_id: str, r: ReferenceRange) -> None:
        self.entries.setdefault(analyte_id, []).append(r)

    def __len__(self) -> int:
        return sum(len(v) for v in self.entries.values())

    def resolve(self, analyte_id: str, sex: Optional[str], age: Optional[float],
                unit: str, report_low: Optional[float] = None,
                report_high: Optional[float] = None, report_op: str = "",
                report_raw: str = "") -> ResolvedRange:
        """三层回落解析。"""
        cands = [r for r in self.entries.get(analyte_id, [])
                 if _matches(r, sex, age, unit)]
        if cands:
            best = max(cands, key=_specificity)
            why = []
            if best.sex is not None:
                why.append(f"性别={best.sex}")
            if best.age_min is not None or best.age_max is not None:
                why.append(f"年龄 {best.age_min}–{best.age_max}")
            if best.unit:
                why.append(f"单位={best.unit}")
            return ResolvedRange(
                best.low, best.high, best.op or "", best.unit or unit,
                _shape(best.low, best.high, best.text), "stratified",
                best.source, "、".join(why) or "通用")

        if report_low is not None or report_high is not None or report_raw:
            return ResolvedRange(
                report_low, report_high, report_op, unit,
                _shape(report_low, report_high, report_raw), "report",
                "报告自带", "本报告标注的区间")

        return ResolvedRange(None, None, "", unit, "empty", "none", "",
                             "无任何可用区间")


def flag_of(value: Optional[float], rr: ResolvedRange):
    """按解析出来的区间判位置轴。区间缺失 → None（不可评估）。"""
    from .schema import Finding
    if value is None:
        return None
    if rr.low is not None and rr.high is not None:
        return Finding.NORMAL if rr.low <= value <= rr.high else Finding.ABNORMAL
    if rr.low is not None:
        return Finding.NORMAL if value >= rr.low else Finding.ABNORMAL
    if rr.high is not None:
        return Finding.NORMAL if value <= rr.high else Finding.ABNORMAL
    return None
