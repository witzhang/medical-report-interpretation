#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入库诊断器（A14）的回归测试。    python3 tests/test_onboard_scan.py

验收条件（工作单 A14）：**一条命令出草案 + ≤10 条待确认问题**，且问题清单与
"本次人工发现的版式差异"重合度 ≥80%。

这里的合成样本**刻意复刻本次 NXYD 案例的四类结构差异**：
① 化验页没有『报告单』字样（只有表头词）② 参考值与单位分列 ③ 主检页有专属字样
④ 检查方式写进项目名 + 多年汇总表要整页排除。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.autotemplate import draft_template  # noqa: E402
from checkup.console import init_console  # noqa: E402
from _synth import CJK  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from onboard_scan import build_questions, classify_pages  # noqa: E402


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="scan_"), name)


def _mk_case(path: str) -> str:
    """合成一份「本次案例那类机构」的报告（6 页，四类结构差异齐全）。"""
    doc = fitz.open()
    # p1 封面
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 60), "临河市第一人民医院", fontname=CJK, fontsize=22)
    pg.insert_text((60, 120), "姓名：张三  性别：男  年龄：45岁",
                   fontname=CJK, fontsize=11)
    # p2 主检
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 60), "主检结论及建议", fontname=CJK, fontsize=14)
    pg.insert_text((60, 90), "一、主要健康问题", fontname=CJK, fontsize=11)
    pg.insert_text((60, 115), "1.血脂异常，建议心内科评估，注意低脂饮食，"
                              "三个月后复查血脂。", fontname=CJK, fontsize=9)
    pg.insert_text((60, 135), "2.甲状腺结节4mm，建议12个月复查超声，"
                              "如有增大及时就诊。", fontname=CJK, fontsize=9)
    pg.insert_text((60, 160), "其他阳性结果：脂肪肝，建议消化内科就诊。",
                   fontname=CJK, fontsize=9)
    # p3 化验（无『报告单』字样；参考值与单位分列）
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), "临河市第一人民医院", fontname=CJK, fontsize=11)
    y = 110
    for x, t in ((124, "检验项目"), (297, "结果"), (347, "标志"),
                 (416, "参考值"), (519, "单位")):
        pg.insert_text((x, y), t, fontname=CJK, fontsize=9)
    y += 26
    pg.insert_text((60, y), "肝功能", fontname=CJK, fontsize=9)
    y += 20
    for name, val, flag, ref, unit in (
            ("丙氨酸氨基转移酶", "61", "↑", "0-40", "U/L"),
            ("天门冬氨酸氨基转移酶", "36", "", "0-40", "U/L"),
            ("总胆红素", "12.3", "", "3.4-20.5", "umol/L")):
        pg.insert_text((124, y), name, fontname=CJK, fontsize=9)
        pg.insert_text((297, y), val, fontsize=9)
        if flag:
            pg.insert_text((347, y), flag, fontname=CJK, fontsize=9)
        pg.insert_text((416, y), ref, fontsize=9)
        pg.insert_text((519, y), unit, fontsize=9)
        y += 18
    # p4 叙述（检查方式写在项目名里）
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 60), "甲状腺彩超", fontname=CJK, fontsize=11)
    pg.insert_text((60, 90), "双叶甲状腺形态饱满，右叶见低回声结节，"
                             "大小约4x3mm，边界清晰。", fontname=CJK, fontsize=9)
    pg.insert_text((60, 115), "小结", fontname=CJK, fontsize=11)
    pg.insert_text((60, 140), "甲状腺右叶结节，建议随访。", fontname=CJK, fontsize=9)
    # p5 历年检验结果汇总表（多年并排）
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 60), "历年检验结果汇总表", fontname=CJK, fontsize=12)
    pg.insert_text((60, 85), "体检日期 2025-08-25 2024-10-25 2023-06-11 "
                             "参考范围(本次）", fontname=CJK, fontsize=9)
    for i in range(12):
        pg.insert_text((60, 105 + i * 16), f"指标{i} 1.1 1.2 1.3 <10ng/mL",
                       fontname=CJK, fontsize=9)
    # p6 页码
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 400), "关爱健康倡导健康生活方式 第6页/共6页",
                   fontname=CJK, fontsize=9)
    doc.save(path)
    doc.close()
    return path


