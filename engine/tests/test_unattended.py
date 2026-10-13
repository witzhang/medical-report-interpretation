#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批次 5「无人值守与体验」的回归测试（C1/C2/C3/C4/C5）。    python3 tests/test_unattended.py

覆盖四件"把人从回答者降为抽检者"的交付物：

* **C1 机器默认决定清单** —— 决定 + 依据 + 不确定标记，进验收报告与档案；
* **C2 无人值守问诊处理单** —— `ask_queue.py` 每条问题都带"无人应答时的默认代入"；
* **C3 抽检单** —— ≤5 条（机器决定 + 依据 + 对照原文页），可复现；
* **C5 写作前置自检** —— 从 `reconcile.py` **自动提取**规则，能预警写作前就能避免的错。
（C4 是文档类：SKILL.md 的 KPI 段落 + 三个新工具在命令速查里可查 —— 一并断言。）
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(ENGINE, "tools"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from checkup.console import init_console  # noqa: E402


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="unatt_"), name)


# ------------------------------------------------------------------ C1

def test_draft_decisions_list_every_machine_default():
    """C1：草稿的机器默认决定清单 = 决定 + 依据 + 不确定标记（依据来自 evidence）。"""
    sys.path.insert(0, os.path.join(ENGINE, "tests"))
    from test_autotemplate import _mk_lab5, _tmp as ttmp  # noqa: PLC0415
    from checkup.autotemplate import draft_template  # noqa: PLC0415
    p = _mk_lab5(ttmp("c1.pdf"), pages=2, sig_row=True)
    d = draft_template([p], org_id="C1-001")
    dec = d.decisions()
    assert dec, "决定清单不能为空"
    assert all({"决定", "依据", "不确定"} <= set(x) for x in dec), dec
    # 有待确认问题时，清单里必须出现"不确定"标记
    assert any(x["不确定"] for x in dec), dec
    # 每个决定都要有依据（"我替你定的"必须能说出为什么）
    assert all(str(x["依据"]).strip() for x in dec)


def test_report_renders_decisions_section():
    """C1：验收报告渲染"机器默认决定清单"区块，且进 to_dict()（档案留痕）。"""
    from checkup import accept as A  # noqa: PLC0415
    from checkup.templates import Template  # noqa: PLC0415
    SYN = os.path.join(SKILL, "examples", "testdata")
    pdfs = [os.path.join(SYN, f) for f in sorted(os.listdir(SYN))
            if f.endswith(".pdf")]
    tpl = Template.from_dict(json.load(open(os.path.join(
        ENGINE, "catalog", "templates", "SYN-002.json"), encoding="utf-8")))
    dec = [{"决定": "page_marker", "依据": "每张化验页都有", "不确定": False},
           {"决定": "（待确认项：已按默认代入继续）", "依据": "标志列只含箭头",
            "不确定": True}]
    rep = A.accept(tpl, pdfs, decisions=dec)
    md = rep.render()
    assert "机器默认决定清单" in md and "⚠ 是" in md, md[:400]
    assert rep.to_dict()["decisions"] == dec
    # 不传 decisions（手写模板路径）→ 不出现该区块、JSON 里也不留空字段
    rep2 = A.accept(tpl, pdfs)
    assert "机器默认决定清单" not in rep2.render()
    assert "decisions" not in rep2.to_dict()


# ------------------------------------------------------------------ C2

def test_ask_queue_every_question_has_a_default_substitution():
    """C2：问诊队列**每条**都带"无人应答时的默认代入"（不阻塞等人）。"""
    from ask_queue import build  # noqa: PLC0415
    empty = tempfile.mkdtemp()          # 空产物目录：只剩 A 类标准问诊项
    q = build(empty)
    assert q, "至少要给出标准问诊项"
    miss = [i["问题"] for i in q if not i.get("默认代入", "").strip()]
    assert not miss, f"缺默认代入：{miss}"
    # 默认代入要含"敏感性"（若回答不同会怎样），而不只是"用默认值"
    assert all("敏感性" in i["默认代入"] or "就高不就低" in i["默认代入"]
               or "无法核实" in i["默认代入"] for i in q), \
        [i["默认代入"] for i in q]


def test_unattended_intake_reference_exists_and_says_dont_block():
    """C2：无人值守问诊处理单模板在位，且写明"不阻塞等人"。"""
    path = os.path.join(SKILL, "references", "unattended-intake.md")
    assert os.path.exists(path), path
    txt = open(path, encoding="utf-8").read()
    assert "不阻塞" in txt and "默认代入" in txt and "敏感性" in txt


# ------------------------------------------------------------------ C3

