#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全链不变式：真实数据 + 12 类扰动 + 6 类降级，一起轰。  python3 tests/test_invariants.py

上一轮靠"临时想输入"抓到两个 bug。这一轮把思路反过来：**不猜输入，
列出输出必须满足的性质**，然后拿所有能想到的输入去撞。

不变式是**一次性写、永远生效**的：新加一条规则、新加一个字段，
只要破坏了不变式，测试立刻响。

不变式的选取标准：**违反它一定是 bug，不可能是设计选择**。
拿不准的一律不写 —— 写一条会误报的不变式比不写更糟。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup import invariants as I  # noqa: E402
from checkup import perturb as P  # noqa: E402
from checkup.compose import Composer, evaluate, load_profile  # noqa: E402
from checkup.conclude import synthesize  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.lipid_table import CHOL_STRATA, HYPERTENSION, NO_HYPERTENSION  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.redflags import check as redflag_check  # noqa: E402
from checkup.schema import Conclusion  # noqa: E402
from checkup.validate import validate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_profile()
SRC = PATHS.real_reports
REAL = {"2024": "20240612体检报告.pdf", "2025": "20250507体检报告.pdf",
        "2026": "20260609体检报告.pdf"}
RANK = {"低危": 0, "中危": 1, "高危": 2, "极高危": 3, "超高危": 4}
_base = None


def base():
    global _base
    if _base is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        obs = [o for rows in build_all(SRC, cat).values() for o in rows]
        metas = {y: extract_report(os.path.join(SRC, f), y, TPL)[0]
                 for y, f in REAL.items()}
        _base = dict(obs=obs, metas=metas, cat=cat,
                     pf=load_profile(PATHS.profile))
    return _base


def snap(obs=None, metas=None, **pf_over):
    obs = base()["obs"] if obs is None else obs
    metas = base()["metas"] if metas is None else metas
    pf = dict(base()["pf"])
    pf.update(pf_over)
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"),
                   catalog=base()["cat"], metas=metas)
    d = evaluate(Composer(js, metas, pf))
    cs = synthesize(js, d, validation=validate(obs, metas=metas), profile=pf)
    # 注意：redflags.check 收的是 **observations**，不是 judgements
    return {"judgements": js, "derived": d, "conclusions": cs,
            "obs": obs, "metas": metas, "red": redflag_check(obs, profile=pf)}


def violations(s) -> list:
    return I.check_all(s["obs"], s["metas"], s["judgements"], s["derived"],
                       s["conclusions"], base()["cat"])


def setv(aid, v, year="2026"):
    return [__import__("dataclasses").replace(o, value=v)
            if o.analyte_id == aid and o.year == year else o
            for o in base()["obs"]]


# =================================================== 不变式本身有效

def test_invariants_actually_fire_on_a_planted_violation():
    """**元测试**：造一条明知违规的输出，不变式必须报出来。

    没有这条，下面所有"✓ 全过"都可能只是因为**不变式根本不工作**。
    """
    s = snap()
    bad = list(s["conclusions"])
    fake = Conclusion(conclusion_id="FAKE", cls="confirmed",
                      text="某项指标 2099 年回复正常，趋势明确",
                      upstream="", evidence=[], uncertainty="low",
                      rule_refs=["NO.SUCH.RULE"], assertion_type="informational",
                      action_hint="")
    bad.append(fake)
    v = I.check_all(s["obs"], s["metas"], s["judgements"], s["derived"], bad,
                    base()["cat"])
    names = {x.invariant for x in v}
    assert "结论年份真实" in names, names
    assert "结论引用真实规则" in names, names
    assert "单年不谈趋势" not in names   # 三年数据，不该报这条


def test_invariants_fire_when_judgements_collapse():
    """判定塌缩（上一轮那个真 bug）必须被不变式抓住。"""
    obs = base()["obs"]
    one = __import__("checkup.judge", fromlist=["x"]).Judgement(
        analyte_id="X", item_raw="x", section="s")
    v = I.check_all(obs, base()["metas"], [one], [], [], base()["cat"])
    assert any(x.invariant == "判定不塌缩" for x in v), [str(x) for x in v]


