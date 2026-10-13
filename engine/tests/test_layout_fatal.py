#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0-2 / P0-7：版式不匹配必须**显式失败**。    python3 tests/test_layout_fatal.py

锁死三件事（全部来自方案 P0-2 / P0-7）：

1. **禁止回落内置模板** —— 注册表为空/目录缺失时不得内置兜底；
   ``template=None`` 必须报错，不许静默套别的机构版式。
2. **UNKNOWN_LAYOUT 且 0 观测 → FATAL** —— 校验层要报 fatal，且该检查**置顶**
   （不许沉在「数据本身存疑的」小节里，读起来像"没发现问题"）。
3. **端到端：换机构必须非零退出** —— ``run_pipeline.py`` 对版式不匹配的目录
   必须非零退出，且中止后续所有层（不生成三视图）。

本测试**不依赖挂载数据**（自造 PDF）。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.console import run_text  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.templates import TemplateRegistry, require_template  # noqa: E402
from checkup.validate import validate  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ALIEN_TEXT = ("某某综合门诊部 健康体检报告 2025-03-01\n"
               "本页没有任何已注册的版式锚点，也没有可识别的表头。\n")


def _alien_pdf(path: str) -> str:
    """造一份"有文字层但版式不匹配"的 PDF。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), _ALIEN_TEXT)
    doc.save(path)
    doc.close()
    return path


# ------------------------------------------------------------ ① 禁止回落

def test_empty_registry_has_no_builtin_fallback():
    reg = TemplateRegistry.load(os.path.join(tempfile.mkdtemp(), "nope"))
    assert reg.templates == [], "空目录不得回落内置模板（P0-2）"
    d = os.path.join(tempfile.mkdtemp(), "x.pdf")
    _alien_pdf(d)
    doc = fitz.open(d)
    try:
        res = reg.match(doc)
        assert res.template is None
        assert "UNKNOWN_LAYOUT" in res.reason
    finally:
        doc.close()


def test_require_template_refuses_none():
    try:
        require_template(None)
    except ValueError as e:
        assert "不内置任何默认版式" in str(e)
        return
    raise AssertionError("require_template(None) 必须显式报错（P0-2）")


def test_require_routed_template_exits_on_unmatched():
    from checkup.pipeline import require_routed_template
    d = os.path.join(tempfile.mkdtemp(), "2025某某报告.pdf")
    _alien_pdf(d)
    try:
        require_routed_template(d, TemplateRegistry([]))
    except SystemExit as e:
        assert e.code != 0
        return
    raise AssertionError("匹配不上必须报错退出，不许猜")


def test_build_all_yields_no_observations_for_unmatched():
    """未匹配年份 → 0 观测（而不是拿默认模板硬抽出一些东西）。"""
    tmp = tempfile.mkdtemp()
    _alien_pdf(os.path.join(tmp, "2025某某报告.pdf"))
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    per = build_all(tmp, cat, registry=TemplateRegistry([]))
    assert per == {"2025": []}, per


# ------------------------------------------------------------ ② 校验层 FATAL

def test_validate_fatal_and_top_for_unknown_layout_with_zero_obs():
    pages = [{"page_no": 1, "chars": 200, "images": 0}]
    rep = validate([], pages=pages,
                   unknown_layout={"2025": "最高分 0.00 < 0.50 → UNKNOWN_LAYOUT"})
    assert rep.level == "fatal", rep.level
    first = rep.checks[0]
    assert "版式路由" in first.name, "FATAL 检查必须置顶"
    assert "未能识别该报告版式" in first.detail, first.detail
    assert "本次未能读取任何数据" in first.detail, first.detail


def test_validate_warn_when_partial_data_read():
    """有一部分数据读到时：warn（不 fatal），但同样置顶说明。"""
    from checkup.schema import NormalizedObservation
    obs = [NormalizedObservation(
        year="2024", page_no=1, section="血常规", item_raw="白细胞",
        occurrence=1, analyte_id="BLD.白细胞", matched_by="x",
        unit_status="ok", repaired_by="", source="lab", value=5.0,
        value_text="5.0", value_type="numeric", unit="10^9/L",
        ref_raw="3.5-9.5", ref_low=3.5, ref_high=9.5, ref_op="range",
        flag="normal", pdf_flag="", evidence="e")]
    rep = validate(obs, pages=[{"page_no": 1, "chars": 200, "images": 0}],
                   unknown_layout={"2025": "UNKNOWN_LAYOUT"})
    assert rep.level == "warn", rep.level
    assert "版式路由" in rep.checks[0].name
    assert "未纳入本次分析" in rep.checks[0].detail


# ------------------------------------------------------------ ③ 端到端非零退出

def test_run_pipeline_exits_nonzero_and_aborts_on_unmatched_layout():
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "报告")
    out = os.path.join(tmp, "out")
    os.makedirs(src)
    _alien_pdf(os.path.join(src, "2025某某报告.pdf"))
    p = run_text(
        [sys.executable, os.path.join(BASE, "run_pipeline.py"),
         "--src", src, "--out", out, "--quiet"],
        cwd=BASE, timeout=300)
    assert p.returncode != 0, (p.returncode, p.stdout[-500:], p.stderr[-500:])
    assert "未能识别该报告版式" in p.stdout, p.stdout[-800:]
    # 中止后续所有层：不得生成三视图（否则报告读起来像"没发现问题"）
    for fn in ("view_patient.md", "view_clinical.md", "view_audit.md"):
        assert not os.path.exists(os.path.join(out, fn)), fn


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
            failed.append((name, e))
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