def _scan_and_questions(org: str = "SCAN-001"):
    p = _mk_case(_tmp("case.pdf"))
    docs = [fitz.open(p)]
    try:
        scan = classify_pages(docs)
        draft = draft_template([p], org_id=org)
        questions = build_questions([p], docs, scan, draft)
    finally:
        for d in docs:
            d.close()
    return p, scan, draft, questions


# ---------------------------------------------------------------- 页型清单

def test_page_kinds_are_classified_as_candidates():
    """页型清单：封面 / 主检 / 化验 / 叙述 / 汇总 / 页码 都要认出来。"""
    _p, scan, _draft, _qs = _scan_and_questions()
    kinds = [pg["kind"] for pg in scan[0]["pages"]]
    assert kinds[:4] == ["封面", "主检", "化验", "叙述"], kinds
    assert kinds[4] == "汇总", kinds                     # 多年并排 → 汇总（不是化验）
    assert kinds[5] == "页码/空白", kinds
    # 化验页那一条要给出依据（表头行内容）
    lab = scan[0]["pages"][2]
    assert lab["why"].startswith("表头行"), lab


def test_semantic_lab_rows_do_not_make_a_summary_page_a_lab_page():
    """汇总页虽然也有"名称+值+区间"，但**不能被当成化验页**（否则污染当年数据）。"""
    _p, scan, _draft, _qs = _scan_and_questions()
    summary = scan[0]["pages"][4]
    assert summary["kind"] == "汇总" and "多年并排" in summary["why"], summary


# ---------------------------------------------------------------- 问题清单

def test_questions_cover_the_known_structural_findings():
    """**验收条件**：问题清单覆盖本次人工发现的 4 类结构差异（重合度 ≥80%）。"""
    _p, _scan, _draft, qs = _scan_and_questions()
    assert len(qs) <= 10, len(qs)
    text = "\n".join(q["text"] for q in qs)
    findings = {
        "①化验页没有报告单字样（闸门改用表头词）": ("报告单" in text and "表头词" in text),
        "②参考值与单位分列（unit_cells）": "unit_cells" in text,
        "③主检页字样": "主检" in text,
        "④检查方式写进项目名（modality_aliases）": "modality_aliases" in text,
        "④多年汇总表整页排除": ("汇总" in text and "排除" in text),
    }
    hit = [k for k, ok in findings.items() if ok]
    assert len(hit) / len(findings) >= 0.8, (hit, text)


def test_every_question_has_a_default_answer():
    """无人值守纪律：**每条问题都带默认代入方案**，不阻塞等人。"""
    _p, _scan, _draft, qs = _scan_and_questions()
    assert qs
    assert all(q["default"].strip() for q in qs), qs
    # 优先级排序：1 在前
    prios = [q["priority"] for q in qs]
    assert prios == sorted(prios), prios


# ---------------------------------------------------------------- 一条命令

def test_cli_produces_draft_and_question_list():
    """一条命令出：诊断报告 + 模板草案 JSON + 问题清单（CLI 端到端）。"""
    import subprocess  # noqa: PLC0415
    p = _mk_case(_tmp("cli.pdf"))
    out = tempfile.mkdtemp(prefix="scan_out_")
    env = dict(os.environ)
    env["CHECKUP_OUT"] = out
    env["PYTHONIOENCODING"] = "utf-8"
    tool = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools", "onboard_scan.py")
    proc = subprocess.run([sys.executable, tool, p, "--org", "SCAN-CLI",
                           "--out", out],
                          cwd=out, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "待确认问题" in proc.stdout
    files = os.listdir(os.path.join(out, "onboard", "SCAN-CLI")) \
        if os.path.isdir(os.path.join(out, "onboard", "SCAN-CLI")) else os.listdir(out)
    assert any(f.endswith(".json") for f in files), files
    assert any("问题清单" in f for f in files), files
    assert any("扫描" in f for f in files), files


# ------------------------------------------------------------------ 主程序

def _main() -> int:
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
