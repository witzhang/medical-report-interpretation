# -*- coding: utf-8 -*-
"""L1 · 通道 A：化验单表格抽取。

版面假设**全部来自模板**（``templates.Template``）——组数、表头字样、
段标题列、噪声模式都可以配，换机构不必改代码。

报告形态::

    检验项目@57  结果@173  参考值@240 | 检验项目@327  结果@439  参考值@506

左/右（或单栏）各三个列锚点，按 x 归属；段标题独立在标签列且与该段签名行同行。

已踩过的坑（都有对应测试）
--------------------------
1. **计数单位被吃进区间**：``3.5-9.510^9/L`` 会被切成区间 ``3.5-9.510``。
2. **同名指标撞键**：尿液检查里两项都叫「白细胞」。键必须是（段, 项目, 第几次）。
3. **签名行混入指标**：审核医生/检验者/审核日期。
4. **段落在续页不重复**：第二页没有「尿液检查」标题，段落状态需跨页继承。
5. **段标题列与项目列只差 14pt**（x≈47 vs ≈61）。这是对一家机构版式的
   **硬假设**，现已提到模板里；判定失灵时靠抽后审计兜底。
"""
from __future__ import annotations

import re
from typing import Optional

import fitz

from .layout import Row, rows_of_page
from .schema import Finding, LabResult
from .templates import Template, compile_patterns, require_template


def _split_ref(ref_raw: str) -> tuple[str, str, Optional[float], Optional[float], str]:
    """把参考值原文切成 (区间文本, 单位, 下界, 上界, 比较符)。

    难点：计数单位写作 ``10^9/L``，紧跟在区间上限后面，会被误当成数字的一部分
    （``3.5-9.510^9/L`` → 区间 ``3.5-9.510``）。先把 ``10^n`` 隔开再切。
    """
    s = ref_raw.strip().replace(" ", "")
    s = re.sub(r"(10\^[-\d]+)", r" \1", s)
    m = re.match(r"^([<>≤≥]?)(\d+(?:\.\d+)?)(?:\s*[-~—]\s*(\d+(?:\.\d+)?))?\s*(.*)$", s)
    if not m:
        return s, "", None, None, ""
    op, a, b, unit = m.group(1), m.group(2), m.group(3), m.group(4)
    op = {"≤": "<=", "≥": ">="}.get(op, op)
    if b is not None:
        return f"{a}-{b}", unit, float(a), float(b), ""
    if op:
        lo = float(a) if op in (">", ">=") else None
        hi = float(a) if op in ("<", "<=") else None
        return f"{op}{a}", unit, lo, hi, op
    return a, unit, None, None, ""


def _to_number(t: str) -> Optional[float]:
    try:
        return float(t)
    except (TypeError, ValueError):
        return None


def _calc_flag(value, low, high, op) -> Optional[Finding]:
    """由「数值 vs 参考区间」自行判定——**不信任报告里画的箭头**。"""
    if value is None:
        return None
    if low is not None and high is not None:
        return Finding.NORMAL if low <= value <= high else Finding.ABNORMAL
    if low is not None:
        return Finding.NORMAL if value >= low else Finding.ABNORMAL
    if high is not None:
        return Finding.NORMAL if value <= high else Finding.ABNORMAL
    return None


def _header_anchors(row: Row, template: Template):
    """从表头行取出**若干组**列锚点与组分界线（+ 可选的"提示列"锚点）。

    组数由 ``template.lab.column_groups`` 决定：本机构是 2（双栏），
    单栏机构配 1 即可，**不用改代码**。第四列（提示/状态）由
    ``template.lab.flag_cells`` 声明，配了就把它的 x 一并返回。
    """
    n = max(1, int(template.lab.column_groups))
    cells = [w for w in row.words if w.text in set(template.lab.header_cells)]
    if len(cells) < 3 * n:
        return None
    groups = [[cells[3 * i + j].x0 for j in range(3)] for i in range(n)]
    bounds = [(groups[i][2] + groups[i + 1][0]) / 2 for i in range(n - 1)]
    flag_x = None
    flag_cells = set(template.lab.flag_cells or ())
    if flag_cells:
        for w in row.words:
            if w.text in flag_cells:
                flag_x = w.x0
                break
    return groups, bounds, flag_x


def _group_of(x: float, groups, bounds) -> int:
    """x 落在第几组。"""
    for i, b in enumerate(bounds):
        if x < b:
            return i
    return len(groups) - 1