def _fake_out(root: str) -> str:
    """造一份最小产物目录（观测 + 判读 + 机构发现），供抽检单用。"""
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "normalized_observations.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["年份", "来源", "段落", "项目原文",
                                          "analyte_id", "匹配方式", "值原文",
                                          "数值", "单位", "单位状态", "修复规则",
                                          "参考区间", "备注", "页"])
        w.writeheader()
        for i in range(6):
            w.writerow({"年份": "2024" if i % 2 else "2026", "来源": "化验",
                        "段落": "血常规", "项目原文": f"项目{i}",
                        "analyte_id": f"BLD.{i}", "匹配方式": "rule",
                        "值原文": f"{i}.5", "数值": f"{i}.5", "单位": "g/L",
                        "单位状态": "ok", "修复规则": "", "参考区间": "1-9",
                        "备注": "", "页": str(3 + i)})
    with open(os.path.join(root, "judgements.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["analyte_id", "项目", "段落", "单位",
                                          "CVi%", "RCV阈值%", "稳健性", "研究数",
                                          "2024", "2026", "位置2024", "位置2026",
                                          "区间2024", "区间2026", "2024→2026%",
                                          "判2024→2026", "备注"])
        w.writeheader()
        w.writerow({"项目": "项目i", "稳健性": "稳健在内", "2024": "1.0",
                    "2026": "2.4", "2024→2026%": "140", "判2024→2026":
                    "beyond_noise", "位置2026": "high"})
    with open(os.path.join(root, "institution_findings.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["年份", "标题", "说明与建议", "页"])
        w.writeheader()
        w.writerow({"年份": "2024", "标题": "左乳结节", "说明与建议": "建议3-6个月复查",
                    "页": "4"})
    return root


def test_spot_check_is_at_most_five_items_with_three_parts():
    """C3：抽检单 ≤5 条，每条三件齐全（机器决定 / 依据 / 对照原文页）。"""
    from spot_check import MAX_ITEMS, build  # noqa: PLC0415
    out = _fake_out(tempfile.mkdtemp(prefix="spot_"))
    items = build(out, seed=0)
    assert 0 < len(items) <= MAX_ITEMS, len(items)
    for it in items:
        assert it["机器决定"] and it["依据"] and it["对照原文页"], it
        assert "页" in it["对照原文页"], it      # 必须能回到原文页
    # 可复现：同 seed 同清单
    assert [i["对照原文页"] for i in build(out, seed=0)] == \
           [i["对照原文页"] for i in items]


# ------------------------------------------------------------------ C5

def test_write_checklist_extracts_rules_from_reconcile_source():
    """C5：规则**从 reconcile.py 源码提取**（不复制口径）—— 检查/禁止正则/词表/窗口。"""
    from write_checklist import extract_from_source  # noqa: PLC0415
    spec = extract_from_source(os.path.join(SKILL, "reconcile", "reconcile.py"))
    ids = {c["id"] for c in spec["checks"]}
    assert {"S05b", "S10", "C04", "G10"} <= ids, ids
    assert spec["bans"], "必须能提取到禁止正则（如 G10 的固定频率建议）"
    assert any("彩超" in v for v in spec["tables"].values()), spec["tables"]
    assert 16 in spec["tolerances"], spec["tolerances"]     # 邻接窗口


def test_write_checklist_flags_the_three_known_mistakes():
    """C5：本次实际踩过的三类错都能在**写作前**被预警（注入即命中）。"""
    from write_checklist import extract_from_source, run_checks  # noqa: PLC0415
    spec = extract_from_source(os.path.join(SKILL, "reconcile", "reconcile.py"))
    # ① 影像发现带了测量值但没带方式词（对账 S05b 会判不过）
    specs = {"m.json": json.dumps({"sections": [{"groups": [{"items": [
        "甲状腺：2024「低回声结节 5.8 mm」→ 2026「弥漫性病变」"]}]}]},
        ensure_ascii=False)}
    report = ("## 附录·缺口表\n- 缺口：粪便常规+隐血 —— 正常\n"
              "- 前列腺特异抗原 PSA：0.8 ng/mL\n")
    found = run_checks(report, spec, specs=specs, female=True)
    ids = {f["id"] for f in found if f["级别"] == "warn"}
    assert {"S05b", "C04", "I03"} <= ids, [(f["id"], f["级别"]) for f in found]
    # 合规文本不误报：方式紧挨发现、缺口不带结论、男性报告不提 PSA 之外
    ok = run_checks("缺口：粪便常规+隐血 —— 缺（2024 未做）\n",
                    spec, specs={"m.json": json.dumps(
                        {"sections": [{"groups": [{"items": [
                            "甲状腺结节 5.8 mm（彩超）"]}]}]},
                        ensure_ascii=False)}, female=True)
    assert [f for f in ok if f["级别"] == "warn"] == [], ok


def test_write_checklist_flags_missing_ab_declaration():
    """C5+：声明 v6.4+ 的报告缺「A/B 类声明」时，**写作前**自检就要预警。

    来历（真实事故）：一次交付漏了这句声明，而当时六道定稿门槛全绿
    （对账/结构回归当时都不查它）。判据**复用** `scripts/validate_report.py`
    的实现（单一来源，不复制逻辑）。
    """
    from write_checklist import extract_from_source, run_checks  # noqa: PLC0415
    spec = extract_from_source(os.path.join(SKILL, "reconcile", "reconcile.py"))
    head = "## 一、总体评估\n- 版本：**v6.4**\n\n### 4. 问题分级总览\n本次没有红色项。\n"
    decl = ("⚠️ **本报告的「红」不等于官方 A/B 类危急值**（血压 ≥180/110 伴急性症状）"
            "——那是机构的紧急召回口径。\n")
    f_no = run_checks(head, spec, specs={}, female=True)
    f_yes = run_checks(head + decl, spec, specs={}, female=True)
    assert any(x["id"] == "v6.4·A/B声明" for x in f_no), f_no
    assert not any(x["id"] == "v6.4·A/B声明" for x in f_yes), f_yes
    # 不声明版本（v6.3 语义）→ 不追溯
    f_v63 = run_checks("## 一、总体评估\n### 4. 问题分级总览\n", spec,
                       specs={}, female=True)
    assert not any(x["id"] == "v6.4·A/B声明" for x in f_v63), f_v63


# ------------------------------------------------------------------ C4（文档）

def test_skill_documents_zero_touch_kpi_and_new_tools():
    """C4：SKILL.md 写明 KPI「零人工介入完成率」并在命令速查里给出新工具入口。"""
    txt = open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8").read()
    assert "零人工介入完成率" in txt
    for tool in ("onboard_scan.py", "accept_template.py", "write_checklist.py",
                 "spot_check.py"):
        assert tool in txt, tool
    assert "unattended-intake.md" in txt or "无人值守问诊处理单" in txt


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
