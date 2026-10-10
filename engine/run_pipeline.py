#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：抽取 → 归一 → 校验 → 判定 → 组合 → 结论 → 三视图。

用法:
    python3 run_pipeline.py                          # 用默认的报告目录
    python3 run_pipeline.py --src /path/to/reports   # 指向别的报告目录
    python3 run_pipeline.py --org NX-001 --out out2  # 指定机构模板与输出目录
    python3 run_pipeline.py --quiet                  # 只写文件，不打印长报告

报告目录里放若干 **PDF**（一份报告一个文件）。**文件名只用于区分年份**
（必须含 4 位年份，如「2024 体检报告.pdf」）；体检日期与机构一律从**内页**读
（本机就有一个反例：文件名写 20260609、内页实际是 2026-08-20）。
"""
from __future__ import annotations

import csv
import json
import os
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checkup import intake  # noqa: E402
from checkup.compose import evaluate as compose_eval  # noqa: E402
from checkup.conclude import group_by_upstream, synthesize  # noqa: E402
from checkup.redflags import check as redflag_check  # noqa: E402
from checkup.render import lint, render_all  # noqa: E402
from checkup.compose import load_profile, Composer  # noqa: E402
from checkup.judge import judge_all  # noqa: E402
from checkup.narrative import extract_report  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all, year_from_name  # noqa: E402
from checkup.tables import lab_page_audit  # noqa: E402
from checkup.templates import default_registry  # noqa: E402
from checkup.validate import validate  # noqa: E402
from devpaths import FIXTURES, guard_out, default_out_dir  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))


from checkup.console import init_console  # noqa: E402
init_console()

def _parse_args():
    import argparse
    ap = argparse.ArgumentParser(
        prog="run_pipeline.py",
        description="体检报告端到端分析：PDF → 结论 + 三视图",
        epilog="报告目录里放 PDF（文件名需含 4 位年份）；日期与机构从内页读。")
    ap.add_argument("--src", default=None,
                    help="报告目录（含 PDF）；缺省用数据根（CHECKUP_FIXTURES）"
                         "下的报告目录，缺则报错并给用法")
    ap.add_argument("--out", default=None,
                    help="产物目录（缺省写数据根/用户工作目录，⛔ 不写 skill 目录）")
    ap.add_argument("--org", default=None,
                    help="强制使用某个机构模板（默认按版式自动匹配）")
    ap.add_argument("--catalog", default=os.path.join(BASE, "catalog",
                                                      "analytes.json"))
    ap.add_argument("--cvi", default=os.path.join(BASE, "catalog", "cvi.json"))
    ap.add_argument("--profile", default=None,
                    help="个体信息（吸烟/糖尿病/用药史等）——**缺失时不猜**；"
                         "缺省取 CHECKUP_PROFILE 环境变量，再退数据根下的 "
                         "profile.json（不存在则按未知处理）")
    ap.add_argument("--quiet", action="store_true",
                    help="只写文件，不打印长报告")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="接受『疑似 OCR 叠层』的输入（数据质量将标降级）")
    return ap.parse_args()



def main() -> None:
    a = _parse_args()
    SRC = intake.pick_src(a.src)
    OUT = guard_out(a.out or default_out_dir())
    #: 个体信息是**运行期输入**（问诊产物），不进 skill 目录；
    #: 缺省取 --profile > CHECKUP_PROFILE > 数据根下的 profile.json，
    #: 取不到就按"未知"处理（不猜）。
    PROFILE_DEFAULT = FIXTURES.profile if os.path.exists(FIXTURES.profile) else ""
    CATALOG, CVI = a.catalog, a.cvi
    PROFILE = a.profile or os.environ.get("CHECKUP_PROFILE") or PROFILE_DEFAULT
    quiet = a.quiet
    # **入口自动预检**：不看格式就硬跑，是"交错报告"的第一步
    intake.preflight_or_exit(SRC, a.allow_degraded)
    # `--quiet` 用重定向实现 —— 免得漏掉某一处 print 就漏了输出
    _real_stdout = sys.stdout
    if quiet:
        import io as _io
        sys.stdout = _io.StringIO()
    os.makedirs(OUT, exist_ok=True)
    catalog = AnalyteCatalog.load(CATALOG)
    registry = default_registry(BASE)
    routing: dict = {}
    per_year = build_all(SRC, catalog, registry=registry, routing=routing,
                         force_org=a.org)
    obs = [o for rows in per_year.values() for o in rows]
    print(f"归一后观测 {len(obs)} 条 · 年份 {sorted(per_year)}")
    print(f"模板注册表 {len(registry.templates)} 套："
          + "、".join(f"{t.org_id} v{t.template_version}"
                      for t in registry.templates))
    for y in sorted(routing):
        r = routing[y]
        print(f"  {y} → {r.template.org_id if r.template else 'UNKNOWN_LAYOUT'}"
              f"  {r.score:.2f}  {r.reason}")
    #: P0-7：版式匹配不上的年份 → 交校验层；0 观测时升级为 FATAL。
    unknown_layout = {y: r.reason for y, r in routing.items()
                      if r.template is None}
    if unknown_layout:
        print()
        print("!" * 100)
        print("⚠ 未能识别该报告版式（机构/版式未注册）—— 下列年份的数据未纳入分析：")
        for y in sorted(unknown_layout):
            print(f"    {y}：{unknown_layout[y]}")
        print("!" * 100)
    print()

    # ---- L3 校验（含 L1 抽取审计：判为化验页却 0 项 → fatal）
    audit = []
    for fn in sorted(os.listdir(SRC)):
        if fn.lower().endswith(".pdf"):
            tpl_year = year_from_name(fn) or "?"
            tpl = routing.get(tpl_year).template if tpl_year in routing else None
            if tpl is None:      # 版式未识别：不抽（P0-2：不许回落默认模板）
                continue
            _d = fitz.open(os.path.join(SRC, fn))
            for _a in lab_page_audit(_d, tpl):
                _a["year"] = year_from_name(fn) or "?"
                audit.append(_a)
            _d.close()
    # 采样日期/间隔（D11）要在校验与判定之前拿到
    metas, all_find, all_narr = {}, [], []
    for fn in sorted(os.listdir(SRC)):
        if fn.lower().endswith(".pdf"):
            y = intake.require_year(fn)
            tpl = routing.get(y).template if y in routing else None
            if tpl is None:      # 版式未识别：不抽（P0-2）
                continue
            m, narr, _, find = extract_report(os.path.join(SRC, fn), y, tpl)
            metas[y] = m
            all_find.extend(find)
            all_narr.extend(narr)

    # 漂移监控用**已匹配模板**的抽取基线
    baseline: dict = {}
    for r in routing.values():
        if r.template and r.template.stats:
            baseline = r.template.stats
            break
    # G0 载体：逐页记「字符数 / 图片数」，交给校验层判断
    pages: list[dict] = []
    for fn in sorted(os.listdir(SRC)):
        if fn.lower().endswith(".pdf"):
            _d = fitz.open(os.path.join(SRC, fn))
            for _i in range(_d.page_count):
                _p = _d[_i]
                pages.append({"page_no": _i + 1,
                              "chars": len(_p.get_text().strip()),
                              "images": len(_p.get_images())})
            _d.close()
    rep = validate(obs, audit=audit, baseline=baseline, metas=metas,
                   pages=pages, unknown_layout=unknown_layout)
    print("=" * 100)
    print("【L3 校验】")
    print("=" * 100)
    print(rep.render())
    with open(os.path.join(OUT, "validation.json"), "w", encoding="utf-8") as f:
        json.dump({"level": rep.level,
                   "checks": [c.__dict__ for c in rep.checks]},
                  f, ensure_ascii=False, indent=2)

    # ---- P0-7：FATAL → 中止后续所有层，非零退出。
    # 宁可停下来说"我没读懂"，也不交一份读起来像"没发现问题"的报告。
    if rep.level == "fatal":
        if quiet:
            sys.stdout = _real_stdout
        print()
        print("!" * 100)
        print("✗ FATAL：本次未能读取任何数据 —— 后续所有层已中止，不生成报告。")
        for c in rep.checks:
            if c.level == "fatal":
                print(f"    {c.name}：{c.detail}")
        print("  处置：为该机构版式起草模板（draft_template.py）并人工确认后重跑；")
        print("       或用 --org 显式指定已注册的机构模板。")
        print("!" * 100)
        raise SystemExit(2)

    # ---- L4 判定
    js = judge_all(obs, CVI, catalog=catalog, metas=metas)
    # 年份序列**从数据推导**（P0-3）：下面从控制台摘要到 CSV 列名都据此展开，
    # 不再写死 2024/2025/2026（他人年份不同会得到空列/漏结论）。
    years = sorted({y for j in js for y in j.series})
    first, last = (years[0], years[-1]) if years else ("", "")
    last_pair = f"{first}→{last}"
    pairs: list[str] = []
    for j in js:
        for k in j.change_pairs():
            if k not in pairs:
                pairs.append(k)
    pairs.sort(key=lambda k: (k.split("→")[0], k.split("→")[-1]))
    print()
    print("=" * 100)
    print(f"【L4 判定 · 位置轴】{last} 年仍超出参考区间的")
    print("=" * 100)
    ab = [j for j in js if j.position.get(last) == "abnormal"]
    for j in sorted(ab, key=lambda x: x.analyte_id):
        print(f"  {j.canonical_name:<16}{j.series.get(last)!s:>9}  "
              f"{j.ref_range.get(last, ''):<18}{j.unit:<8}{j.evidence.get(last,'')}")

    print()
    print("=" * 100)
    print(f"【L4 判定 · 变化轴】{first}→{last} 超出噪声的")
    print("=" * 100)
    beyond = [j for j in js if j.change.get(last_pair) == "beyond_noise"]
    year_head = "".join(f"{y:>8}" for y in years)
    print(f"  {'项目':<16}{'CVi%':>7}{'RCV%':>7}  {year_head}"
          f"  {f'Δ{first}→{last}':>9}  {'稳健性':<12}研究数")
    for j in sorted(beyond, key=lambda x: -abs(x.change_pct.get(last_pair) or 0)):
        vals = "".join(f"{str(j.series.get(y, '')):>8}" for y in years)
        print(f"  {j.canonical_name:<16}{j.cv_i or 0:>7.1f}{j.rcv_threshold or 0:>7.1f}  "
              f"{vals}  "
              f"{j.change_pct.get(last_pair) or 0:>+9.1f}  {j.robustness:<12}"
              f"{j.n_studies}")

    within = [j for j in js if j.change.get(last_pair) == "within_noise"]
    print(f"\n  无法与随机波动区分: {len(within)} 项")

    # 真实间隔（D11）：阈值不变，但敏感度随间隔变
    if js and js[0].intervals:
        print()
        print("=" * 100)
        print("【L4 判定 · 采样间隔】RCV 阈值不随间隔变，但敏感度随间隔变")
        print("=" * 100)
        j0 = js[0]
        for k, v in sorted(j0.intervals.items()):
            note = j0.interval_notes.get(k, "与名义步长相当")
            print(f"  {k}: {v} 个月 —— {note}")
        long_pairs = [(k, v) for k, v in j0.intervals.items()
                      if "偏长" in j0.interval_notes.get(k, "")]
        for k, v in sorted(long_pairs):
            print(f"\n  ⚠ {k} 间隔偏长（{v} 个月）→ 这一年更**容易漏判**："
                  f"落在阈值内侧的变化未必不是真变化")
    noref = [j for j in js if not j.rcv_threshold]
    print(f"  无 CVi 不做变化轴判定: {len(noref)} 项")

    # ---- 落盘
    with open(os.path.join(OUT, "judgements.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["analyte_id", "项目", "段落", "单位", "CVi%", "RCV阈值%",
                    "稳健性", "研究数",
                    *years,
                    *[f"位置{y}" for y in years],
                    *[f"区间{y}" for y in years],
                    *[f"{p}%" for p in pairs],
                    *[f"判{p}" for p in pairs],
                    "备注"])
        for j in sorted(js, key=lambda x: x.analyte_id):
            w.writerow([
                j.analyte_id, j.canonical_name, j.section, j.unit,
                j.cv_i if j.cv_i is not None else "",
                j.rcv_threshold if j.rcv_threshold is not None else "",
                j.robustness, j.n_studies if j.n_studies is not None else "",
                *[j.series.get(y, "") for y in years],
                *[j.position.get(y, "") for y in years],
                *[j.ref_range.get(y, "") for y in years],
                *[j.change_pct.get(p, "") for p in pairs],
                *[j.change.get(p, "") for p in pairs],
                " | ".join(j.notes)])

    # ---- L5 组合与分层（metas / findings / narratives 已在上方收集）
    profile = load_profile(PROFILE)
    derived = compose_eval(Composer(js, metas, profile))

    print()
    print("=" * 100)
    print("【L5 组合与分层】")
    print("=" * 100)
    order = ["COMPOSITE.NON_HDL_C", "COMPOSITE.AIP", "RENAL.EGFR_CKDEPI2021",
             "RENAL.CKD_STAGE",
             "HEPATIC.FIB4", "METSYN.CDS2004", "METSYN.GD2007", "METSYN.IDF2005",
             "ASCVD.CHINAPAR", "LIPID.RISK_STRATIFICATION", "LIPID.LDL_TARGET"]
    for rid in order:
        rows = [d for d in derived if d.rule_id == rid]
        if not rows:
            continue
        print(f"\n  ▼ {rows[0].name}")
        for d in sorted(rows, key=lambda x: x.year):
            v = "" if d.value is None else f" = {d.value}{(' ' + d.unit) if d.unit else ''}"
            print(f"     {d.year}: {d.band}{v}")
            print(f"            {d.text}")
            for n in d.notes:
                print(f"            ⚠ {n}")

    with open(os.path.join(OUT, "derived.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "规则", "年份", "值", "单位", "分层",
                    "状态", "缺失输入", "输入", "说明", "备注"])
        for d in derived:
            w.writerow([d.rule_id, d.name, d.year, d.value if d.value is not None else "",
                        d.unit, d.band, d.status, "、".join(d.missing),
                        json.dumps(d.inputs, ensure_ascii=False),
                        d.text, " | ".join(d.notes)])

    # ---- L6 结论合成
    cons = synthesize(js, derived, validation=rep, findings=all_find,
                      narratives=all_narr, profile=profile)
    print()
    print("=" * 100)
    print("【L6 结论合成】按共同上游归并")
    print("=" * 100)
    labels = {"confirmed": "✔ 已确认", "to_verify": "? 待核查",
              "to_followup": "→ 待随访", "data_quality": "! 数据质量"}
    for up, items in group_by_upstream(cons).items():
        print(f"\n  ◆ 共同上游：{up}")
        for c in items:
            print(f"    [{labels.get(c.cls, c.cls)}] {c.text}")
            if c.action_hint:
                print(f"            → {c.action_hint}")
            for e in c.evidence[:2]:
                if e:
                    print(f"            · {e[:78]}")

    from collections import Counter
    print(f"\n  合计 {len(cons)} 条：" +
          " · ".join(f"{labels.get(k, k)} {v}" for k, v in
                     Counter(c.cls for c in cons).most_common()))

    with open(os.path.join(OUT, "conclusions.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "分类", "共同上游", "结论", "不确定性",
                    "断言类型", "建议动作", "规则", "证据"])
        for c in cons:
            w.writerow([c.conclusion_id, c.cls, c.upstream, c.text,
                        c.uncertainty, c.assertion_type, c.action_hint,
                        "、".join(c.rule_refs), " | ".join(c.evidence)])

    # ---- L7 输出层
    red = redflag_check(obs, profile=profile)
    notices = []
    if unknown_layout:
        notices.append(
            "⚠ **本次有报告未能读取**（未能识别该报告版式/机构未注册）："
            + "、".join(sorted(unknown_layout))
            + " —— 这些年份的数据**未纳入本说明**。这不是「没有异常」。")
    views = render_all(cons, js, derived, red, validation=rep, notices=notices)
    for name, text in views.items():
        with open(os.path.join(OUT, f"view_{name}.md"), "w",
                  encoding="utf-8") as f:
            f.write(text)

    print()
    print("=" * 100)
    print("【L7 输出层】")
    print("=" * 100)
    if red:
        print("  ⚠ 红旗命中（强制就医提示，不得软化）：")
        for h in red:
            print(f"    {h.year} {h.label} = {h.value} {h.unit}")
    else:
        print("  ✓ 红旗：本次三年数据**未命中**任何 A 类阈值")
    bad = lint(cons)
    print(f"  ✓ 措辞分层检查：{'全部合规（无诊断性措辞）' if not bad else bad}")
    for name in views:
        print(f"  · view_{name}.md")

    if quiet:
        sys.stdout = _real_stdout
    print(f"\n已写入 {OUT}/：report_*.json · validation.json · judgements.csv · "
          f"derived.csv · conclusions.csv · view_*.md")


if __name__ == "__main__":
    main()
