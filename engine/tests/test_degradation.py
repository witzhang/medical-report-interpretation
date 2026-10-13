#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端降级：报告"不像预期"时，整条链路怎么表现。    python3 tests/test_degradation.py

已有测试多数是**层内**的（抽取、归一、判定各自降级）。这一套测的是
**L1→L7 整条链**在异常输入下的表现，因为本轮的三个真缺陷全是在这里露头的：

1. 全未映射 → 466 条观测塌缩成**1 条判定**（其余静默吞掉）
2. 单年报告 → 结论却写「**三年均**超出参考区间」
3. 空输入 → 未验证过

判据同项目其余部分：**降级可以，静默不行。**
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.conclude import span_text, synthesize  # noqa: E402
from checkup.judge import judge_all, obs_key  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.validate import validate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_profile()
SRC = PATHS.real_reports
REAL = {"2024": "20240612体检报告.pdf", "2025": "20250507体检报告.pdf",
        "2026": "20260609体检报告.pdf"}
_base = None


def base():
    global _base
    if _base is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        obs = [o for rows in build_all(SRC, cat).values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        _base = dict(obs=obs, metas=metas, cat=cat, pf=load_profile(
            PATHS.profile))
    return _base


def run(obs=None, metas=None):
    metas = metas if metas is not None else base()["metas"]
    obs = obs if obs is not None else base()["obs"]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=metas)
    derived = evaluate(Composer(js, metas, base()["pf"]))
    cons = synthesize(js, derived, validation=validate(obs, metas=metas),
                      profile=base()["pf"])
    return js, derived, cons


def years_in(text: str) -> set:
    return set(re.findall(r"\b(19|20)\d{2}\b", "x" + text)) and set(
        re.findall(r"\b((?:19|20)\d{2})\b", text))


def one_year(y="2026"):
    return ([o for o in base()["obs"] if o.year == y], {y: base()["metas"][y]})


# =========================================== 全未映射

def test_unmapped_items_do_not_collapse_into_one_judgement():
    """**本轮修掉的核心缺陷**：未归一的观测**不能**共用一个判定键。

    原先 ``judge_position`` 用 ``analyte_id`` 做键，而所有未映射项都叫
    ``UNMAPPED`` → 466 条塌缩成 1 条：项目名取第一条（白细胞）、
    数值取每年最后一条，**其余 465 项静默消失**。
    """
    unmapped = [replace(o, analyte_id=UNMAPPED) for o in base()["obs"]]
    js, _d, _c = run(unmapped)
    distinct = {obs_key(o) for o in unmapped}
    assert len(js) == len(distinct), (len(js), len(distinct))
    assert len(js) > 100, len(js)


def test_unmapped_judgements_keep_their_own_values():
    """塌缩时数值会串（显示"白细胞"却装着别的指标的数）。

    注意**显示名可以重复** —— 「白细胞」在血常规与尿液检查里都有，
    它们本来就是同名不同物。所以要看的是**每条判定的数值是否各自独立**，
    而不是名字是否唯一。
    """
    unmapped = [replace(o, analyte_id=UNMAPPED) for o in base()["obs"]]
    js, _d, _c = run(unmapped)
    names = [j.canonical_name for j in js]
    assert "白细胞" in names and "甘油三酯" in names
    # 每条判定的键互不相同（这才是防塌缩的实质）
    keys = {obs_key(o) for o in unmapped}
    assert len(js) == len(keys)
    # 抽两个同名不同段的，必须各自保有各自的值
    wbc = [j for j in js if j.canonical_name == "白细胞"]
    assert len(wbc) >= 2, [j.section for j in wbc]
    assert len({j.section for j in wbc}) >= 2
    assert len({str(sorted(j.series.items())) for j in wbc}) >= 2, \
        "两个「白细胞」的数值序列一模一样，说明仍被合到了一起"


def test_unmapped_same_name_in_different_sections_stays_separate():
    """同名不同段（尿液白细胞 vs 血常规白细胞）未映射时也必须分开。"""
    obs = [
        NormalizedObservation(year="2026", page_no=1, section="血常规",
                              item_raw="白细胞", occurrence=1,
                              analyte_id=UNMAPPED, value=7.2),
        NormalizedObservation(year="2026", page_no=1, section="尿液检查",
                              item_raw="白细胞", occurrence=1,
                              analyte_id=UNMAPPED, value=0.1),
    ]
    js, _d, _c = run(obs, {"2026": base()["metas"]["2026"]})
    assert len(js) == 2, [j.canonical_name for j in js]


