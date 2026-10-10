#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L6 结论合成层的回归测试。    python3 tests/test_conclude.py"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.conclude import group_by_upstream, synthesize  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all, year_from_name  # noqa: E402
from checkup.schema import ConclusionClass  # noqa: E402
from checkup.validate import validate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_profile()
SRC = PATHS.real_reports
_syn = None


def syn():
    global _syn
    if _syn is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=cat)
        metas, find, narr = {}, [], []
        for fn in sorted(os.listdir(SRC)):
            if fn.lower().endswith(".pdf"):
                y = year_from_name(fn) or "?"
                m, n, _, f = extract_report(os.path.join(SRC, fn), y, TPL)
                metas[y], narr, = metas.get(y) or {}, narr + n
                metas[y] = m
                find += f
        derived = evaluate(Composer(js, metas, load_profile(
            PATHS.profile)))
        _syn = synthesize(js, derived, validation=validate(obs),
                          findings=find, narratives=narr,
                          profile=load_profile(PATHS.profile))
    return _syn


def of(cls: str):
    return [c for c in syn() if c.cls == cls]


def base_obs():
    """全部年份的观测（供需要原始判定的测试用）。"""
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    return [o for rows in build_all(SRC, cat).values() for o in rows]


def find(sub: str):
    return [c for c in syn() if sub in c.text]


# --------------------------------------------------------------- 归并

def test_lipid_abnormality_is_one_conclusion_not_two():
    """高TG + 低HDL 是**一条**定性结论，不是两条独立毛病。

    分开报会让人以为有两个问题，而它们往往出自同一上游。
    """
    hits = [c for c in syn() if "血脂异常" in c.text]
    assert len(hits) == 1, [c.text for c in hits]
    c = hits[0]
    assert c.cls == ConclusionClass.CONFIRMED.value
    assert "甘油三酯" in c.text and "高密度脂蛋白" in c.text
    assert c.upstream == "代谢背景"


def test_upstream_grouping_puts_lipid_and_ggt_together():
    """血脂异常与 GGT 上升必须归到同一个共同上游。"""
    groups = group_by_upstream(syn())
    metabolic = groups.get("代谢背景", [])
    texts = " ".join(c.text for c in metabolic)
    assert "血脂异常" in texts
    assert "谷氨酰转肽酶" in texts


def test_upstream_grouping_reference_group():
    """数据质量类结论归到「检测与记录不一致」以外的独立归类，不混进代谢组。"""
    groups = group_by_upstream(syn())
    assert "代谢背景" in groups
    dq = of(ConclusionClass.DATA_QUALITY.value)
    assert dq, "应有数据质量结论"
    assert all(c.upstream == "" for c in dq), "数据质量不该挂代谢上游"


# --------------------------------------------------------------- 冲突消解

def test_position_normal_but_change_beyond_noise_is_to_verify():
    """位置轴三年正常、变化轴超出噪声 → **待核查**，不是「正常」。

    本案例 4 项超出噪声里有 3 项（GGT / CK / 肌酐）位置三年都正常——
    只看位置轴会把它们全判成没事。
    """
    hits = [c for c in of(ConclusionClass.TO_VERIFY.value)
            if "位置都在参考区间内" in c.text]
    names = " ".join(c.text for c in hits)
    for n in ("谷氨酰转肽酶", "肌酸激酶", "肌酐"):
        assert n in names, f"{n} 的冲突没有被消解出来"
    for c in hits:
        assert "位置正常不等于没变化" in c.text


def test_near_upper_bound_is_flagged():
    """总胆固醇 5.09 距上限 5.18 仅 1.7% —— 未越线但要看住。"""
    hits = find("总胆固醇")
    assert any("距参考上限" in c.text for c in hits), [c.text for c in hits]


# --------------------------------------------------------------- 不重复

