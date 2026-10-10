#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归一层（D6–D8）的回归测试。

    python3 tests/test_normalize.py
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.normalize import (UNMAPPED, AnalyteCatalog,  # noqa: E402
                               MatchRule, normalize_name)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_engine_out()   # 数据未挂载 → 跳过，不算失败
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
NORMED = os.path.join(PATHS.engine_out, "normalized_observations.csv")
ORG = "NX-001"

_cat = None
_rows = None


def cat() -> AnalyteCatalog:
    global _cat
    if _cat is None:
        _cat = AnalyteCatalog.load(CATALOG)
    return _cat


def rows() -> list[dict]:
    global _rows
    if _rows is None:
        with open(NORMED, encoding="utf-8-sig") as f:
            _rows = list(csv.DictReader(f))
    return _rows


def pick(name: str, section: str | None = None, year: str | None = None) -> list[dict]:
    out = [r for r in rows() if r["项目原文"] == name]
    if section:
        out = [r for r in out if r["段落"] == section]
    if year:
        out = [r for r in out if r["年份"] == year]
    return out


# --------------------------------------------------- RDW：标签写反的修复

def test_rdw_labels_swapped_in_2024_are_repaired():
    """2024 报告把 RDW-CV / RDW-SD 两个**标签写反了**，单位列才是对的。

    40.30 配 fL → 实际是 SD；13.20 配 ％ → 实际是 CV。
    """
    cv = pick("红细胞分布宽度CV", "血常规", "2024")[0]
    sd = pick("红细胞分布宽度SD", "血常规", "2024")[0]
    assert cv["analyte_id"] == "BLD.红细胞分布宽度SD", cv["analyte_id"]
    assert sd["analyte_id"] == "BLD.红细胞分布宽度CV", sd["analyte_id"]
    assert cv["修复规则"] == "NX-001.RDW.SWAP"
    assert sd["修复规则"] == "NX-001.RDW.SWAP"


def test_rdw_is_stable_after_repair():
    """修复后三年应当稳定。修复前按名字对齐会得出「下降 68%」的假趋势。"""
    want = {"BLD.红细胞分布宽度CV": {"2024": 13.20, "2025": 12.90, "2026": 12.80},
            "BLD.红细胞分布宽度SD": {"2024": 40.30, "2025": 39.60, "2026": 38.50}}
    for aid, by_year in want.items():
        got = {r["年份"]: float(r["数值"]) for r in rows() if r["analyte_id"] == aid}
        assert got == by_year, f"{aid}: {got}"


def test_name_based_alignment_would_be_wrong():
    """反证：**不修**的话，按项目名对齐会得出 40.30 → 12.90 → 12.80（假的 −68%）。"""
    naive = [float(r["值原文"])
             for r in rows() if r["项目原文"] == "红细胞分布宽度CV"]
    naive.sort(key=lambda _: 0)
    seq = [float(pick("红细胞分布宽度CV", "血常规", y)[0]["值原文"])
           for y in ("2024", "2025", "2026")]
    assert seq == [40.30, 12.90, 12.80]
    pct = (seq[2] - seq[0]) / seq[0] * 100
    assert pct < -60, "按名字比确实会得出约 −68% 的假趋势"


# --------------------------------------------------- 同名撞键

def test_duplicate_name_split_by_value_type():
    """尿液检查里两项都叫「白细胞」：一个定性"阴性"，一个定量。

    单靠 (段, 项目) 做键会撞掉一项，必须用 value_type 区分。
    """
    ws = pick("白细胞", "尿液检查")
    ids = {r["analyte_id"] for r in ws}
    assert len(ids) == 2, ids
    qual = [r for r in ws if r["analyte_id"].endswith(".qual")]
    num = [r for r in ws if not r["analyte_id"].endswith(".qual")]
    assert all(r["值原文"] == "阴性" for r in qual)
    assert all(r["值原文"] not in ("阴性", "") for r in num)


def test_ambiguous_key_not_in_seed_index():
    """歧义键必须**自动剔除**出种子表——否则会随机挑一个。"""
    assert ("尿液检查", "白细胞") in cat().ambiguous


def test_urine_wbc_is_not_blood_wbc():
    """尿液白细胞与血常规白细胞必须是两个不同的 analyte。"""
    urine = {r["analyte_id"] for r in pick("白细胞", "尿液检查")}
    blood = {r["analyte_id"] for r in pick("白细胞", "血常规")}
    assert urine and blood
    assert not (urine & blood), f"尿液与血液白细胞被合并了: {urine & blood}"


# --------------------------------------------------- 单位

def test_unit_mismatch_is_detected_not_silently_passed():
    """单位与规范单位矛盾时必须能被检出（且只在规则允许时才重归属）。"""
    res = cat().resolve(ORG, "血常规", "红细胞分布宽度CV", "numeric", "fL")
    assert res.unit_status == "mismatch", res
    rep = cat().repair_by_unit("血常规", res.analyte_id, "fL")
    assert rep is not None and rep.analyte_id == "BLD.红细胞分布宽度SD"


def test_unit_equivalence_10_3_per_ul_equals_10_9_per_l():
    """``10^3/μL`` 与 ``10^9/L`` 是**等价**，不是换算（框架 §四 C 组）。"""
    c = cat()
    a = c.resolve(ORG, "血常规", "白细胞", "numeric", "10^9/L")
    b = c.resolve(ORG, "血常规", "白细胞", "numeric", "10^3/μL")
    assert a.analyte_id == b.analyte_id
    assert a.unit_status == b.unit_status == "ok"