def test_unmapped_report_still_produces_readable_output():
    """全未映射**不是**崩溃、也不是空输出 —— 靠报告自带区间仍能判位置轴。"""
    unmapped = [replace(o, analyte_id=UNMAPPED) for o in base()["obs"]]
    _js, _d, cons = run(unmapped)
    assert cons, "全未映射时一条结论都没有，用户看不到任何东西"
    assert all(c.text.strip() for c in cons)


# =========================================== 年数不必是三年

def test_single_year_conclusions_never_say_three_years():
    """**本轮修掉的第二个缺陷**：单年数据却写「三年均超出参考区间」。"""
    obs, metas = one_year()
    _js, _d, cons = run(obs, metas)
    for c in cons:
        assert "三年" not in c.text, c.text
        assert "2024" not in c.text, c.text
        assert "2025" not in c.text, c.text


def test_single_year_says_the_actual_year():
    obs, metas = one_year()
    _js, _d, cons = run(obs, metas)
    assert any("2026 年超出参考区间" in c.text for c in cons), \
        [c.text[:80] for c in cons]


def test_two_year_conclusions_say_two_years():
    obs = [o for o in base()["obs"] if o.year in ("2025", "2026")]
    metas = {y: base()["metas"][y] for y in ("2025", "2026")}
    _js, _d, cons = run(obs, metas)
    assert any("两年" in c.text for c in cons), [c.text[:80] for c in cons]
    for c in cons:
        assert "三年" not in c.text and "2024" not in c.text, c.text


def test_every_year_in_conclusions_exists_in_the_data():
    """**不变式**：结论文本里出现的每个年份都必须真的在数据里。

    这条能一次性抓住"硬编码某一年"的整类问题，比逐句断言更耐改。
    """
    for label, (obs, metas) in {
            "三年": (base()["obs"], base()["metas"]),
            "两年": ([o for o in base()["obs"] if o.year in ("2025", "2026")],
                     {y: base()["metas"][y] for y in ("2025", "2026")}),
            "单年": one_year(),
    }.items():
        have = {o.year for o in obs}
        _js, _d, cons = run(obs, metas)
        for c in cons:
            got = years_in(c.text)
            assert got <= have, f"{label}: 结论提到 {got - have}，但数据里只有 {have}"


def test_span_text():
    assert span_text([]) == "（无年份）"
    assert span_text(["2026"]) == "2026 年"
    assert span_text(["2025", "2026"]) == "两年"
    assert span_text(["2024", "2025", "2026"]) == "三年"


def test_three_year_case_is_unchanged():
    """本案例仍说「三年」—— 修年数不能把原来对的改错。"""
    _js, _d, cons = run()
    assert any("三年均超出参考区间" in c.text for c in cons)


# =========================================== 空输入

def test_empty_input_does_not_crash():
    js, derived, cons = run([], {})
    assert js == [] and derived == []
    assert cons, "空输入至少要有一条说明，不能什么都不给"


def test_missing_metas_does_not_crash():
    """没有 meta（拿不到日期/性别/年龄）也要能跑。"""
    js, _d, cons = run(base()["obs"], {})
    assert js and cons


# =========================================== 单通道

#: 化验类段前缀。去掉它们 = 只留体格检查/眼科/叙述通道
LAB_PREFIXES = ("BLD.", "CHEM.", "IMM.", "URI.", "STOOL.")


def test_only_narrative_channel_still_works():
    """只有体格检查与主检报告、没有任何化验项 —— 整条链仍应走通。"""
    _m, narr, _raw, _find = extract_report(
        os.path.join(SRC, REAL["2026"]), "2026", TPL)
    assert narr, "主检报告通道没抽出叙述项"
    obs = [o for o in base()["obs"]
           if o.year == "2026" and not o.analyte_id.startswith(LAB_PREFIXES)]
    assert obs, "去掉化验项后什么都不剩"
    js, _d, cons = run(obs, {"2026": base()["metas"]["2026"]})
    assert js and cons, (len(js), len(cons))


def test_no_lab_items_means_no_lab_derived_rules():
    """没有化验项时，依赖化验的派生规则必须落到 not_assessable，**不能算出数**。"""
    obs = [o for o in base()["obs"]
           if o.year == "2026" and not o.analyte_id.startswith(LAB_PREFIXES)]
    _js, derived, _cons = run(obs, {"2026": base()["metas"]["2026"]})
    for rid in ("RENAL.EGFR_CKDEPI2021", "HEPATIC.FIB4", "COMPOSITE.AIP"):
        rows = [d for d in derived if d.rule_id == rid]
        assert rows, rid
        assert all(d.status == "not_assessable" for d in rows), \
            (rid, [(d.year, d.value) for d in rows])


# ------------------------------------------------------------------ 主程序

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