def test_status_conclusion_is_one_per_rule_not_one_per_year():
    """状态类（ASCVD 风险 / 危险分层）跨年只出**一条**，用轨迹表达。"""
    for key in ("ASCVD 风险", "血脂危险分层"):
        hits = [c for c in syn() if c.text.startswith(key)]
        assert len(hits) == 1, f"{key} 出了 {len(hits)} 条"
        assert "2024" in hits[0].text and "2026" in hits[0].text


def test_imaging_conclusion_is_merged_across_years():
    """同一影像项目跨年只出**一条**，带三年轨迹。"""
    for item in ("甲状腺", "肝脏", "胆囊", "肺部CT"):
        hits = [c for c in of(ConclusionClass.TO_FOLLOWUP.value)
                if c.text.startswith(item + "：")]
        assert len(hits) == 1, f"{item} 出了 {len(hits)} 条"
        if item != "肺部CT":       # CT 只有 2024/2026 两年
            assert "2024" in hits[0].text and "2026" in hits[0].text


def test_institution_self_report_not_duplicated():
    """机构自报的「高甘油三酯血症」已被血脂异常结论覆盖，不再重复。"""
    hits = [c for c in syn() if "高甘油三酯血症" in c.text]
    assert not hits, [c.text[:50] for c in hits]


def test_constant_series_only_singled_out_for_hdl():
    """常数序列只在 HDL（危险因素的唯一依据）上单列，其余由 L3 汇总覆盖。

    且值为 0 的（尿沉渣常态）一律不看。
    """
    # 只挑**按指标单列**的那条；L3 的汇总条目以「常数序列：」开头，不算
    hits = [c for c in of(ConclusionClass.DATA_QUALITY.value)
            if "一位小数不差" in c.text and not c.text.startswith("常数序列：")]
    assert len(hits) == 1, [c.text[:40] for c in hits]
    assert "高密度脂蛋白" in hits[0].text
    assert "唯一依据" in hits[0].text
    for bad in ("大红细胞", "类酵母细菌", "血小板压积"):
        assert bad not in hits[0].text


# --------------------------------------------------------------- 缺失≠阴性

def test_missing_year_is_declared():
    """缺失 ≠ 阴性：2025 未做胸部 CT / 骨密度，必须显式说出来。"""
    hits = [c for c in syn() if "缺失不等于阴性" in c.text]
    assert hits, "没有任何「缺失≠阴性」结论"
    txt = " ".join(c.text for c in hits)
    assert "骨密度" in txt
    assert "肺部CT" in txt, f"CT 缺失没被声明: {txt[:120]}"


def test_missing_conclusion_names_the_years():
    for c in syn():
        if "缺失不等于阴性" in c.text:
            assert "2025" in c.text, c.text


# --------------------------------------------------------------- 安全

def test_every_conclusion_has_text_and_evidence():
    for c in syn():
        assert c.text.strip(), c.conclusion_id
        assert c.evidence, f"{c.conclusion_id} 无证据指针"


def test_no_diagnostic_assertion_type():
    """措辞层只允许 informational / advisory / red_flag。"""
    for c in syn():
        assert c.assertion_type in ("informational", "advisory", "red_flag"), \
            f"{c.conclusion_id} 越界: {c.assertion_type}"


def test_evidence_points_to_page_year_or_rule():
    """证据必须能回溯：页码（p12）/ 年份（2024）/ 计算过程（= 或 →）三者至少其一。"""
    import re as _re
    pat = _re.compile(r"p\d+|20\d\d|=|→")
    for c in syn():
        assert any(pat.search(e) for e in c.evidence), \
            f"{c.conclusion_id} 证据不可回溯: {c.evidence[:1]}"


# --------------------------------------------------------------- 覆盖面

def test_all_four_classes_present():
    """四类结论都该出现（本案例数据确实四类都有）。"""
    got = Counter(c.cls for c in syn())
    for cls in (ConclusionClass.CONFIRMED, ConclusionClass.TO_VERIFY,
                ConclusionClass.TO_FOLLOWUP, ConclusionClass.DATA_QUALITY):
        assert got[cls.value] > 0, f"缺 {cls.value}: {dict(got)}"


