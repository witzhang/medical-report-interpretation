#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冷启动：漂移监控 + 归一词表草案。    python3 tests/test_coldstart.py

覆盖"接入一家新机构"这条链的两端：

* **漂移监控** —— 同机构改版不报错，只是悄悄少抽几项。必须能被发现。
* **词表草案** —— 新机构项目名一个都不认识时，起草映射规则。
  两条不可逾越的线：**绝不自动合并**、**不做模糊子串匹配**。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.autotemplate import draft_template  # noqa: E402
from checkup.catalog_draft import (propose, save_draft,  # noqa: E402
                                   specimen_of_section, stratify,
                                   unmapped_items)
from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.tables import extract_lab_results  # noqa: E402
from checkup.templates import Template, TemplateRegistry  # noqa: E402
from checkup.validate import check_drift  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cross_institution import _mk, _tmp  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(BASE, "catalog", "analytes.json")
SHIPPED = os.path.join(BASE, "catalog", "templates", "SYN-002.json")

_cat = None


def cat() -> AnalyteCatalog:
    global _cat
    if _cat is None:
        _cat = AnalyteCatalog.load(CATALOG)
    return _cat


def _obs_from(path: str, section_org: str = "YY-002") -> list[NormalizedObservation]:
    """抽一份合成报告 → 未映射的观测（模拟新机构首次接入）。"""
    d = draft_template([path], org_id=section_org)
    doc = fitz.open(path)
    try:
        labs = extract_lab_results(doc, "2025", d.template)
    finally:
        doc.close()
    return [NormalizedObservation(
        year="2025", page_no=x.page_no, section=x.section, item_raw=x.item_raw,
        occurrence=x.occurrence, analyte_id=UNMAPPED, value_type=x.value_type,
        unit=x.unit_raw, value=x.value, ref_low=x.ref_low, ref_high=x.ref_high)
        for x in labs]


# =============================================================== 漂移监控

def test_drift_passes_when_counts_match_baseline():
    c = check_drift([{"year": "2024", "items": 81},
                     {"year": "2025", "items": 81},
                     {"year": "2026", "items": 81}],
                    {"items_per_report": [81, 81, 81]})
    assert c.level == "pass", c.detail


def test_drift_flags_layout_change():
    """改版**不会报错**，只是悄悄少抽几项 —— 必须被这条抓住。"""
    c = check_drift([{"year": "2024", "items": 81},
                     {"year": "2026", "items": 40}],
                    {"items_per_report": [81, 81, 81]})
    assert c.level == "warn", c.detail
    assert "疑似机构改版" in c.detail
    assert any("2026" in i for i in c.items)


def test_drift_silent_for_new_institution():
    """新机构首次接入没有基线 —— 不该因为"跟谁都不一样"而报警。"""
    c = check_drift([{"year": "2025", "items": 42}], {})
    assert c.level == "pass"
    assert "首次接入" in c.detail


def test_shipped_template_has_baseline():
    """仓库里的模板必须带抽取基线，否则漂移监控形同虚设。"""
    t = Template.from_dict(json.load(open(SHIPPED, encoding="utf-8")))
    assert t.stats.get("items_per_report") == [22, 22], t.stats


# =============================================================== 指纹

def test_required_anchors_reject_other_institutions():
    """机构专属字样缺一不可 —— 否则别家报告会被通用表头词带进来。

    这是上一轮埋下的 bug：自动起草把含机构全名的**完整标题**当成了
    ``page_marker``，结果别家报告因为抬头对不上而**整页被跳过**（抽 0 项），
    同时路由又只要求 3/4 锚点命中，把别家报告错配给了自家模板。
    """
    t = Template.from_dict(json.load(open(SHIPPED, encoding="utf-8")))
    assert t.required_anchors, "机构专属字样必须有"

    alien = _mk(_tmp("alien_req.pdf"), "血液检查", columns=2)
    r = route_document(alien, TemplateRegistry([t]))
    assert r.template is None, f"别家报告被错配到 {r.template.org_id}"


