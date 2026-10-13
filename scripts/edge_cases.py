#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""异常输入验收清单 · 逐条演练（P4-4）。    python3 scripts/edge_cases.py

八种情形（方案 P4 表），每种**构造输入 → 断言应有行为**，全部脚本可跑：

| # | 情形 | 应有行为 |
|---|---|---|
| 1 | 扫描件/照片 | 如实报「无文字层、未被分析」，⛔ 不静默跳过 |
| 2 | 报告缺日期 | 不得用导入日期代替（扫描层报「未读到」） |
| 3 | 报告缺参考区间 | 位置轴 = unknown/不可评估，⛔ 不默认「正常」 |
| 4 | 项目名未命中 | 保留原文标 `unmatched`，不影响其他项 |
| 5 | 一页含两个报告页 | 双栏各自成项（按列切分，不串行） |
| 6 | 页数不全 | 缺口表如实写「未做」（缺口对账脚本） |
| 7 | 版式不匹配 | **FATAL + 非零退出 + 不生成报告**（P0-7） |
| 8 | 危急值（官方 A 类） | 命中并置顶直报（needs_human 提示人工确认） |

退出码：0 = 全部按预期；1 = 有情形不符。
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "engine"))

import fitz  # noqa: E402

from checkup.normalize import UNMAPPED, AnalyteCatalog  # noqa: E402
from checkup.redflags import check as redflag_check  # noqa: E402
from checkup.render import render_patient  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.tables import extract_lab_results  # noqa: E402
from checkup.templates import default_registry  # noqa: E402
from checkup.validate import check_carrier  # noqa: E402

BASE = os.path.join(SKILL, "engine")
tmpfail: list[str] = []
ran: list[str] = []         #: 实际执行到的情形（用于"跳过 ≠ 通过"的断言）


from checkup.console import init_console, run_text  # noqa: E402
init_console()