def _is_section_row(head: str, x0: float, template: Template, noise) -> bool:
    """段标题行判定：首词落在段标题列、够短、不含数字/冒号/箭头。

    这是**几何启发式**，与项目名只差十几 pt。判定失灵（项目名也落在这一列）
    会把整页项目当成段标题丢掉——所以必须有 ``lab_page_audit`` 兜底。
    装饰线包住的段名（``===== 血常规 =====``）走 ``_decorated_section``。
    """
    return bool(head and x0 <= template.lab.section_x_max
                and head not in set(template.lab.section_stop)
                and not noise.match(head) and len(head) <= 8
                and not re.search(r"[\d:：↑↓]", head))


#: 段标题两端的装饰线：``===== 血常规 =====``／``---- 肝功能 ----``／``── 一般检查 ──``。
#: 段名限定为**非装饰字符**——PDF 里装饰线与段名常常连在一起、
#: 抽出的文本没有空格（``--------------------一般项目--------------------``）。
_SECTION_DECOR = re.compile(
    r"^[=\-—─·*_~]{3,}\s*(?P<name>[^=\-—─·*_~\s]+)\s*[=\-—─·*_~]{3,}$")


def _decorated_section(text: str) -> str:
    """装饰线包住的段标题 → 段名；不是这种行返回 ``""``。

    真实机构的报告大量用分隔线把段名"框"出来（本合成数据两家机构都是这样）。
    这种行的**首词是装饰线**，几何启发式（首词短、落在段标题列）认不出来，
    整行会被当成数据行抽成 ``--------------------``。取到段名后仍做
    "够短、不含数字/冒号/箭头"的守卫，避免把表头/数据行误判成段标题。
    """
    m = _SECTION_DECOR.match(text.strip())
    if not m:
        return ""
    name = m.group("name").strip()
    if not name or len(name) > 12 or re.search(r"[\d:：↑↓]", name):
        return ""
    return name


def _section_of(row: Row, template: Template, noise) -> str:
    """整行判定段标题 → 段名；不是段标题行返回 ``""``。

    先认装饰线形态，再退回首词几何启发式（无装饰线的裸段标题）。
    """
    deco = _decorated_section(row.text)
    if deco:
        return deco
    first = row.first.strip()
    if _is_section_row(first, row.x0, template, noise):
        return first
    return ""


def extract_lab_results(doc, year: str,
                        template: Template) -> list[LabResult]:
    """抽一份报告全部化验单页。"""
    template = require_template(template, "tables.extract_lab_results")
    noise = compile_patterns(template)["lab_noise"]
    n_grp = max(1, int(template.lab.column_groups))
    out: list[LabResult] = []

    # 段名与同名计数**跨页继承**——已踩：化验单第二页不重复「尿液检查」标题，
    # 若每页重置，续页的项会落到空段名下，既撞键又丢归属。
    section = ""
    seen_in_section: dict = {}

    for pno in range(doc.page_count):
        page = doc[pno]
        if template.lab.page_marker not in page.get_text():
            continue

        anchors = None
        pending: list = [None] * n_grp

        for row in rows_of_page(page, pno + 1):
            sec = _section_of(row, template, noise)
            if sec:
                # 段标题（与该段签名行同行，故只取段名）。
                # **不要清空 anchors**：表头只在页首出现一次，段标题在其后，
                # 清空会导致整页除首段外全部抽不到。
                section = sec
                seen_in_section = {}
                pending = [None] * n_grp
                continue

            if noise.search(row.text.strip()):
                continue

            hdr = _header_anchors(row, template)
            if hdr is not None:
                anchors = hdr
                continue
            if anchors is None:
                continue

            groups, bounds, flag_x = anchors
            cells = [{"name": [], "val": [], "ref": []} for _ in range(n_grp)]
            flags = [""] * n_grp
            for w in row.words:
                # 提示/状态列（第四列）的词不参与抽取：旗标由引擎自算，
                # 报告画的箭头/字母只作交叉参考，不采信、也不混进参考值。
                if flag_x is not None and min(
                        abs(w.x0 - a) for grp in groups for a in grp
                ) > abs(w.x0 - flag_x):
                    continue
                if w.text in ("↑", "↓"):
                    flags[_group_of(w.x0, groups, bounds)] += w.text
                    continue
                g = _group_of(w.x0, groups, bounds)
                ci = min(range(3), key=lambda i: abs(w.x0 - groups[g][i]))
                cells[g][["name", "val", "ref"][ci]].append(w.text)

            for g in range(n_grp):
                raw_name = "".join(cells[g]["name"])
                raw_val = "".join(cells[g]["val"])
                # 箭头可能独立成词（已被 flags 收走），也可能与名字/数值合成
                # 一个词（如 ``↓甘油三酯``）。两种都留下供交叉比对。
                arrow = "".join(c for c in (raw_name + raw_val) if c in "↑↓")
                name = raw_name.strip("↑↓ ").strip()
                val = raw_val.strip("↑↓ ").strip()
                ref = "".join(cells[g]["ref"]).strip()
                flag = flags[g] or arrow

                if not name:
                    # 断行回填：只有值/参考值，挂到上一项。
                    # 回填后必须重算派生字段——记录在创建时已 _finalize 过。
                    p = pending[g]
                    if p is not None:
                        rec = p["rec"]
                        if val and not p["result_raw"]:
                            p["result_raw"] = rec.result_raw = val
                        if ref and not p["ref_raw"]:
                            p["ref_raw"] = rec.ref_raw = ref
                        if flag and not p["pdf_flag"]:
                            p["pdf_flag"] = rec.pdf_flag = flag
                        _finalize(rec)
                    continue

                seen_in_section[name] = seen_in_section.get(name, 0) + 1
                rec = LabResult(
                    year=year, page_no=pno + 1, section=section, item_raw=name,
                    occurrence=seen_in_section[name], result_raw=val,
                    ref_raw=ref, pdf_flag=flag)
                _finalize(rec)
                out.append(rec)
                pending[g] = {"result_raw": val, "ref_raw": ref,
                              "pdf_flag": flag, "rec": rec}

    return out


