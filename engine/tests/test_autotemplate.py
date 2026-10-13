#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板自动起草的回归测试。    python3 tests/test_autotemplate.py

验收条件：**丢一份 PDF 进去，能起草出一份可用的模板草案**，
且草案抽出的项数与手写模板一致。

产物一定是草案（``confirmed=False``），关键判断都记在 ``evidence`` 里。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.autotemplate import draft_template  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402
from checkup.tables import extract_lab_results, lab_page_audit  # noqa: E402
from checkup.templates import Template, TemplateRegistry  # noqa: E402
from checkup.validate import check_extraction_audit  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cross_institution import _mk, _tmp  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.dirname(BASE)
SYN = os.path.join(SKILL, "examples", "testdata")
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
from _synth import CJK  # noqa: E402
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
SRC = PATHS.real_reports
REAL = [os.path.join(SRC, f) for f in sorted(os.listdir(SRC))
        if f.endswith(".pdf")]


def _syn_pdfs() -> list[str]:
    """随包样例 PDF（合成机构，无数据根也能跑）。"""
    return [os.path.join(SYN, f) for f in sorted(os.listdir(SYN))
            if f.endswith(".pdf")]

_drafts = {}


def draft(key: str, paths, **kw):
    """带 kwargs 的缓存 —— 只按 key 缓存会让 org_id 互相污染。"""
    ck = (key, tuple(sorted(kw.items())))
    if ck not in _drafts:
        _drafts[ck] = draft_template(paths, **kw)
    return _drafts[ck]


def _marker_covers_lab_pages(template, path: str) -> bool:
    """落盘的 ``page_marker`` 是否出现在**被判定为化验页**的页面上（A1 断言）。

    化验页判定 = 该页存在含 ≥3 个表头词的表头行（``_pick_header_row``）。
    """
    from checkup.autotemplate import _pick_header_row  # noqa: PLC0415
    from checkup.layout import rows_of_page  # noqa: PLC0415
    doc = fitz.open(path)
    try:
        lab = [i for i in range(doc.page_count)
               if _pick_header_row(rows_of_page(doc[i], i + 1)) is not None]
        assert lab, f"{os.path.basename(path)} 上应当有化验页"
        return all(template.lab.page_marker in doc[i].get_text() for i in lab)
    finally:
        doc.close()


def _mk_lab5(path: str, marker: str = "", size: tuple[float, float] = (595, 842),
             pages: int = 1, drift2: float = 0.0, cont_page: bool = False,
             org: str = "临河市第一人民医院", sig_row: bool = False) -> str:
    """合成一版「本类机构式」化验页：**没有页标记词**，只有表头词 + 单位分列。

    表头 5 列：检验项目 | 结果 | 标志 | 参考值 | 单位（标志列只放箭头）。
    用它验收 A1 的判据反转：旧判据（先找页标记词）在这一页上 conf=0，
    新判据（命中 ≥3 个表头词即认定）必须能起草。

    ``pages=2``：页 2 是续页 —— ``cont_page=True`` 时**不写页标记词、不重复表头**
    （页首只写机构名），用来验收 A3 的**页集自愈**；``drift2`` 把页 2 的数据行
    整体右移（A13 列位漂移）；``sig_row=True`` 插一行"长段标题 + 检验者签名"
    （复刻真实机构的段标题行形态，用来验收 A11 的签名噪声变体）。
    """
    doc = fitz.open()
    for pi in range(pages):
        pg = doc.new_page(width=size[0], height=size[1])
        title = org if (cont_page and pi) else f"{org}{marker}"
        pg.insert_text((50, 60), title, fontname=CJK, fontsize=11)
        y = 110
        if pi == 0 or not cont_page:
            for x, t in ((124, "检验项目"), (297, "结果"), (347, "标志"),
                         (416, "参考值"), (519, "单位")):
                pg.insert_text((x, y), t, fontname=CJK, fontsize=9)
            y += 26
        dx = drift2 if pi else 0.0
        pg.insert_text((60 + dx, y), "肝功能", fontname=CJK, fontsize=9)
        y += 20
        if sig_row and (pi == 0 or not cont_page):
            # 长段标题（>8 字 → 抽取层判不出段标题）+ 签名：默认噪声下会变成假项
            pg.insert_text((124 + dx, y), "*离子四项[心脑]", fontname=CJK, fontsize=9)
            pg.insert_text((330 + dx, y), "检验者：张某", fontname=CJK, fontsize=9)
            y += 18
        for name, val, flag, ref, unit in (
                ("丙氨酸氨基转移酶", "61", "↑", "0-40", "U/L"),
                ("天门冬氨酸氨基转移酶", "36", "", "0-40", "U/L"),
                ("总胆红素", "12.3", "", "3.4-20.5", "umol/L")):
            pg.insert_text((124 + dx, y), name, fontname=CJK, fontsize=9)
            pg.insert_text((297 + dx, y), val, fontsize=9)
            if flag:
                pg.insert_text((347 + dx, y), flag, fontname=CJK, fontsize=9)
            pg.insert_text((416 + dx, y), ref, fontsize=9)
            pg.insert_text((519 + dx, y), unit, fontsize=9)
            y += 18
    doc.save(path)
    doc.close()
    return path


