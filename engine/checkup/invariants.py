# -*- coding: utf-8 -*-
"""全链不变式（invariants）：对**任何输入**都必须成立的性质。

上一轮用"端到端降级"抓到两个 bug，靠的是临时想输入。这一轮把思路反过来：
不猜输入，而是**列出输出必须满足的性质**，然后拿真实数据、12 类扰动、
4 类降级场景一起轰。

为什么这比"多想几个输入"强：不变式是**一次性写、永远生效**的。
新加一条规则、新加一个字段，只要它破坏了不变式，测试立刻响。

不变式的选取标准是「**违反它一定是 bug，不可能是设计选择**」。
拿不准的一律不写 —— 写一条会误报的不变式，比不写更糟。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .judge import Judgement, obs_key
from .schema import Conclusion, DerivedResult, Finding

#: 五值枚举 —— 位置轴只能是这五个之一（或空 = 该年没数据）
POSITION_VALUES = {f.value for f in Finding} | {""}

#: 结论分类的合法取值
CONCLUSION_CLASSES = {"confirmed", "to_verify", "data_quality", "to_followup"}

_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass
class Violation:
    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


def check_all(obs, metas, judgements: list[Judgement],
              derived: list[DerivedResult], conclusions: list[Conclusion],
              catalog=None) -> list[Violation]:
    """跑全部不变式，返回被违反的清单（空 = 全过）。"""
    v: list[Violation] = []
    years_present = {o.year for o in obs} | set((metas or {}).keys())

    # --- 1. 判定不塌缩：判定数不能少于"互不相同的观测键"数
    keys = {obs_key(o) for o in obs}
    if len(judgements) < len(keys):
        v.append(Violation(
            "判定不塌缩",
            f"观测有 {len(keys)} 个不同的键，却只产出 {len(judgements)} 条判定 "
            f"—— 有项目被合并或吞掉"))

    # --- 2. 位置轴取值只允许五值枚举
    for j in judgements:
        for y, val in j.position.items():
            if val not in POSITION_VALUES:
                v.append(Violation(
                    "位置轴取值合法",
                    f"{j.canonical_name} {y} 的位置轴 = {val!r}，不在五值枚举内"))

    # --- 3. 判定里不得出现数据里没有的年份
    for j in judgements:
        extra = set(j.position) - years_present
        if extra:
            v.append(Violation(
                "判定年份真实",
                f"{j.canonical_name} 的位置轴提到 {sorted(extra)}，"
                f"但数据里只有 {sorted(years_present)}"))

    # --- 4. 结论里不得出现数据里没有的年份
    for c in conclusions:
        extra = set(_YEAR.findall(c.text)) - years_present
        if extra:
            v.append(Violation(
                "结论年份真实",
                f"结论提到 {sorted(extra)}，但数据里只有 {sorted(years_present)}："
                f"{c.text[:70]}"))

    # --- 5. 只有一年数据时，不得出现"趋势/变化"措辞
    if len(years_present) < 2:
        trend_words = ("趋势", "变化", "增大", "减小", "上升", "下降",
                       "逐年", "→")
        for c in conclusions:
            if any(w in c.text for w in trend_words):
                v.append(Violation(
                    "单年不谈趋势",
                    f"只有 {len(years_present)} 年数据，结论却用了趋势措辞："
                    f"{c.text[:70]}"))

    # --- 6. 结论分类合法
    for c in conclusions:
        if c.cls not in CONCLUSION_CLASSES:
            v.append(Violation("结论分类合法",
                               f"结论分类 {c.cls!r} 不在 {CONCLUSION_CLASSES}"))
        if not c.text.strip():
            v.append(Violation("结论非空", f"{c.conclusion_id} 的正文是空的"))

    # --- 7. 派生结果只能来自已声明的规则
    if catalog is not None or True:
        from .rules import RULES
        declared = {r.rule_id for r in RULES}
        for d in derived:
            if d.rule_id not in declared:
                v.append(Violation(
                    "派生规则已声明",
                    f"{d.rule_id} 不在规则库中 —— 出现了一条没有出处的派生结果"))

    # --- 8. 派生结果不得凭空有值
    for d in derived:
        if d.status == "ok" and d.value is None and not d.band and not d.text:
            v.append(Violation(
                "派生结果有内容",
                f"{d.rule_id} {d.year} 状态是 ok，但 value/band/text 全空"))

    # --- 9. 不可评估必须有理由
    for d in derived:
        if d.status == "not_assessable" and not d.missing and not d.notes:
            v.append(Violation(
                "不可评估说明理由",
                f"{d.rule_id} {d.year} 判不可评估，但没说缺什么"))

    # --- 10. 结论引用的规则必须都存在
    from .rules import RULES
    declared = {r.rule_id for r in RULES}
    for c in conclusions:
        for ref in (c.rule_refs or []):
            if ref not in declared:
                v.append(Violation(
                    "结论引用真实规则",
                    f"{c.conclusion_id} 引用了不存在的规则 {ref!r}"))

    # --- 11. 判定不得凭空有位置值却没有对应观测
    for j in judgements:
        if j.position and not j.series and not j.item_raw:
            # 位置轴有值但既无数值也无原始名 —— 基本不可能，除非构造错了
            if all(x is None for x in j.series.values()):
                v.append(Violation(
                    "判定有依据",
                    f"{j.canonical_name} 的位置轴有值，但 series 全是 None 且无原始名"))
    return v


def render(violations: list[Violation]) -> str:
    if not violations:
        return "✓ 全部不变式通过"
    return "\n".join(f"  ✗ {x}" for x in violations)


# ==================================================== 方向性不变式
#
# 上一组（上面）是"输出本身必须自洽"，这一组更强：
# **把输入往已知方向推，输出只能往已知方向动**。
# 反向移动 = 一定有 bug —— 这比"断言某个具体数值"耐改，
# 也比"断言某条结论存在"更能抓到静默错误。

def _pos(js, aid, year):
    j = next((x for x in js if x.analyte_id == aid), None)
    return j.position.get(year) if j else None


def _derived(derived, rid, year):
    return next((d for d in derived if d.rule_id == rid and d.year == year), None)


def check_monotonic_creatinine(before, after, year,  # 年份由调用方给（P0-3：不再默认 2026）
                               aid="CHEM.肌酐",
                               rid="RENAL.EGFR_CKDEPI2021") -> list[Violation]:
    """肌酐**升高** → eGFR 只能**下降或不变**，绝不能上升。

    CKD-EPI 是单调递减函数，所以这是一条硬性质。它同时能抓到"换算了单位
    又换回去""取了错年份"这类静默错。
    """
    v = []
    b = _derived(before["derived"], rid, year)
    a = _derived(after["derived"], rid, year)
    if b and a and b.value is not None and a.value is not None:
        if a.value > b.value + 1e-9:
            v.append(Violation(
                "肌酐↑则eGFR↓",
                f"{aid} 升高后 eGFR 反而从 {b.value} 升到 {a.value}"))
    return v


def check_direction_of_abnormal(before, after, aid, year) -> list[Violation]:
    """指标**变大**（且原本正常）→ 位置轴**不能**从 abnormal 变回 normal。

    只检查方向确定的：变大只会让它更可能越上限，不会越下限。
    """
    v = []
    pb, pa = _pos(before["judgements"], aid, year), _pos(after["judgements"], aid, year)
    if pb == "abnormal" and pa == "normal":
        v.append(Violation(
            "变大不会变正常",
            f"{aid} {year} 数值变大后，位置轴从 abnormal 变成 normal"))
    return v


def check_dropped_range_is_not_assessable(before, after, aid,
                                          year) -> list[Violation]:
    """**抹掉参考区间** → 该指标位置轴必须 ``not_assessable``，不能判正常。

    没有区间就无法判正常/异常 —— 这条抓住"区间缺失时默默按某种默认判"。
    """
    v = []
    pa = _pos(after["judgements"], aid, year)
    if pa not in (None, "not_assessable"):
        v.append(Violation(
            "无区间则不可评估",
            f"{aid} {year} 参考区间被抹掉后，位置轴仍给出 {pa!r}"))
    return v


def check_missing_is_not_improvement(before, after, aid) -> list[Violation]:
    """**删掉某年的数据** → 不得出现"回复正常/好转/回落"这类结论。

    「缺失 ≠ 阴性」的推论版：拿掉数据不能让结论变得更好。

    年份**从数据推导**（P0-3）：原签名带 ``years=("2024","2025","2026")`` 默认
    元组，是绑死本案例三年的硬编码（而且从未被函数体用到）。这里不再接受年份
    参数 —— 判断"哪一年被删过"由 ``before/after`` 的 judgements 自己说话。
    """
    v = []
    words = ("回复正常", "已恢复", "好转", "回落至区间", "回到区间内")
    new = [c for c in after["conclusions"]
           if any(w in c.text for w in words)
           and c.text not in {x.text for x in before["conclusions"]}]
    for c in new:
        v.append(Violation(
            "删数据不产生好转",
            f"删掉数据后新出现「好转」类结论：{c.text[:70]}"))
    return v


def check_determinism(run_twice_pair) -> list[Violation]:
    """同一输入跑两次 → 输出必须**完全一致**（无隐藏的集合序依赖）。"""
    a, b = run_twice_pair
    v = []
    if a["judgements"] != b["judgements"]:
        v.append(Violation("确定性", "两次运行的判定结果不一致"))
    if [c.text for c in a["conclusions"]] != [c.text for c in b["conclusions"]]:
        v.append(Violation("确定性", "两次运行的结论不一致"))
    return v


def check_sex_flip_blocks_male_only_rules(before, after, year,  # 年份由调用方给（P0-3）
                                         rid="ASCVD.CHINAPAR") -> list[Violation]:
    """性别改为女 → **只有男性方程**的规则必须变成不可评估或不可适用，
    绝不能沿用男性的数。"""
    v = []
    a = _derived(after["derived"], rid, year)
    b = _derived(before["derived"], rid, year)
    if a is None:
        return v
    if a.value is not None and b and b.value is not None:
        v.append(Violation(
            "性别改变不沿用男性读数",
            f"{rid} 在性别改为女后仍给出数值 {a.value}"))
    return v


def check_red_flags_are_declared(red_flag_items, judgements=None) -> list[Violation]:
    """红旗必须来自**已声明的 A 类规则**，且必须指到真实存在的判定上。

    早先这版按 ``analyte_id`` 找，而 ``RedFlagHit`` 根本没有这个字段 ——
    于是不变式**空转**（永远返回 0 条）。空转的不变式比没有更糟：
    它给人一种"查过了"的错觉。
    """
    from .redflags import DEFAULT_A

    v = []
    declared = {getattr(r, "rule_id", None) for r in DEFAULT_A}
    known = ({j.analyte_id for j in judgements} | {j.canonical_name for j in judgements}
             if judgements else None)
    for h in red_flag_items or []:
        rid = getattr(h, "rule_id", None)
        if rid is not None and declared and rid not in declared:
            v.append(Violation("红旗来自已声明规则",
                               f"红旗用了未声明的规则 {rid!r}"))
        label = getattr(h, "label", None)
        if known is not None and label and label not in known:
            v.append(Violation("红旗指向真实项",
                               f"红旗点名了不存在的项目 {label!r}"))
    return v
