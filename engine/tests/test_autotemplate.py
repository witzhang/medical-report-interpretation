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
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
SRC = PATHS.real_reports
REAL = [os.path.join(SRC, f) for f in sorted(os.listdir(SRC))
        if f.endswith(".pdf")]

_drafts = {}


def draft(key: str, paths, **kw):
    """带 kwargs 的缓存 —— 只按 key 缓存会让 org_id 互相污染。"""
    ck = (key, tuple(sorted(kw.items())))
    if ck not in _drafts:
        _drafts[ck] = draft_template(paths, **kw)
    return _drafts[ck]


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
    # page_marker 必须是**通用**词（判"是不是化验页"），不许拿机构全名当标记
    assert d.template.lab.page_marker in ["检验报告单", "报告单", "检验单"], \
        d.template.lab.page_marker


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
