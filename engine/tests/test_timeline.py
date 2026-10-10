#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D11 时间轴引擎的回归测试。    python3 tests/test_timeline.py

框架里唯一标着"**无现成参照、风险最高**"的一项：6 个开源项目没有一个
把真实采样间隔放进数据结构，也没有一个做不等间隔的方向性提示。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.judge import interval_notes, judge_all, months_between  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.schema import DocumentMeta  # noqa: E402
from checkup.validate import check_intervals  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
SRC = PATHS.real_reports
REAL = {"2024": "20240612体检报告.pdf", "2025": "20250507体检报告.pdf",
        "2026": "20260609体检报告.pdf"}
_ctx = None


def ctx():
    global _ctx
    if _ctx is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        obs = [o for rows in per.values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                       catalog=cat, metas=metas)
        _ctx = dict(js=js, metas=metas, obs=obs)
    return _ctx


def _m(y, d):
    return DocumentMeta(doc_id=y, org_id="X", exam_date=d, page_count=1)


# --------------------------------------------------------------- 月数

def test_months_between():
    assert months_between("2024-06-12", "2025-06-12") == 12.0
    assert months_between("2024-06-12", "2025-05-07") == 10.8
    assert months_between("2025-05-07", "2026-08-20") == 15.4
    assert months_between("", "2025-01-01") is None
    assert months_between("不是日期", "2025-01-01") is None


def test_real_intervals_are_unequal():
    """本案例两段相邻间隔是 10.8 与 15.4 个月 —— 相差 43%。

    这个事实原先**不在任何数据结构里**，只写在文档的一句话里。
    """
    ivs, _notes, nominal = interval_notes(
        {"2024": "2024-06-12", "2025": "2025-05-07", "2026": "2026-08-20"})
    assert ivs["2024→2025"] == 10.8
    assert ivs["2025→2026"] == 15.4
    assert ivs["2024→2026"] == 26.2
    assert nominal == 13.1, nominal


def test_nominal_uses_adjacent_intervals_only():
    """名义步长取**相邻**间隔的中位数。

    拿所有间隔一起取中位，跨年对（天然两步）会被判成"必然偏长"，
    等于什么都没说。
    """
    _ivs, notes, nominal = interval_notes(
        {"2024": "2024-06-12", "2025": "2025-05-07", "2026": "2026-08-20"})
    assert nominal == 13.1, nominal
    # 跨年对 26.2 = 2 × 13.1，恰好合拍 → 不该有提示
    assert "2024→2026" not in notes, notes


def test_interval_notes_give_direction():
    """方向性提示是这一层的核心：偏长容易**漏判**，偏短容易**误判**。"""
    _ivs, notes, _n = interval_notes(
        {"2024": "2024-06-12", "2025": "2025-05-07", "2026": "2026-08-20"})
    assert "漏判" in notes["2025→2026"], notes["2025→2026"]
    assert "误判" in notes["2024→2025"], notes["2024→2025"]


def test_equal_intervals_produce_no_notes():
    """间隔均匀时不该有任何提示 —— 守卫不能把正常数据也标上。"""
    _ivs, notes, nominal = interval_notes(
        {"2024": "2024-06-01", "2025": "2025-06-01", "2026": "2026-06-01"})
    assert not notes, notes
    assert nominal == 12.0


# --------------------------------------------------------------- 判定层

def test_judgement_carries_dates_and_intervals():
    """每条判定都要带真实日期与间隔 —— 否则下游无从谈起。"""
    j = next(x for x in ctx()["js"] if x.intervals)
    assert j.dates["2026"] == "2026-08-20"
    assert j.intervals["2025→2026"] == 15.4
    # 只保留与本条目做过的比较相关的提示
    for k in j.interval_notes:
        assert k in j.change_pairs(), (k, j.change_pairs())


