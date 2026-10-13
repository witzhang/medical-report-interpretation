#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新机构上手：**加一个 JSON，不改代码**（框架 D1）。

这是模板注册表存在的全部意义。上一轮暴露的硬假设

    SECTION_X_MAX = 60.0   # 对一家机构版式的硬编码

现在必须能被一个 JSON 覆盖掉。本测试就是验收条件：

    1. 没登记的新版式 → UNKNOWN_LAYOUT，**不硬套默认模板**
    2. 给它写一个 JSON → 立刻能匹配、能抽、能归一
    3. 全程**零行代码改动**

    python3 tests/test_onboarding.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402
from checkup.tables import extract_lab_results, lab_page_audit  # noqa: E402
from checkup.templates import (Template,  # noqa: E402
                               TemplateRegistry, observed_column_groups)
from checkup.validate import check_extraction_audit, check_unmapped_rate  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tests"))
from test_cross_institution import _mk, _tmp  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
LEGACY_T = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
ORG = "SYN-LEGACY"   # 合成夹具机构（P0-3 去真实机构名）


def _alien_pdf(section: str = "血液检查") -> str:
    """一份"别家机构"的单栏化验单。"""
    return _mk(_tmp("alien_single.pdf"), section, columns=1)


def _alien_template(org_id: str = "YY-002") -> Template:
    """描述那份单栏版式的模板 —— 全部是数据，没有一行代码改动。"""
    return Template(
        org_id=org_id,
        org_name="XX市第一人民医院",
        template_version=1,
        valid_from="2025-01-01",
        page_size=[595.0, 842.0],
        header_anchor_strings=["检验报告单", "检验项目", "参考值"],
        lab=__import__("checkup.templates", fromlist=["LabTemplate"]).LabTemplate(
            column_groups=1),
    )


# --------------------------------------------------------------- 未登记

def test_unregistered_layout_is_not_forced_into_default_template():
    """未登记的单栏版式**不得**被硬套进双栏模板。

    只按锚点串+页尺寸打分的话，它和双栏报告长得一模一样，会拿 0.8 分被
    错路由过去，然后抽出 0 项 —— 而 0 项在某些路径上是静默的。
    """
    p = _alien_pdf()
    doc = fitz.open(p)
    try:
        assert observed_column_groups(doc, ["检验项目", "结果", "参考值"]) == 1
    finally:
        doc.close()

    r = route_document(p, TemplateRegistry([LEGACY_T]))
    assert r.template is None, f"被错路由到 {r.template.org_id}"
    assert "UNKNOWN_LAYOUT" in r.reason


def test_default_template_does_not_parse_single_column():
    """双栏模板拿去解析单栏报告 → 0 项，且审计必须报 fatal。"""
    p = _alien_pdf()
    doc = fitz.open(p)
    try:
        assert extract_lab_results(doc, "2025", LEGACY_T) == []
        assert check_extraction_audit(lab_page_audit(doc, LEGACY_T)).level \
            == "fatal"
    finally:
        doc.close()


# --------------------------------------------------------------- 登记后

def test_adding_a_json_onboards_the_institution():
    """**验收条件**：写一个 JSON，这份报告立刻能匹配、能抽、能归一。"""
    p = _alien_pdf()
    tpl = _alien_template()

    # 1) 注册表能匹配上
    reg = TemplateRegistry([LEGACY_T, tpl])
    r = reg.match(fitz.open(p))
    assert r.template is not None and r.template.org_id == "YY-002", r.reason

    # 2) 抽取正常
    doc = fitz.open(p)
    try:
        labs = extract_lab_results(doc, "2025", tpl)
        assert len(labs) == 5, [(x.section, x.item_raw, x.result_raw) for x in labs]
        assert {x.item_raw for x in labs} == {
            "白细胞", "红细胞", "血红蛋白浓度", "血小板", "中性粒细胞绝对值"}
        # 数值/区间都解析出来了
        wbc = next(x for x in labs if x.item_raw == "白细胞")
        assert wbc.value == 7.2 and wbc.ref_low == 3.5 and wbc.ref_high == 9.5
        # 审计通过
        assert check_extraction_audit(lab_page_audit(doc, tpl)).level == "pass"
    finally:
        doc.close()

    # 3) 段名没登记 → 归一标 unmapped（**不猜**），并触发覆盖率告警
    cat = AnalyteCatalog.load(CATALOG)
    un = [cat.resolve("YY-002", "血液检查", x.item_raw, "numeric", x.unit_raw)
          for x in labs]
    assert all(u.analyte_id == UNMAPPED for u in un)