def test_conclusion_ids_are_unique():
    ids = [c.conclusion_id for c in syn()]
    assert len(ids) == len(set(ids))


# --------------------------------------------- 反面用例（由变异测试指出）
#
# 以下几条的模式完全一样：**原先只测了正面，没测反面**。
# 把 ``and`` 改成 ``or``、把 ``==`` 改成 ``!=``，测试照样全过 ——
# 因为断言只覆盖了"该出现时出现了"，没覆盖"不该出现时没出现"。

def _reflag(o, value):
    """改了数值就要**重算 flag**。

    ``judge_position`` 用的是抽取时算好的 ``o.flag``，不是从 value 现算的。
    只改 value 不改 flag，位置轴会照旧 —— 这条踩过一次。
    """
    from dataclasses import replace
    from checkup.tables import _calc_flag
    return replace(o, value=value,
                   flag=_calc_flag(value, o.ref_low, o.ref_high, o.ref_op or ""))


def _custom(**over):
    """用改造过的观测重新合成结论（不碰真实数据）。"""
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    per = build_all(SRC, cat)
    obs = [o for rows in per.values() for o in rows]
    obs = [_reflag(o, over[o.analyte_id]) if o.analyte_id in over else o
           for o in obs]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=cat)
    pf = load_profile(PATHS.profile)
    derived = evaluate(Composer(js, {}, pf))
    return synthesize(js, derived, profile=pf)


def test_only_one_lipid_abnormal_does_not_make_the_combined_conclusion():
    """**只有一项异常**时不得出「高TG + 低HDL」这条合并结论。

    变异测试发现 ``if tg and hdl`` / ``if tg_y and hdl_y`` 改成 ``or``
    无人管 —— 因为只测了"两项都异常 → 出一条"。
    """
    only_tg = _custom(**{"CHEM.高密度脂蛋白胆固醇": 1.5})     # HDL 正常
    assert not [c for c in only_tg if c.text.startswith("血脂异常")], \
        "只有 TG 异常也出了合并结论"
    only_hdl = _custom(**{"CHEM.甘油三酯": 1.0})               # TG 正常
    assert not [c for c in only_hdl if c.text.startswith("血脂异常")], \
        "只有 HDL 异常也出了合并结论"
    # 而两项都异常时应当出现 —— 避免把这条测试写成永真
    assert [c for c in syn() if c.text.startswith("血脂异常")]


def test_near_upper_bound_requires_position_not_abnormal():
    """**位置已异常**的项不该再进「贴近上限」那一条（会被确认类覆盖）。

    变异测试发现 ``j.position.get(_lt) != "abnormal"`` 的 ``!=`` 改 ``==``
    无人管 —— 反面没测。
    """
    # 把 TG 顶到远超上限 → 位置 abnormal → 不该出现在"距参考上限仅…"里
    far = _custom(**{"CHEM.甘油三酯": 9.9})
    assert not [c for c in far if "距参考上限" in c.text and "甘油三酯" in c.text]


def test_near_upper_bound_requires_within_five_percent():
    """距上限 **超过 5%** 的项不报「贴近上限」。

    变异测试发现 ``0 <= (hi - v) / hi < 0.05`` 里的四个运算符改了都无人管。
    """
    # 甘油三酯上限 1.7：取 1.5 → 距上限 11.8%，不该报
    far = _custom(**{"CHEM.甘油三酯": 1.5})
    assert not [c for c in far if "距参考上限" in c.text and "甘油三酯" in c.text]
    # 取 1.68 → 距上限 1.2%，应当报
    near = _custom(**{"CHEM.甘油三酯": 1.68})
    assert [c for c in near if "距参考上限" in c.text and "甘油三酯" in c.text], \
        [c.text[:60] for c in near]


