# -*- coding: utf-8 -*-
"""L6 · 结论合成层：把判定与分层变成结论。

对应框架 D15。做三件事：

1. **归并共同上游** —— 高TG + 低HDL + 脂肪肝 + GGT↑ + 体重正常 不是五个独立毛病，
   是同一件事在血脂、肝脏、肝酶上的投影。分开报会让人以为有五个问题。
2. **冲突消解** —— 位置轴正常、变化轴却超出噪声时，判"待核查"而**不是"正常"**。
   本案例 4 项超出噪声中有 3 项位置三年都正常，正是这一条要接住的。
3. **分类落格** —— ``confirmed`` / ``to_verify`` / ``to_followup`` / ``data_quality``。

一条硬规则：**缺失 ≠ 阴性**。某年没做某项检查，不能把后续年份的发现说成"新增"。

本层不做诊断——``assertion_type`` 只允许 informational / advisory / red_flag，
措辞降级在 L7 做。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .judge import Judgement
from .schema import (Change, Conclusion, ConclusionClass, DerivedResult,
                     Finding, InstitutionFinding, NarrativeItem)

NEGATIVE_MARKERS = ("未见明显异常", "未见异常", "正常", "无异常", "未见", "无")


@dataclass
class Upstream:
    """一个共同上游，及其在数据上的投影。"""

    key: str
    name: str
    analytes: list[str]
    context_terms: list[str]      #: 来自叙述层的词（如「脂肪肝」）
    note: str = ""


#: 共同上游（来自 因果导图.md 的三条「关联共同导致」）
UPSTREAMS: list[Upstream] = [
    Upstream(
        key="metabolic", name="代谢背景",
        analytes=["CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇", "CHEM.谷氨酰转肽酶"],
        context_terms=["脂肪肝"],
        note="这一组不是几个独立毛病，是同一件事在血脂、肝脏、肝酶上的投影"),
    Upstream(
        key="tobacco", name="烟草暴露",
        analytes=["CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇",
                  "CHEM.谷氨酰转肽酶", "CHEM.肌酸激酶"],
        context_terms=["肺结节", "肺"],
        note="吸烟是整张图里唯一跨两个上游、且完全由本人控制、不需任何检查就能改变的变量"),
    Upstream(
        key="record", name="检测与记录不一致",
        analytes=["CHEM.肌酐", "CHEM.高密度脂蛋白胆固醇"],
        context_terms=["骨密度"],
        note="这一组导致的不是『有问题』，而是『无法确定』——它污染了前两组的部分证据"),
]


def _is_negative(line: str) -> bool:
    return any(m in line for m in NEGATIVE_MARKERS)


def _abnormal_years(j: Judgement) -> list[str]:
    return sorted(y for y, p in j.position.items() if p == Finding.ABNORMAL.value)


def _beyond_years(j: Judgement) -> list[str]:
    return sorted(k for k, v in j.change.items() if v == Change.BEYOND_NOISE.value)


def synthesize(judgements: list[Judgement],
               derived: list[DerivedResult],
               validation=None,
               findings: Optional[list[InstitutionFinding]] = None,
               narratives: Optional[list[NarrativeItem]] = None,
               profile: Optional[dict] = None) -> list[Conclusion]:
    """把 L1–L5 的输出合成结论。"""
    findings = findings or []
    narratives = narratives or []
    profile = profile or {}
    jm = {j.analyte_id: j for j in judgements}
    out: list[Conclusion] = []
    n = [0]

    def add(cls: str, text: str, **kw) -> None:
        n[0] += 1
        out.append(Conclusion(conclusion_id=f"C{n[0]:02d}", cls=cls, text=text, **kw))

    # ---------------------------------------------------------- 1. 已确认问题

    # 血脂异常：高TG + 低HDL 同时异常 → 一个定性结论，不是两条
    tg, hdl = jm.get("CHEM.甘油三酯"), jm.get("CHEM.高密度脂蛋白胆固醇")
    if tg and hdl:
        tg_y, hdl_y = _abnormal_years(tg), _abnormal_years(hdl)
        if tg_y and hdl_y:
            _both = sorted(set(tg_y) & set(hdl_y)) or sorted(set(tg_y) | set(hdl_y))
            _lt = latest_year(tg) or (years_of(tg) or [""])[-1]
            _n = len(tg_y)
            _scope = (f"{span_text(_both)}均超出参考区间"
                      if _n > 1 else f"{span_text(_both)}超出参考区间")
            add(ConclusionClass.CONFIRMED.value,
                f"血脂异常：高甘油三酯 + 低高密度脂蛋白胆固醇，"
                f"{_scope}（TG {tg.series.get(_lt)} {tg.unit}，"
                f"HDL {hdl.series.get(_lt)} {hdl.unit}）",
                upstream="代谢背景",
                evidence=[tg.evidence.get(_lt, ""), hdl.evidence.get(_lt, "")],
                uncertainty="low", assertion_type="informational")

    # ---------------------------------------------------------- 2. 已确认状态
    # 同一条规则跨年只出**一条**结论，用轨迹表达，不是三年三条。
    for rid, label in (("ASCVD.CHINAPAR", "ASCVD 风险"),
                       ("LIPID.RISK_STRATIFICATION", "血脂危险分层")):
        rows = sorted((d for d in derived
                       if d.rule_id == rid and d.status == "ok"),
                      key=lambda x: x.year)
        if not rows:
            continue
        bands = {d.band for d in rows}
        if rid == "ASCVD.CHINAPAR":
            traj = " → ".join(f"{d.year} {d.value}%" for d in rows)
            band_txt = rows[-1].band if len(bands) == 1 else f"分层有变化：{sorted(bands)}"
            text = f"{label}：{traj}，最新属{band_txt}"
            hint = "按报告建议定期复查血脂"
        else:
            traj = " → ".join(f"{d.year} {d.band}" for d in rows)
            text = (f"{label}：{traj}（{rows[-1].inputs.get('危险因素')}）"
                    if len(bands) == 1 else f"{label}：{traj}")
            hint = ""
        add(ConclusionClass.CONFIRMED.value, text,
            evidence=[d.text for d in rows], uncertainty="medium",
            rule_refs=[rid], assertion_type="advisory", action_hint=hint)

        # 分层/风险若各年一致，说明是稳定的状态；若变化则要提示
        if len(bands) > 1:
            add(ConclusionClass.TO_VERIFY.value,
                f"{label}{span_text([d.year for d in rows])}分层发生变化：{traj}"
                f" —— 状态变化需核实原因",
                evidence=[d.text for d in rows], uncertainty="high",
                rule_refs=[rid])

    # 贴近参考区间上限的：不改判定，但要看住
    for j in judgements:
        _lt = latest_year(j)
        v, hi = (j.series.get(_lt) if _lt else None), None
        rg = j.ref_range.get(_lt, "") if _lt else ""
        if v is None or "-" not in rg:
            continue
        try:
            parts = rg.replace("mmol/L", "").replace("umol/L", "").split("-")
            hi = float(parts[-1])
        except (ValueError, IndexError):
            continue
        if hi > 0 and 0 <= (hi - v) / hi < 0.05 and j.position.get(_lt) != "abnormal":
            add(ConclusionClass.TO_VERIFY.value,
                f"{j.canonical_name} {v} 距参考上限 {hi} 仅 {hi - v:.3f}"
                f"（{(hi - v) / hi * 100:.1f}%），未越线但需盯住",
                evidence=[j.evidence.get(_lt, "")], uncertainty="medium",
                action_hint="复查时重点看这一项")

    # ---------------------------------------------------------- 3. 冲突消解
    # 位置轴正常、变化轴却超出噪声 → 待核查，**不是"正常"**
    for j in judgements:
        # 变化对**从数据推导**：写死 "2024→2026" 时，任何别的年份集合
        # 都取不到键 → 冲突消解类结论整段丢失（P0-3）。
        _e0, _l0 = earliest_year(j), latest_year(j)
        if not (_e0 and _l0):
            continue
        if j.change.get(f"{_e0}→{_l0}") != Change.BEYOND_NOISE.value:
            continue
        pos_ab = _abnormal_years(j)
        if pos_ab:
            continue                      # 位置也异常，已被上面的确认类覆盖
        stab = f"（{j.robustness}）" if j.robustness else ""
        _e, _l = earliest_year(j), latest_year(j)
        add(ConclusionClass.TO_VERIFY.value,
            f"{j.canonical_name} {span_text(years_of(j))}位置都在参考区间内，"
            f"但 {_e}→{_l} "
            f"变化 {j.change_pct.get(f'{_e}→{_l}'):+.1f}% 超出了噪声阈值 "
            f"{j.rcv_threshold}%{stab} —— 位置正常不等于没变化",
            upstream=_upstream_of(j.analyte_id),
            evidence=[j.evidence.get(y, "") for y in (_e, _l)],
            uncertainty="high" if j.robustness != "稳健超出" else "medium",
            action_hint="复查并与机构核对检测方法")

    # 位置异常但不在确认组的
    for j in judgements:
        ab_years = _abnormal_years(j)
        if not ab_years or j.analyte_id in ("CHEM.甘油三酯",
                                            "CHEM.高密度脂蛋白胆固醇"):
            continue
        if any(c.text.startswith(j.canonical_name) for c in out):
            continue
        last = max(j.series)
        traj = " / ".join(f"{y}:{j.series.get(y)}" for y in sorted(j.series))
        if last in ab_years:
            text = (f"{j.canonical_name} 超出参考区间"
                    f"（{traj}，区间 {j.ref_range.get(last)}）")
            unc = "medium"
        else:
            # 曾经异常、现在回到区间内 —— 不能写成"超出"
            text = (f"{j.canonical_name} 曾在 {'、'.join(ab_years)} 超出参考区间，"
                    f"{last} 已回到区间内（{traj}）")
            unc = "low"
        add(ConclusionClass.TO_VERIFY.value, text,
            evidence=[j.evidence.get(y, "") for y in ab_years],
            uncertainty=unc, assertion_type="informational")

    # ---------------------------------------------------------- 4. 数据质量
    if validation is not None:
        for c in validation.checks:
            if c.level != "warn":
                continue
            add(ConclusionClass.DATA_QUALITY.value,
                f"{c.name}：{c.detail}",
                evidence=c.items[:4], uncertainty="high",
                action_hint="向机构核对数据与检测方法")

    # 常数序列里**只有真正要紧的那条**单列：HDL-C 同时是危险因素的唯一依据。
    # 其余由 L3 的汇总条目覆盖，不重复报；值为 0 的（尿沉渣常态）一律不看。
    hdl_j = jm.get("CHEM.高密度脂蛋白胆固醇")
    if hdl_j is not None:
        vals = [v for v in hdl_j.series.values() if v is not None]
        if len(vals) >= 3 and len(set(vals)) == 1 and vals[0] != 0:
            add(ConclusionClass.DATA_QUALITY.value,
                f"高密度脂蛋白胆固醇{span_text(sorted(hdl_j.series))}"
                f"一位小数不差（{vals[0]}），"
                f"生物学上概率极低，疑似未被重新检测；"
                f"**该指标同时是『低 HDL-C』这条危险因素的唯一依据**，"
                f"若未重测则危险因素本身存疑",
                evidence=[hdl_j.evidence.get(y, "") for y in sorted(hdl_j.series)],
                uncertainty="high", action_hint="要求机构确认是否重新检测")

    # 经过了显式修复规则的数据
    for j in judgements:
        if any("修复" in n_ for n_ in j.notes):
            add(ConclusionClass.DATA_QUALITY.value,
                f"{j.canonical_name} 有标签/单位错位，已按显式修复规则重归属",
                evidence=[n_ for n_ in j.notes if "修复" in n_],
                uncertainty="medium")

    # ---------------------------------------------------------- 5. 待随访（影像）
    # 同一个检查项目跨年只出**一条**，用轨迹表达。逐年三条等于把归并白做了。
    covered_terms = {j.canonical_name for j in judgements
                     if any(c.text.startswith(j.canonical_name)
                            or j.canonical_name in c.text for c in out)}
    by_item: dict[str, list[NarrativeItem]] = {}
    for it in narratives:
        if [s for s in it.summary_lines if not _is_negative(s)]:
            by_item.setdefault(it.item, []).append(it)

    def _advice(detail: str) -> str:
        """取机构原文里的建议部分（从「建议」二字起）。"""
        i = detail.find("建议")
        return detail[i:i + 40] if i >= 0 else ""

    def _core(detail: str) -> str:
        """取机构原文里的发现描述（「提示：」之后到第一个句号）。"""
        i = detail.find("提示：")
        j = detail.find("提示:")
        k = i if i >= 0 else j
        if k < 0:
            return ""
        seg = detail[k + 3:]
        return seg.split("。")[0][:16]

    used: set[int] = set()
    for item, items in sorted(by_item.items()):
        items.sort(key=lambda x: x.year)
        legs = []
        for it in items:
            keep = [s for s in it.summary_lines if not _is_negative(s)]
            meas = "/".join(m.raw for m in it.measurements[:2]) or "—"
            g = f" {it.grade_system}{it.grade_value}" if it.grade_system else ""
            legs.append(f"{it.year} {'；'.join(keep)}{g}（{meas}）")
        traj = " → ".join(legs)

        # 把机构自报里同一发现的**建议原文**并进来，而不是并列出第二条
        advices = []
        for idx, f in enumerate(findings):
            if idx in used:
                continue
            core = _core(f.detail)
            if core and core[:8] in traj:
                a = _advice(f.detail)
                if a and a not in advices:
                    advices.append(f"{f.year}：{a}")
                used.add(idx)
        hint = " / ".join(advices) if advices else "按报告建议随访"
        add(ConclusionClass.TO_FOLLOWUP.value, f"{item}：{traj}",
            evidence=[f"{x.year} p{x.page_no} {x.description_verbatim[:44]}…"
                      for x in items[:2]],
            uncertainty="low", assertion_type="informational",
            action_hint=hint)
        covered_terms.add(item)

    # 机构自报里，凡已被上述结论覆盖的不再重复；其余单列
    for idx, f in enumerate(findings):
        if idx in used:
            continue
        if any(t in f.title for t in covered_terms if len(t) >= 3):
            continue
        if any(f.title[:4] in x.text for x in out):
            continue
        add(ConclusionClass.TO_FOLLOWUP.value,
            f"{f.title}：{f.detail[:56]}…",
            evidence=[f"{f.year} p{f.page_no} 主检报告 {f.no}.{f.title}"],
            uncertainty="low", assertion_type="informational",
            action_hint=_advice(f.detail) or "按报告建议随访")

    # ---------------------------------------------------------- 6. 缺失 ≠ 阴性
    by_item: dict[str, set[str]] = {}
    for it in narratives:
        by_item.setdefault(it.item, set()).add(it.year)
    all_years = sorted({it.year for it in narratives})
    for item, ys in sorted(by_item.items()):
        miss = [y for y in all_years if y not in ys]
        if miss and len(ys) >= 1:
            add(ConclusionClass.DATA_QUALITY.value,
                f"{item} 只在 {','.join(sorted(ys))} 做，"
                f"{','.join(miss)} 未做 —— 缺失不等于阴性，不能据此判断『新增』或『好转』",
                evidence=[f"该项仅出现于 {','.join(sorted(ys))} 的报告"],
                uncertainty="high")

    return out


def _upstream_of(analyte_id: str) -> str:
    for u in UPSTREAMS:
        if analyte_id in u.analytes:
            return u.name
    return ""


_CN_NUM = {1: "一年", 2: "两年", 3: "三年", 4: "四年", 5: "五年",
           6: "六年", 7: "七年", 8: "八年", 9: "九年", 10: "十年"}


def years_of(j: Judgement) -> list[str]:
    """该判定**实际有数据的**年份，升序。

    结论文案原先硬编码「三年」「2024」「2026」—— 那是为本案三份报告写的，
    换成单年或五年报告就会说假话（单年数据却写「三年均超出」）。
    """
    ys = [y for y in j.series if j.series.get(y) is not None]
    return sorted(ys or list(j.series))


def latest_year(j: Judgement) -> Optional[str]:
    ys = years_of(j)
    return ys[-1] if ys else None


def earliest_year(j: Judgement) -> Optional[str]:
    ys = years_of(j)
    return ys[0] if ys else None


def span_text(years: list[str]) -> str:
    """把年份列表说成人话：1 个 → ``2026 年``；3 个 → ``三年``。"""
    if not years:
        return "（无年份）"
    if len(years) == 1:
        return f"{years[0]} 年"
    return _CN_NUM.get(len(years), f"{len(years)} 年")


def group_by_upstream(conclusions: list[Conclusion]) -> dict[str, list[Conclusion]]:
    """按共同上游归并，供 L7 输出层使用。"""
    out: dict[str, list[Conclusion]] = {}
    for c in conclusions:
        out.setdefault(c.upstream or "(未归并)", []).append(c)
    return out