def test_invariants_fire_on_illegal_position_value():
    from checkup.judge import Judgement
    j = Judgement(analyte_id="A", item_raw="a", section="s")
    j.position["2026"] = "rubbish"
    v = I.check_all([], {"2026": base()["metas"]["2026"]}, [j], [], [],
                    base()["cat"])
    assert any(x.invariant == "位置轴取值合法" for x in v), [str(x) for x in v]


# =================================================== 真实数据与降级

def test_real_data_satisfies_all_invariants():
    assert not violations(snap()), I.render(violations(snap()))


def test_degraded_scenarios_satisfy_all_invariants():
    from dataclasses import replace
    cases = {
        "全未映射": ([replace(o, analyte_id=UNMAPPED) for o in base()["obs"]],
                     base()["metas"]),
        "单年": ([o for o in base()["obs"] if o.year == "2026"],
                 {"2026": base()["metas"]["2026"]}),
        "两年": ([o for o in base()["obs"] if o.year in ("2025", "2026")],
                 {y: base()["metas"][y] for y in ("2025", "2026")}),
        "空观测": ([], {}),
        "缺 meta": (base()["obs"], {}),
    }
    for label, (o, m) in cases.items():
        v = violations(snap(o, m))
        assert not v, f"{label}: " + I.render(v)


# =================================================== 扰动

def test_all_perturbations_satisfy_all_invariants():
    obs = base()["obs"]
    metas = base()["metas"]
    cases = {
        "等价单位": (P.replace_unit(obs, "BLD.白细胞", "10^3/μL"), metas),
        "不可比单位": (P.replace_unit(obs, "BLD.白细胞", "10^12/L", year="2026"), metas),
        "数值×10": (P.scale_value(obs, "CHEM.肌酐", "2026", 10), metas),
        "抹区间": (P.drop_ref_range(obs, "CHEM.甘油三酯"), metas),
        "整段删除": (P.drop_section(obs, "血常规", "2026"), metas),
        "段名别称": (P.rename_section(obs, "血常规", "血液检查"), metas),
        "数值对调": (P.swap_values(obs, "CHEM.总胆红素", "CHEM.直接胆红素", "2026"), metas),
        "年龄缺失": (obs, P.drop_age(metas)),
        "性别改女": (obs, P.flip_sex(metas)),
        "同名撞键": (P.duplicate_item(obs, "BLD.白细胞", "2026", "白细胞(新)", 9.9), metas),
    }
    for label, (o, m) in cases.items():
        v = violations(snap(o, m))
        assert not v, f"{label}: " + I.render(v)


# =================================================== 方向性

def test_creatinine_up_never_raises_egfr():
    b = snap()
    a = snap(P.scale_value(base()["obs"], "CHEM.肌酐", "2026", 10))
    assert not I.check_monotonic_creatinine(b, a, "2026"), \
        I.render(I.check_monotonic_creatinine(b, a, "2026"))
    # 方向也要真的是下降（否则这条不变式是空的）
    gb = I._derived(b["derived"], "RENAL.EGFR_CKDEPI2021", "2026").value
    ga = I._derived(a["derived"], "RENAL.EGFR_CKDEPI2021", "2026").value
    assert ga < gb, (gb, ga)


def test_dropped_range_makes_position_not_assessable():
    b = snap()
    a = snap(P.drop_ref_range(base()["obs"], "CHEM.甘油三酯"))
    assert not I.check_dropped_range_is_not_assessable(b, a, "CHEM.甘油三酯",
                                                       "2026")
    assert I._pos(a["judgements"], "CHEM.甘油三酯", "2026") == "not_assessable"