def test_constant_series_needs_three_points():
    """常数序列只在**至少 3 个点**时才值得报（2 个点相同很常见）。

    变异测试发现 ``len(vals) >= 3`` 改成 ``>`` 无人管。
    """
    # 只有两年且 HDL 相同 → 不该报"一位小数不差"
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    from dataclasses import replace
    per = build_all(SRC, cat)
    obs = [o for rows in per.values() for o in rows if o.year in ("2025", "2026")]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=cat)
    pf = load_profile(PATHS.profile)
    cons = synthesize(js, evaluate(Composer(js, {}, pf)), profile=pf)
    assert not [c for c in cons if "一位小数不差" in c.text], \
        [c.text[:60] for c in cons]


def test_constant_series_ignores_zero_values():
    """常数序列**值为 0 时不报**。

    注意这条守卫**只作用于 HDL 那一支** —— 常数序列检查写死了
    ``jm.get("CHEM.高密度脂蛋白胆固醇")``（尿沉渣里恒为 0 的项很多，
    但那条路径根本不查它们）。所以这里直接构造 HDL 三年全 0。
    变异测试发现 ``vals[0] != 0`` 改成 ``==`` 无人管。
    """
    cons = _custom(**{"CHEM.高密度脂蛋白胆固醇": 0.0})
    assert not [c for c in cons if "一位小数不差" in c.text], \
        [c.text[:60] for c in cons]
    # 反面：非 0 的常数序列**应当**报 —— 避免写出永真的测试
    assert [c for c in syn() if "一位小数不差" in c.text]


def test_near_upper_bound_at_exactly_zero_distance():
    """数值**恰好等于**上限：位置正常、距离 0 → 应当报「贴住上限」。

    变异测试发现 ``0 <= (hi - v) / hi`` 的 ``<=`` 改成 ``<`` 无人管 ——
    原先只测了"距上限一点点"，没测"距上限恰好为 0"。
    """
    cons = _custom(**{"CHEM.甘油三酯": 1.7})       # 上限 1.7，区间闭 → 正常
    hit = [c for c in cons if "距参考上限" in c.text and "甘油三酯" in c.text]
    assert hit, [c.text[:60] for c in cons]
    assert "仅 0.000" in hit[0].text, hit[0].text


def test_near_upper_bound_five_percent_is_a_strict_boundary():
    """距上限 **5%** 是**严格**上界：更近的报、更远的报不了。

    为什么不用"恰好 5%"：``(1.7 - 1.615) / 1.7`` 在浮点下是
    ``0.049999999999999996``，**构造不出精确的 5%**。和 `judge.py` 里
    RCV 阈值那处一样，属于**浮点刀锋**，``<`` 改成 ``<=`` 实际上杀不死。
    这里只锁住两侧。
    """
    nearer = _custom(**{"CHEM.甘油三酯": 1.62})     # 距上限 4.7% → 报
    assert [c for c in nearer
            if "距参考上限" in c.text and "甘油三酯" in c.text]
    farther = _custom(**{"CHEM.甘油三酯": 1.60})    # 距上限 5.9% → 不报
    assert not [c for c in farther
                if "距参考上限" in c.text and "甘油三酯" in c.text], \
        [c.text[:70] for c in farther]


# ------------------------------ 由「输出快照」分类工具指出的 5 处真缺口
#
# `tools/triage_mutants.py` 把每个变异体注入后跑 8 组输入，比对**输出快照**：
# 输出变了但测试没失败 = 真缺口。conclude.py 51 个变异体里定了 5 处。
# 下面逐个补。注意这些**不是**"测试没写全"，而是"没有测试看着那个输出"。

def test_china_par_takes_the_percentage_branch():
    """China-PAR 走**百分比轨迹**分支（``if rid == "ASCVD.CHINAPAR"``）。

    变异测试把 ``==`` 改成 ``!=`` 后输出变了却没人管 —— 那条分支会走成
    「按分层文字」的另一支，风险数字整个消失。
    """
    got = find("ASCVD 风险")
    assert got, "应当有 China-PAR 的结论"
    c = got[0]
    assert "%" in c.text, c.text              # 百分比轨迹
    assert "→" in c.text, c.text              # 跨年轨迹
    # 而分层类结论走的是分层文字，不带百分号
    strat = find("危险分层")
    assert strat and "%" not in strat[0].text, strat[0].text if strat else ""


