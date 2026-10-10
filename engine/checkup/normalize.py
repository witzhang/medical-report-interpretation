# -*- coding: utf-8 -*-
"""L2 · 归一层：把「机构原始写法」映射到稳定的规范指标。

对应框架 D6–D8。设计约束来自对 6 个开源项目的实地读码
（见 ``体检分析/调研/03-开源项目借鉴.md``）：

1. **机构原始写法一律带 ``org_id``，绝不跨机构自动合并。**
2. **映射必须带段落 + 标本类型作用域**——尿液检查里两项都叫「白细胞」
   （定性"阴性" / 定量"3.5 个/ul"），只有 (段, 项, 值类型) 三元组才能区分。
   借 health-assistant 的目录/实例分离，但**补上它没有的 org/section 作用域**。
3. **匹配不上保留原文标 ``unmapped``，不猜不丢。**
4. **单位不一致要拦下来**，不能静默比较——RDW-CV / RDW-SD 就是活例。
5. **修复必须显式可审计**（借 Vital 的 ``biomarker_match_rules`` 人工确认思路），
   不能靠隐式启发式悄悄改数据。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .schema import AnalyteDefinition

#: 段落 → analyte_id 前缀
SECTION_PREFIX = {
    "血常规": "BLD", "生化检查": "CHEM", "免疫检查": "IMM",
    "尿液检查": "URI", "粪便检查": "STOOL",
    "一般项目": "VITAL", "眼科": "EYE",
    "彩超": "US", "CT": "CT", "心电图": "ECG",
    "经颅多普勒": "TCD", "骨密度": "BMD",
}

#: 段落 → 标本类型
SECTION_SPECIMEN = {
    "血常规": "blood", "生化检查": "blood", "免疫检查": "blood",
    "尿液检查": "urine", "粪便检查": "stool",
}

UNMAPPED = "UNMAPPED"


def normalize_name(s: str) -> str:
    """名称无损归一：只折叠**书写差异**（全角/半角、大小写、空白）。

    刻意不做的事（借鉴 Vital 的 ``normalise_unit`` 与 health-assistant 的教训）：
    不去括号、不去复数、不做子串匹配——那会把「高度相似」的中文项目名误合并。
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"\s+", "", s)
    return s.casefold()


@dataclass
class MatchRule:
    """人工确认的映射规则。

    最具体者优先：``value_type`` 有值 > 空；``section`` 有值 > 空。
    """

    analyte_id: str
    org_id: Optional[str] = None
    section: Optional[str] = None
    source_name: str = ""
    value_type: Optional[str] = None
    note: str = ""

    @property
    def specificity(self) -> int:
        return (2 if self.org_id else 0) + (1 if self.section else 0) \
            + (1 if self.value_type else 0)


@dataclass
class RepairRule:
    """显式的、可审计的数据修复规则（不是隐式启发式）。

    用途举例：2024 报告把 RDW-CV / RDW-SD 两个**标签写反了**，而**单位列是对的**
    （40.30 配 fL、13.20 配 ％）。按名字比会得出"下降 68%"的假趋势。
    这条规则让「按单位重归属」成为一次有记录的动作，而不是悄悄改数。
    """

    rule_id: str
    section: str
    kind: str                  #: swap_by_unit
    members: list[str]         #: 参与重归属的 analyte_id
    note: str = ""


@dataclass
class Resolution:
    """一次归一的结果。"""

    analyte_id: str
    matched_by: str = "unmapped"    #: rule | seed | unmapped
    unit_status: str = "unknown"    #: ok | mismatch | unknown
    repaired_by: str = ""
    note: str = ""