def _mk_header_only(path: str) -> str:
    """只有表头行、没有任何数据行的化验页（用来触发『0 条观测』）。"""
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "XX市第一人民医院检验报告单", fontname=CJK, fontsize=11)
    for x, t in ((50, "检验项目"), (200, "结果"), (300, "参考值")):
        pg.insert_text((x, 110), t, fontname=CJK, fontsize=9)
    doc.save(path)
    doc.close()
    return path


# --------------------------------------------------------------- 真实报告

def test_draft_from_real_reports_extracts_everything():
    """真实报告自动起草 → 用草案抽出与手写模板**同样的 81 项**。"""
    d = draft("real", REAL, org_id="NX-001")
    assert d.template.lab.column_groups == 2
    doc = fitz.open(REAL[0])
    try:
        assert len(extract_lab_results(doc, "2024", d.template)) == 81
        assert check_extraction_audit(lab_page_audit(doc, d.template)).level == "pass"
    finally:
        doc.close()
    assert d.confidence >= 0.9, d.render()


def test_drafted_geometry_matches_handwritten():
    """自动起草的几何应当与手写模板语义一致（段标题列落在同一区间）。"""
    d = draft("real", REAL, org_id="NX-001")
    # 手写值是 60.0，自动推的是 53.8 —— 都能把 x≈47 的段标题与 x≈61 的项目分开
    assert 47 < d.template.lab.section_x_max < 61
    # page_marker 的**语义断言**（A1）：落盘的标记必须出现在"被判定为化验页"的
    # 页面上。它已不再限定为某几个固定词 —— 通用标记词 0 命中的机构会退到
    # 表头词（本类机构实测用「检验项目」）；但无论选到什么，抽取闸门必须成立。
    assert _marker_covers_lab_pages(d.template, REAL[0]), \
        f"page_marker={d.template.lab.page_marker!r} 未覆盖化验页"


# --------------------------------------------------------------- 合成报告

def test_single_column_is_detected_as_one_group():
    """单栏版式必须被识别成 **1 栏**，而不是硬套 2 栏。"""
    p = _mk(_tmp("auto_single.pdf"), "血液检查", columns=1)
    d = draft("single", [p], org_id="YY-002")
    assert d.template.lab.column_groups == 1, d.render()
    doc = fitz.open(p)
    try:
        assert len(extract_lab_results(doc, "2025", d.template)) == 5
    finally:
        doc.close()


def test_two_column_is_detected_as_two_groups():
    p = _mk(_tmp("auto_two.pdf"), "血常规", columns=2)
    d = draft("two", [p], org_id="YY-003")
    assert d.template.lab.column_groups == 2
    doc = fitz.open(p)
    try:
        assert len(extract_lab_results(doc, "2025", d.template)) == 5
    finally:
        doc.close()


def test_drafted_section_column_separates_labels_from_items():
    """段标题列与项目列必须被分开 —— 分不开就会把项目当段标题整页丢掉。"""
    p = _mk(_tmp("auto_sec.pdf"), "血常规", columns=2)
    d = draft("two", [p], org_id="YY-003")
    assert 47 < d.template.lab.section_x_max < 61, d.render()
    doc = fitz.open(p)
    try:
        # 抽出来的 section 应当是「血常规」而不是空
        labs = extract_lab_results(doc, "2025", d.template)
        assert {x.section for x in labs} == {"血常规"}, {x.section for x in labs}
    finally:
        doc.close()


