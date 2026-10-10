# -*- coding: utf-8 -*-
"""L3 · 校验层：这批数据能信吗？

对应框架 §一 步骤 4 与 §三 的 G2 关卡。四类校验：

* **内部恒等式** —— 用化验项之间的确定性关系验抽取质量
* **覆盖率** —— 某年抽到的项数明显偏离基线 → 疑似版式改版
* **单位一致性** —— 归一阶段拦下的不一致
* **Delta Check** —— 跨年跳变。检验科的标准做法，同一个人同一指标
  不可能一年内变动好几倍；出现即是抽取错或真事件
* **生理不可能 / 常数序列** —— 报告本身的数据质量

校验不过 → 标 ``warn`` 或 ``fatal``，**不静默继续**。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .bands import 跨年记录口径
from .schema import NormalizedObservation


@dataclass
class Check:
    name: str
    level: str                  #: pass | warn | fatal
    detail: str
    items: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    checks: list[Check]

    @property
    def level(self) -> str:
        if any(c.level == "fatal" for c in self.checks):
            return "fatal"
        if any(c.level == "warn" for c in self.checks):
            return "warn"
        return "pass"

    def render(self) -> str:
        mark = {"pass": "✓", "warn": "!", "fatal": "✗"}
        lines = [f"整体等级: {self.level.upper()}"]
        for c in self.checks:
            lines.append(f"  {mark[c.level]} {c.name}: {c.detail}")
            for it in c.items[:6]:
                lines.append(f"      · {it}")
            if len(c.items) > 6:
                lines.append(f"      · …另有 {len(c.items) - 6} 条")
        return "\n".join(lines)


#: 化验项之间的确定性恒等式（用于验证抽取质量，不是医学判断）
IDENTITIES = [
    ("总胆红素", ["直接胆红素", "间接胆红素"], 0.35),   # 容差 0.35 umol/L（四舍五入）
]


def _by_analyte(obs: Iterable[NormalizedObservation]) -> dict[str, list[NormalizedObservation]]:
    out: dict[str, list[NormalizedObservation]] = {}
    for o in obs:
        out.setdefault(o.analyte_id, []).append(o)
    return out


# ------------------------------------------------------------------ 各检查

def check_internal_identity(obs: list[NormalizedObservation]) -> Check:
    """直接胆红素 + 间接胆红素 应当等于 总胆红素。

    这是**唯一能证明抽取准确**的手段之一：三个值来自同一页不同列，
    只有坐标归属、断行回填、单位剥离全部正确时才会吻合。
    """
    by_id = _by_analyte(obs)
    bad: list[str] = []
    checked = 0
    for total_name, part_names, tol in IDENTITIES:
        for year in sorted({o.year for o in obs}):
            def val(nm, y=year):
                for o in by_id.get(_find_id(by_id, nm), []):
                    if o.year == y and o.value is not None:
                        return o.value
                return None
            t = val(total_name)
            parts = [val(p) for p in part_names]
            if t is None or any(p is None for p in parts):
                continue
            checked += 1
            s = sum(parts)          # type: ignore[arg-type]
            if abs(t - s) > tol:
                bad.append(f"{year} {total_name}={t} ≠ "
                           f"{' + '.join(f'{p}' for p in parts)}={s:.1f}")
    if bad:
        return Check("内部恒等式", "warn", f"{len(bad)}/{checked} 年不吻合", bad)
    return Check("内部恒等式", "pass", f"{checked} 年全部吻合（直接+间接=总胆红素）")


def _find_id(by_id: dict, name: str) -> str:
    for aid in by_id:
        if aid.endswith("." + name):
            return aid
    return ""


def check_coverage(obs: list[NormalizedObservation]) -> Check:
    """各年化验项数是否稳定。明显偏少 → 疑似版式改版导致漏抽。"""
    per_year: dict[str, int] = {}
    for o in obs:
        if o.source == "lab":
            per_year[o.year] = per_year.get(o.year, 0) + 1
    if len(per_year) < 2:
        return Check("覆盖率", "pass", f"仅 {len(per_year)} 年数据，不判")
    base = statistics.median(per_year.values())
    bad = [f"{y}: {n} 项（基线 {base:.0f}）" for y, n in sorted(per_year.items())
           if abs(n - base) > max(3, base * 0.1)]
    detail = " · ".join(f"{y}:{n}" for y, n in sorted(per_year.items()))
    if bad:
        return Check("覆盖率", "warn", f"项数偏离基线 — {detail}", bad)
    return Check("覆盖率", "pass", f"{detail}（基线 {base:.0f}）")


def check_unit_consistency(obs: list[NormalizedObservation]) -> Check:
    """单位与规范单位矛盾、且未能按修复规则处置的条目。"""
    bad = [f"{o.year} {o.item_raw} → {o.analyte_id} 单位={o.unit!r}"
           for o in obs if o.unit_status == "mismatch" and not o.repaired_by]
    repaired = sum(1 for o in obs if o.repaired_by)
    if bad:
        return Check("单位一致性", "warn",
                     f"{len(bad)} 条单位矛盾且未修复（已按修复规则处置 {repaired} 条）", bad)
    return Check("单位一致性", "pass",
                 f"无未处置的单位矛盾；按显式修复规则重归属 {repaired} 条")


def check_delta(obs: list[NormalizedObservation], factor: float = 5.0) -> Check:
    """Delta Check：同一指标相邻年份变动超过 ``factor`` 倍即高度可疑。

    检验科用差值检查抓标本/录入错误；跨年同人数据同样适用。
    """
    by_id = _by_analyte(obs)
    bad: list[str] = []
    for aid, rows in sorted(by_id.items()):
        nums = sorted((r.year, r.value) for r in rows
                      if r.value is not None and r.value != 0)
        for (y1, v1), (y2, v2) in zip(nums, nums[1:]):
            ratio = max(abs(v2), abs(v1)) / max(1e-9, min(abs(v2), abs(v1)))
            if ratio > factor:
                why = 跨年记录口径.get(aid)
                note = (f"；**跨年记录口径不同**（{why}）→ 数值不可比，"
                        f"报告须按『不可比』声明") if why else ""
                bad.append(f"{aid} {y1}={v1} → {y2}={v2}（{ratio:.1f}×{note}）")
    if bad:
        return Check("Delta Check", "warn",
                     f"{len(bad)} 处跨年跳变超过 {factor:g}×（疑似抽取错或真事件）", bad)
    return Check("Delta Check", "pass", f"无跨年跳变超过 {factor:g}× 的指标")


def check_plausibility(obs: list[NormalizedObservation],
                       max_height_delta: float = 2.0) -> Check:
    """生理不可能的值：成年人身高逐年变化超过阈值。"""
    bad = [f"{o.year} 身高={o.value}"
           for o in obs if o.item_raw == "身高" and o.value is not None]
    hs = [o for o in obs if o.item_raw == "身高" and o.value is not None]
    if len(hs) >= 2:
        lo = min(h.value for h in hs)
        hi = max(h.value for h in hs)
        if hi - lo > max_height_delta:
            return Check("生理合理性", "warn",
                         f"身高跨年差异 {hi - lo:.1f}cm 超过 {max_height_delta}cm —— "
                         f"成年人不该如此，BMI 相关结论应标不可评估", bad)
    return Check("生理合理性", "pass", f"身高跨年差异在 {max_height_delta}cm 内")


def check_constant_series(obs: list[NormalizedObservation]) -> Check:
    """三年数值一位小数不差 —— 生物学上概率极低，疑似未被重新检测。"""
    by_id = _by_analyte(obs)
    bad: list[str] = []
    for aid, rows in sorted(by_id.items()):
        nums = [r.value for r in rows if r.value is not None]
        yrs = {r.year for r in rows if r.value is not None}
        # 值全为 0 的一律不看：尿沉渣里「大红细胞 0.0」「类酵母细菌 0.0」
        # 三年为 0 是常态，不是「未重测」。
        if len(yrs) >= 3 and len(nums) >= 3 and len(set(nums)) == 1 and nums[0] not in (0, None):
            bad.append(f"{aid} 三年同为 {nums[0]}（{', '.join(sorted(yrs))}）")
    if bad:
        return Check("常数序列", "warn",
                     f"{len(bad)} 项三年一位小数不差，疑似未重测", bad)
    return Check("常数序列", "pass", "无三年完全同值的指标")


def check_duplicates(obs: list[NormalizedObservation]) -> Check:
    """同一 (指标, 年份) 出现多条 —— 必须先拦下，不能静默取一条。

    判定层是 ``series[year] = value`` 的写法，重复会让**后一条静默覆盖前一条**。
    同名撞键（尿液检查里两项都叫「白细胞」）本应靠 (段, 项目, 值类型) 落成
    不同 analyte_id；若仍然重复，说明归一没做到位或数据被污染。
    """
    seen: dict[tuple[str, str], list] = {}
    for o in obs:
        if o.value is None:
            continue
        seen.setdefault((o.analyte_id, o.year), []).append(o)
    bad = []
    for (aid, y), rows in sorted(seen.items()):
        vals = {r.value for r in rows}
        if len(rows) > 1:
            bad.append(f"{aid} {y} 有 {len(rows)} 条记录，值 {sorted(vals)}"
                       + ("（值相同）" if len(vals) == 1 else "（**值不同，会静默覆盖**）"))
    if bad:
        return Check("重复观测", "warn",
                     f"{len(bad)} 处同一(指标,年份)出现多条", bad)
    return Check("重复观测", "pass", "无同一(指标,年份)的重复记录")


def check_extraction_audit(audit: list[dict]) -> Check:
    """判为化验页却**一项都没抽到** → fatal。

    为什么单列这一条：同一机构的历史报告靠覆盖率校验能发现"这一年项数异常"，
    但**新机构的第一份报告没有基线** —— 0 项会被当成"这份报告没有化验数据"，
    静默通过。这是最危险的一类失败：错得毫无征兆。
    """
    empty = [f"{a.get('year', '?')} p{a['page_no']}"
             for a in audit if a.get("kind") == "lab" and a.get("items", 0) == 0]
    if empty:
        return Check("化验页抽取审计", "fatal",
                     f"{len(empty)} 页被判为化验页但一项都没抽到 —— "
                     f"版式未识别，数据整段丢失且无任何征兆（需新增模板）", empty)
    labs = [a for a in audit if a.get("kind") == "lab"]
    if not labs:
        return Check("化验页抽取审计", "warn", "没有识别出任何化验页")
    return Check("化验页抽取审计", "pass",
                 f"{len(labs)} 页化验页共抽到 {sum(a['items'] for a in labs)} 项")


def check_unmapped_rate(obs: list[NormalizedObservation],
                        threshold: float = 0.2) -> Check:
    """归一失败率过高 → 说明这是**没见过的机构**，需要建词表条目。

    匹配不上本身是正确行为（不猜不丢），但整批都匹配不上就不是"个别新项目"，
    而是"这个机构的命名体系没登记过"。
    """
    from .normalize import UNMAPPED
    total = len(obs)
    if not total:
        return Check("归一覆盖率", "warn", "没有观测数据")
    un = [o for o in obs if o.analyte_id == UNMAPPED]
    rate = len(un) / total
    sample = sorted({f"{o.section}/{o.item_raw}" for o in un})[:6]
    if rate > threshold:
        return Check("归一覆盖率", "warn",
                     f"{len(un)}/{total}（{rate:.0%}）未映射，超过阈值 "
                     f"{threshold:.0%} —— 疑似新机构命名体系未登记", sample)
    return Check("归一覆盖率", "pass",
                 f"未映射 {len(un)}/{total}（{rate:.0%}）" +
                 (f"，样例：{'、'.join(sample)}" if sample else ""))


def check_drift(audit: list[dict], baseline: dict,
                tol: float = 0.15) -> Check:
    """漂移监控：同机构的项数突然偏离基线 → 疑似改版（框架 D1）。

    为什么单列这一条：**改版不会报错**。列位挪几 pt、表头换个词，抽取器照跑，
    只是悄悄少抽或多抽几项。同一机构的历史报告靠这条能立刻发现；而覆盖率校验
    （跨年比较）要等到"这一年明显不同"才响，太迟。

    ``baseline`` 来自模板的 ``stats``，由 ``draft_template`` 从历史报告算出。
    """
    base = baseline.get("items_per_report") or []
    if not base or not audit:
        return Check("版式漂移", "pass", "无基线可比（新机构首次接入）")
    import statistics
    med = statistics.median(base)
    per_doc: dict[str, int] = {}
    for a in audit:
        y = a.get("year", "?")
        per_doc[y] = per_doc.get(y, 0) + a.get("items", 0)
    bad = []
    for y, n in sorted(per_doc.items()):
        if med and abs(n - med) / med > tol:
            bad.append(f"{y}: {n} 项（基线中位数 {med:.0f}，偏离 "
                       f"{(n - med) / med:+.0%} > {tol:.0%}）")
    detail = " · ".join(f"{y}:{n}" for y, n in sorted(per_doc.items()))
    if bad:
        return Check("版式漂移", "warn",
                     f"项数偏离基线，**疑似机构改版**（{detail}）", bad)
    return Check("版式漂移", "pass", f"项数与基线一致（{detail}）")


def check_carrier(pages: list[dict],
                  unknown_layout: Optional[dict] = None,
                  n_obs: Optional[int] = None) -> Check:
    """G0 载体判定 + 版式路由（**P0-7**）：**声明了却一直没接线**的一关。

    两件事：

    1. **扫描件**（原有）：v1 没有图像通道（框架 §七明确排除）。遇到"有图像、
       无文字层"的页，正确的做法不是静默跳过，而是**说出来**。
    2. **版式不匹配**（P0-7 新增）："有文字层但版式不匹配"同样危险 —— 检测层
       一直报 ``UNKNOWN_LAYOUT``，但级别是 WARN、退出码 0、报告照出，致命的
       "一条都没读到"被归进「数据本身存疑的」小节，**读起来像"没发现问题"**。
       现在：``UNKNOWN_LAYOUT`` 且**一条观测都没有** → **FATAL**（上层必须
       非零退出、中止后续所有层），且这条检查**置顶**。

    ``pages`` 每项：``{page_no, chars, images}``；空白页（无字无图）不算问题。
    ``unknown_layout``：``{年份: 匹配失败原因}``（模板路由 template is None 的年份）。
    ``n_obs``：本次全流程读到的观测总数（判"一条都没读到"）。
    """
    unknown_layout = unknown_layout or {}
    scanned = [p for p in pages
               if p.get("chars", 0) < 40 and p.get("images", 0) > 0]
    blank = [p for p in pages if p.get("chars", 0) < 40 and p.get("images", 0) == 0]

    if unknown_layout:
        items = [f"{y}：{unknown_layout[y]}" for y in sorted(unknown_layout)]
        if n_obs == 0:
            return Check(
                "版式路由（G0/G1）", "fatal",
                "**未能识别该报告版式（机构/版式未注册）** —— "
                "**本次未能读取任何数据**（有文字层，但版式匹配不上；"
                "内容未被分析，不是「没有异常」）",
                items + scanned_items(scanned))
        return Check(
            "版式路由（G0/G1）", "warn",
            f"**{len(unknown_layout)} 份报告未能识别版式（机构/版式未注册）**"
            "—— 其数据**未纳入本次分析**（不是「没有异常」）",
            items + scanned_items(scanned))

    if scanned:
        return Check(
            "载体判定（G0）", "warn",
            f"**{len(scanned)} 页疑似扫描件**（有图无文字层）—— v1 无图像通道，"
            f"这些页的内容**未被分析**",
            scanned_items(scanned))
    detail = f"{len(pages) - len(blank)}/{len(pages)} 页有文字层"
    if blank:
        detail += f"；{len(blank)} 页空白（无字无图，正常）"
    return Check("载体判定（G0）", "pass", detail)


def scanned_items(scanned: list[dict]) -> list[str]:
    return [f"第 {p['page_no']} 页：{p['images']} 张图 / {p['chars']} 字"
            for p in scanned]


def check_intervals(metas: Optional[dict]) -> Check:
    """采样间隔：把真实日期与间隔显式呈现出来（框架 D11）。

    为什么单列：RCV 公式里没有时间，但真实变化的累积是随时间的。
    本案例 10.8 与 15.4 个月两段相差 43%，而这个事实原先**不在任何数据结构里**，
    只写在文档的一句话里。放进来之后，判定层才能对它负责。
    """
    if not metas:
        return Check("采样间隔", "pass", "未提供采样日期")
    from .judge import interval_notes
    dates = {y: m.exam_date for y, m in metas.items() if m.exam_date}
    if len(dates) < 2:
        return Check("采样间隔", "warn", f"只有 {len(dates)} 个采样日期")
    ivs, notes, nominal = interval_notes(dates)
    detail = " · ".join(f"{k} {v}个月" for k, v in sorted(ivs.items()))
    detail += f"（名义步长 {nominal} 个月）"
    if notes:
        return Check("采样间隔", "warn",
                     f"**间隔不均等** — {detail}", list(notes.values()))
    return Check("采样间隔", "pass", detail)


def validate(obs: list[NormalizedObservation],
             audit: Optional[list[dict]] = None,
             baseline: Optional[dict] = None,
             metas: Optional[dict] = None,
             pages: Optional[list[dict]] = None,
             unknown_layout: Optional[dict] = None) -> ValidationReport:
    """全套 L3 检查。

    ``unknown_layout``（P0-7）：模板路由不上的年份 → ``{年份: 原因}``。
    一旦存在，**版式路由检查置顶**（读报告的人第一眼就看到），并且
    "UNKNOWN_LAYOUT 且 0 观测" 时为 **fatal**（上层必须非零退出、中止后续层）。
    """
    checks = [
        check_internal_identity(obs),
        check_coverage(obs),
        check_unit_consistency(obs),
        check_delta(obs),
        check_plausibility(obs),
        check_constant_series(obs),
        check_duplicates(obs),
        check_unmapped_rate(obs),
    ]
    if audit is not None:
        checks.append(check_extraction_audit(audit))
        checks.append(check_drift(audit, baseline or {}))
    checks.append(check_intervals(metas))
    if pages is not None:
        carrier = check_carrier(pages, unknown_layout=unknown_layout,
                                n_obs=len(obs))
        if unknown_layout:
            checks.insert(0, carrier)      # P0-7：置顶，不许沉在数据质量小节里
        else:
            checks.append(carrier)
    return ValidationReport(checks=checks)