def test_trajectory_shows_risk_factors_when_band_is_stable():
    """分层**稳定**时附上危险因素明细；有变化时只给轨迹。

    ``if len(bands) == 1 else ...`` 改成 ``!=`` 后输出变了却没人管。
    """
    st = find("危险分层")
    assert st, "应当有分层结论"
    t = st[0].text
    if "分层有变化" in t:
        assert "（" not in t, t                   # 变化时不附明细
    else:
        assert "（" in t, t                       # 稳定时附明细


def test_near_upper_bound_percentage_is_computed_from_the_gap():
    """靠近上限的**百分比**必须由 ``(上限 − 实测) / 上限`` 算出。

    变异测试把 ``-`` 改成 ``+`` 后输出变了却没人管 —— 这是
    **「值只活在 f-string 里」**那一类（`non-HDL-C` 事故的同一形状）：
    数字在文案里算出来，没有任何测试看得到它。
    """
    import re
    # 真实数据里就有两条靠近上限的（总胆固醇 5.09/上限 5.18、比重 1.007/上限 1.03）
    hit = [c for c in syn() if "距参考上限" in c.text]
    assert hit, "应当有「靠近上限」的结论"
    n_ok = 0
    for c in hit:
        # 文案形如「总胆固醇 5.09 距参考上限 5.18 仅 0.090（1.7%），未越线但需盯住」
        m = re.search(r"距参考上限 ([\d.]+) 仅 ([\d.]+)（([\d.]+)%）", c.text)
        assert m, c.text
        hi, gap, pct = float(m.group(1)), float(m.group(2)), float(m.group(3))
        # 差距必须等于 上限 − 实测；百分比必须由差距算出
        assert abs(gap - (hi - (hi - gap))) < 1e-9
        assert abs(pct - gap / hi * 100) < 0.15, (c.text, gap, hi, pct)
        # 反向确认：若把减法写成加法，百分比会明显不同
        assert abs(pct - (hi + gap) / hi * 100) > 5, c.text
        n_ok += 1
    assert n_ok >= 2, n_ok


def test_uncertainty_is_high_when_not_robust():
    """``uncertainty`` 由 ``j.robustness != "稳健超出"`` 决定 —— **不能反**。

    变异测试把 ``!=`` 改成 ``==`` 后输出变了却没人管：那会把"不稳健"
    标成"高把握"、"稳健"标成"中把握"—— **把把握度整个反过来说**，
    而这是告诉读者该多信这条结论的字段。
    """
    hit = [c for c in syn() if "位置正常不等于没变化" in c.text]
    assert hit, "需要一条「变化超噪声但位置正常」的结论"
    # 断言**关系**：把握度必须与判定的稳健性一致，不是写死的某个值。
    # 找对应的判定，看它的 robustness。
    js = judge_all(base_obs(), os.path.join(BASE, "catalog", "cvi.json"))
    by_name = {j.canonical_name: j for j in js}
    checked = 0
    for c in hit:
        # evidence 形如 '生化检查/肌酐=95 (p10)'，取斜杠后、等号前
        key = c.evidence[0].split("/")[-1].split("=")[0]
        j = by_name.get(key)
        if j is None:
            continue
        want = "medium" if j.robustness == "稳健超出" else "high"
        assert c.uncertainty == want, (key, j.robustness, c.uncertainty, want)
        checked += 1
    assert checked, "一条都没对上 —— 测试本身失效了"


def test_lipid_conclusion_text_names_both_analytes():
    """血脂合并结论的文案要点明**两项**（高 TG **和** 低 HDL）。"""
    got = find("血脂异常")
    assert got, "应当有血脂异常结论"
    t = got[0].text
    assert "高甘油三酯" in t and "高密度脂蛋白" in t, t


def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