# --------------------------------------------------------------- 判据反转（A1）
#
# 本次事故：某机构 8 张化验页上「报告单/检验单/化验单」全部 0 命中 →
# 起草器把"真化验页"整批跳过 → 表头分支失败 → conf=0。修法是把判据反转成
# "该页存在一行命中 ≥3 个表头词即为化验页"，页标记词降为线索之一。

def test_header_words_alone_identify_lab_page():
    """**没有页标记词**、只有表头词的化验页必须能起草（旧判据在这里 conf=0）。"""
    p = _mk_lab5(_tmp("nxyd_style.pdf"))
    d = draft("nxyd_style", [p], org_id="NXYD-STYLE")
    assert d.confidence > 0, d.render()
    assert d.template.lab.page_marker, "必须落一个化验页标记"
    assert _marker_covers_lab_pages(d.template, p), \
        f"page_marker={d.template.lab.page_marker!r} 未覆盖化验页"
    # 起草的模板要真的能抽到数据（不只是"看起来成功"）
    doc = fitz.open(p)
    try:
        labs = extract_lab_results(doc, "2025", d.template)
        assert {x.item_raw for x in labs} == {
            "丙氨酸氨基转移酶", "天门冬氨酸氨基转移酶", "总胆红素"}, \
            [(x.item_raw, x.result_raw) for x in labs]
    finally:
        doc.close()