def test_dropping_data_never_creates_improvement():
    b = snap()
    a = snap(P.drop_section(base()["obs"], "血常规", "2026"))
    assert not I.check_missing_is_not_improvement(b, a, "血常规")


def test_sex_flip_does_not_reuse_male_readings():
    b = snap()
    a = snap(metas=P.flip_sex(base()["metas"]))
    assert not I.check_sex_flip_blocks_male_only_rules(b, a, year="2026")


def test_chain_is_deterministic():
    assert not I.check_determinism((snap(), snap()))


def test_red_flags_point_at_real_items():
    s = snap()
    assert not I.check_red_flags_are_declared(s["red"], s["judgements"])
    # 造一条假的必须被抓
    class Fake:
        rule_id = "NO.SUCH.RULE"
        label = "不存在项目"
    v = I.check_red_flags_are_declared([Fake()], s["judgements"])
    assert {x.invariant for x in v} == {"红旗来自已声明规则", "红旗指向真实项"}, v


# =================================================== 表结构（穷举）

def test_risk_table_is_monotone_in_every_direction():
    """21 格表**可以穷举**，所以直接穷举验三个方向的单调性。

    这是纯结构性质，不依赖任何数据 —— 表改了就立刻报。
    """
    bad = []
    for name, tbl in (("无高血压", NO_HYPERTENSION), ("有高血压", HYPERTENSION)):
        rows = list(tbl)
        for r in rows:                       # 行内：列越重不降
            seq = [RANK[x] for x in tbl[r]]
            if any(a > b for a, b in zip(seq, seq[1:])):
                bad.append(f"{name} 行{r} 列非单调")
        for i in range(3):                   # 列内：因素越多不降
            seq = [RANK[tbl[r][i]] for r in rows]
            if any(a > b for a, b in zip(seq, seq[1:])):
                bad.append(f"{name} 列{i+1} 因素非单调")
    for k in ("2", "3"):                     # 加高血压不降
        for i in range(3):
            if RANK[HYPERTENSION[k][i]] < RANK[NO_HYPERTENSION[k][i]]:
                bad.append(f"因素{k} 列{i+1}：有高血压低于无高血压")
    for k in ("0", "1"):
        for i in range(3):
            if RANK[HYPERTENSION[k][i]] < RANK[NO_HYPERTENSION["0~1"][i]]:
                bad.append(f"因素{k} 列{i+1}：有高血压低于无高血压")
    assert not bad, bad


def test_cholesterol_strata_are_contiguous_and_ordered():
    """三个胆固醇分层必须首尾相接、逐段升高 —— 中间有空隙就会有人掉进缝里。"""
    for a, b in zip(CHOL_STRATA, CHOL_STRATA[1:]):
        assert a.tc_high == b.tc_low, (a.key, a.tc_high, b.key, b.tc_low)
        assert a.ldl_high == b.ldl_low, (a.key, b.key)
    assert all(a.tc_high > a.tc_low and a.ldl_high > a.ldl_low
               for a in CHOL_STRATA)


# =================================================== 管线级单调

def _band(obs=None, **pf_over):
    s = snap(obs, **pf_over)
    r = next((x for x in s["derived"]
              if x.rule_id == "LIPID.RISK_STRATIFICATION"
              and x.year == "2026"), None)
    return r.band if r and r.band else None


def test_risk_band_is_monotone_in_tc_and_ldl():
    """把 TC / LDL-C 推过分层边界，风险等级**只能升不能降**。"""
    prev = None
    for tc in (3.0, 4.0, 4.5, 5.0, 5.19, 5.2, 5.5, 6.0):
        b = _band(setv("CHEM.总胆固醇", tc))
        if prev and b and RANK[b] < RANK[prev]:
            raise AssertionError(f"TC {tc} 时分层从 {prev} 降到 {b}")
        prev = b
    prev = None
    for ldl in (1.8, 2.5, 3.0, 3.39, 3.4, 3.8, 4.5, 4.89, 4.9):
        b = _band(setv("CHEM.低密度脂蛋白胆固醇", ldl))
        if prev and b and RANK[b] < RANK[prev]:
            raise AssertionError(f"LDL-C {ldl} 时分层从 {prev} 降到 {b}")
        prev = b