def test_template_json_round_trips():
    """模板必须能存成 JSON 再读回来，且语义不变。"""
    tpl = _alien_template()
    d = tempfile.mkdtemp()
    path = os.path.join(d, "YY-002.json")
    tpl.save(path)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["lab"]["column_groups"] == 1
    back = Template.from_dict(raw)
    assert back.org_id == tpl.org_id
    assert back.lab.column_groups == 1
    assert back.narrative.label_x_max == tpl.narrative.label_x_max


def test_registry_loads_from_directory():
    """从目录加载模板；注册表里同时存在两套版式时各归各。"""
    d = tempfile.mkdtemp()
    _alien_template("YY-002").save(os.path.join(d, "YY-002.json"))
    LEGACY_T.save(os.path.join(d, "SYN-LEGACY.json"))
    reg = TemplateRegistry.load(d)
    assert {t.org_id for t in reg.templates} == {"SYN-LEGACY", "YY-002"}

    src = PATHS.real_reports
    real = os.path.join(src, "20240612体检报告.pdf")
    alien = _alien_pdf()
    assert route_document(real, reg).template.org_id == "SYN-LEGACY"
    assert route_document(alien, reg).template.org_id == "YY-002"


def test_empty_directory_does_not_fall_back_to_builtin():
    """**空/缺失目录 → 空注册表，禁止回落内置模板**（P0-2）。

    原行为"目录不存在时回落到内置模板"已被删除：那会让换机构时**静默套用
    本机构版式**，既报不出 ``UNKNOWN_LAYOUT``，也交出一份读起来像"没有发现问题"
    的报告 —— 最危险的失败模式。现在拿不到模板就要显式失败（上层 FATAL）。
    """
    reg = TemplateRegistry.load(os.path.join(tempfile.mkdtemp(), "nope"))
    assert len(reg.templates) == 0
    # 空注册表对任何文档都匹配不上 → None（由调用方按 UNKNOWN_LAYOUT 处理）
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "检验报告单 检验项目 结果 参考值")
    try:
        assert reg.match(doc).template is None
    finally:
        doc.close()


def test_require_template_refuses_none():
    """显式取模板：``None`` → **报错**，不许任何形式的静默回落（P0-2）。"""
    from checkup.templates import require_template
    try:
        require_template(None)
    except ValueError as e:
        assert "不内置任何默认版式" in str(e), str(e)
        return
    raise AssertionError("require_template(None) 必须报错")


# --------------------------------------------------------------- 版本

def test_new_version_supersedes_without_touching_old():
    """改版 = 新增版本，**不修改旧版**（沿用项目既有的"逐年保留不覆盖"）。"""
    v1 = _alien_template("YY-002")
    v1.template_version = 1
    v2 = _alien_template("YY-002")
    v2.template_version = 2
    v2.supersedes = 1
    v2.lab.column_groups = 1
    reg = TemplateRegistry([v1, v2])
    assert [t.template_version for t in reg.templates] == [1, 2]
    assert reg.templates[1].supersedes == 1


# --------------------------------------------------------------- 内置

def test_builtin_template_matches_repo_constant():
    """内置模板的值必须与原先硬编码的常量一一对应（防止参数化时改错）。"""
    from checkup import narrative as N
    assert LEGACY_T.lab.section_x_max == 60.0
    assert LEGACY_T.narrative.label_x_max == 130.0
    assert LEGACY_T.narrative.section_x_max == 70.0
    assert LEGACY_T.lab.column_groups == 2
    assert set(LEGACY_T.narrative.narrative_sections) == N.NARRATIVE_SECTIONS
    assert set(map(tuple, LEGACY_T.narrative.organ_keys)) == set(N.ORGAN_KEYS)


def test_shipped_template_file_is_valid():
    """随包的合成机构模板（SYN-002）必须能被读回，且几何自洽。"""
    path = os.path.join(BASE, "catalog", "templates", "SYN-002.json")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        t = Template.from_dict(json.load(f))
    assert t.org_id == "SYN-002"
    assert t.lab.column_groups == 1
    assert t.required_anchors, "机构专属锚点必须有（路由硬门槛）"
    assert t.stats.get("items_per_report"), "抽取基线必须有（漂移监控用）"


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
