# -*- coding: utf-8 -*-
"""L4 · 判定层：位置轴 + 变化轴（**必须分开**）。

* **位置轴** —— 数值 vs **当年各自的**参考区间。回答"现在正常吗"。
  体检报告只回答这一条。
* **变化轴** —— RCV（参考变化值）。回答"变化是真的吗"。
  费用：

      RCV% = √2 · Z · √(CVi² + CVa²)      Z=1.96（95%），CVa 为假设值

  出处：Díaz-Garzón J, Fernández-Calle P, Ricós C. Adv Lab Med.
  2020;1(3):20200017. DOI 10.1515/almed-2020-0017

文献自陈的局限必须随结论输出：① 假设所有个体 CVi 相同；② CVa 随浓度不同
而不同；③ **真实世界前分析变量未标准化，RCV 可能过严**，会把非生物学变化
判成显著。

另有一条硬约束来自本项目的坑：**单位不可比时拒绝判趋势**。独立佐证是
Vital 的模块原话 —— *an absent delta is honest, a wrong one is not.*
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Optional

from .normalize import UNMAPPED, _same_unit
from .schema import Change, Finding, NormalizedObservation

Z = 1.96
CVA_DEFAULT = 3.0
ROBUST_CAVEAT = ("RCV 假设所有个体 CVi 相同，且真实世界前分析变量未标准化，"
                 "可能过严（把非生物学变化判成显著）")


@dataclass
class Judgement:
    analyte_id: str
    #: 显示名**取自目录**，不取"排序第一年的原文"——RDW 那对修复后，
    #: 同一个原文（如「红细胞分布宽度CV」）在不同年份指向不同 analyte，
    #: 取原文会让显示名与分析对象错配。
    canonical_name: str = ""
    item_raw: str = ""
    section: str = ""
    unit: str = ""
    series: dict[str, Optional[float]] = field(default_factory=dict)
    position: dict[str, Optional[str]] = field(default_factory=dict)
    ref_range: dict[str, str] = field(default_factory=dict)
    change_pct: dict[str, Optional[float]] = field(default_factory=dict)
    change: dict[str, str] = field(default_factory=dict)
    rcv_threshold: Optional[float] = None
    cv_i: Optional[float] = None
    cvi_range: str = ""
    n_studies: Optional[int] = None
    robustness: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    #: 真实采样日期与间隔（框架 D11）。RCV 的**阈值**不随间隔变，
    #: 但它的**敏感度**随间隔变 —— 间隔越长越容易漏判。
    dates: dict[str, str] = field(default_factory=dict)
    intervals: dict[str, float] = field(default_factory=dict)   #: 月
    interval_notes: dict[str, str] = field(default_factory=dict)
    #: 逐年"观测单位与规范单位是否等价"。**不换算**（设计：只判等价）——
    #: 不等价的年份不得用于组合/分层评估（SI 阈值套 mg/dL 会算错），
    #: 位置轴仍照当年机构区间判定（那与单位无关）。
    unit_ok: dict[str, bool] = field(default_factory=dict)

    def change_pairs(self) -> set:
        """本条目做了哪几对比较。"""
        return set(self.change) | set(self.change_pct)


def obs_key(o: NormalizedObservation) -> str:
    """判定层的分组键。

    **不能直接用 ``analyte_id``** —— 未归一的观测全都叫 ``UNMAPPED``，
    用它会把这 466 条互不相干的观测塌缩成**一条**判定：项目名取到第一条、
    数值取到每年最后一条，其余全部**静默吞掉**。

    （这正是"同名撞键"在归一层失守之后的第二次发作 —— 第一次是尿液的
    两个「白细胞」，那次的教训是键要带上段与序号。）
    """
    if o.analyte_id != UNMAPPED:
        return o.analyte_id
    return f"{UNMAPPED}|{o.section}|{o.item_raw}|{o.occurrence}"


def months_between(d1: str, d2: str) -> Optional[float]:
    """两个日期之间的月数（按 30.44 天/月）。取不到返回 None。"""
    import datetime as _dt
    try:
        a = _dt.date.fromisoformat(d1)
        b = _dt.date.fromisoformat(d2)
    except (TypeError, ValueError):
        return None
    return round((b - a).days / 30.44, 1)


def interval_notes(dates: dict[str, str], tol: float = 0.15):
    """算出各对的真实间隔，并给出**方向性**提示。

    这是框架 D11 的核心：RCV 公式里没有时间，但真实变化是随时间累积的。

    * 间隔**偏长** → 真实变化累积更多，同一阈值更容易**漏判**（把真变化当噪声）
    * 间隔**偏短** → 反过来，更容易**误判**为超出

    基准怎么定：``nominal`` 取**相邻**间隔的中位数，然后每一对拿
    ``nominal × 步数`` 去比。不能拿所有间隔一起取中位 —— 那样跨年对
    （天然是两步）会被判成"必然偏长"，等于什么都没说。

    本项目实际：2024-06-12 → 2025-05-07 是 10.8 个月，
    2025-05-07 → 2026-08-20 是 **15.4 个月**。两段相差 43%。
    """
    import statistics
    years = sorted(dates)
    adj: dict[str, float] = {}
    for a, b in zip(years, years[1:]):
        m = months_between(dates[a], dates[b])
        if m is not None:
            adj[f"{a}→{b}"] = m
    if not adj:
        return {}, {}, 0.0
    nominal = statistics.median(list(adj.values()))

    ivs: dict[str, float] = {}
    notes: dict[str, str] = {}
    for i in range(len(years)):
        for k in range(i + 1, len(years)):
            span = years[i:k + 1]
            steps = len(span) - 1
            actual = months_between(dates[span[0]], dates[span[-1]])
            if actual is None:
                continue
            key = f"{span[0]}→{span[-1]}"
            ivs[key] = actual
            expect = nominal * steps
            if not expect:
                continue
            dev = actual / expect - 1
            if abs(dev) < tol:
                continue
            if dev > 0:
                notes[key] = (
                    f"间隔 {actual} 个月（{steps} 步名义 {expect:.1f} 个月，"
                    f"偏长 {dev * 100:.0f}%）—— 真实变化累积更多，"
                    f"同一 RCV 阈值更容易**漏判**（把真变化当噪声）")
            else:
                notes[key] = (
                    f"间隔 {actual} 个月（{steps} 步名义 {expect:.1f} 个月，"
                    f"偏短 {-dev * 100:.0f}%）—— 同一阈值更容易**误判**为超出")
    return ivs, notes, round(nominal, 1)


def rcv(cvi: float, cva: float = CVA_DEFAULT) -> float:
    """参考变化值（%）。"""
    return Z * math.sqrt(2) * math.sqrt(cvi ** 2 + cva ** 2)


def load_cvi(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("entries", {})


def judge_position(obs: list[NormalizedObservation], ranges=None,
                   metas: Optional[dict] = None) -> dict[str, Judgement]:
    """位置轴：每个指标每年 vs **当年各自的**参考区间。

    同一数值在不同年份被判正常/异常，多数是机构换了区间——所以区间逐年保留、
    不覆盖。缺值的年份判 ``undetermined``，**不按其他年份推断**。

    ``ranges`` 传 :class:`checkup.ranges.RangeTable` 时走**分层区间**解析
    （框架 D8）：分层命中 → 报告自带 → 不可评估。空表等于不启用。
    """
    out: dict[str, Judgement] = {}
    for o in obs:
        # 注意：**不能**按 value_type 过滤掉不合格输入。定性项要显式标
        # not_applicable，数值项解析失败要显式标 undetermined——
        # 直接 continue 会让这一年在判定结果里**凭空消失**，而"消失"
        # 与"正常"在输出上无法区分。
        j = out.setdefault(obs_key(o), Judgement(
            analyte_id=o.analyte_id, item_raw=o.item_raw, section=o.section,
            unit=o.unit))
        j.series[o.year] = o.value
        j.evidence[o.year] = o.evidence
        if o.value_type != "numeric":
            j.position[o.year] = Finding.NOT_APPLICABLE.value
            note = f"{o.year} 为定性项（{o.value_text}），不适用数值区间判定"
            if note not in j.notes:
                j.notes.append(note)
            continue
        # 分层区间解析（可选）
        rr = None
        if ranges is not None and len(ranges):
            from .ranges import flag_of
            m = (metas or {}).get(o.year)
            rr = ranges.resolve(
                o.analyte_id,
                getattr(m, "sex", None) if m else None,
                getattr(m, "age", None) if m else None,
                o.unit, o.ref_low, o.ref_high, o.ref_op, o.ref_raw)
            if rr.shape in ("bounded", "low-only", "high-only"):
                j.position[o.year] = (flag_of(o.value, rr).value
                                      if o.value is not None and flag_of(o.value, rr)
                                      else Finding.UNDETERMINED.value)
                if rr.low is not None and rr.high is not None:
                    txt = f"{rr.low}-{rr.high}"
                elif rr.low is not None:
                    txt = f"{rr.op or '>'}{rr.low}"
                elif rr.high is not None:
                    txt = f"{rr.op or '<'}{rr.high}"
                else:
                    txt = o.ref_raw
                j.ref_range[o.year] = txt + (rr.unit or "")
                if rr.origin == "stratified":
                    note = f"{o.year} 参考区间来自分层表（{rr.matched_by}）"
                    if note not in j.notes:
                        j.notes.append(note)
                continue
            if rr.shape == "empty":
                j.position[o.year] = Finding.NOT_ASSESSABLE.value
                j.ref_range[o.year] = o.ref_raw or "(无任何可用区间)"
                continue

        if o.value is None:
            j.position[o.year] = Finding.UNDETERMINED.value
        elif o.ref_low is None and o.ref_high is None:
            j.position[o.year] = Finding.NOT_ASSESSABLE.value
            j.ref_range[o.year] = o.ref_raw or "(报告未给参考区间)"
            j.notes.append(f"{o.year} 无参考区间 → 位置轴不可评估")
        else:
            j.position[o.year] = o.flag.value if o.flag else Finding.UNDETERMINED.value
            j.ref_range[o.year] = o.ref_raw
    return out


def _comparability(rows: list[NormalizedObservation]) -> tuple[bool, str]:
    """G4 关卡：单位是否可比。**不可比就拒绝判趋势。**"""
    units = {r.unit for r in rows if r.value is not None and r.unit}
    if len(units) <= 1:
        return True, ""
    base = sorted(units)[0]
    if all(_same_unit(u, base) for u in units):
        return True, ""      # 等价（如 10^9/L 与 10^3/μL），不算不可比
    return False, f"单位跨年不可比: {' / '.join(sorted(units))}"


def judge_change(obs: list[NormalizedObservation], cvi: dict,
                 cva: float = CVA_DEFAULT,
                 catalog=None) -> dict[str, Judgement]:
    """变化轴：RCV 两两比较（24→25 / 25→26 / 24→26）。

    3 个数据点**不做回归**——只用 RCV 做两两比较。这条是硬规则：3–5 个点的
    R² 几乎由点数决定，且易被单个离群点主导（6 个开源项目全量 grep 无一人这么做）。
    """
    by_id: dict[str, list[NormalizedObservation]] = {}
    for o in obs:
        if o.value_type == "numeric" and o.value is not None:
            by_id.setdefault(obs_key(o), []).append(o)

    out: dict[str, Judgement] = {}
    for aid, rows in by_id.items():
        rows = sorted(rows, key=lambda r: r.year)
        if len({r.year for r in rows}) < 2:
            continue
        a = catalog.analytes.get(aid) if catalog is not None else None
        canon = (a.canonical_unit if a and a.canonical_unit else "")
        units = {r.unit for r in rows if r.unit}
        unit_ok = (not canon) or all(_same_unit(u, canon) for u in units)
        j = Judgement(
            analyte_id=aid,
            canonical_name=(a.name if a else rows[0].item_raw),
            item_raw=rows[0].item_raw,
            section=(a.section if a and a.section else rows[0].section),
            # 标签必须与实际数值同单位：单位不等价时**不许**贴规范单位的标
            # （曾把 201 mg/dL 写成 "201.0 mmol/L" —— 换算被禁，标签也不能借）
            unit=(canon if unit_ok else rows[0].unit))
        if not unit_ok:
            j.notes.append(
                f"单位 {rows[0].unit} 与规范单位 {canon} 不同（按设计不换算）："
                f"位置轴照当年机构区间判定；组合/分层评估不采用该年数值")

        comparable, why = _comparability(rows)
        if not comparable:
            j.notes.append(f"拒绝判趋势 —— {why}")
            out[aid] = j
            continue

        e = cvi.get(aid)
        if not e:
            j.notes.append("无 CVi，不做变化轴判定")
            out[aid] = j
            continue

        thr = rcv(e["cvi"], cva)
        lo = rcv(e["cvi_low"], cva) if e.get("cvi_low") else thr
        hi = rcv(e["cvi_high"], cva) if e.get("cvi_high") else thr
        j.rcv_threshold, j.cv_i = round(thr, 1), e["cvi"]
        j.n_studies = e.get("n_studies")
        j.cvi_range = f"{e.get('cvi_low')}-{e.get('cvi_high')}"

        vmap = {r.year: r.value for r in rows}
        years = sorted(vmap)
        # **全部两两比较**（对 3 个数据点即 24vs25、25vs26、24vs26）。
        # 只做相邻比较会漏掉跨度最大的那一对（本项目里 CK 的 +58.5% 就在
        # 24→26 上，只比相邻时它会落在阈值内）；写死三对则会让 **4–5 年**
        # 的报告漏掉中间相邻对（P0-3 一并泛化）。
        for i in range(len(years)):
            for k in range(i + 1, len(years)):
                a, b = years[i], years[k]
                va, vb = vmap[a], vmap[b]
                if not va:
                    j.change[f"{a}→{b}"] = Change.INSUFFICIENT_POINTS.value
                    j.change_pct[f"{a}→{b}"] = None
                    continue
                ch = (vb - va) / va * 100
                j.change_pct[f"{a}→{b}"] = round(ch, 1)
                j.change[f"{a}→{b}"] = (
                    Change.BEYOND_NOISE.value if abs(ch) > thr
                    else Change.WITHIN_NOISE.value)
        if len(years) >= 2:
            first, last = years[0], years[-1]
            k = f"{first}→{last}"
            ch = j.change_pct.get(k)
            if ch is None:
                j.robustness = ""
            elif abs(ch) > hi:
                j.robustness = "稳健超出"
            elif abs(ch) < lo:
                j.robustness = "稳健在内"
            else:
                j.robustness = "取决于CVi取值"
            j.notes.append(ROBUST_CAVEAT)
        if isinstance(j.n_studies, int) and j.n_studies < 3:
            j.notes.append(f"CVi 仅基于 {j.n_studies} 项研究，估计不稳")
        out[aid] = j
    return out


def judge_all(obs: list[NormalizedObservation], cvi_path: str,
              cva: float = CVA_DEFAULT, catalog=None,
              metas: Optional[dict] = None, ranges=None) -> list[Judgement]:
    """位置轴 + 变化轴合并输出。

    ``metas`` 传 ``{年份: DocumentMeta}`` 时，会把**真实采样日期与间隔**
    填进每条判定（框架 D11）。
    """
    pos = judge_position(obs, ranges=ranges, metas=metas)
    chg = judge_change(obs, load_cvi(cvi_path), cva, catalog)
    merged: dict[str, Judgement] = {}
    for aid in sorted(set(pos) | set(chg)):
        j = chg.get(aid) or pos.get(aid)
        assert j is not None
        p = pos.get(aid)
        if p is not None:
            j.position = p.position
            j.ref_range = p.ref_range
            j.series = p.series or j.series
            j.evidence.update(p.evidence)
            for n in p.notes:
                if n not in j.notes:
                    j.notes.append(n)
        # 显示名统一从目录补一次：judge_position 建的条目没带 canonical_name，
        # 只有位置轴的项（如体格检查、眼科）标题会是空的。
        if catalog is not None and "|" not in aid:
            a = catalog.analytes.get(aid)
            if a is not None:
                j.canonical_name = a.name
                if not j.section:
                    j.section = a.section or j.section
        if not j.canonical_name:
            # 未映射的键形如 ``UNMAPPED|段|项目|序号``，显示名取项目原名
            j.canonical_name = j.item_raw or aid.rsplit("|", 2)[0].split(".")[-1]
        merged[aid] = j

    # 逐年补齐"单位是否等价"（组合/分层评估据此**拦下**不可比的年份）。
    # 放在合并之后：单年条目只出现在位置轴里，也必须被标上。
    if catalog is not None:
        for o in obs:
            if o.value_type != "numeric" or o.value is None:
                continue
            j = merged.get(obs_key(o))
            if j is None:
                continue
            a = catalog.analytes.get(o.analyte_id) if "|" not in o.analyte_id else None
            if a is None or not a.canonical_unit:
                continue
            j.unit_ok[o.year] = _same_unit(o.unit, a.canonical_unit)

    out = list(merged.values())
    if metas:
        dates = {y: m.exam_date for y, m in metas.items() if m.exam_date}
        ivs, iv_notes, base = interval_notes(dates)
        for j in out:
            j.dates = dict(dates)
            j.intervals = dict(ivs)
            j.interval_notes = {
                k: v for k, v in iv_notes.items() if k in j.change_pairs()}
    return out