def test_adding_hypertension_never_lowers_the_band():
    off = _band(hypertension_diagnosed=False)
    on = _band(hypertension_diagnosed=True)
    assert RANK[on] >= RANK[off], (off, on)
    assert on == "高危", on        # 本例 2 个危险因素 + C3 → 有高血压应升到高危


def test_band_boundaries_land_where_the_table_says():
    assert _band(setv("CHEM.总胆固醇", 5.19)) == "低危"
    assert _band(setv("CHEM.总胆固醇", 5.2)) == "中危"
    assert _band(setv("CHEM.低密度脂蛋白胆固醇", 4.9)) == "高危"


# ------------------------------------------------------------------ 主程序

# =========================================== 每条不变式都必须能被触发
#
# 上一轮的经验：**不变式本身也会空转**（有一条按不存在的字段找，永远返回 0 条）。
# 所以每条不变式都要有一个"种下违规 → 它必须响"的用例。
# 只验"干净数据不报"是不够的 —— 一个永远不报的不变式同样能通过那种测试。

def _conclusion(text="x", cls="confirmed", refs=(), cid="C"):
    return Conclusion(conclusion_id=cid, cls=cls, text=text, upstream="",
                      evidence=[], uncertainty="low", rule_refs=list(refs),
                      assertion_type="informational", action_hint="")


def _judgement(aid="A", name="a", section="s"):
    from checkup.judge import Judgement
    return Judgement(analyte_id=aid, item_raw=name, section=section)


def _derived(rid="X", year="2026", status="ok", value=1.0, band="", text="t"):
    from checkup.schema import DerivedResult
    return DerivedResult(rule_id=rid, name=rid, year=year, status=status,
                         value=value, band=band, text=text)


def _fires(needle: str, **kw) -> bool:
    v = I.check_all(kw.get("obs", []), kw.get("metas", {}),
                    kw.get("judgements", []), kw.get("derived", []),
                    kw.get("conclusions", []), kw.get("catalog"))
    return any(needle in x.invariant for x in v)


def test_every_check_all_invariant_can_be_triggered():
    """**20 条不变式逐条种违规**，每条都必须响。

    一条永远不报的不变式和没有不变式是一样的 —— 而且更糟，因为它给人一种
    "查过了"的错觉（仓库里真出现过一次）。
    """
    from checkup.judge import obs_key  # noqa: F401
    from checkup.schema import NormalizedObservation

    def o(aid, year="2026"):
        return NormalizedObservation(year=year, page_no=1, section="s",
                                     item_raw=aid, occurrence=1,
                                     analyte_id=aid, value=1.0,
                                     value_type="numeric", unit="U")

    cases = [
        ("判定不塌缩", dict(obs=[o("A"), o("B")], judgements=[_judgement()])),
        ("位置轴取值合法", dict(judgements=[
            _replace_pos(_judgement(), "2026", "rubbish")], metas={"2026": None})),
        ("判定年份真实", dict(judgements=[
            _replace_pos(_judgement(), "2099", "normal")], metas={"2026": None})),
        ("结论年份真实", dict(conclusions=[_conclusion("某指标 2099 年异常")])),
        ("单年不谈趋势", dict(obs=[o("A", "2026")],
                          metas={"2026": None},
                          conclusions=[_conclusion("该指标呈上升趋势")])),
        ("结论分类合法", dict(conclusions=[_conclusion(cls="bogus")])),
        ("结论非空", dict(conclusions=[_conclusion(text="")])),
        ("派生规则已声明", dict(derived=[_derived(rid="NO.SUCH.RULE")])),
        ("派生结果有内容", dict(derived=[_derived(value=None, band="", text="")])),
        ("不可评估说明理由", dict(derived=[
            _derived(status="not_assessable", value=None, band="", text="x")])),
        ("结论引用真实规则", dict(conclusions=[
            _conclusion(refs=["NO.SUCH.RULE"])])),
        # 这一条是**防御性**的：需要 position 有值、series 全空、且 item_raw 也为空。
        # 实际构造出的 Judgement 一定带 item_raw，所以它在真实数据上不可达 ——
        # 但"不可达"不等于"不该有"，故仍要证明它会响。
        ("判定有依据", dict(judgements=[_replace_pos(
            _judgement(aid="A", name=""), "2026", "normal")])),
    ]
    missing = []
    for name, kw in cases:
        kw.setdefault("metas", {})
        if not _fires(name, **kw):
            missing.append(name)
    assert not missing, f"这些不变式种了违规却不响：{missing}"