def test_page_marker_is_generic_not_org_specific():
    """``page_marker`` 要通用（判"是不是化验页"），机构专属字样只进指纹。"""
    t = Template.from_dict(json.load(open(SHIPPED, encoding="utf-8")))
    assert t.lab.page_marker in ("检验报告单", "报告单", "检验单"), t.lab.page_marker
    assert t.lab.page_marker not in "康泰综合医院", t.lab.page_marker
    assert any("康泰综合医院" in a for a in t.required_anchors), t.required_anchors


# =============================================================== 词表草案

def test_proposals_map_alien_section_to_known_analytes():
    """别家机构的「血液检查」段 → 提议映射到已知的 BLD.* 指标。"""
    obs = _obs_from(_mk(_tmp("cd1.pdf"), "血液检查", columns=2))
    props = propose(unmapped_items(obs), cat(), "YY-002")
    got = {p.source_name: p.analyte_id for p in props}
    assert got == {"白细胞": "BLD.白细胞", "红细胞": "BLD.红细胞",
                   "血红蛋白浓度": "BLD.血红蛋白浓度", "血小板": "BLD.血小板",
                   "中性粒细胞绝对值": "BLD.中性粒细胞绝对值"}, got


def test_high_confidence_needs_three_evidences():
    """名字 + 单位 + 标本类型三项齐了才是高置信。"""
    obs = _obs_from(_mk(_tmp("cd2.pdf"), "血液检查", columns=2))
    props = propose(unmapped_items(obs), cat(), "YY-002")
    p = next(x for x in props if x.source_name == "白细胞")
    assert p.confidence >= 0.9, p.reasons
    joined = " ".join(p.reasons)
    assert "精确同名" in joined and "单位一致" in joined and "标本类型一致" in joined


def test_duplicate_name_is_resolved_when_evidence_discriminates():
    """同名撞键**有足够证据时**可以定：单位 + 值类型都指向定量那一个。"""
    props = propose([("尿液检查", "白细胞", "numeric", "个/ul")], cat(), "YY-002")
    p = props[0]
    assert p.analyte_id == "URI.白细胞", (p.analyte_id, p.reasons)
    assert p.confidence >= 0.8, p.reasons
    joined = " ".join(p.reasons)
    assert "值类型一致" in joined, joined


def test_duplicate_name_is_flagged_for_human_when_ambiguous():
    """**证据不足时**（连值类型都不知道）→ 必须压低置信度并要求人工指定。

    同名撞键是归一层的正题：尿液白细胞有定性/定量两个指标，
    目录正是靠值类型把它们分开的。不给值类型，就没有理由挑任何一个。
    """
    props = propose([("尿液检查", "白细胞", "", "")], cat(), "YY-002")
    p = props[0]
    assert p.confidence < 0.8, (p.confidence, p.reasons)
    joined = " ".join(p.reasons)
    assert "无法消歧" in joined and "必须人工" in joined
    assert len(p.candidates) >= 2, p.candidates


def test_no_fuzzy_substring_matching():
    """「白细胞(高倍视野)」**不得**被匹配到「白细胞」。

    health-assistant 用子串 + 去括号 + 去复数兜底，中国检验项目名高度相似，
    那样会静默并错。这里只做归一化精确匹配。
    """
    props = propose([("尿液检查", "白细胞(高倍视野)", "numeric", "个/HP")],
                    cat(), "YY-002")
    p = props[0]
    # 要么提议新建，要么命中它自己的项 —— 但绝不能命中 URI.白细胞
    assert p.analyte_id != "URI.白细胞", p.reasons
    assert p.analyte_id != "BLD.白细胞", p.reasons


def test_unknown_name_proposes_new_analyte():
    props = propose([("血液检查", "某个全新指标", "numeric", "mg/dL")],
                    cat(), "YY-002")
    p = props[0]
    assert p.analyte_id is None
    assert p.confidence == 0.0
    assert p.new_analyte and p.new_analyte["confirmed"] is False
    assert any("新建" in r for r in p.reasons)