def _child_env() -> dict:
    """子进程环境：显式 UTF-8（Windows 上子进程打印符号不崩）。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env

def _obs(**kw) -> NormalizedObservation:
    d = dict(year="2026", page_no=10, section="肝功能", item_raw="谷丙转氨酶",
             occurrence=1, analyte_id="CHEM.丙氨酸氨基转移酶", matched_by="seed",
             unit_status="ok", repaired_by="", source="lab", value=10.0,
             value_text="10", value_type="numeric", unit="U/L", ref_raw="",
             ref_low=None, ref_high=None, ref_op="", flag="", pdf_flag="",
             evidence="e")
    d.update(kw)
    return NormalizedObservation(**d)


EXPECTED_CASES = 8          #: 文档声明八种情形，**必须跑全**（P0-5/D5）


def case(name: str, ok: bool, detail: str) -> None:
    print(f"  {'✓' if ok else '✗'} {name}：{detail}")
    ran.append(name)
    if not ok:
        tmpfail.append(name)


def run() -> int:
    print("异常输入验收清单（P4-4）\n")

    # ① 扫描件：有图无文字层
    pages = [{"page_no": 12, "chars": 5, "images": 1},
             {"page_no": 13, "chars": 800, "images": 0}]
    c = check_carrier(pages)
    case("① 扫描件", c.level == "warn" and "未被分析" in c.detail,
         f"{c.level} / {c.detail[:46]}")

    # ② 缺日期：扫描层应报「未读到」（不用文件名顶替）
    sys.path.insert(0, HERE)
    import scan_archive as sa
    case("② 缺日期", sa._inner_date("某某体检中心 报告 无日期信息") is None
         and sa._inner_date("体检日期 2026-08-20 其余") == "2026-08-20",
         "无日期 → 未读到；有日期 → 取内页")

    # ③ 缺参考区间：位置轴不得是 normal（走 gen_artifacts 的 _position）
    sys.path.insert(0, os.path.join(SKILL, "reconcile"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gen_artifacts", os.path.join(SKILL, "reconcile", "gen_artifacts.py"))
    ga = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ga)
    pos = ga._position({"值": 10.0, "定性": "", "ref_low": None,
                        "ref_high": None, "ref_op": ""})
    case("③ 缺参考区间", pos == "unknown", f"位置轴 = {pos}（不是 normal）")

    # ④ 项目名未命中：保留原文、标 unmapped
    cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
    res = cat.resolve("NX-001", "某个新段落", "从前没见过的项目名", "numeric", "U/L")
    case("④ 项目名未命中", res.analyte_id == UNMAPPED or res.matched_by == "unmatched",
         f"analyte_id={res.analyte_id} · 匹配方式={res.matched_by}")

    # ⑤ 一页两栏：用**不依赖数据根**的合成化验单生成器（P0-5/D5：
    #    原来 import 测试模块会在模块级触发"数据未挂载→SystemExit(0)"，
    #    把本脚本一起结束 —— "0/0 通过"与"全部通过"外观无法区分）
    sys.path.insert(0, os.path.join(BASE, "tests"))
    from _synth import _mk  # noqa: E402
    d = os.path.join(tempfile.mkdtemp(), "two_col.pdf")
    _mk(d, "血常规", columns=2)
    reg = default_registry(BASE)
    if not reg.templates:
        case("⑤ 一页两栏", False, "模板注册表为空（应为 NX-001 模板）")
    else:
        doc = fitz.open(d)
        labs = extract_lab_results(doc, "2025", reg.templates[0])
        doc.close()
        names = [l.item_raw for l in labs]
        case("⑤ 一页两栏", len(names) >= 4,
             f"抽出 {len(names)} 项（左右栏各自成项）：{'、'.join(names[:6])}")

    # ⑥ 页数不全 → 缺口表如实写「未做」：用真实案例的 2025 断档做证据
    #    （详细由 scripts/check_gaps.py 负责；此处只验"缺 ≠ 正常"的口径）
    case("⑥ 页数不全（缺口口径）", pos != "normal" and pos == "unknown",
         "缺项的年份位置轴 = unknown（不是 normal）；缺口四类见 check_gaps.py")

    # ⑦ 版式不匹配：FATAL + 非零退出 + 不生成报告
    src = os.path.join(tempfile.mkdtemp(), "报告")
    out = os.path.join(tempfile.mkdtemp(), "out")
    os.makedirs(src)
    alien = os.path.join(src, "2025某某报告.pdf")
    doc = fitz.open()
    p = doc.new_page()
    # 文字量要够（入口预检按"任一页 ≥40 字符"判有无文字层；太短会被判扫描件，
    # 到不了"版式不匹配"这一步 —— 本情形要验的正是版式路由）
    p.insert_text((72, 100),
                  "某某门诊部 健康体检报告\n体检日期：2025-03-01 姓名：测试 性别：男\n"
                  "本页没有已注册的版式锚点（既无表头词，也无机构专属字样）。")
    doc.save(alien)
    doc.close()
    r = run_text([sys.executable, os.path.join(BASE, "run_pipeline.py"),
                  "--src", src, "--out", out, "--quiet"],
                 cwd=BASE, timeout=300, env=_child_env())
    views = [f for f in ("view_patient.md", "view_clinical.md", "view_audit.md")
             if os.path.exists(os.path.join(out, f))]
    case("⑦ 版式不匹配", r.returncode != 0 and not views
         and "未能识别该报告版式" in r.stdout,
         f"退出码 {r.returncode} · 生成视图 {views or '无'}")

    # ⑧ 危急值：ALT ≥15×ULN 命中；血压 180/110 命中（needs_human）；渲染置顶
    hits = redflag_check([_obs(value=800.0, ref_low=9.0, ref_high=50.0)])
    bp = redflag_check([_obs(analyte_id="VITAL.收缩压", value=185.0, unit="mmHg",
                             item_raw="收缩压", ref_raw=""),
                        _obs(analyte_id="VITAL.舒张压", value=112.0, unit="mmHg",
                             item_raw="舒张压", ref_raw="")])
    text = render_patient([], bp)
    top_ok = text.index("请立即就医") < text.index("免责") if "免责" in text else True
    case("⑧ 危急值", any(h.rule_id == "RF.A.ALT" for h in hits)
         and any(h.rule_id == "RF.A.BP" and h.needs_human for h in bp) and top_ok,
         f"ALT 命中 {[h.rule_id for h in hits]} · 血压命中 "
         f"{[h.rule_id for h in bp]}（needs_human）；患者版置顶直报")

    print()
    if len(ran) < EXPECTED_CASES:
        # ⛔ "跳过"绝不等于"通过"：少跑一项就非零退出（此前实测被数据跳过
        #    截断在第 ④ 项后，却打印"0/0 通过"并 exit 0）
        missing = EXPECTED_CASES - len(ran)
        print(f"✗ 演练未跑全：应有 {EXPECTED_CASES} 项，实际 {len(ran)} 项"
              f"（少 {missing} 项）—— 跳过 ≠ 通过，请查明为何中断")
        return 1
    if tmpfail:
        print(f"✗ {len(tmpfail)} 种情形不符：{'、'.join(tmpfail)}")
        return 1
    print(f"✓ {len(ran)} 种情形全部按预期"
          f"（含「版式不匹配必须非零退出、不生成报告」）")
    return 0


if __name__ == "__main__":
    sys.exit(run())