def _replace_pos(j, year, val):
    j.position[year] = val
    return j


def test_directional_invariants_can_be_triggered():
    """**9 条方向性/结构性不变式**同样逐条种违规。

    它们全都拿 (before, after) 两帧做比较 —— 只验"真实数据两边一致"证明不了
    它们会响，必须**伪造一个反向变化**。
    """
    from checkup.judge import Judgement
    from checkup.schema import DerivedResult

    def jd(aid, pos):
        j = Judgement(analyte_id=aid, item_raw=aid, section="s")
        j.position.update(pos)
        return j

    def dr(rid, year, value):
        return DerivedResult(rule_id=rid, name=rid, year=year, status="ok",
                             value=value)

    fired = {}

    # 肌酐↑则eGFR↓
    fired["肌酐↑则eGFR↓"] = bool(I.check_monotonic_creatinine(
        {"derived": [dr("RENAL.EGFR_CKDEPI2021", "2026", 50.0)]},
        {"derived": [dr("RENAL.EGFR_CKDEPI2021", "2026", 90.0)]}, "2026"))

    # 变大不会变正常
    fired["变大不会变正常"] = bool(I.check_direction_of_abnormal(
        {"judgements": [jd("A", {"2026": "abnormal"})]},
        {"judgements": [jd("A", {"2026": "normal"})]}, "A", "2026"))

    # 无区间则不可评估
    fired["无区间则不可评估"] = bool(I.check_dropped_range_is_not_assessable(
        {"judgements": [jd("A", {"2026": "normal"})]},
        {"judgements": [jd("A", {"2026": "normal"})]}, "A", "2026"))

    # 删数据不产生好转
    fired["删数据不产生好转"] = bool(I.check_missing_is_not_improvement(
        {"conclusions": []},
        {"conclusions": [_conclusion("该指标已恢复至参考区间内")]}, "A"))

    # 确定性
    fired["确定性"] = bool(I.check_determinism((
        {"judgements": [jd("A", {"2026": "normal"})], "conclusions": []},
        {"judgements": [jd("A", {"2026": "abnormal"})], "conclusions": []})))

    # 性别改变不沿用男性读数
    fired["性别改变不沿用男性读数"] = bool(
        I.check_sex_flip_blocks_male_only_rules(
            {"derived": [dr("ASCVD.CHINAPAR", "2026", 1.4)]},
            {"derived": [dr("ASCVD.CHINAPAR", "2026", 1.4)]}, year="2026"))

    # 红旗来自已声明规则 / 红旗指向真实项
    class Fake:
        rule_id = "NO.SUCH.RULE"
        label = "不存在项目"
    v = I.check_red_flags_are_declared([Fake()], [jd("A", {})])
    fired["红旗来自已声明规则"] = any("已声明规则" in x.invariant for x in v)
    fired["红旗指向真实项"] = any("真实项" in x.invariant for x in v)

    missing = [k for k, ok in fired.items() if not ok]
    assert not missing, f"这些方向性不变式种了违规却不响：{missing}"


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