def test_unit_conflicts_report_exists():
    """单位跨年不一致要被自动检出并落盘。"""
    p = os.path.join(PATHS.engine_out, "unit_conflicts.csv")
    assert os.path.exists(p)
    with open(p, encoding="utf-8-sig") as f:
        got = {(r["段落"], r["项目"]) for r in csv.DictReader(f)}
    assert ("血常规", "红细胞分布宽度CV") in got
    assert ("血常规", "红细胞分布宽度SD") in got


# --------------------------------------------------- 不猜

def test_unknown_name_is_unmapped_not_guessed():
    res = cat().resolve(ORG, "血常规", "这个指标不存在", "numeric", "")
    assert res.analyte_id == UNMAPPED
    assert res.matched_by == "unmapped"


def test_no_lookalike_substring_matching():
    """不做子串/去括号/去复数的模糊匹配——中文项目名高度相似，会误合并。"""
    # 「白细胞」与「白细胞(高倍视野)」是不同的项，不能互相命中
    a = cat().resolve(ORG, "尿液检查", "白细胞", "numeric", "个/ul")
    b = cat().resolve(ORG, "尿液检查", "白细胞(高倍视野)", "numeric", "个/HP")
    assert a.analyte_id != b.analyte_id


def test_normalize_name_only_folds_writing_differences():
    """名称归一只折叠书写差异（全角/半角、大小写、空白）。"""
    assert normalize_name(" 白细胞 ") == normalize_name("白细胞")
    assert normalize_name("ＷＢＣ") == normalize_name("wbc")
    # 不删括号内容
    assert "(" in normalize_name("白细胞(高倍视野)")


# --------------------------------------------------- 覆盖率

def test_all_observations_mapped_or_explicitly_unmapped():
    """归一覆盖率：本机构数据应当 100% 命中（种子来自同一批数据）。"""
    un = [r for r in rows() if r["analyte_id"] == UNMAPPED]
    assert not un, f"存在未映射项: {sorted({(r['段落'], r['项目原文']) for r in un})}"


def test_catalog_is_versioned_and_unconfirmed_seed():
    """目录必须带版本号；种子条目未人工确认，不得当通用目录用。"""
    c = cat()
    assert c.version
    assert len(c.analytes) > 150
    seed = [a for a in c.analytes.values() if a.org_id == ORG]
    assert seed and all(a.confirmed is False for a in seed), \
        "自动生成的种子必须标 confirmed=false"


def test_match_rule_specificity_wins():
    """规则冲突时，最具体者胜。"""
    c = AnalyteCatalog(
        analytes=[], rules=[
            MatchRule(analyte_id="A", section="尿液检查", source_name="白细胞"),
            MatchRule(analyte_id="B", org_id=ORG, section="尿液检查",
                      source_name="白细胞", value_type="numeric"),
        ])
    assert c.resolve(ORG, "尿液检查", "白细胞", "numeric").analyte_id == "B"
    assert c.resolve(ORG, "尿液检查", "白细胞", "qualitative").analyte_id == "A"


# ------------------------------------------------------------------ 主程序

def test_specificity_score_is_additive():
    """特异性得分是**累加**：org=2 分、section=1 分、value_type=1 分。

    变异测试发现两处 ``+`` 改成 ``-`` 无人管 —— 因为原先那条"最具体者胜"
    只验了**序**，验不了**分**。打分写错但序恰好不变时，测试照样过。
    """
    assert MatchRule(analyte_id="A").specificity == 0
    assert MatchRule(analyte_id="A", org_id=ORG).specificity == 2
    assert MatchRule(analyte_id="A", org_id=ORG, section="血常规").specificity == 3
    assert MatchRule(analyte_id="A", org_id=ORG, section="血常规",
                     value_type="numeric").specificity == 4


def test_specificity_order_flips_if_scoring_is_wrong():
    """构造一个**减法会反转序**的用例。

    A = org + section + value_type ＝ 2+1+1 = **4**
    B = org + value_type           ＝ 2+0+1 = **3**
    若第一个 ``+`` 写成 ``-``：A = 2-1+1 = 2，B = 3 → **序反转**，B 会赢。
    """
    c = AnalyteCatalog(
        analytes=[], rules=[
            MatchRule(analyte_id="A", org_id=ORG, section="血常规",
                      source_name="白细胞", value_type="numeric"),
            MatchRule(analyte_id="B", org_id=ORG, source_name="白细胞",
                      value_type="numeric"),
        ])
    assert c.resolve(ORG, "血常规", "白细胞", "numeric").analyte_id == "A"


def test_single_member_key_is_not_ambiguous():
    """只有一个成员键**不算歧义**。

    变异测试发现 ``len(v) > 1`` 改成 ``>=`` 无人管 ——
    没有构造过"某键只有一个成员"的目录。
    """
    c = AnalyteCatalog(analytes=[], rules=[
        MatchRule(analyte_id="ONLY", section="血常规", source_name="白细胞"),
    ])
    assert c.resolve(ORG, "血常规", "白细胞", "numeric").analyte_id == "ONLY"


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
