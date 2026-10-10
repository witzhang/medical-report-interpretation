# -*- coding: utf-8 -*-
"""L7 · 红旗检查（框架 D19）。

依据：《健康体检重要异常结果管理专家共识（试行版）》
中华健康管理学杂志 2019;13(2):97-101. DOI 10.3760/cma.j.issn.1674-0815.2019.02.001

该共识的 **A 类**定义（逐字）：

    需要立即进行临床干预，否则将危及生命或导致严重不良后果的异常结果。

命中 A 类必须**强制就医提示，不得用「仅供参考」软化**（框架 §三 G8）。

⚠️ **阈值不能硬编码为全局常量。** 共识自陈：
「受各实验室检测系统不同影响，导致医学决定水平不同，仅提供参考」。
因此这里的 ``DEFAULT_A`` 只是**默认值**，可被机构级覆盖文件替换。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .schema import NormalizedObservation

#: 相对参考区间上限的倍数（如 ALT ≥15 倍上限）
MULTIPLE = "ge_uln_multiple"


@dataclass
class RedFlagRule:
    rule_id: str
    analyte_id: str
    op: str                     #: le | ge | ge_uln_multiple
    value: float
    unit: str
    label: str
    condition: Optional[str] = None   #: no_diabetes | diabetes | None
    note: str = ""
    #: **判据里包含本工具读不到的上下文**（如血压需要"伴急性症状/复测值"），
    #: 命中后必须提示人工确认。**与"严重程度"无关** —— 化验阈值是自足的，
    #: 不设此位。患者版据此决定是否附上「请立即携报告就医确认」。
    #:
    #: 曾经写作 ``needs_human=bool(r.note)``，而默认规则**全都没有 note**，
    #: 于是这个机制**永远不生效**（死代码）；而且它把文档字段当成了行为开关。
    needs_human: bool = False


#: A 类默认阈值（可被机构覆盖）
DEFAULT_A: list[RedFlagRule] = [
    RedFlagRule("RF.A.HGB", "BLD.血红蛋白浓度", "le", 60, "g/L",
                "血红蛋白 ≤60 g/L（首次）"),
    RedFlagRule("RF.A.PLT.LOW", "BLD.血小板", "le", 30, "10^9/L",
                "血小板 ≤30.0×10⁹/L（首次）或有明显出血倾向"),
    RedFlagRule("RF.A.PLT.HIGH", "BLD.血小板", "ge", 1000, "10^9/L",
                "血小板 ≥1000.0×10⁹/L"),
    RedFlagRule("RF.A.WBC", "BLD.白细胞", "le", 1.0, "10^9/L",
                "白细胞 ≤1.0×10⁹/L"),
    RedFlagRule("RF.A.NEU", "BLD.中性粒细胞绝对值", "le", 0.5, "10^9/L",
                "中性粒细胞绝对值 ≤0.5×10⁹/L"),
    RedFlagRule("RF.A.ALT", "CHEM.丙氨酸氨基转移酶", MULTIPLE, 15, "×ULN",
                "ALT ≥15 倍参考上限"),
    RedFlagRule("RF.A.AST", "CHEM.天门冬氨基转移酶", MULTIPLE, 15, "×ULN",
                "AST ≥15 倍参考上限"),
    RedFlagRule("RF.A.TBIL", "CHEM.总胆红素", MULTIPLE, 5, "×ULN",
                "总胆红素 ≥5 倍参考上限"),
    RedFlagRule("RF.A.SCR", "CHEM.肌酐", "ge", 707, "umol/L",
                "血肌酐 ≥707 μmol/L（首次）"),
    RedFlagRule("RF.A.FPG.LOW.NODM", "CHEM.葡萄糖", "le", 2.8, "mmol/L",
                "低血糖：FPG ≤2.8 mmol/L（无糖尿病史）", condition="no_diabetes"),
    RedFlagRule("RF.A.FPG.LOW.DM", "CHEM.葡萄糖", "le", 3.9, "mmol/L",
                "低血糖：FPG ≤3.9 mmol/L（糖尿病史）", condition="diabetes"),
    RedFlagRule("RF.A.FPG.HIGH.DM", "CHEM.葡萄糖", "ge", 16.7, "mmol/L",
                "高血糖：FPG ≥16.7 mmol/L（糖尿病史）", condition="diabetes"),
]


@dataclass
class RedFlagHit:
    rule_id: str
    label: str
    year: str
    value: float
    unit: str
    evidence: str
    needs_human: bool = False


def load_rules(override_path: Optional[str] = None) -> list[RedFlagRule]:
    """载入 A 类阈值。机构若有自己的危急值清单，从 override 文件替换。"""
    if override_path and os.path.exists(override_path):
        with open(override_path, encoding="utf-8") as f:
            d = json.load(f)
        return [RedFlagRule(**r) for r in d.get("a_class", [])]
    return list(DEFAULT_A)


def check(obs: list[NormalizedObservation],
          rules: Optional[list[RedFlagRule]] = None,
          profile: Optional[dict] = None) -> list[RedFlagHit]:
    """扫全部观测，返回命中的 A 类红旗。"""
    rules = rules if rules is not None else load_rules()
    profile = profile or {}
    dm = bool(profile.get("diabetes", False))
    hits: list[RedFlagHit] = []

    for o in obs:
        if o.value is None:
            continue
        for r in rules:
            if r.analyte_id and o.analyte_id != r.analyte_id:
                continue
            if r.condition == "diabetes" and not dm:
                continue
            if r.condition == "no_diabetes" and dm:
                continue
            hit = False
            if r.op == "le":
                hit = o.value <= r.value
            elif r.op == "ge":
                hit = o.value >= r.value
            elif r.op == MULTIPLE:
                # 相对**该年报告自己的**参考上限——不是全局常量
                if o.ref_high:
                    hit = o.value >= r.value * o.ref_high
            if hit:
                hits.append(RedFlagHit(
                    rule_id=r.rule_id, label=r.label, year=o.year,
                    value=o.value, unit=o.unit or r.unit,
                    evidence=o.evidence, needs_human=r.needs_human))

    # 血压：共识原文是「收缩压 ≥180 mmHg **和/或** 舒张压 ≥110 mmHg
    # **伴急性症状**，或安静休息后复测仍达此标准」——是 OR 不是 AND。
    # 症状与复测值本工具都读不到，故命中即标 needs_human。
    sbp = [o for o in obs if o.analyte_id == "VITAL.收缩压" and o.value is not None]
    dbp = [o for o in obs if o.analyte_id == "VITAL.舒张压" and o.value is not None]
    dmap = {o.year: o for o in dbp}
    for s_ in sbp:
        d = dmap.get(s_.year)
        sys_v = s_.value >= 180
        dia_v = d is not None and d.value >= 110
        if sys_v or dia_v:
            which = ("收缩压 ≥180" if sys_v else "") +                     ("；" if sys_v and dia_v else "") +                     ("舒张压 ≥110" if dia_v else "")
            hits.append(RedFlagHit(
                rule_id="RF.A.BP", label=f"血压达危急值（{which}）",
                year=s_.year, value=s_.value, unit="mmHg",
                evidence=f"{s_.evidence}；{d.evidence if d else '无舒张压记录'}",
                needs_human=True))

    seen = set()
    out = []
    for h in hits:
        k = (h.rule_id, h.year)
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out