def test_marker_page_without_header_row_fails_loud():
    """命中页标记词、但没有表头行 → conf=0 且写明"没找到表头行"（不假装成功）。"""
    path = _tmp("marker_no_header.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "XX医院检验报告单", fontname=CJK, fontsize=11)
    pg.insert_text((50, 90), "第一列    第二列", fontname=CJK, fontsize=9)
    doc.save(path)
    doc.close()
    d = draft_template([path], org_id="ZZ-002")
    assert d.confidence == 0.0
    assert any("没找到表头行" in n for n in d.notes), d.notes
    assert not d.template.lab.unit_cells and not d.template.lab.flag_cells


# ------------------------------------------------- 机构名多路来源（A4）
#
# 本次误取「…超声检查报告」当机构名：MARKER_RE 最长匹配把**报告标题**也算进来了。

def test_cut_org_name_stops_at_org_suffix():
    """机构名**切在第一个机构后缀词末尾** —— 报告标题/部门字样不带进锚点。"""
    from checkup.autotemplate import _cut_org_name  # noqa: PLC0415
    assert _cut_org_name("临河市第一人民医院健康管理中心超声检查报告") \
        == "临河市第一人民医院"
    assert _cut_org_name("临河市第一人民医院健康体检中心") \
        == "临河市第一人民医院"
    assert _cut_org_name("临河市体检康复保健中心检验报告单") \
        == "临河市体检康复保健中心"
    # 封面被截断的字样（没有后缀词）不像机构名 → 不采纳
    assert _cut_org_name("某某市干部健康") == ""
    assert _cut_org_name("检验项目结果参考值") == ""


def test_draft_anchor_is_org_name_not_report_title():
    """A4：随包样例的 ``required_anchors`` 落在机构名上（与手写模板一致）。"""
    d = draft("syn", _syn_pdfs(), org_id="SYN-002")
    assert d.template.required_anchors == ["康泰综合医院"], \
        d.template.required_anchors
    assert all("报告单" not in a for a in d.template.required_anchors)
    # 机构名同时进 header_anchor_strings 首位（路由用）
    assert d.template.header_anchor_strings[0] == "康泰综合医院"


def test_anchor_needs_two_samples():
    """A4：**只给 1 份样本**（机构名只出现在 1 页）→ 不写 required_anchors + 留问题。"""
    p = _mk_lab5(_tmp("one_sample.pdf"), marker="检验报告单")
    d = draft_template([p], org_id="ONE-001")
    assert d.template.required_anchors == [], d.template.required_anchors
    assert any("2 份样本" in q or "required_anchors" in q for q in d.open_questions), \
        d.open_questions


# ------------------------------------------------- 列锚点稳健化（A13）
#
# 旧规则"取最左两簇"在左侧有 3 簇（页脚/段标题/项目名）时切错：
# 实测该机构 89 项 → 98 项，多出的全是"段标题 → 检验者：某某"这类假项。

def test_section_split_takes_densest_cluster_as_item_column():
    """项目列 = **最密簇**；分界取"项目列与左侧最近簇的正中"。"""
    from checkup.autotemplate import _pick_section_split  # noqa: PLC0415
    section_x, item_x, x_max, _why = _pick_section_split(
        [(56.9, 8), (66.9, 9), (98.8, 100)])
    assert item_x == 98.8 and section_x == 66.9          # 不是 56.9（页脚）
    assert 66.9 < x_max < 98.8
    # 只有一簇（段标题与项目同名列）→ 由项目列回推
    _s, item_x2, x_max2, _why2 = _pick_section_split([(42.0, 38)])
    assert item_x2 == 42.0 and x_max2 == 36.0
    # 没有数据行 → 兜底值（并说清是兜底）
    _s3, item3, x_max3, why3 = _pick_section_split([])
    assert item3 is None and x_max3 == 60.0 and "兜底" in why3


def test_column_consistency_passes_small_drift_flags_big_drift():
    """列位自检：**轻微漂移仍通过**，明显漂移必须被点名（A13）。"""
    from checkup.autotemplate import (_column_consistency,  # noqa: PLC0415
                                      _generic_marker, _scan_pages, _x_hist,
                                      _pick_section_split)
    for drift, should_flag in ((4.0, False), (40.0, True)):
        p = _mk_lab5(_tmp(f"drift{int(drift)}.pdf"), pages=2, drift2=drift)
        doc = fitz.open(p)
        try:
            marker, _ = _generic_marker([doc])
            pages = _scan_pages([doc], marker)
            hp = [x for x in pages if x["hit"] is not None]
            xs = []
            for x in hp:
                seen = False
                for row in x["rows"]:
                    if row is x["hit"][0]:
                        seen = True
                        continue
                    if seen and 1 <= len(row.first.strip()) <= 10:
                        xs.append(round(row.x0, 1))
            _s, item_x, _m, _w = _pick_section_split(_x_hist(xs))
            _detail, issues = _column_consistency(hp, item_x)
            assert bool(issues) is should_flag, (drift, issues)
        finally:
            doc.close()


# ------------------------------------------------- 自举表头词（A2）

def test_bootstrap_header_tokens_from_shipped_sample():
    """A2：随包样例（4 列：项目/结果/参考值/提示）能自举出表头词。"""
    from checkup.autotemplate import bootstrap_header_tokens  # noqa: PLC0415
    docs = [fitz.open(p) for p in _syn_pdfs()]
    try:
        toks, why = bootstrap_header_tokens(docs)
    finally:
        for d in docs:
            d.close()
    assert toks == ["项目", "结果", "参考值", "提示"], (toks, why)
    assert "跨页重复" in why


def test_bootstrap_header_tokens_from_synthetic_five_columns():
    """A2：本类机构式（5 列：…/标志/…/单位）也能自举出表头词。"""
    from checkup.autotemplate import bootstrap_header_tokens  # noqa: PLC0415
    p = _mk_lab5(_tmp("boot5.pdf"), pages=2)
    doc = fitz.open(p)
    try:
        toks, _why = bootstrap_header_tokens([doc])
    finally:
        doc.close()
    assert toks == ["检验项目", "结果", "标志", "参考值", "单位"], toks


def test_bootstrap_finds_nothing_on_single_page():
    """只有一页 → "跨页重复"这条证据不成立 → 如实说挖不到（不硬猜）。"""
    from checkup.autotemplate import bootstrap_header_tokens  # noqa: PLC0415
    p = _mk_lab5(_tmp("boot1.pdf"), pages=1)
    doc = fitz.open(p)
    try:
        toks, why = bootstrap_header_tokens([doc])
    finally:
        doc.close()
    assert toks == [] and "无法自举" in why


# ------------------------------------------------- 候选-验证闭环（A3）

def test_readback_verification_passes_on_good_geometry():
    """A3：几何对 → 回读全过（每个数值都能回原页、与项目名同行）。"""
    from checkup.autotemplate import verify_extraction  # noqa: PLC0415
    p = _mk_lab5(_tmp("verify.pdf"), pages=2)
    d = draft_template([p], org_id="VV-001")
    n_ok, fails = verify_extraction([p], d.template)
    assert not fails, fails
    assert n_ok >= 6, n_ok


def test_readback_verification_flags_value_on_a_different_row():
    """A3：值被回填到上一项（换行排版）→ 回读报"含该数值的行里找不到项目名"。

    ⚠ 这是 E2 同款的**保守**判定：排版换行时值在下一行，属"值得人看一眼"，
    不是"数据错"。A3 拿它当"这份假设站不住"的证据 —— 宁可多报。
    """
    from checkup.autotemplate import verify_extraction  # noqa: PLC0415
    path = _tmp("wrapped.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "临河市第一人民医院检验报告单", fontname=CJK, fontsize=11)
    for x, t in ((124, "检验项目"), (297, "结果"), (416, "参考值")):
        pg.insert_text((x, 110), t, fontname=CJK, fontsize=9)
    pg.insert_text((124, 140), "丙氨酸氨基转移酶", fontname=CJK, fontsize=9)
    pg.insert_text((416, 140), "0-40", fontsize=9)
    pg.insert_text((297, 160), "61", fontsize=9)      # 值换行到下一行
    doc.save(path)
    doc.close()
    d = draft_template([path], org_id="WW-001")
    _n_ok, fails = verify_extraction([path], d.template)
    assert any("找不到项目名" in f for f in fails), (fails, d.render())


def test_geometry_variants_pick_the_one_with_verified_items():
    """A3：几何变体逐个回读，**回读通过数多的那款**出线（空转/项少的淘汰）。"""
    from checkup.autotemplate import _pick_by_verification, verify_extraction  # noqa: PLC0415
    p = _mk_lab5(_tmp("verify2.pdf"), pages=2)
    d = draft_template([p], org_id="VV-002")
    base = d.template
    doc = fitz.open(p)
    try:
        n_good, fails_good = verify_extraction([p], base, [doc])
        assert not fails_good and n_good >= 6, (n_good, fails_good)
        chosen, info = _pick_by_verification(
            [p], [doc], base,
            [("全页段标题列", 590.0), ("nearest", base.lab.section_x_max)])
    finally:
        doc.close()
    assert info["chosen"] == "nearest" and info["n_verified"] > 0, info
    assert chosen.lab.section_x_max == base.lab.section_x_max
    assert all("失败 0" in t for t in info["trials"]), info["trials"]


def test_page_set_selfheal_recovers_misclassified_page():
    """A3 自愈：**把一页标错**（续页没有页标记词）→ 系统自己换标记词救回来。

    构造：页 1 有表头行 + 页标记词；页 2 是续页（不重复表头，只在页首写机构名）
    但有 ≥3 行"名称+值+区间"。默认标记词只覆盖页 1 → 页 2 整页丢 →
    内容语义候选集发现漏页 → 换一个覆盖两页的标记词并回读验证通过。
    """
    from checkup.autotemplate import semantic_lab_pages  # noqa: PLC0415
    p = _mk_lab5(_tmp("selfheal.pdf"), marker="检验报告单", pages=2,
                 cont_page=True)
    d = draft_template([p], org_id="SH-001")
    doc = fitz.open(p)
    try:
        # 自愈后：标记词覆盖两页，且两页的项都抽到（正确页不受影响）
        assert d.template.lab.page_marker in doc[1].get_text(), \
            d.template.lab.page_marker
        labs = extract_lab_results(doc, "2025", d.template)
        assert len(labs) == 6, [(x.page_no, x.item_raw) for x in labs]
        assert {x.page_no for x in labs} == {1, 2}
        assert {x.item_raw for x in labs} == {
            "丙氨酸氨基转移酶", "天门冬氨酸氨基转移酶", "总胆红素"}
        # 语义候选集确实把两页都算进来了（自愈的触发条件）
        assert [p_ for _d, p_, _n in semantic_lab_pages([doc])] == [0, 1]
    finally:
        doc.close()
    assert any("页集自愈" in q for q in d.open_questions), d.open_questions


def test_page_set_selfheal_does_not_widen_to_other_pages():
    """A3 自愈的**硬约束**：新标记词不得扫到候选集之外的页（宁可报出来让人看）。"""
    from checkup.autotemplate import _cover_all_pages, _scan_pages  # noqa: PLC0415
    p = _mk_lab5(_tmp("narrow.pdf"), marker="检验报告单", pages=2, cont_page=True)
    doc = fitz.open(p)
    try:
        _pages = _scan_pages([doc], "检验报告单")
        need = {(0, 0), (0, 1)}
        # 只有一个词能同时覆盖两页时才会提名；覆盖不到的候选词一律不提名
        assert _cover_all_pages([doc], need, ["结果"]) == ""
        assert _cover_all_pages([doc], need, ["临河市第一人民医院"]) \
            == "临河市第一人民医院"
    finally:
        doc.close()


# ------------------------------------------------- 求解器化（A11）

def test_variant_space_expands_disputed_decisions():
    """A11：变体空间把可争议决定都展开（噪声三档/分界/单位列/栏数…）且**去重**。"""
    from checkup.autotemplate import variant_space  # noqa: PLC0415
    p = _mk_lab5(_tmp("space.pdf"), pages=2)
    d = draft_template([p], org_id="SP-001")
    space = variant_space(d)
    names = [n for n, _ in space]
    assert "默认组合" in names
    assert any(n.startswith("噪声=签名") for n in names)
    assert any("噪声=严格" in n for n in names)
    keys = {json.dumps(t.to_dict(), sort_keys=True, ensure_ascii=False)
            for _, t in space}
    assert len(keys) == len(space), "同款变体必须去重"


def test_search_prefers_variant_without_fake_items():
    """A11：**假项（段标题 → 检验者）会被求解器排除** —— 采用款假项数为 0。

    复刻真实机构的段标题行形态：长标题（>8 字，抽取层判不出段标题）+ 签名。
    默认噪声下它会变成"项目名=段标题、值=检验者：某某"的假项（实测 93 vs 89）。
    """
    from checkup.autotemplate import quality_of, search_template  # noqa: PLC0415
    p = _mk_lab5(_tmp("sig.pdf"), pages=2, sig_row=True)
    raw = draft_template([p], org_id="SG-000")
    bad_default, negn_default, _n = quality_of(raw.template, [p])
    assert bad_default == 2 and -negn_default == 8, (bad_default, -negn_default)
    res = search_template([p], org_id="SG-001")
    assert res.template is not None, res.render()
    bad, negn, _notes = quality_of(res.template, [p])
    assert bad == 0 and -negn == 6, (-negn, bad, res.render())
    # 选定后基线按选定几何重算（不是起草时那份含假项的基线：[8] → [6]）
    assert res.template.stats.get("items_per_report") == [6], res.template.stats


def test_search_never_accepts_a_variant_accept_rejects():
    """A11：**不产生假绿** —— accept 打回的候选一律不出线（宁可没有候选）。

    注入一个恒定 block 的 accept：求解器必须返回 template=None，
    而不是"没有全绿就随便挑一个"。
    """
    from checkup.autotemplate import search_template  # noqa: PLC0415

    class _Blocked:
        verdict = "block"
        n_items = 0

    p = _mk_lab5(_tmp("nogreen.pdf"), pages=2)
    res = search_template([p], org_id="NG-001",
                          accept_fn=lambda t, ps: _Blocked())
    assert res.template is None, res.render()
    assert res.green == [] and res.trials, res.render()


def test_search_picks_green_variant_when_accept_accepts():
    """A11：accept 放行的候选才出线；选出的就是被放行的那一款。"""
    from checkup.autotemplate import search_template  # noqa: PLC0415
    p = _mk_lab5(_tmp("green.pdf"), pages=2)
    seen: list[str] = []

    class _Ok:
        verdict = "ok"
        n_items = 3

    def _accept(t, ps):  # 记录每一款的噪声风格，返回 ok
        seen.append(t.lab.noise_pattern[:24])
        return _Ok()

    res = search_template([p], org_id="GR-001", accept_fn=_accept)
    assert res.template is not None and res.green, res.render()
    assert res.chosen in res.green
    assert len(seen) <= 5, seen      # 只对预筛前 max_accept 款真跑验收


# --------------------------------------------------- 表头全貌 / 回写（A5·A6·A7）

def test_draft_nominates_unit_cell_column():
    """A5：第 5 列「单位」必须被提名为 ``unit_cells``（本机构参考值与单位分列）。"""
    p = _mk_lab5(_tmp("nxyd_style.pdf"))
    d = draft("nxyd_style", [p], org_id="NXYD-STYLE")
    assert d.template.lab.unit_cells == ["单位"], d.template.lab.unit_cells
    assert "单位" in d.template.lab.section_stop
    # 单位列真的进了产物（不是只写在模板里）
    doc = fitz.open(p)
    try:
        labs = {x.item_raw: x for x in extract_lab_results(doc, "2025", d.template)}
        assert labs["丙氨酸氨基转移酶"].unit_raw == "U/L", \
            labs["丙氨酸氨基转移酶"].unit_raw
        assert labs["总胆红素"].unit_raw == "umol/L"
    finally:
        doc.close()


def test_draft_nominates_flag_cell_column_of_shipped_sample():
    """A5：随包样例的第 4 列「提示」被提名为 ``flag_cells``（列里有文字提示）。"""
    d = draft("syn", _syn_pdfs(), org_id="SYN-002")
    assert d.template.lab.flag_cells == ["提示"], d.template.lab.flag_cells
    assert "提示" in d.template.lab.section_stop


def test_draft_leaves_arrow_only_flag_column_empty_with_question():
    """A5「不确定则留空」：标志列只有箭头/星号 → 不配 ``flag_cells`` + 待确认问题。

    收进 ``flag_cells`` 会把箭头从 E4 箭头交叉里拿走、把值内标记（``+-*``）
    从值里删掉 —— 那类列的信息已经在「值 vs 区间」里了。
    """
    d = draft("nxyd_style", [_mk_lab5(_tmp("nxyd_style.pdf"))], org_id="NXYD-STYLE")
    assert d.template.lab.flag_cells == [], d.template.lab.flag_cells
    assert any("标志" in q for q in d.open_questions), d.open_questions


def test_draft_writes_back_noise_labels():
    """A6：化验页检出的签名词必须**回写模板**（原来算完就丢，是纯 bug）。"""
    d = draft("syn", _syn_pdfs(), org_id="SYN-002")
    labels = set(d.template.narrative.noise_labels)
    assert {"审核医生", "检验者", "审核日期", "操作员", "主检医生"} <= labels, labels
    # 检出结果（冒号收尾、每张化验页都出现的短词）确实进了模板
    assert labels & {"报告编号", "检查日期", "身高", "体重"}, \
        sorted(labels)
    # 但检查方式同名标签必须被拦下：写进去会整段吞掉叙述条目
    assert not (labels & {"甲状腺超声", "腹部超声"}), sorted(labels)


def test_draft_writes_page_size_from_pdf():
    """A7：起草产物必须带 ``page_size``，且取自 PDF 实测（不是默认值）。"""
    p = _mk_lab5(_tmp("odd_size.pdf"), size=(610.0, 880.0))
    d = draft_template([p], org_id="ODD-001")
    assert d.template.page_size == [610.0, 880.0], d.template.page_size
    assert d.template.page_size != [595.0, 842.0]     # 默认值就是它，别写成"恰好相等"


def test_draft_status_grades_partial_and_ok():
    """A9：草稿分级 —— 有待确认字段 = 部分成功；全绿 = 结构已定；conf=0 = 无法起草。"""
    partial = draft("nxyd_style", [_mk_lab5(_tmp("nxyd_style.pdf"))],
                    org_id="NXYD-STYLE")
    assert partial.status == "partial" and "部分成功" in partial.status_label

    ok = draft("real", REAL, org_id="NX-001")
    assert ok.status == "ok" and ok.status_label == "结构已定"

    path = _tmp("nothing2.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "某某单据", fontname=CJK, fontsize=11)
    doc.save(path)
    doc.close()
    bad = draft_template([path], org_id="ZZ-003")
    assert bad.status == "fail" and bad.status_label == "无法起草"


# --------------------------------------------------- 词表起草 fail-loud（A8）
#
# F6 的假成功：模板坏 → 抽 0 项 → 打印「✓ 全部已映射」退出 0。
# 现在两条路径都必须报错停下（非零退出）。

def _run_draft_catalog(pdf: str, out_dir: str) -> tuple[int, str]:
    import subprocess  # noqa: PLC0415
    env = dict(os.environ)
    env["CHECKUP_OUT"] = out_dir
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(
        [sys.executable, os.path.join(BASE, "draft_catalog.py"), pdf,
         "--org", "ZZ-009"],
        cwd=out_dir, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_draft_catalog_fails_loud_when_template_unrecognized():
    """模板未识别（起草 conf=0）→ 报错说明"词表起草无法进行"，不产出假草案。"""
    out = tempfile.mkdtemp(prefix="draftcat_")
    path = _tmp("unrecognizable.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "某某单据", fontname=CJK, fontsize=11)
    pg.insert_text((50, 90), "第一列    第二列", fontname=CJK, fontsize=9)
    doc.save(path)
    doc.close()
    code, out_text = _run_draft_catalog(path, out)
    assert code != 0, out_text
    assert "词表起草无法进行" in out_text, out_text
    assert "全部已映射" not in out_text, out_text
    assert not os.listdir(out), f"失败路径不该产出草案：{os.listdir(out)}"


def test_draft_catalog_fails_loud_on_zero_observations():
    """抽出 0 条观测（模板过了、但一行都没抽到）→ 报错，不当成"全部已映射"。"""
    out = tempfile.mkdtemp(prefix="draftcat_")
    code, out_text = _run_draft_catalog(_mk_header_only(_tmp("headonly.pdf")), out)
    assert code != 0, out_text
    assert "0 条观测" in out_text, out_text
    assert "全部已映射" not in out_text, out_text


# --------------------------------------------------------------- 草案性质

def test_draft_is_always_unconfirmed():
    """自动起草**永远**是草案 —— 错了会静默抽错数据，必须人工确认。"""
    for key in ("real", "single", "two"):
        d = draft(key, *( [REAL] if key == "real" else
                          [[_mk(_tmp(f"{key}.pdf"), "血常规",
                                columns=2 if key == "two" else 1)]] ))
        assert d.template.notes, key
        assert any("人工确认" in n for n in d.template.notes), d.template.notes


def test_draft_records_evidence_for_every_decision():
    """每一项推断都要留下"从哪来"的证据，否则人工没法复核。"""
    d = draft("real", REAL, org_id="NX-001")
    for k in ("page_marker", "header_cells", "section_x_max", "noise_labels"):
        assert k in d.evidence, k
        assert d.evidence[k]


def test_unrecognizable_pdf_yields_zero_confidence():
    """表头词不认识 → 置信度 0 且写明原因，**不假装成功**。"""
    path = _tmp("nothing.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "某某单据", fontname="china-s", fontsize=11)
    pg.insert_text((50, 90), "第一列    第二列", fontname="china-s", fontsize=9)
    doc.save(path)
    doc.close()
    d = draft_template([path], org_id="ZZ-001")
    assert d.confidence == 0.0
    assert any("没找到表头行" in n for n in d.notes), d.notes


# --------------------------------------------------------------- 可用性

def test_drafted_template_round_trips_and_routes():
    """草案能存成 JSON、读回来、并被注册表路由到。"""
    p = _mk(_tmp("auto_rt.pdf"), "血液检查", columns=1)
    d = draft("single", [p], org_id="YY-002")
    path = os.path.join(tempfile.mkdtemp(), "YY-002.json")
    d.template.save(path)
    back = Template.from_dict(json.load(open(path, encoding="utf-8")))
    assert back.lab.column_groups == 1

    reg = TemplateRegistry([back])
    r = reg.match(fitz.open(p))
    assert r.template is not None and r.template.org_id == "YY-002"
    assert len(extract_lab_results(fitz.open(p), "2025", r.template)) == 5


def test_draft_from_multiple_reports_is_more_confident():
    """多份同机构报告一起起草，置信度不该更低（噪声标签靠重复出现）。"""
    one = draft("two", [_mk(_tmp("m1.pdf"), "血常规", columns=2)], org_id="YY-003")
    assert one.confidence > 0


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
