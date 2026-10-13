#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨机构鲁棒性：用**合成的"另一家机构"报告**检验 L1（框架 D21）。

扰动台（``test_robustness.py``）只能改已经抽出来的观测，测不到 L1。
要测"新机构第一次见到"，只能造 PDF。这里用 PyMuPDF 生成不同版式/不同
命名习惯的化验单，看抽取层是**优雅降级**还是**静默出错**。

判据同扰动台：

    错误必须被拦下或降级并明说，绝不能静默接受。

最危险的情形是"**静默空结果**"——新机构第一份报告抽到 0 项，
若没有任何守卫，会被当成"这份报告没有化验数据"而通过。

    python3 tests/test_cross_institution.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import year_from_name  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.tables import extract_lab_results, lab_page_audit  # noqa: E402
from checkup.validate import check_extraction_audit, check_unmapped_rate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
from _synth import CJK, ROWS, _mk  # noqa: E402  （P0-5/D5：抽到无数据依赖模块）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
ORG = "NX-001"


#: 一份"别家机构"的化验数据（项目名沿用通用写法，段名/版式可变）




def _run(path: str, year: str = "2025"):
    doc = fitz.open(path)
    try:
        audit = lab_page_audit(doc, TPL)
        labs = extract_lab_results(doc, year, TPL)
    finally:
        doc.close()
    cat = AnalyteCatalog.load(CATALOG)
    obs = [NormalizedObservation(
        year=year, page_no=r.page_no, section=r.section, item_raw=r.item_raw,
        occurrence=r.occurrence,
        analyte_id=cat.resolve(ORG, r.section, r.item_raw, r.value_type,
                               r.unit_raw).analyte_id,
        value=r.value, value_type=r.value_type, unit=r.unit_raw,
        ref_raw=r.ref_raw, ref_low=r.ref_low, ref_high=r.ref_high, flag=r.flag)
        for r in labs]
    return audit, labs, obs


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="synth_"), name)


# --------------------------------------------------------------- 对照

def test_two_column_standard_sections_is_the_control():
    """对照组：双栏 + 已登记段名 → 必须正常抽出并归一。

    这证明合成报告本身是"能被解析的"，后面两个失败案例才有意义。
    """
    p = _mk(_tmp("ok.pdf"), "血常规", columns=2)
    audit, labs, obs = _run(p)
    assert sum(a["items"] for a in audit) == len(ROWS), audit
    assert all(o.analyte_id != UNMAPPED for o in obs), \
        [(o.section, o.item_raw, o.analyte_id) for o in obs]


def test_misindented_items_are_flagged_not_silently_dropped():
    """段标题启发式失灵时（项目名与段名同列）→ 抽到 0 项。

    这正是我们刚踩到的：项目名若落在段标题列，**每个项目都会被当成段标题
    而整页丢弃**。单靠几何启发式挡不住，必须靠抽后审计报 fatal。
    这也说明段标题判定是本引擎对一个机构版式的**硬假设**，换机构必然要改。
    """
    p = _mk(_tmp("misfit.pdf"), "血常规", columns=2, item_x=47.0)
    audit, labs, obs = _run(p)
    assert labs == [], "启发式失灵时不该抽出项目"
    assert check_extraction_audit(audit).level == "fatal"


# --------------------------------------------------------------- 单栏

def test_single_column_layout_yields_zero_and_is_flagged():
    """单栏版式 → 抽到 0 项，但**必须被审计标记为 fatal**。

    这是最危险的失败：新机构第一份报告没有覆盖率基线，0 项若无人拦截，
    会被当成"这份报告没有化验数据"，静默通过。
    """
    p = _mk(_tmp("single.pdf"), "血常规", columns=1)
    audit, labs, obs = _run(p)

    assert labs == [], "单栏版式不该抽出项目（表头锚点不足）"
    assert audit and audit[0]["items"] == 0, audit

    c = check_extraction_audit(audit)
    assert c.level == "fatal", c.detail
    assert "版式未识别" in c.detail
    assert "无任何征兆" in c.detail


def test_clean_reports_pass_the_same_audit():
    """真实三份报告必须通过同一审计 —— 守卫不能把正常数据也毙掉。"""
    audit = []
    src = PATHS.real_reports
    for fn in sorted(os.listdir(src)):
        if not fn.lower().endswith(".pdf"):
            continue
        d = fitz.open(os.path.join(src, fn))
        for a in lab_page_audit(d, TPL):
            a["year"] = year_from_name(fn) or "?"
            audit.append(a)
        d.close()
    c = check_extraction_audit(audit)
    assert c.level == "pass", c.detail
    assert "243" in c.detail or "81" in c.detail, c.detail


# --------------------------------------------------------------- 段名

def test_alien_section_names_are_unmapped_not_merged():
    """双栏 + 未登记段名 → 项目能抽出，但**全部标 UNMAPPED**，不猜。"""
    p = _mk(_tmp("alien.pdf"), "血液检查", columns=2)
    audit, labs, obs = _run(p)

    assert sum(a["items"] for a in audit) == len(ROWS), audit
    assert all(o.analyte_id == UNMAPPED for o in obs), \
        [(o.section, o.analyte_id) for o in obs]
    assert {o.section for o in obs} == {"血液检查"}


def test_high_unmapped_rate_is_flagged():
    """整批匹配不上 → 报警说"疑似新机构命名体系未登记"。

    个别新项目匹配不上是正常的；**整批**匹配不上就不是"个别"了。
    """
    p = _mk(_tmp("alien2.pdf"), "血液检查", columns=2)
    _, _, obs = _run(p)
    c = check_unmapped_rate(obs)
    assert c.level == "warn", c.detail
    assert "新机构" in c.detail


def test_low_unmapped_rate_passes():
    """少量未映射不该报警 —— 守卫过严会把正常的新项目也拦下。"""
    p = _mk(_tmp("mixed.pdf"), "血常规", columns=2)
    _, _, obs = _run(p)
    assert check_unmapped_rate(obs).level == "pass"


# --------------------------------------------------------------- 不静默

def test_no_silent_wrong_values():
    """任何版式下都不得产出"看着正常但数值错"的记录。

    具体做法：抽出来的每一条，其数值必须能从**该页原文**里找到。
    """
    for cols, sec in ((2, "血常规"), (2, "血液检查"), (1, "血常规")):
        p = _mk(_tmp(f"v{cols}{sec}.pdf"), sec, columns=cols)
        _, labs, _ = _run(p)
        d = fitz.open(p)
        text = d[0].get_text().replace(" ", "")
        d.close()
        for r in labs:
            assert r.result_raw.replace(" ", "") in text, \
                f"抽出的结果 {r.result_raw!r} 在原文里找不到（疑似臆造）"
            assert r.item_raw in text, f"项目名 {r.item_raw!r} 不在原文里"


def test_unknown_title_still_classified_by_marker():
    """标题换成「检验报告单」以外时，不应被误判成化验页。"""
    p = _mk(_tmp("other.pdf"), "血常规", columns=2, title="检查结果单")
    doc = fitz.open(p)
    audit = lab_page_audit(doc, TPL)
    doc.close()
    assert audit == [], "不含「检验报告单」标记的页不该被当成化验页"


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
