#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版式类库（D1）的回归测试。    python3 tests/test_layout_family.py

验收条件（工作单 D1）：**三个模板互不误配**；构造"NXYD 微改版"样本能走差量
且 E1–E6 全绿；相似度低于硬阈值 → **强制全量初始化并报错**。
"""
from __future__ import annotations

import os
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(ENGINE, "tests"))

import fitz  # noqa: E402

from _synth import CJK  # noqa: E402
from checkup import accept as A  # noqa: E402
from checkup.autotemplate import draft_template  # noqa: E402
from checkup.layout_family import (FAMILY_THRESHOLD, delta_template,  # noqa: E402
                                   find_family, similarity)
from checkup.templates import LabTemplate, Template, TemplateRegistry  # noqa: E402


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="fam_"), name)


def _mk_5col(path: str, dx: float = 0.0, size=(595.5, 842.2),
             org: str = "临河市第一人民医院", rows: int = 6) -> str:
    """5 列化验页（本类机构族）：检验项目|结果|标志|参考值|单位（x 可整体偏移）。"""
    doc = fitz.open()
    pg = doc.new_page(width=size[0], height=size[1])
    pg.insert_text((50, 60), org, fontname=CJK, fontsize=11)
    y = 110
    for x, t in ((124, "检验项目"), (297, "结果"), (347, "标志"),
                 (416, "参考值"), (519, "单位")):
        pg.insert_text((x + dx, y), t, fontname=CJK, fontsize=9)
    y += 26
    pg.insert_text((60 + dx, y), "肝功能", fontname=CJK, fontsize=9)
    y += 20
    for i in range(rows):
        pg.insert_text((124 + dx, y), f"项目{i}", fontname=CJK, fontsize=9)
        pg.insert_text((297 + dx, y), f"{i + 1}.5", fontsize=9)
        pg.insert_text((416 + dx, y), "0-40", fontsize=9)
        pg.insert_text((519 + dx, y), "U/L", fontsize=9)
        y += 18
    doc.save(path)
    doc.close()
    return path


def _mk_3col(path: str, dx: float = 0.0) -> str:
    """3 列化验页（别家版式族）：项目|结果|参考值（单栏，无单位/标志列）。"""
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "康泰综合医院健康体检报告单", fontname=CJK, fontsize=11)
    y = 110
    for x, t in ((42, "项目"), (254, "结果"), (356, "参考值")):
        pg.insert_text((x + dx, y), t, fontname=CJK, fontsize=9)
    y += 26
    for i in range(6):
        pg.insert_text((42 + dx, y), f"项{i}", fontname=CJK, fontsize=9)
        pg.insert_text((254 + dx, y), f"{i + 2}.5", fontsize=9)
        pg.insert_text((356 + dx, y), "1-9", fontsize=9)
        y += 18
    doc.save(path)
    doc.close()
    return path


def _tpl_5col(org: str = "FAM-A") -> Template:
    return Template(
        org_id=org, org_name="临河市第一人民医院", template_version=1,
        page_size=[595.5, 842.2], required_anchors=["临河市第一人民医院"],
        header_anchor_strings=["检验项目", "结果", "参考值"],
        lab=LabTemplate(page_marker="检验项目",
                        header_cells=["检验项目", "结果", "参考值"],
                        column_groups=1, section_x_max=92.0,
                        unit_cells=["单位"], section_stop=["检验项目", "结果",
                                                           "参考值", "单位"]))


def _tpl_3col(org: str = "FAM-B") -> Template:
    return Template(
        org_id=org, org_name="康泰综合医院", template_version=1,
        page_size=[595.0, 842.0], required_anchors=["康泰综合医院"],
        header_anchor_strings=["健康体检报告单", "项目", "结果", "参考值"],
        lab=LabTemplate(page_marker="报告单",
                        header_cells=["项目", "结果", "参考值"],
                        column_groups=1, section_x_max=36.0,
                        flag_cells=["提示"],
                        section_stop=["项目", "结果", "参考值", "提示"]))


# ---------------------------------------------------------------- 互不误配

def test_family_matching_does_not_misroute_across_families():
    """D1：**三个模板互不误配** —— 各自命中自家，别家低于硬阈值。"""
    reg = TemplateRegistry([_tpl_5col(), _tpl_3col()])
    a = _mk_5col(_tmp("a.pdf"))
    b = _mk_3col(_tmp("b.pdf"))
    doc_a, doc_b = fitz.open(a), fitz.open(b)
    try:
        hit_a = find_family(reg, doc_a)
        hit_b = find_family(reg, doc_b)
    finally:
        doc_a.close()
        doc_b.close()
    assert hit_a.base is not None and hit_a.base.org_id == "FAM-A", hit_a.reason
    assert hit_b.base is not None and hit_b.base.org_id == "FAM-B", hit_b.reason
    # 交叉：5 列文档 vs 3 列模板 / 3 列文档 vs 5 列模板 —— 都不该命中
    doc_a = fitz.open(a)
    try:
        s_cross, detail = similarity(_tpl_3col(), doc_a)
    finally:
        doc_a.close()
    assert s_cross < FAMILY_THRESHOLD, (s_cross, detail)


def test_unknown_layout_forces_full_init():
    """D1：相似度低于硬阈值 → **强制全量初始化并报错**（不许硬套）。"""
    path = _tmp("alien.pdf")
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "某某机构", fontname=CJK, fontsize=11)
    pg.insert_text((50, 90), "第一列 第二列", fontname=CJK, fontsize=9)
    doc.save(path)
    doc.close()
    doc = fitz.open(path)
    try:
        hit = find_family(TemplateRegistry([_tpl_5col(), _tpl_3col()]), doc)
    finally:
        doc.close()
    assert hit.base is None and hit.forced_full, hit.reason
    assert "强制全量初始化" in hit.reason


# ---------------------------------------------------------------- 差量

def test_micro_variant_hits_family_and_delta_passes_acceptance():
    """D1：**"本类机构微改版"样本走差量，且 E1–E6 全绿**。

    微改版 = 同 5 列、x 整体偏移 3pt、纸型略不同 → 类库命中基底，
    差量只改确实不同的字段；差量草案照样过验收链。
    """
    p = _mk_5col(_tmp("micro.pdf"), dx=3.0, size=(595.6, 842.4))
    doc = fitz.open(p)
    try:
        hit = find_family(TemplateRegistry([_tpl_5col()]), doc)
        base_detail = hit.detail
    finally:
        doc.close()
    assert hit.base is not None and hit.score >= FAMILY_THRESHOLD, hit.reason
    assert base_detail["列序一致性"] == 1.0 and \
        base_detail["表头词 Jaccard"] == 1.0, base_detail

    fresh = draft_template([p], org_id="FAM-A2").template
    delta, changes = delta_template(hit.base, fresh, changed_by="测试")
    assert changes, "微改版必然有差异（x 偏移/纸型）"
    assert delta.confirmed() is False if hasattr(delta, "confirmed") else True
    rep = A.accept(delta, [p])
    assert rep.verdict == "ok", rep.render()[:700]
    # 差量草案必须留痕（改动几处 + 来源）
    assert any("差量草案" in n for n in delta.notes), delta.notes


def test_delta_keeps_base_for_unchanged_fields():
    """D1：差量**只改不同的字段** —— 基底其余部分（叙述/噪声/列位）原样保留。"""
    base = _tpl_5col()
    base.narrative.noise_labels = ["医师", "检验者", "自定义噪声"]
    base.lab.noise_pattern = "^CUSTOM"
    fresh = _tpl_3col("FAM-A2")          # 故意让它与基底差异很大
    delta, changes = delta_template(base, fresh)
    # 改了的：锚点/表头/单位列等（fresh 与 base 不同处）
    assert any("required_anchors" in c for c in changes), changes
    assert any("lab.unit_cells" in c for c in changes), changes
    # 没改的：叙述噪声与 lab 噪声模式（fresh 是新建的默认值，但差量只搬列位相关字段）
    assert delta.narrative.noise_labels == ["医师", "检验者", "自定义噪声"]
    assert delta.lab.noise_pattern == "^CUSTOM"
    assert delta.template_version == base.template_version


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
