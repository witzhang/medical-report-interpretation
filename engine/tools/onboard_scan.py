#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新机构入库诊断器（A14）：**一条命令**出「页型清单 + 候选结构 + 模板草案 + 待确认问题」。

    python3 engine/tools/onboard_scan.py <报告.pdf> [更多.pdf ...] \
        --org NEW-001 --name "某某医院" [--search] [--out <目录>]

它替代的是"人工读源码/拷问 PDF 定位版式差异"这一段（本案例实测约 50 分钟）。
输出四件：

1. **页型清单** —— 化验 / 叙述 / 主检 / 封面 / 页码 / 汇总（每页一条 + 证据）。
   ⚠️ 全部是**候选**：判定交给 E1–E6 验收链，这里只负责"提候选 + 说依据"。
2. **每类的候选结构** —— 表头词、列锚点（x 直方图）、段标题分界、栏数、单位/标志列。
3. **模板草案 JSON** —— ``draft_template()`` 的产出；``--search`` 时改用求解器
   （枚举候选变体逐款跑验收链，取全绿者）。
4. **待确认问题 ≤10** —— 按优先级排序，每条带"无人应答时的默认代入方案"
   （无人值守纪律：不阻塞等人，只把不确定标记出来）。

退出码：0 = 出完；1 = 连页型都扫不出（版式不认识，先看诊断输出）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

import fitz  # noqa: E402

from checkup import autotemplate as AT  # noqa: E402
from checkup import intake  # noqa: E402
from checkup.layout import rows_of_page  # noqa: E402
from checkup.templates import NarrativeTemplate  # noqa: E402

from checkup.console import init_console  # noqa: E402
init_console()

#: 主检报告页的常见字样（判断"这页像不像主检页"，不用于抽取）
MAIN_HINTS = ["主检", "结论", "健康问题", "阳性结果", "建议"]
#: 叙述页的检查方式词（模板默认那一套）
MODALITIES = NarrativeTemplate().narrative_sections
_PROSE = re.compile(r"[\u4e00-\u9fff]{4,}")
_MAX_QUESTIONS = 10


# ------------------------------------------------------------------ 页型候选

def classify_pages(docs: list) -> list[dict]:
    """逐页给出**页型候选** + 证据（不判定，只提候选）。"""
    lab = {(di, pi): n for di, pi, n in AT.semantic_lab_pages(docs)}
    out: list[dict] = []
    for di, doc in enumerate(docs):
        pages = []
        for i in range(doc.page_count):
            text = doc[i].get_text()
            rows = rows_of_page(doc[i], i + 1)
            n_chars = len(text.strip())
            n_rows = len(rows)
            hit = AT._pick_header_row(rows)
            max_size = 0.0
            for size, _t in AT._page_spans(doc[i]):
                max_size = max(max_size, size)
            prose = sum(1 for r in rows if _PROSE.search(r.text)
                        and len(r.text) >= 15)
            digits_rows = sum(
                1 for r in rows
                if sum(1 for w in r.words if re.match(r"^\d+(\.\d+)?$", w.text)) >= 4)
            multi_year_rows = sum(
                1 for r in rows if len(re.findall(r"20\d{2}", r.text)) >= 2)
            kind, why = "其他", ""
            if hit is not None:
                kind, why = "化验", f"表头行 {hit[1]}"
            elif i == 0 and (max_size >= 18 or re.search(
                    r"姓名|性别|年龄|体检号|报告编号", text)):
                # 封面先判：它也可能只有两三行（身份栏），别被"页码/空白"吃掉
                kind, why = "封面", f"首面，最大字号 {max_size}pt，含身份栏"
            elif n_rows <= 3 and n_chars < 40:
                kind, why = "页码/空白", f"{n_rows} 行 / {n_chars} 字"
            elif (multi_year_rows >= 1 and n_rows >= 10) or digits_rows >= 2:
                # 多年并排/汇总表：一行里出现 ≥2 个年份（历年值并排），
                # 或 ≥2 行含 ≥4 个独立数值。混进当年数据里是静默错，必须单独标出来。
                kind, why = ("汇总",
                             (f"{multi_year_rows} 行含 ≥2 个年份（多年并排）"
                              if multi_year_rows else
                              f"{digits_rows} 行含 ≥4 个数值列") + "，共 "
                             + f"{n_rows} 行 → 历年汇总表候选")
            elif (di, i) in lab:
                kind, why = "化验", f"内容语义：{lab[(di, i)]} 行『名称+值+区间』（无表头行）"
            elif sum(1 for h in MAIN_HINTS if h in text) >= 2 and prose >= 2:
                kind, why = "主检", ("含 " + "、".join(
                    h for h in MAIN_HINTS if h in text) + f"，叙述行 {prose}")
            elif any(m in text for m in MODALITIES) or prose >= 3:
                mods = [m for m in MODALITIES if m in text]
                kind, why = "叙述", (f"检查方式字样 {mods[:4]}；叙述行 {prose}")
            elif prose >= 1:
                kind, why = "叙述", f"叙述行 {prose}"
            pages.append({"doc": di, "page": i + 1, "kind": kind, "why": why,
                          "rows": n_rows, "chars": n_chars,
                          "has_header": hit is not None})
        out.append({"file": None, "pages": pages})
    return out


