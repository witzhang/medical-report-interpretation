#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板验收链（accept）回归 —— 含**元测试**：种错必须被抓。

这是本模块的生死线：验收链的价值全在"它真的会响"。所以每条证据都配一个
"故意种错"的用例：把模板改坏 → 断言链给出 review/block（而不是 ok）。

    python3 tests/test_accept.py
"""
from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup import accept as A  # noqa: E402
from checkup.templates import Template  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.dirname(BASE)
SYN = os.path.join(SKILL, "examples", "testdata")
TPL = os.path.join(BASE, "catalog", "templates", "SYN-002.json")


def _syn_pdfs() -> list[str]:
    out = sorted(f for f in os.listdir(SYN) if f.endswith(".pdf"))
    return [os.path.join(SYN, f) for f in out]


def _base_dict() -> dict:
    with open(TPL, encoding="utf-8") as f:
        return json.load(f)


def _accept(mutate=None) -> A.Report:
    d = _base_dict()
    if mutate:
        mutate(d)
    return A.accept(Template.from_dict(d), _syn_pdfs())


# ---------------------------------------------------------------- 不误报

def test_known_good_template_is_ok():
    """基线：随包模板 + 随包样例必须 **ok / 0 张对照卡**（不误报）。"""
    rep = _accept()
    assert rep.verdict == "ok", rep.render()[:800]
    assert rep.n_review == 0
    # 证据要真的跑了（比对行数不能是 0 —— 空转通过等于没验）
    e3 = [e for e in rep.evidences if e.key == "E3"]
    assert e3 and all("比对 0 行" not in e.detail for e in e3), e3[0].detail


# ---------------------------------------------------------------- 元测试：必须会响

def test_meta_column_groups_mismatch_blocks():
    """栏数写错（单栏写成双栏）→ 路由不命中 → BLOCK。"""
    rep = _accept(lambda d: d["lab"].update(column_groups=2))
    assert rep.verdict == "block"
    assert any(e.key == "E1" and e.level == "block" for e in rep.evidences)


def test_meta_wrong_page_marker_blocks():
    rep = _accept(lambda d: d["lab"].update(page_marker="检验报告单"))
    assert rep.verdict == "block"
    assert any("化验页" in e.detail for e in rep.evidences if e.level == "block")


def test_meta_wrong_anchor_blocks():
    rep = _accept(lambda d: d.update(required_anchors=["不存在的医院"]))
    assert rep.verdict == "block"


def test_meta_value_column_swapped_fires_e3():
    """值列锚点换成提示列 → 双实现交叉必须报不一致（review）。"""
    rep = _accept(lambda d: d["lab"].update(header_cells=["项目", "提示", "参考值"]))
    assert rep.verdict == "review"
    e3 = [e for e in rep.evidences if e.key == "E3"]
    assert e3 and e3[0].level == "review" and len(e3[0].cards) >= 10


def test_meta_flag_column_pollution_fires_e3_with_advice():
    """提示列不再忽略 → 参考值被污染 → E3 报不一致 **且给出可执行建议**。"""
    rep = _accept(lambda d: d["lab"].update(flag_cells=[]))
    assert rep.verdict == "review"
    advices = [e.advice for e in rep.evidences if e.advice]
    assert any("flag_cells" in a for a in advices), advices


def test_meta_too_wide_section_column_blocks_via_baseline():
    """段标题列放宽到全页 → 抽漏一大半 → **模板基线**判定 BLOCK。

    （只有样本间比较会漏掉"所有样本一起抽漏"——这条专门盯它。）
    """
    rep = _accept(lambda d: d["lab"].update(section_x_max=590.0))
    assert rep.verdict == "block"
    e6 = [e for e in rep.evidences if e.key == "E6"]
    assert e6 and e6[0].level == "block" and "基线" in e6[0].detail


# ---------------------------------------------------------------- 双实现解析器

def test_row_items_parses_two_columns_per_row():
    toks = ["白细胞", "7.5", "3.5-9.510^9/L", "淋巴细胞百分比", "29.2", "20-50%"]
    items = A.row_items(toks)
    assert [i["name"] for i in items] == ["白细胞", "淋巴细胞百分比"], items
    assert items[0]["value"] == "7.5" and items[0]["ref"] == "3.5-9.510^9/L"
    assert items[1]["value"] == "29.2"


def test_row_items_keeps_latin_abbreviation_in_name():
    """拉丁缩写属于名称（WBC），不能当单位丢掉（曾因此对不上引擎的 item_raw）。"""
    items = A.row_items(["WBC", "白细胞", "6.8", "x10^9/L", "4.0-10.0", "正常"])
    assert items and items[0]["name"] == "WBC白细胞", items
    assert items[0]["value"] == "6.8" and items[0]["unit"] == "x10^9/L"


def test_row_items_returns_none_for_unclear_rows():
    """形态不典型（血压对值 138/88、无区间无数值）→ 不猜，返回空。"""
    assert A.row_items(["血压", "BP", "138/88", "mmHg", "<140/90", "正常"]) == []
    assert A.row_pattern(["项目", "结果", "参考值"]) is None


def test_row_items_qualitative_row_keeps_name():
    items = A.row_items(["尿蛋白", "阴性(-)"])
    assert items and items[0]["name"] == "尿蛋白" and items[0]["flag"] == "阴性(-)"


# ---------------------------------------------------------------- VLM 第二读者

class _FakeVlm:
    """假 VLM：按预设读行结果回答（测比对逻辑，不联网）。"""

    available = True

    def __init__(self, shift: float = 0.0):
        self.shift = shift

    def describe(self) -> str:
        return "fake"

    def read_row(self, png: bytes, key: str = ""):  # noqa: ANN001
        # 从 clip 文件名猜不到内容 —— 统一返回"与引擎一致"或"差 shift"
        return {"name": None, "value": None} if self.shift == 0 else {
            "value": 999.0}


def test_vlm_second_reader_agreement_passes():
    rep = A.accept(Template.from_dict(_base_dict()), _syn_pdfs(),
                   vlm=_FakeVlm())
    e7 = [e for e in rep.evidences if e.key == "E7"]
    assert e7 and e7[0].level in ("ok", "skip"), e7[0].detail


def test_vlm_second_reader_disagreement_fires_review():
    rep = A.accept(Template.from_dict(_base_dict()), _syn_pdfs(),
                   vlm=_FakeVlm(shift=1.0))
    e7 = [e for e in rep.evidences if e.key == "E7" and e.level == "review"]
    assert e7, "VLM 读数与引擎不一致时必须出对照卡"


def test_vlm_optional_skip_but_visible():
    """没请求 VLM → skip（**但带建议**，不是静默）；整个验收仍可 ok。"""
    rep = A.accept(Template.from_dict(_base_dict()), _syn_pdfs())
    e7 = [e for e in rep.evidences if e.key == "E7"]
    assert e7 and all(e.level == "skip" for e in e7)
    assert all(e.advice for e in e7), "可选跳过也必须给『怎么加一道复核』的建议"
    assert rep.verdict == "ok"


def test_vlm_requested_but_unavailable_must_prompt():
    """明确请求 VLM 却无通道 → **必须提示**（换视觉模型 / 配端点），不许静默跳过。"""
    rep = A.accept(Template.from_dict(_base_dict()), _syn_pdfs(),
                   vlm_required=True)
    e7 = [e for e in rep.evidences if e.key == "E7"]
    assert e7 and e7[0].level == "review"
    assert "视觉" in e7[0].advice and "CHECKUP_VLM" in e7[0].advice


# ---------------------------------------------------------------- 真实案例（需数据根）

def test_real_case_template_is_ok_if_mounted():
    """真模板 + 真报告（若本机挂载）→ 必须 ok（链子不能对真实数据误报）。"""
    sys.path.insert(0, BASE)
    from devpaths import FIXTURES  # noqa: PLC0415
    from checkup import intake  # noqa: PLC0415
    from checkup.templates import default_registry  # noqa: PLC0415
    root = FIXTURES.real_reports
    # list_pdfs 对不存在的目录会如实抛错（入口路径已先验证目录）；
    # 本测试是"若挂载"语义，故这里先判目录存在。
    pdfs = intake.list_pdfs(root) if root and os.path.isdir(root) else []
    tpls = [t for t in default_registry(BASE).templates if t.org_id == "NX-001"]
    if not pdfs or not tpls:
        import pytest  # noqa: PLC0415
        pytest.skip("真实报告/模板未挂载（作者本机回归用）")
    rep = A.accept(tpls[0], pdfs)
    assert rep.verdict == "ok", rep.render()[:900]


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed, skipped = [], 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except BaseException as e:  # noqa: BLE001
            if type(e).__name__ in ("Skipped", "DataMissing"):
                skipped += 1
                print(f"  · {name} 跳过：{e}")
                continue
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    done = len(tests) - skipped
    print(f"\n{done - len(failed)}/{done} 通过" + (f"（跳过 {skipped}）" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