def test_draft_file_is_marked_unconfirmed():
    """草案文件必须写死 confirmed=false —— 自动合并会静默并错。"""
    obs = _obs_from(_mk(_tmp("cd3.pdf"), "血液检查", columns=2))
    props = propose(unmapped_items(obs), cat(), "YY-002")
    path = os.path.join(tempfile.mkdtemp(), "rules.json")
    save_draft(props, path)
    d = json.load(open(path, encoding="utf-8"))
    assert d["confirmed"] is False
    assert "人工确认" in d["note"]
    assert len(d["rules"]) == len(props)


def test_specimen_inference_from_section_name():
    """段名推断标本类型：血/尿/便三路。"""
    assert specimen_of_section("血液检查") == "blood"
    assert specimen_of_section("血常规") == "blood"
    assert specimen_of_section("尿液检查") == "urine"
    assert specimen_of_section("粪便检查") == "stool"
    assert specimen_of_section("免疫检查") == ""


# =============================================================== 分层确认（A12）

def _props(confs: list[float]) -> list:
    """造一批指定置信度的提案（只看分层逻辑，不跑词表）。"""
    from checkup.catalog_draft import RuleProposal  # noqa: PLC0415
    return [RuleProposal(org_id="ZZ-001", section="血常规", source_name=f"项{i}",
                         value_type="numeric", unit="", analyte_id=f"BLD.{i}",
                         confidence=c, reasons=[])
            for i, c in enumerate(confs)]


def test_stratify_batches_high_confidence_and_samples_ten_percent():
    """A12：≥0.8 批量通过；复核清单 = 随机 10% 高置信 + **全部**低置信。"""
    from checkup.catalog_draft import stratify  # noqa: PLC0415
    confs = [1.0, 0.95, 0.9, 0.85, 0.8, 0.65, 0.5, 0.0]      # 5 高 + 3 低
    d = stratify(_props(confs), seed=0)
    assert len(d.auto_pass) == 5 and len(d.low) == 3
    assert len(d.sample) == 1 + 3, [p.confidence for p in d.sample]
    # 低置信项**全部可见**
    assert all(any(p is lp for p in d.sample) for lp in d.low)
    # 边界：恰好 0.8 算高置信
    assert all(p.confidence >= 0.8 for p in d.auto_pass)


def test_stratify_is_reproducible():
    """抽样必须可复现：同 seed 同清单（项目"双跑一致"纪律）。"""
    from checkup.catalog_draft import stratify  # noqa: PLC0415
    confs = [0.9, 0.85, 0.81, 0.95, 0.88, 0.92, 0.83, 0.87, 0.99, 0.86]
    a = [p.source_name for p in stratify(_props(confs), seed=7).sample]
    b = [p.source_name for p in stratify(_props(confs), seed=7).sample]
    c = [p.source_name for p in stratify(_props(confs), seed=8).sample]
    assert a == b
    assert a != c, "换种子应换抽样（否则说明没用种子）"


def test_layered_draft_still_never_auto_merges():
    """A12 的纪律**不变**：产物仍 confirmed=false；批量通过 = 抽检通过，不是入库。"""
    from checkup.catalog_draft import stratify  # noqa: PLC0415
    obs = _obs_from(_mk(_tmp("layered.pdf"), "血液检查", columns=2))
    props = propose(unmapped_items(obs), cat(), "YY-002")
    path = os.path.join(tempfile.mkdtemp(), "rules.json")
    save_draft(props, path, layered=stratify(props, seed=0))
    d = json.load(open(path, encoding="utf-8"))
    assert d["confirmed"] is False
    assert "人工确认" in d["note"]
    assert len(d["rules"]) == len(props)              # 全量仍在（低置信不隐藏）
    lay = d["layered"]
    assert lay["auto_pass_count"] + len(lay["review_sample"]) >= len(props)
    assert "抽检通过" in lay["review_note"] or "不 = 入库" in lay["review_note"]


def test_urine_items_are_not_mapped_to_blood():
    """尿标本的项目不该被提议映射到血液指标上。"""
    props = propose([("尿液检查", "红细胞", "numeric", "个/ul")], cat(), "YY-002")
    p = props[0]
    assert p.analyte_id != "BLD.红细胞", p.reasons


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
