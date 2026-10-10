# -*- coding: utf-8 -*-
"""L7 · 输出层：措辞分层 + 三视图（框架 D17–D19）。

措辞三档（框架 §五 A-1.3）：

======  ==================================================  ==============
层级    允许的形状                                          锚点
======  ==================================================  ==============
L1      「本次检测 LDL-C 3.12 mmol/L，报告参考区间 <3.4」    FDA CDS 第 2 条
信息性   任何疾病名、风险分级词都**不许出现**
L2      「依据《中国血脂管理指南（2023年）》表7，该水平        FDA CDS 第 3、4 条
建议性   对应低危，目标 <3.4。是否治疗由临床医师判断」
L3      「您患有高脂血症」「按此方案服用他汀」                《互联网诊疗管理办法》
诊断性   ——**本工具必须不产出**                              第十六条
======  ==================================================  ==============

界面上另有一条硬规则：**红旗优先级最高**，命中时置顶，不得被
「仅供参考」之类的软化措辞冲淡。
"""
from __future__ import annotations

import re
from typing import Optional

from .judge import Judgement
from .redflags import RedFlagHit
from .rules import RULES_BY_ID
from .schema import Conclusion, ConclusionClass, DerivedResult, Finding

#: 诊断性措辞（L3）——出现即视为越界
DIAGNOSTIC_PATTERNS = [
    r"您患有", r"您已患", r"确诊为", r"诊断为", r"可确诊", r"可诊断为",
    r"按此方案", r"建议服用", r"开具", r"处方", r"每日\s*\d+\s*(?:mg|g|片|粒)",
    r"服用[\u4e00-\u9fff]{2,8}(?:片|胶囊|mg)",
]

#: 建议性措辞（L2）
ADVISORY_PATTERNS = [r"建议", r"应(?:当)?", r"需(?:要)?", r"请", r"随访", r"复查"]

_COMPILED_D = [re.compile(p) for p in DIAGNOSTIC_PATTERNS]
_COMPILED_A = [re.compile(p) for p in ADVISORY_PATTERNS]


# ---------------------------------------------------------------- 措辞分层

def classify_wording(text: str) -> str:
    """返回 L1 / L2 / L3。"""
    if any(p.search(text) for p in _COMPILED_D):
        return "L3"
    if any(p.search(text) for p in _COMPILED_A):
        return "L2"
    return "L1"


def lint(conclusions: list[Conclusion]) -> list[str]:
    """扫全部结论文本，返回越界项（应为空）。"""
    bad = []
    for c in conclusions:
        lv = classify_wording(c.text)
        if lv == "L3":
            bad.append(f"{c.conclusion_id} 出现诊断性措辞：{c.text[:60]}")
        if c.assertion_type == "diagnostic":
            bad.append(f"{c.conclusion_id} assertion_type=diagnostic")
    return bad


# ---------------------------------------------------------------- 三视图

def _flag_mark(f: Optional[Finding]) -> str:
    return {Finding.ABNORMAL: "↑↓", Finding.NORMAL: "", None: ""}.get(f, "")


