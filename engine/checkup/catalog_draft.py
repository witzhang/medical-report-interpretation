# -*- coding: utf-8 -*-
"""归一冷启动：给新机构的项目名**起草**映射规则（框架 D6 的下一半）。

上一轮让模板能自动起草；这一轮让**词表**也能。拿到一家新机构时，它的项目名
（「白细胞」「谷丙转氨酶」…）在本机构的词表里可能一个都没有，于是全部标
``UNMAPPED``。手工加规则太慢，本模块提出草案。

两条不可逾越的线（来自对 6 个开源项目读码的结论）：

1. **绝不自动合并。** 输出是 ``confirmed=False`` 的草案，人工确认后才进词表。
2. **不做模糊子串匹配。** health-assistant 用 ``code.text`` 子串 + 去括号 +
   去复数兜底，中国检验项目名高度相似（白细胞 / 白细胞(高倍视野)），
   那样会静默并错。这里只做**归一化后的精确匹配**，靠**单位**与**标本类型**
   做消歧——单位是上一轮 RDW 那件事教给我们的可靠信号。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from .normalize import AnalyteCatalog, normalize_name, _same_unit
from .schema import AnalyteDefinition

#: analyte_id 前缀 → 标本类型
PREFIX_SPECIMEN = {"BLD": "blood", "CHEM": "blood", "IMM": "blood",
                   "URI": "urine", "STOOL": "stool"}


def specimen_of(analyte_id: str) -> str:
    return PREFIX_SPECIMEN.get(analyte_id.split(".")[0], "")


def specimen_of_section(section: str) -> str:
    """从**段名**推断标本类型。

    新机构的段名五花八门（血液检查 / 血常规 / 全血细胞分析），但都含「血」；
    尿/便同理。有了这一条，单位之外又多一个独立证据。
    """
    if not section:
        return ""
    if "尿" in section:
        return "urine"
    if "便" in section or "粪" in section:
        return "stool"
    if "血" in section:
        return "blood"
    return ""


@dataclass
class RuleProposal:
    """一条映射规则草案。"""

    org_id: str
    section: str
    source_name: str
    value_type: str
    unit: str
    analyte_id: Optional[str]     #: None 表示提议**新建**指标
    confidence: float
    reasons: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    new_analyte: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


def propose(items: list[tuple[str, str, str, str]],
            catalog: AnalyteCatalog, org_id: str) -> list[RuleProposal]:
    """给一批未映射的 (段, 项目, 值类型, 单位) 起草规则。

    打分用四项**独立**证据：归一化精确同名 0.45 · 单位一致 0.20 ·
    标本类型一致 0.15 · 值类型一致 0.20。

    多个候选并列且无法消歧时，**置信度压低并列出全部候选**，交给人工 ——
    绝不挑一个看起来像的。
    """
    by_name: dict[str, list[AnalyteDefinition]] = {}
    for a in catalog.analytes.values():
        for nm in [a.name, *a.aliases]:
            by_name.setdefault(normalize_name(nm), []).append(a)

    out: list[RuleProposal] = []
    for section, name, vt, unit in items:
        norm = normalize_name(name)
        cands = by_name.get(norm, [])
        if not cands:
            out.append(RuleProposal(
                org_id=org_id, section=section, source_name=name,
                value_type=vt, unit=unit, analyte_id=None, confidence=0.0,
                reasons=["词表里没有同名或同别名的指标 → 提议**新建**"],
                new_analyte=AnalyteDefinition(
                    analyte_id=f"NEW.{name}", name=name, value_type=vt,
                    canonical_unit=unit, org_id=org_id, section=section,
                    confirmed=False).__dict__))
            continue

        # 四项独立证据累加（合计 1.0）：
        #   归一化精确同名 0.45 · 单位一致 0.20 · 标本类型一致 0.15 · 值类型一致 0.20
        # **值类型必须参与**——目录正是靠它把「尿液白细胞(定性)」与
        # 「尿液白细胞(定量)」分成两个指标的；不看它，同名撞键就只剩单位一条线索。
        sec_spec = specimen_of_section(section)
        scored: list[tuple[float, AnalyteDefinition, list[str]]] = []
        for a in cands:
            score, why = 0.45, ["归一化精确同名"]
            if unit and a.canonical_unit and _same_unit(unit, a.canonical_unit):
                score += 0.20
                why.append(f"单位一致（{unit}）")
            elif unit and a.canonical_unit:
                why.append(f"单位不一致（观测 {unit} vs 规范 {a.canonical_unit}）")
            a_spec = specimen_of(a.analyte_id)
            if a_spec and a_spec == sec_spec:
                score += 0.15
                why.append(f"标本类型一致（{a_spec}）")
            if vt and a.value_type and vt == a.value_type:
                score += 0.20
                why.append(f"值类型一致（{vt}）")
            scored.append((round(score, 2), a, why))

        scored.sort(key=lambda x: -x[0])
        top = scored[0]
        tie = [s for s in scored if abs(s[0] - top[0]) < 1e-9]
        reasons = list(top[2])
        conf = top[0]
        if len(tie) > 1:
            conf = round(min(conf, 0.5) * 0.7, 2)
            reasons.append(f"**{len(tie)} 个候选同分，无法消歧** → 必须人工指定")
        if conf < 0.8:
            reasons.append("置信度不足 0.8，必须人工确认")
        out.append(RuleProposal(
            org_id=org_id, section=section, source_name=name, value_type=vt,
            unit=unit, analyte_id=top[1].analyte_id, confidence=conf,
            reasons=reasons, candidates=[a.analyte_id for _, a, _ in scored[:4]]))
    return out


def unmapped_items(obs) -> list[tuple[str, str, str, str]]:
    """从观测里挑出未映射的 (段, 项目, 值类型, 单位)，去重。"""
    seen, out = set(), []
    for o in obs:
        if o.analyte_id != "UNMAPPED":
            continue
        k = (o.section, o.item_raw, o.value_type, o.unit)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return sorted(out)


# ---------------------------------------------------------- 分层确认（A12）

#: 高置信门槛：≥ 它 = 批量通过（四项证据分满分 1.0 里的 0.8）
CONFIRM_THRESHOLD = 0.8
#: 抽样率：高置信里随机抽 10% 复核（**全部**低置信都要逐条看）
SAMPLE_RATE = 0.1


@dataclass
class LayeredDraft:
    """分层确认结果（A12）：批量通过 / 抽样复核 / 低置信逐条。"""

    auto_pass: list[RuleProposal]
    sample: list[RuleProposal]          #: 随机抽的高置信 + **全部**低置信
    low: list[RuleProposal]             #: 低置信（全部可见，不抽样）
    threshold: float = CONFIRM_THRESHOLD
    sample_rate: float = SAMPLE_RATE
    seed: int = 0

    def render(self) -> str:
        L = [f"分层确认（阈值 {self.threshold}，抽样率 {self.sample_rate:.0%}，"
             f"种子 {self.seed}）：",
             f"  · 批量通过（≥{self.threshold}）：{len(self.auto_pass)} 条",
             f"  · 抽样复核：{len(self.sample)} 条"
             f"（随机高置信 {len(self.sample) - len(self.low)} + "
             f"**全部**低置信 {len(self.low)}）",
             f"  · 低置信逐条：{len(self.low)} 条（全部可见）"]
        for p in self.sample:
            tag = "低置信" if p.confidence < self.threshold else "抽样"
            L.append(f"    [{tag}] {p.section}/{p.source_name} → "
                     f"{p.analyte_id or '[新建]'}"
                     f"   confidence={p.confidence}")
        return "\n".join(L)


def stratify(proposals: list[RuleProposal], *,
             threshold: float = CONFIRM_THRESHOLD,
             sample_rate: float = SAMPLE_RATE, seed: int = 0) -> LayeredDraft:
    """把草案分层（A12）：**高置信批量通过 + 抽样复核**，低置信全部逐条。

    为什么敢批量通过：高置信的门槛是**四项独立证据**（归一化精确同名 + 单位 +
    标本类型 + 值类型）里拿到 ≥3 项，实测这类项的错并率极低；而低置信项
    （同名撞键、单位矛盾、词表里没有）才是真正需要人一条条看的。

    ⚠️ **绝不自动合并**的纪律不变：这里只是把"人要看多少条"从 158 降到 ≈30，
    产物仍是 ``confirmed=false`` 的草案；批量通过 = 人**抽检通过**，不是入库。

    抽样**可复现**（同 ``seed`` 同结果）—— 与项目"双跑一致"的纪律一致。
    """
    import random  # noqa: PLC0415
    high = [p for p in proposals if p.confidence >= threshold]
    low = [p for p in proposals if p.confidence < threshold]
    rng = random.Random(seed)
    k = max(1, round(len(high) * sample_rate)) if high else 0
    picked = rng.sample(high, k) if k else []
    sample = sorted(picked + low, key=lambda p: (p.confidence, p.section,
                                                 p.source_name))
    return LayeredDraft(auto_pass=high, sample=sample, low=low,
                        threshold=threshold, sample_rate=sample_rate, seed=seed)


def save_draft(proposals: list[RuleProposal], path: str,
               layered: Optional[LayeredDraft] = None) -> None:
    """落盘草案。

    ``confirmed=false`` 写死在文件里（**绝不自动合并**）；给了 ``layered``
    就把分层结论一并写进去（自动通过数 / 抽样清单 / 低置信清单），
    让人只看抽样那几十条。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    doc = {"confirmed": False,
           "note": "自动起草的映射规则草案，**人工确认后**才可并入 catalog",
           "rules": [p.to_dict() for p in proposals]}
    if layered is not None:
        doc["layered"] = {
            "threshold": layered.threshold,
            "sample_rate": layered.sample_rate,
            "seed": layered.seed,
            "auto_pass_count": len(layered.auto_pass),
            "review_sample": [p.to_dict() for p in layered.sample],
            "review_note": ("批量通过 = 抽检通过，不 = 入库；低置信全部在 "
                            "review_sample 里"),
        }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