#: 「数值 + 单位」挤在结果列（"149 g/L"、"6.8 x10^9/L"、"5.9 %"）。
#: 单位不得以 ``/`` 或 ``-`` 开头（那是血压对值 ``138/88 mmHg``
#: 或区间 ``5-10`` 的尾巴），单位里允许含数字（``x10^9/L``）。
_VALUE_WITH_UNIT = re.compile(r"^([<>≤≥]?)(\d+(?:\.\d+)?)\s*([^\d/–—-][^\s]*(?:/\S+)?)$")


def _finalize(rec: LabResult) -> None:
    """补齐派生字段：数值、区间、单位、自算标记。"""
    _, unit, low, high, op = _split_ref(rec.ref_raw)
    rec.ref_low, rec.ref_high, rec.ref_op = low, high, op
    rec.unit_raw = unit
    rec.value = _to_number(rec.result_raw)
    if rec.value is None and rec.result_raw:
        # 参考列没写单位、单位跟在数值后面（多家机构都这么排）→ 从结果列取回。
        # 只认"数值 + 可读单位"的形状；血压对值（138/88）与区间（5-10）不在此列。
        m = _VALUE_WITH_UNIT.match(rec.result_raw.strip())
        if m and not rec.unit_raw:
            rec.value = float(m.group(2))
            rec.unit_raw = m.group(3).strip()
    if rec.value is None and rec.result_raw:
        rec.value_type = "qualitative"
    rec.flag = _calc_flag(rec.value, low, high, op)


def lab_page_audit(doc, template: Template) -> list[dict]:
    """逐页审计：**判为化验页的页面各抽到多少项**。

    为什么需要它：``extract_lab_results`` 在新版式下可能**一项都抽不到**却
    不报任何错。若是同一机构的历史报告，覆盖率校验还能发现"这一年项数异常"；
    但**新机构的第一份报告没有基线**，0 项会被当成"这份报告没有化验数据"，
    静默通过。这是最危险的一类失败：错得毫无征兆。
    """
    template = require_template(template, "tables.lab_page_audit")
    noise = compile_patterns(template)["lab_noise"]
    n_grp = max(1, int(template.lab.column_groups))
    out: list[dict] = []

    for pno in range(doc.page_count):
        page = doc[pno]
        if template.lab.page_marker not in page.get_text():
            continue
        anchors = None
        n = 0
        for row in rows_of_page(page, pno + 1):
            # 审计与抽取必须用**同一套行过滤**（段标题 + 噪声行），否则
            # "基线项数"会把叙述/结论行也算进来 → 漂移监控对着错数字报警。
            if _section_of(row, template, noise):
                continue
            if noise.search(row.text.strip()):
                continue
            hdr = _header_anchors(row, template)
            if hdr is not None:
                anchors = hdr
                continue
            if anchors is None:
                continue
            groups, bounds, flag_x = anchors
            names = [[] for _ in range(n_grp)]
            for w in row.words:
                if w.text in ("↑", "↓"):
                    continue
                if flag_x is not None and min(
                        abs(w.x0 - a) for grp in groups for a in grp
                ) > abs(w.x0 - flag_x):
                    continue
                g = _group_of(w.x0, groups, bounds)
                if min(range(3), key=lambda i: abs(w.x0 - groups[g][i])) == 0:
                    names[g].append(w.text)
            for g in range(n_grp):
                nm = "".join(names[g]).strip("↑↓ ").strip()
                if nm and nm not in set(template.lab.section_stop) \
                        and not noise.match(nm):
                    n += 1
        out.append({"page_no": pno + 1, "kind": "lab", "items": n,
                    "has_header": anchors is not None})
    return out
