# -*- coding: utf-8 -*-
"""版式类库（D1）：按"系统族"找最像的模板，**差量**初始化新机构。

为什么可行（调研结论）：同厂商体检系统（天方达/蓝网等的"预制模板 + 机构定制"）
出来的报告版式高度同构 —— 列锚点、表头词、页型分布都只差几处。于是新机构
初始化不必从零开始：

    命中同类 → 以最像的模板为**基底**，只改差异（锚点/表头词/页尺寸）→ 差量草案
    相似度低于硬阈值 → **强制全量初始化并报错**（延续"不许硬套"）

相似度 = 列锚点集合相似度 + 表头词集合重叠 + 页型分布相似（三项加权）。
**分数只用于提名，不用于放行**：差量草案照样要过 E1–E6 全绿（``accept_template``
的求解器会把它当一个候选去跑）。

产出永远是草案（``confirmed=False``）：类库只负责"少改几处"，不负责"改对"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .layout import rows_of_page
from .templates import Template, TemplateRegistry

#: 相似度硬阈值：低于它**不提名**类库差量，强制全量初始化（"不许硬套"）
FAMILY_THRESHOLD = 0.62
#: 三项权重（列序最重：它决定"数据能不能落到正确的列"）
W_ANCHORS, W_HEADERS, W_PAGES = 0.45, 0.35, 0.20
#: 页型分布用的页型标签（与 onboard_scan 的页型清单同源）
PAGE_KINDS = ("lab", "narrative", "summary", "cover", "blank")


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _anchor_set(t: Template) -> set[str]:
    """列锚点集合（**语义化**：把锚点写成"列角色"，让不同 x 的同行列也能对上）。

    实测版式同族的机构：列名一致（检验项目/结果/参考值）、x 各有偏移 ——
    所以"列角色集合"比"绝对 x"更能反映同族性；具体 x 由差量步骤自己算。
    """
    s = {f"item:{c}" for c in t.lab.header_cells}
    s |= {f"flag:{c}" for c in (t.lab.flag_cells or [])}
    s |= {f"unit:{c}" for c in (t.lab.unit_cells or [])}
    s.add(f"groups:{t.lab.column_groups}")
    return s


def page_kinds(doc, template: Template) -> dict[str, int]:
    """页型分布（粗判）：化验/叙述/汇总/封面/空白各几页。"""
    kinds = dict.fromkeys(PAGE_KINDS, 0)
    for i in range(doc.page_count):
        text = doc[i].get_text()
        rows = rows_of_page(doc[i], i + 1)
        if len(rows) <= 3 and len(text.strip()) < 40:
            kinds["blank"] += 1
        elif template.lab.page_marker and template.lab.page_marker in text:
            kinds["lab"] += 1
        elif len(re.findall(r"20\d{2}", text)) >= 3:
            kinds["summary"] += 1
        elif i == 0:
            kinds["cover"] += 1
        else:
            kinds["narrative"] += 1
    return kinds


def doc_header_tokens(doc) -> list[tuple[int, list[str]]]:
    """文档自己的**表头行**：``[(页号, 表头词), …]``（用内置收敛词表认）。

    类库匹配只认"文档自己长出来的表头行"，不拿模板的词表去套文档 ——
    后者会自我循环：随便一页有「结果」二字就算命中（实测把 2 栏机构错配到
    1 栏模板上，相似度 0.93）。
    """
    from .autotemplate import _pick_header_row
    out: list[tuple[int, list[str]]] = []
    for i in range(doc.page_count):
        hit = _pick_header_row(rows_of_page(doc[i], i + 1))
        if hit is not None:
            out.append((i + 1, hit[1]))
    return out


def _expected_cols(t: Template) -> int:
    """模板期望的表头列数 = 3×栏数 + 单位列 + 标志列。"""
    return (3 * max(1, int(t.lab.column_groups)) + len(t.lab.unit_cells or [])
            + len(t.lab.flag_cells or []))


def similarity(base: Template, doc) -> tuple[float, dict]:
    """候选模板与**这份文档**的相似度（0–1）+ 三项明细（供对照卡）。

    三项（都用"文档自己的表头行"做对照，避免自我循环）：

    ① **列序一致性**：document 表头行的词数 vs 模板期望列数（相同 = 1，差 1 = 0.5）；
    ② **表头词 Jaccard**：文档表头词集合 vs 模板的列名词集合（含单位/标志列）；
    ③ **页型分布**：文档里化验页占比（模板认得出来的比例）。
    """
    kinds = page_kinds(doc, base)
    n = max(1, doc.page_count)
    lab_ratio = kinds["lab"] / n
    got = doc_header_tokens(doc)
    doc_tokens = max((toks for _p, toks in got), key=len, default=[])
    want_cols = _expected_cols(base)
    delta = abs(len(doc_tokens) - want_cols)
    col_score = 1.0 if delta == 0 else (0.5 if delta == 1 else 0.0)
    base_tokens = set(base.lab.header_cells) | set(base.lab.unit_cells or ()) \
        | set(base.lab.flag_cells or ())
    head_j = _jaccard(set(doc_tokens), base_tokens)
    same_org = any(a and a in "".join(doc[i].get_text()
                                      for i in range(doc.page_count))
                   for a in (base.required_anchors or []))
    detail = {
        "列序一致性": round(col_score, 2),
        "表头词 Jaccard": round(head_j, 2),
        "页型分布（化验页占比）": round(lab_ratio, 2),
        "文档表头词": doc_tokens,
        "模板列名词": sorted(base_tokens),
        "同机构锚点命中": same_org,
        "锚点集合（语义）": sorted(_anchor_set(base)),
    }
    score = (W_ANCHORS * col_score + W_HEADERS * head_j + W_PAGES * lab_ratio
             + (0.1 if same_org else 0.0))
    return round(min(score, 1.0), 3), detail


@dataclass
class FamilyHit:
    """类库提名结果。"""

    base: Optional[Template]
    score: float
    detail: dict
    reason: str
    forced_full: bool = False       #: True = 低于硬阈值，强制全量初始化


def find_family(reg: TemplateRegistry, doc,
                threshold: float = FAMILY_THRESHOLD) -> FamilyHit:
    """在类库里找最像的模板；低于硬阈值 → 不提名（强制全量初始化）。"""
    best, best_score, best_detail = None, 0.0, {}
    for t in reg.templates:
        s, d = similarity(t, doc)
        if s > best_score:
            best, best_score, best_detail = t, s, d
    if best is None or best_score < threshold:
        return FamilyHit(None, best_score, best_detail,
                         f"最像的是 {best.org_id if best else '—'}（{best_score:.2f}）"
                         f"< 硬阈值 {threshold} → **强制全量初始化**",
                         forced_full=True)
    return FamilyHit(best, best_score, best_detail,
                     f"命中同类：{best.org_id}（相似度 {best_score:.2f}；"
                     f"{best_detail}）")


def delta_template(base: Template, fresh: Template,
                   changed_by: str = "auto") -> tuple[Template, list[str]]:
    """**差量生成**：以 ``base`` 为基底，只把 ``fresh``（新机构自己起草的草案）
    里**确实不同**的字段搬过来；返回 (差量模板, 差异清单)。

    搬什么（其余一律保留 base 的值）：

    * ``required_anchors`` / ``header_anchor_strings``（机构专属字样必须换）；
    * ``lab.page_marker`` / ``header_cells`` / ``unit_cells`` / ``flag_cells``
      / ``column_groups`` / ``section_x_max``（版式锚点）；
    * ``section_stop``；
    * ``page_size``（纸型不同必须换）；
    * ``stats``（基线随新机构重算，不继承别家的项数）。
    """
    t = Template.from_dict(base.to_dict())
    changes: list[str] = []

    def _set(attr: str, new, old) -> None:
        if new == old:
            return
        changes.append(f"{attr}: {old!r} → {new!r}")

    _set("required_anchors", fresh.required_anchors, t.required_anchors)
    t.required_anchors = list(fresh.required_anchors)
    _set("header_anchor_strings", fresh.header_anchor_strings,
         t.header_anchor_strings)
    t.header_anchor_strings = list(fresh.header_anchor_strings)
    for attr in ("page_marker", "header_cells", "unit_cells", "flag_cells",
                 "column_groups", "section_x_max", "section_stop"):
        new = getattr(fresh.lab, attr)
        old = getattr(t.lab, attr)
        _set(f"lab.{attr}", new, old)
        setattr(t.lab, attr, new)
    if list(fresh.page_size) != list(t.page_size):
        _set("page_size", fresh.page_size, t.page_size)
        t.page_size = list(fresh.page_size)
    _set("stats", fresh.stats, t.stats)
    t.stats = dict(fresh.stats)
    t.notes = list(t.notes) + [
        f"**差量草案**（D1）：以 {base.org_id} 为基底，改动 {len(changes)} 处；"
        f"来源={changed_by}；仍须过 E1–E6 验收"]
    return t, changes