def render_clinical(judgements: list[Judgement],
                    derived: list[DerivedResult],
                    red: list[RedFlagHit]) -> str:
    """门诊数值版：给医生看的纯数值，尽量不带解释。"""
    L: list[str] = ["# 门诊数值版", ""]
    L.append("> 只列**异常项**、**变化超出噪声项**与**关键复合指标**。"
             "原始报告见附页。")
    L.append("")

    if red:
        L += ["## ⚠ 危急值（需立即处理）", ""]
        for h in red:
            L.append(f"- **{h.year} {h.label}** —— 实测 {h.value} {h.unit}"
                     + ("（需人工确认症状/复测值）" if h.needs_human else ""))
        L.append("")

    # 年份序列一律**从数据推导**（P0-3）：此前的 2024/2025/2026 是首例三年
    # 报告的年份，换成任何别的年份集合都会出现"空列/整节为空"。
    years = sorted({y for j in judgements for y in j.series})
    if not years:
        years = sorted({d.year for d in derived})
    first, last = (years[0], years[-1]) if years else ("", "")
    last_pair = f"{first}→{last}"

    ab = [j for j in judgements if j.position.get(last) == Finding.ABNORMAL.value]
    if ab:
        head = " | ".join(years)
        L += [f"## {last} 年超出参考区间", "",
              f"| 项目 | {head} | 参考区间({last}) | 单位 |",
              "|---" * (len(years) + 3) + "|"]
        for j in sorted(ab, key=lambda x: x.canonical_name):
            vals = " | ".join(
                f"**{j.series.get(y, '')}**" if y == last else f"{j.series.get(y, '')}"
                for y in years)
            L.append(f"| {j.canonical_name} | {vals} | "
                     f"{j.ref_range.get(last, '')} | {j.unit} |")
        L.append("")

    ch = [j for j in judgements if j.change.get(last_pair) == "beyond_noise"]
    if ch:
        head = " | ".join(years)
        L += ["## 变化超出噪声（RCV）", "",
              f"| 项目 | CVi% | RCV阈值% | {head} | Δ{first}→{last} | 稳健性 |",
              "|---" * (len(years) + 4) + "|"]
        for j in sorted(ch, key=lambda x: -abs(x.change_pct.get(last_pair) or 0)):
            vals = " | ".join(f"{j.series.get(y, '')}" for y in years)
            L.append(f"| {j.canonical_name} | {j.cv_i:.1f} | {j.rcv_threshold} | "
                     f"{vals} | "
                     f"{j.change_pct.get(last_pair):+.1f}% | {j.robustness} |")
        L += ["", "> CVa 为假设值 3%；RCV 阈值不随采样间隔变化，但间隔越长越迟钝。"]
        L.append("")

    if derived:
        dyears = sorted({d.year for d in derived})
        head = " | ".join(dyears)
        L += ["## 复合指标与分层", "",
              f"| 规则 | {head} |", "|---" * (len(dyears) + 1) + "|"]
        by_rule: dict[str, dict[str, DerivedResult]] = {}
        for d in derived:
            by_rule.setdefault(d.name, {})[d.year] = d
        for name, ys in by_rule.items():
            cells = []
            for y in dyears:
                d = ys.get(y)
                if d is None:
                    cells.append("")
                elif d.status == "not_assessable":
                    cells.append("不可评估")
                elif d.status == "not_applicable":
                    cells.append("不适用")
                else:
                    cells.append(
                        f"{d.value if d.value is not None else ''} {d.unit}".strip()
                        or d.band)
            L.append(f"| {name} | " + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L)


def render_patient(conclusions: list[Conclusion],
                   red: list[RedFlagHit],
                   notices: Optional[list[str]] = None) -> str:
    """患者话版：结论按共同上游归并，措辞只走 L1/L2。

    ``notices``（P0-7）：必须**置顶**的提示（如"本次有报告未能读取"）——
    这类信息不许沉在「数据本身存疑的」小节里，那样读起来像"没发现问题"。
    """
    L: list[str] = ["# 体检结果说明", ""]
    for n in (notices or []):
        L += [n, ""]

    if red:
        L += ["## ⚠ 请立即就医", ""]
        for h in red:
            L.append(f"- **{h.year} {h.label}**")
            if h.needs_human:
                L.append("  - 本工具读不到症状与复测值，请立即携报告就医确认")
        L += ["", "以上为危急值，**不要等待**，请立即到医疗机构就诊。", ""]

    labels = {
        ConclusionClass.CONFIRMED.value: "## 已确认的问题",
        ConclusionClass.TO_VERIFY.value: "## 需要进一步核查的",
        ConclusionClass.DATA_QUALITY.value: "## 数据本身存疑的",
        ConclusionClass.TO_FOLLOWUP.value: "## 需要按建议随访的",
    }
    order = [ConclusionClass.CONFIRMED.value, ConclusionClass.TO_VERIFY.value,
             ConclusionClass.DATA_QUALITY.value, ConclusionClass.TO_FOLLOWUP.value]
    for cls in order:
        rows = [c for c in conclusions if c.cls == cls]
        if not rows:
            continue
        L += [labels[cls], ""]
        for c in rows:
            L.append(f"- {c.text}")
            if c.action_hint:
                L.append(f"  - → {c.action_hint}")
        L.append("")

    L += ["---", "",
          "> 本说明只做「数值 vs 参考区间」与「变化是否超出噪声」的计算，"
          "并转述体检机构自己的建议。**它不是诊断**，"
          "是否患病、是否需要治疗，一律由临床医师结合完整病史判断。",
          "> 原始报告请完整保存并携带就诊。", ""]
    return "\n".join(L)


