# -*- coding: utf-8 -*-
"""鲁棒性扰动台（框架 D21 的一半）。

为什么需要它：整套引擎对「新机构第一次见到」的假设（模板指纹、别名词表、
单位归一、段名归一）目前只有**一家机构**的数据在验证，等于自证。在拿到
第二家机构的报告之前，能做的最近似检验是——**故意把输入改坏，看守卫会不会响**。

核心判据不是"能不能跑通"，而是：

    错误必须被**拦下**或**降级并明说**，绝不能静默接受。

每一类扰动都声明它**期望触发哪个守卫**；守卫没响就是缺陷。
"""
from __future__ import annotations

import copy
from dataclasses import replace
from typing import Callable, Optional

from .schema import DocumentMeta, NormalizedObservation


# ---------------------------------------------------------------- 基础操作

def _map(obs: list[NormalizedObservation],
         pred: Callable[[NormalizedObservation], bool],
         fn: Callable[[NormalizedObservation], NormalizedObservation]
         ) -> list[NormalizedObservation]:
    return [fn(o) if pred(o) else o for o in obs]


# ---------------------------------------------------------------- 各类扰动

def scale_value(obs, analyte_id: str, year: str, factor: float):
    """把某一项某一年的数值乘一个系数 —— 模拟录入错位（如小数点错位）。"""
    return _map(obs, lambda o: o.analyte_id == analyte_id and o.year == year,
                lambda o: replace(o, value=o.value * factor if o.value else o.value,
                                  value_text=f"{o.value * factor:g}" if o.value else o.value_text))


def replace_unit(obs, analyte_id: str, new_unit: str, year: Optional[str] = None):
    """改写单位 —— 模拟换机构/换检测系统。"""
    return _map(obs, lambda o: o.analyte_id == analyte_id
                and (year is None or o.year == year),
                lambda o: replace(o, unit=new_unit))


def blank_value(obs, analyte_id: str, year: str):
    """把数值改成非数值文本 —— 模拟定性化或抽取失败。"""
    return _map(obs, lambda o: o.analyte_id == analyte_id and o.year == year,
                lambda o: replace(o, value=None, value_type="qualitative",
                                  value_text="未见异常", flag=None))


def drop_section(obs, section: str, year: str):
    """整段删除 —— 模拟该年漏抽或未做该检查。"""
    return [o for o in obs if not (o.section == section and o.year == year)]


def rename_section(obs, old: str, new: str):
    """段名换成别称 —— 模拟换机构的命名习惯。"""
    return _map(obs, lambda o: o.section == old,
                lambda o: replace(o, section=new))


def drop_ref_range(obs, analyte_id: str, year: Optional[str] = None):
    """抹掉参考区间 —— 模拟报告不给区间。"""
    return _map(obs, lambda o: o.analyte_id == analyte_id
                and (year is None or o.year == year),
                lambda o: replace(o, ref_raw="", ref_low=None, ref_high=None,
                                  ref_op="", flag=None))


def swap_values(obs, aid_a: str, aid_b: str, year: str):
    """把两项在某一年的**值对调** —— 模拟断行回填错位。

    内部恒等式（直接+间接=总胆红素）是唯一能抓住这类错的守卫。
    注意要挑**会破坏恒等式**的一对：直接胆红素与间接胆红素互换，和不变，
    等于没扰动；必须拿总胆红素去换其中一项。
    """
    ia = next((i for i, o in enumerate(obs)
               if o.analyte_id == aid_a and o.year == year), None)
    ib = next((i for i, o in enumerate(obs)
               if o.analyte_id == aid_b and o.year == year), None)
    if ia is None or ib is None:
        return obs
    A, B = obs[ia], obs[ib]
    out = list(obs)
    out[ia] = replace(A, value=B.value, value_text=B.value_text, flag=B.flag)
    out[ib] = replace(B, value=A.value, value_text=A.value_text, flag=A.flag)
    return out


def drop_age(metas: dict[str, DocumentMeta]) -> dict[str, DocumentMeta]:
    """抹掉年龄 —— eGFR / FIB-4 / China-PAR 都应落到 not_assessable。"""
    return {y: replace(m, age=None) for y, m in metas.items()}


def flip_sex(metas: dict[str, DocumentMeta]) -> dict[str, DocumentMeta]:
    """把性别改成女 —— 男性方程应按 not_applicable 拒绝，而不是硬套。"""
    return {y: replace(m, sex="女") for y, m in metas.items()}


def duplicate_item(obs, analyte_id: str, year: str,
                   new_item: str, new_value: float):
    """在同一段里插入一条同名但不同值的项 —— 模拟同名撞键。"""
    src = next((o for o in obs if o.analyte_id == analyte_id and o.year == year), None)
    if src is None:
        return obs
    clone = replace(src, item_raw=new_item, analyte_id=src.analyte_id,
                    occurrence=2, value=new_value, value_text=f"{new_value:g}",
                    evidence=f"注入的同名项 (p{src.page_no})")
    return obs + [clone]


# ---------------------------------------------------------------- 核对

#: (扰动名, 期望守卫, 说明)
EXPECTATIONS = [
    ("等价单位改写", "不报不可比",
     "10^9/L → 10^3/μL 是**等价**，不该被当成单位不可比"),
    ("不可比单位改写", "拒绝判趋势",
     "10^9/L → 10^12/L 真的不同类，必须拒绝比较"),
    ("数值放大 10 倍", "Delta Check 报警",
     "小数点错位这类录入错误必须被差值检查抓住"),
    ("数值定性化", "位置轴 undetermined",
     "值不可解析时判 undetermined，不能默认正常"),
    ("整段删除", "覆盖率报警",
     "某年整段漏抽要能被发现"),
    ("段名换成别称", "不静默合并",
     "未登记的段名应标 unmapped，不猜不丢"),
    ("参考区间抹掉", "位置轴 not_assessable",
     "没有区间就不能判正常/异常"),
    ("两项数值对调", "内部恒等式报警",
     "断行回填错位只有恒等式能抓"),
    ("年龄缺失", "组合层 not_assessable",
     "缺前置条件的规则不能按其他项推断"),
    ("性别改为女", "China-PAR not_applicable",
     "只实现了男性方程，不能硬套"),
    ("同名撞键注入", "出现同段同名的两条记录",
     "同名不同物必须能被区分或至少可见"),
]