def test_rcv_threshold_does_not_depend_on_interval():
    """RCV 公式里**没有时间** —— 阈值不随间隔变，变的是它的敏感度。

    这是最容易搞混的一点：把间隔塞进 RCV 公式是错的。
    """
    import inspect
    from checkup.judge import judge_all, rcv

    # ① 结构证据：rcv() 的**签名里根本没有时间参数** —— 这正是"公式无时间"的含义。
    params = set(inspect.signature(rcv).parameters)
    assert not (params & {"interval", "months", "days", "years", "dt"}), params

    # ② 行为证据：同样的 CVi，间隔差一倍的两次比较必须得到**同一个阈值**。
    #    （原写法是 `rcv(x) == rcv(x)` —— 自己等于自己，恒真、不可能失败。）
    metas = dict(ctx()["metas"])
    obs = ctx()["obs"]
    a = {j.analyte_id: j for j in judge_all(
        obs, os.path.join(BASE, "catalog", "cvi.json"), metas=metas)}
    # 同一个指标、同一份 CVi 表 → 阈值只由 CVi/CVa 决定
    ck = next(j for j in a.values() if j.rcv_threshold)
    assert ck.rcv_threshold == round(rcv(ck.cv_i, 3.0), 1)
    # 换一组间隔完全不同的日期，阈值必须**一字不变**
    shifted = {y: metas[y] for y in metas}
    from dataclasses import replace
    shifted["2025"] = replace(shifted["2025"], exam_date="2025-01-01")
    shifted["2026"] = replace(shifted["2026"], exam_date="2026-01-01")
    b = {j.analyte_id: j for j in judge_all(
        obs, os.path.join(BASE, "catalog", "cvi.json"), metas=shifted)}
    assert a[ck.analyte_id].rcv_threshold == b[ck.analyte_id].rcv_threshold, \
        "间隔变了阈值也变了 —— 说明时间被塞进了 RCV 公式"
    # 而间隔本身确实变了（否则上面那条也是空的）
    assert a[ck.analyte_id].intervals != b[ck.analyte_id].intervals


def test_check_intervals_flags_unequal_spacing():
    c = check_intervals(ctx()["metas"])
    assert c.level == "warn", c.detail
    assert "间隔不均等" in c.detail
    assert any("漏判" in i for i in c.items)


def test_check_intervals_passes_when_missing_dates():
    c = check_intervals(None)
    assert c.level == "pass"


def test_long_interval_makes_within_noise_less_reassuring():
    """实际用途：偏长的那一段若判「无法与波动区分」，措辞上不能当"没事"。

    判定值本身不变（阈值没变），但**不确定性更高**——这一层就是把这件事
    显式挂到条目上，而不是靠读文档的人自己记得。
    """
    j = next(x for x in ctx()["js"]
             if "2025→2026" in x.interval_notes and x.rcv_threshold)
    assert "漏判" in j.interval_notes["2025→2026"]


# ------------------------------------------------------------------ 主程序

def test_judging_a_single_year_does_not_crash():
    """**只有一年数据时，判定必须安全返回**，不能抛越界。

    三次两两比较写成 ``for i, k in ((0,1),(1,2),(0,2))``，所以必须有一道
    边界守卫 ``if i >= len(years) or k >= len(years): continue``。
    变异测试把 ``or`` 改成 ``and`` 后**单年份会 IndexError** ——
    而没有任何测试判过单年份（输出快照分类工具挑出来的唯一一处）。

    单年不是假想输入：报告只有一份、或用户只给了一份时就是这个形态。
    """
    from checkup.judge import judge_all
    obs = [o for o in ctx()["obs"] if o.year == "2026"]
    assert obs, "需要 2026 的数据"
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                   metas={"2026": ctx()["metas"]["2026"]})
    assert js, "应当仍然产出判定"
    # 单年份**没有跨年比较**可言
    for j in js:
        assert not j.change, (j.analyte_id, j.change)
        assert not j.intervals, j.analyte_id


def test_judging_two_years_gives_exactly_one_pair():
    """两个年份 → 恰好一对比较（三次两两比较里只有 (0,1) 成立）。"""
    from checkup.judge import judge_all
    obs = [o for o in ctx()["obs"] if o.year in ("2025", "2026")]
    metas = {y: ctx()["metas"][y] for y in ("2025", "2026")}
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), metas=metas)
    for j in js:
        assert len(j.change) <= 1, (j.analyte_id, j.change)


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