def render_audit(conclusions: list[Conclusion],
                 judgements: list[Judgement],
                 derived: list[DerivedResult],
                 red: list[RedFlagHit],
                 validation=None,
                 notices: Optional[list[str]] = None) -> str:
    """技术审计版：每条结论带规则、出处、稳健性与证据指针。

    ``notices``（P0-7）同样**置顶**。"""
    L: list[str] = ["# 技术审计版", ""]
    for n in (notices or []):
        L += [n, ""]
    # 交叉核对：让"算得对不对"与"原文核没核到"分开呈现
    try:
        from .crosschecks import CROSS_CHECKS, describe_boundary
        if CROSS_CHECKS:
            L += ["## 独立算例交叉核对", "",
                  "  出处核实（`verified`）只说明原文核没核到，不说明**算得对不对**。"
                  "本表给出与独立实现的偏差及其后果。", ""]
            for c in CROSS_CHECKS:
                kind = {"primary": "一级", "secondary": "二级",
                        "tertiary": "三级"}[c.source_kind]
                L.append(f"- **{c.rule_id}** · 来源层级 {kind}")
                L.append(f"  - 对方读数：{c.expected_text}")
                L.append(f"  - 输入：{c.inputs}")
                L.append(f"  - {c.boundary_note}")
                if c.note:
                    L.append(f"  - ⚠ {c.note}")
            for rid in sorted({c.rule_id for c in CROSS_CHECKS}):
                L.append(f"- **{rid} 偏差后果**：{describe_boundary(rid, tol=0.5)}")
            L.append("")
    except Exception as _e:  # noqa: BLE001
        L.append(f"（交叉核对渲染失败：{_e}）")
        L.append("")
    L.append("> 每条结论可点回规则条目 → 出处 → 原始页码。供复核用。")
    L.append("")

    if red:
        L += ["## 红旗命中", ""]
        for h in red:
            L.append(f"- `{h.rule_id}` {h.year} {h.label} = {h.value} {h.unit} "
                     f"| {h.evidence}")
        L.append("")

    if validation is not None:
        L += ["## 校验报告", "", "```", validation.render(), "```", ""]

    L += ["## 结论与出处", ""]
    for c in conclusions:
        L.append(f"### {c.conclusion_id} [{c.cls}] 不确定性：{c.uncertainty}")
        L.append("")
        L.append(c.text)
        L.append("")
        if c.rule_refs:
            L.append("规则与出处：")
            for rid in c.rule_refs:
                r = RULES_BY_ID.get(rid)
                if not r:
                    continue
                L.append(f"- `{rid}` {r.name} v{r.version}"
                         f"（{r.assertion_type}，缺项策略 {r.missing_policy}）")
                for p in r.provenance:
                    mark = "逐字" if p.verified else "仅题录·未核到原文"
                    L.append(f"  - [{mark}] {p.source_title} · {p.source_citation} "
                             f"· {p.locator or '—'}")
                    if p.quote_verbatim:
                        L.append(f"    > {p.quote_verbatim[:80]}…")
        if c.action_hint:
            L.append(f"建议动作：{c.action_hint}")
        if c.evidence:
            L.append("证据：")
            for e in c.evidence:
                if e:
                    L.append(f"- {e}")
        L.append("")

    # 变化轴明细：列名按**实际生成的变化对**展开（judge 数据驱动，年份不再写死）
    pairs: list[str] = []
    for j in judgements:
        for k in j.change_pairs():
            if k not in pairs:
                pairs.append(k)
    pairs.sort(key=lambda k: (k.split("→")[0], k.split("→")[-1]))
    L += ["## 变化轴明细（含稳健性）", "",
          "| 项目 | CVi% | CVi范围 | RCV% | "
          + " | ".join(f"Δ{p}" for p in pairs)
          + " | 稳健性 | 研究数 |",
          "|---" * (len(pairs) + 5) + "|"]
    for j in sorted(judgements, key=lambda x: x.analyte_id):
        if not j.rcv_threshold:
            continue
        cells = " | ".join(f"{j.change_pct.get(p, '')}" for p in pairs)
        L.append(f"| {j.canonical_name} | {j.cv_i:.1f} | {j.cvi_range} | "
                 f"{j.rcv_threshold} | {cells} | {j.robustness} | "
                 f"{j.n_studies} |")
    L.append("")
    # 只列**数值型**里没做变化轴的 —— 定性项（尿颜色/浊度…）不适用数值区间
    # 判定，列出来只是噪音，不是"缺口"。
    NUM = {Finding.NORMAL.value, Finding.ABNORMAL.value,
           Finding.UNDETERMINED.value, Finding.NOT_ASSESSABLE.value}
    L += ["## 数值型中未做变化轴判定的", ""]
    for j in sorted(judgements, key=lambda x: x.analyte_id):
        if j.rcv_threshold:
            continue
        if not any(p in NUM for p in j.position.values()):
            continue
        why = next((n for n in j.notes if "CVi" in n or "拒绝" in n), "无 CVi")
        L.append(f"- {j.canonical_name}（{j.section}）：{why}")
    L.append("")
    return "\n".join(L)


def render_all(conclusions, judgements, derived, red, validation=None,
               notices: Optional[list[str]] = None) -> dict[str, str]:
    """三视图。``notices``：置顶提示（P0-7），发给患者版与审计版。"""
    return {
        "clinical": render_clinical(judgements, derived, red),
        "patient": render_patient(conclusions, red, notices=notices),
        "audit": render_audit(conclusions, judgements, derived, red, validation,
                              notices=notices),
    }