class AnalyteCatalog:
    """规范指标目录 + 映射规则 + 修复规则。"""

    def __init__(self, analytes: Iterable[AnalyteDefinition] = (),
                 rules: Iterable[MatchRule] = (),
                 repairs: Iterable[RepairRule] = (),
                 version: str = "0"):
        self.version = version
        self.analytes: dict[str, AnalyteDefinition] = {a.analyte_id: a for a in analytes}
        self.rules: list[MatchRule] = list(rules)
        self.repairs: list[RepairRule] = list(repairs)
        #: (section, 原文) → analyte_id，用于种子快速命中。
        #: **歧义键自动剔除**——同一 (段, 名称) 对应多个 analyte 时（如尿液检查里
        #: 两项都叫「白细胞」），种子表命中会随机挑一个，必须交给显式 MatchRule。
        tmp: dict[tuple[Optional[str], str], set[str]] = {}
        for a in self.analytes.values():
            if a.org_id is None:
                continue
            keys = [(a.section, normalize_name(a.name))]
            keys += [(a.section, normalize_name(al)) for al in a.aliases]
            for k in keys:
                tmp.setdefault(k, set()).add(a.analyte_id)
        self._seed: dict[tuple[Optional[str], str], str] = {
            k: next(iter(v)) for k, v in tmp.items() if len(v) == 1}
        self.ambiguous: set[tuple[Optional[str], str]] = {
            k for k, v in tmp.items() if len(v) > 1}

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "analytes": [a.__dict__ for a in self.analytes.values()],
            "match_rules": [r.__dict__ for r in self.rules],
            "repair_rules": [{**r.__dict__} for r in self.repairs],
        }

    @classmethod
    def load(cls, path: str) -> "AnalyteCatalog":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(
            analytes=[AnalyteDefinition(**a) for a in d.get("analytes", [])],
            rules=[MatchRule(**r) for r in d.get("match_rules", [])],
            repairs=[RepairRule(**r) for r in d.get("repair_rules", [])],
            version=d.get("version", "0"))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------ 归一

    def resolve(self, org_id: Optional[str], section: Optional[str],
                source_name: str, value_type: str = "numeric",
                unit: str = "") -> Resolution:
        """(机构, 段落, 原文, 值类型) → 规范指标。"""
        name = normalize_name(source_name)

        # 1) 人工确认的映射规则，最具体者优先
        cands = [r for r in self.rules
                 if normalize_name(r.source_name) == name
                 and (r.section is None or r.section == section)
                 and (r.org_id is None or r.org_id == org_id)
                 and (r.value_type is None or r.value_type == value_type)]
        if cands:
            rule = max(cands, key=lambda r: r.specificity)
            return self._finish(rule.analyte_id, "rule", unit)

        # 2) 目录种子（仅同机构内命中）
        aid = self._seed.get((section, name))
        if aid:
            return self._finish(aid, "seed", unit)

        # 3) 匹配不上：保留原文，不猜
        return Resolution(analyte_id=UNMAPPED, matched_by="unmapped",
                          note=f"未映射: {section}/{source_name}")

    def _finish(self, analyte_id: str, matched_by: str, unit: str) -> Resolution:
        """判定单位是否与规范单位一致。"""
        a = self.analytes.get(analyte_id)
        res = Resolution(analyte_id=analyte_id, matched_by=matched_by)
        if a is None or not a.canonical_unit:
            return res
        res.unit_status = "ok" if _same_unit(unit, a.canonical_unit) else "mismatch"
        return res

    def repair_by_unit(self, section: str, analyte_id: str, unit: str) -> Optional[Resolution]:
        """当「名字对不上单位」时，尝试按**单位**重归属到同段的另一个指标。

        只在显式声明了 ``RepairRule`` 的段落生效，并记录 ``repaired_by``，
        保证这次改动可审计、可回滚。
        """
        for rr in self.repairs:
            if rr.section != section or analyte_id not in rr.members:
                continue
            for other in rr.members:
                if other == analyte_id:
                    continue
                a = self.analytes.get(other)
                if a and a.canonical_unit and _same_unit(unit, a.canonical_unit):
                    return Resolution(analyte_id=other, matched_by="repair",
                                      unit_status="ok", repaired_by=rr.rule_id,
                                      note=rr.note)
        return None


#: 单位等价。**只做等价判断，不做换算**——真换算需要分子量，
#: 且必须带出处（框架 D7）。这里刻意只处理「写法不同但其实是同一个」。
_UNIT_EQUIV = {
    "10^9/l": "10*9/L", "10*9/l": "10*9/L", "x109/l": "10*9/L",
    "x10^9/l": "10*9/L", "×10^9/l": "10*9/L",      # ×10^9/L / x10^9/L
    "10^12/l": "10*12/L", "10*12/l": "10*12/L",
    "x10^12/l": "10*12/L", "×10^12/l": "10*12/L",
    "10^3/ul": "10*9/L", "10^3/μl": "10*9/L",     # 10^3/μL ≡ 10^9/L
    "%": "%", "％": "%",
    "fl": "fL", "pg": "pg", "g/l": "g/L",
}


def _norm_unit(u: str) -> str:
    s = unicodedata.normalize("NFKC", (u or "").strip()).replace(" ", "")
    return _UNIT_EQUIV.get(s.casefold(), s)


def _same_unit(a: str, b: str) -> bool:
    return bool(a) and _norm_unit(a) == _norm_unit(b)