# ------------------------------------------------------------------ 问题清单

def _q(priority: int, text: str, default: str) -> dict:
    """一条待确认问题：``priority`` 越小越靠前；``default`` = 无人应答时的默认代入。"""
    return {"priority": priority, "text": text, "default": default}


def build_questions(pdf_paths: list[str], docs: list, scan: list[dict],
                    draft) -> list[dict]:
    """把扫描结果 + 草稿信息汇成 **≤10 条**待确认问题（每条带默认代入方案）。"""
    qs: list[dict] = []
    kinds: dict[str, list[str]] = {}
    where: dict[str, list[tuple[int, int]]] = {}
    for fi, (f, path) in enumerate(zip(scan, pdf_paths)):
        base = os.path.basename(path)
        for p in f["pages"]:
            kinds.setdefault(p["kind"], []).append(f"{base} 第{p['page']}页")
            where.setdefault(p["kind"], []).append((fi, p["page"]))

    # ① 化验页闸门
    marker = draft.template.lab.page_marker
    if marker and marker not in AT.GENERIC_MARKERS:
        qs.append(_q(1, f"化验页的闸门词落成**表头词** {marker!r}（该机构化验页上没有"
                       f"『报告单/检验单』这类通用字样；旧判据在这里会 conf=0）",
                     f"沿用 {marker!r}；若该机构另有更专属的页眉词，换掉"))
    elif not marker:
        qs.append(_q(1, "化验页闸门词没定下来 → 抽取会整页跳过",
                     "手工指定一个『每张化验页都有』的通用短词"))
    # ② 单位/标志列（A5 的分级结论直出）
    if draft.template.lab.unit_cells:
        qs.append(_q(1, f"第 4/5 列提名 unit_cells={draft.template.lab.unit_cells}"
                       f"（参考值与单位分列）→ 确认「哪一列是单位」",
                     f"沿用 {draft.template.lab.unit_cells}"))
    for question in draft.open_questions:
        if "flag_cells" in question or "既不像单位列也不像提示列" in question:
            qs.append(_q(2, question, "留空（不猜）：不确定的列一律不进模板"))
    # ③ 空基线
    if not (draft.template.stats or {}).get("items_per_report"):
        qs.append(_q(2, "模板回抽 0 项 or 未建立基线 → **漂移监控未启用**",
                     "先修 page_marker/栏数再回抽；否则该机构改版无法自动发现"))
    # ④ 主检页字样
    if kinds.get("主检"):
        qs.append(_q(2, f"第 {kinds['主检']} 页像**主检报告页** → 主检字样用哪种"
                       f"（决定主检通道抽哪几页）",
                     "把该页里重复出现的字样（如『主检结论及建议』）填进 "
                     "main_report_markers"))
    # ⑤ 检查方式写进项目名 / 别名
    if kinds.get("叙述"):
        firsts = []
        for di, page_no in where["叙述"]:
            for r in rows_of_page(docs[di][page_no - 1], page_no)[:40]:
                head = r.first.strip()
                if (any(m in head for m in MODALITIES) and head
                        not in MODALITIES):
                    firsts.append(head)
        if firsts:
            qs.append(_q(3, f"**检查方式写在项目名里**（如 {sorted(set(firsts))[:3]}）"
                           f"→ 覆盖判定需要 modality_aliases 折算",
                         "按首次出现的项目名逐条登记别名（不改代码）"))
    # ⑥ 多年汇总页
    if kinds.get("汇总"):
        qs.append(_q(3, f"第 {kinds['汇总']} 页像**多年并排/汇总表** → 是否整页排除"
                       f"（混进来会污染当年数据）",
                     "先排除：page_exclude_markers 填该页的标题字样"))
    # ⑦ 越界候选页（A3 的语义候选没被标记词覆盖）
    for question in draft.open_questions:
        if "没被标记词覆盖" in question or "页集自愈" in question:
            qs.append(_q(4, question, "先按候选页处理，人工确认后再改标记词"))
    # ⑧ 栏数/段列
    qs.append(_q(4, f"栏数推断 {draft.template.lab.column_groups} 栏、段标题分界 "
                   f"section_x_max={draft.template.lab.section_x_max}"
                   f"（{'；'.join(draft.evidence.get('column_consistency', '').split('；')[:2])}）"
                   f" → 确认「段标题列与项目列的分界」",
                 "沿用草稿值；列位自检低于 60% 的页按问题清单逐页看"))
    # ⑨ 机构名锚点
    qs.append(_q(5, f"机构专属锚点 required_anchors={draft.template.required_anchors}"
                   f"（缺它别家报告可能被误配）",
                 "沿用；若为空，补一个『每份样本都出现』的机构字样"))
    # ⑩ 套餐/基线口径
    qs.append(_q(5, "该机构**套餐是否多变**（项数会随之变）→ 决定基线按套餐分组还是整体",
                 "先按整体基线；出现同机构项数大偏差时再按套餐分组（D3）"))
    qs.sort(key=lambda x: x["priority"])
    return qs[:_MAX_QUESTIONS]


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="新机构入库诊断：页型清单 + 候选结构 + 草案 + 问题清单")
    ap.add_argument("pdfs", nargs="+", help="同机构样本 PDF（≥1，建议 2-3 份）")
    ap.add_argument("--org", default="NEW-001")
    ap.add_argument("--name", default="")
    ap.add_argument("--search", action="store_true",
                    help="直接用求解器（枚举变体跑验收链取全绿者）出草案")
    ap.add_argument("--out", default="", help="输出目录（缺省 <产物根>/onboard/<org>）")
    args = ap.parse_args()

    verdicts = [intake.classify(p) for p in args.pdfs]
    blocked = [v for v in verdicts if not v.usable]
    for v in verdicts:
        print(f"{'✓' if v.usable else '✗'} {os.path.basename(v.path)} —— {v.label}：{v.reason}")
    if blocked:
        print("\n✗ 有文件不可用，先按提示处理：")
        for v in blocked:
            print(f"   · {os.path.basename(v.path)}：{v.next_step}")
        return 1

    docs = [fitz.open(p) for p in args.pdfs]
    try:
        scan = classify_pages(docs)
    finally:
        for d in docs:
            d.close()
    for f in scan:
        for p in f["pages"]:
            print(f"   p{p['page']:>2}  {p['kind']:<6} {p['why']}")

    draft = None
    search_res = None
    if args.search:
        from checkup.autotemplate import search_template  # noqa: PLC0415
        search_res = search_template(args.pdfs, org_id=args.org,
                                     org_name=args.name or "")
        draft = search_res.draft
        template = search_res.template or (draft.template if draft else None)
        print(f"\n[求解器] 试跑 {len(search_res.trials)} 款 · 全绿 "
              f"{len(search_res.green)} 款 · 采用 {search_res.chosen}"
              f"（试跑表见诊断报告）")
    else:
        from checkup.autotemplate import draft_template  # noqa: PLC0415
        draft = draft_template(args.pdfs, org_id=args.org, org_name=args.name or "")
        template = draft.template

    if draft is None:
        print("✗ 连草稿都产不出 —— 见上面的页型清单")
        return 1

    docs = [fitz.open(p) for p in args.pdfs]
    try:
        questions = build_questions(args.pdfs, docs, scan, draft)
    finally:
        for d in docs:
            d.close()

    from devpaths import default_out_dir, guard_out  # noqa: PLC0415
    out_dir = guard_out(args.out or os.path.join(default_out_dir(), "onboard",
                                                args.org))
    os.makedirs(out_dir, exist_ok=True)

    md: list[str] = [f"# 入库诊断 · {args.org}", "",
                     f"- 样本：{len(args.pdfs)} 份（{'、'.join(os.path.basename(p) for p in args.pdfs)}）",
                     "", "## 页型清单（候选）", "",
                     "| 文件 | 页 | 页型（候选） | 依据 |", "|---|---|---|---|"]
    for f, path in zip(scan, args.pdfs):
        for p in f["pages"]:
            md.append(f"| {os.path.basename(path)} | {p['page']} | {p['kind']} "
                      f"| {p['why']} |")
    md += ["", "## 候选结构（草稿依据）", ""]
    for k, v in (draft.evidence or {}).items():
        md.append(f"- **{k}**：{v}")
    if draft.open_questions:
        md += ["", "### 草稿分级与待确认字段", ""]
        md.append(f"- 分级：**{draft.status_label}**（置信度 {draft.confidence:.2f}）")
        md += [f"- ⚠ {n}" for n in draft.notes]
        md += [f"- ？ {q}" for q in draft.open_questions]
    md += ["", f"## 待确认问题（{len(questions)} 条，≤{_MAX_QUESTIONS}）", "",
           "> 每条都带**无人应答时的默认代入方案** —— 不阻塞等人，只把不确定标记出来。", ""]
    for i, q in enumerate(questions, 1):
        md.append(f"{i}. {q['text']}")
        md.append(f"   - 默认代入：{q['default']}")
    if search_res is not None:
        md += ["", "## 求解器试跑", "", search_res.render()]
    md_path = os.path.join(out_dir, f"{args.org}_扫描.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    tpl_path = os.path.join(out_dir, f"{args.org}.json")
    if template is not None:
        template.save(tpl_path)
    qs_path = os.path.join(out_dir, f"{args.org}_问题清单.md")
    with open(qs_path, "w", encoding="utf-8") as f:
        for i, q in enumerate(questions, 1):
            f.write(f"{i}. {q['text']}\n   - 默认代入：{q['default']}\n")

    print()
    print("=" * 88)
    print("待确认问题（≤10，含无人应答时的默认代入）")
    print("=" * 88)
    for i, q in enumerate(questions, 1):
        print(f"  {i}. {q['text']}")
        print(f"     默认代入：{q['default']}")
    print()
    print(f"诊断报告：{md_path}")
    print(f"模板草案：{tpl_path}")
    print(f"问题清单：{qs_path}")
    print("→ 下一步：tools/accept_template.py --pdfs … --template "
          f"{tpl_path} [--search]（验收链全绿才落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
