# -*- coding: utf-8 -*-
"""模板自动起草（框架 D1 的下一半）。

上一轮把「改代码」降成了「写配置」，但**写配置仍需要一个懂版式的人**。
本模块把这一步也自动化：丢一份 PDF 进去，扫出锚点、列位、栏数、段标题列、
噪声模式，产出一份**草案**模板（``confirmed=False``），人工确认后即可用。

为什么可行：中文化验单的表头词高度收敛——「检验项目 / 结果 / 参考值」
几乎家家这么写。真正每家不同的是**几何**（栏数、列位、缩进），
而几何是能算出来的。

**产出一定是草案。** 自动起草错了会静默抽错数据，所以：
``confirmed=False`` + ``evidence`` 逐条记明"这一项是从哪里推出来的"。
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import fitz

from .layout import rows_of_page
from .tables import extract_lab_results
from .templates import LabTemplate, Template

#: 中文化验单表头词的收敛词表（这是本方法可行的前提）
ITEM_TOKENS = ["检验项目", "检测项目", "项目名称", "项目"]
VALUE_TOKENS = ["检测结果", "检验结果", "结果"]
REF_TOKENS = ["正常参考值", "参考区间", "参考范围", "参考值"]
UNIT_TOKENS = ["单位"]

ALL_HEADER_TOKENS = ITEM_TOKENS + VALUE_TOKENS + REF_TOKENS + UNIT_TOKENS

MARKER_RE = re.compile(r"[\u4e00-\u9fffA-Za-z]{2,24}?(报告单|检验单|报告)")

#: 通用页面标记候选（用于"这一页是不是化验页"，跨机构通用）
GENERIC_MARKERS = ["检验报告单", "检验单", "化验单", "报告单"]


def _generic_marker(docs: list) -> str:
    """挑一个**通用**页面标记：出现在最多页面上的那个候选。"""
    best, best_pages = "检验报告单", 0
    for cand in GENERIC_MARKERS:
        pages = sum(1 for doc in docs for i in range(doc.page_count)
                    if cand in doc[i].get_text())
        if pages > best_pages:
            best, best_pages = cand, pages
    return best


@dataclass
class Draft:
    """自动起草的模板草案。"""

    template: Template
    confidence: float                       #: 0–1，越高越可信
    notes: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)   #: 每一项是从哪里推出来的

    def render(self) -> str:
        lines = [f"草案 {self.template.org_id} v{self.template.template_version}"
                 f" · 置信度 {self.confidence:.2f} · confirmed=False", ""]
        for k, v in self.evidence.items():
            lines.append(f"  {k}: {v}")
        for n in self.notes:
            lines.append(f"  ⚠ {n}")
        return "\n".join(lines)


def _pick_header_row(rows: list):
    """找出含最多表头词的行，返回 (Row, 按出现顺序的表头词)。

    **返回 Row 而不是它的词表** —— 调用方要靠 ``row is header_row`` 判断
    "数据行从表头之后开始"，返回词表会让这个判断永远为假。
    """
    best_row, best_hits, best_tokens = None, 0, []
    for row in rows:
        toks = [w.text for w in row.words if w.text in ALL_HEADER_TOKENS]
        if len(toks) > best_hits:
            best_row, best_hits, best_tokens = row, len(toks), toks
    if best_row is None or best_hits < 3:
        return None
    return best_row, best_tokens


def _detect_marker(docs: list) -> tuple[str, int]:
    """页面标记：**出现在最多页面**上的「…报告单」字样。

    两个坑：
    * 按出现**次数**计会把封面上的「体检报告」算赢（封面 + 每页页眉），
      要按**页数**去重。
    * 正则用非贪婪，会先匹配短的「…报告」。同一位置要取**最长**匹配
      （「检验报告单」优于「报告」）。
    """
    per_page: dict[str, set] = {}
    for di, doc in enumerate(docs):
        for i in range(doc.page_count):
            text = doc[i].get_text()
            # 同一页同一位置取最长匹配
            spans: list[tuple[int, int, str]] = []
            for m in MARKER_RE.finditer(text):
                spans.append((m.start(), m.end(), m.group(0)))
            keep: list[str] = []
            for a, b, g in spans:
                if any(a >= x and b <= y and (y - x) > (b - a)
                       for x, y, _ in spans):
                    continue
                keep.append(g)
            for g in keep:
                per_page.setdefault(g, set()).add((di, i))
    if not per_page:
        return "检验报告单", 0
    top = max(per_page, key=lambda k: (len(per_page[k]), len(k)))
    return top, len(per_page[top])


def _detect_noise_labels_pages(lab_rows: list, n_lab_pages: int) -> list[str]:
    """噪声标签：以冒号结尾、且**几乎每张化验页都出现**的短词。

    只在化验页上收，并且要求出现在每一张化验页 —— 否则会把体格检查、
    眼科那些「身高:」「心率:」全部收进来（它们本身是数据项，不是噪声）。
    """
    per_page: dict[str, set] = {}
    pat = re.compile(r"^([\u4e00-\u9fff]{2,6})[:：]$")
    for pi, (rows, _hit) in enumerate(lab_rows):
        for row in rows:
            for w in row.words:
                m = pat.match(w.text)
                if m:
                    per_page.setdefault(m.group(1), set()).add(pi)
    need = max(1, n_lab_pages)
    return sorted(t for t, ps in per_page.items() if len(ps) >= need)


def _cluster_x(values: list[float], tol: float = 4.0) -> list[float]:
    """把一堆 x 坐标聚成若干列中心。"""
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [round(sum(g) / len(g), 1) for g in out]


def draft_template(pdf_paths: list[str], org_id: str = "AUTO-001",
                   org_name: str = "") -> Draft:
    """从一至多份 PDF 起草模板。多份同机构报告能显著提高置信度。"""
    docs = [fitz.open(p) for p in pdf_paths if os.path.exists(p)]
    try:
        if not docs:
            raise ValueError("没有可读的 PDF")
        notes: list[str] = []
        evidence: dict = {}

        # ---- 1. 页面标记
        #
        # 要分开两件事：
        # * ``page_marker`` —— "这一页是不是化验页"，必须是**通用**短标记
        # * ``required_anchors`` —— "这是不是本机构"，用**专属**抬头
        # 混成一个的后果：别家报告因为抬头对不上而整页被跳过。
        title, title_n = _detect_marker(docs)
        marker = _generic_marker(docs)
        evidence["page_marker"] = (f"page_marker={marker!r}（通用）；"
                                   f"required_anchors=[{title!r}]"
                                   f"（专属，出现 {title_n} 次）")

        # ---- 2. 表头行与栏数（只在含标记的化验页上找）
        header_row = None
        header_words = None
        header_tokens: list[str] = []
        lab_rows: list[tuple] = []          # (row, page_idx, is_after_header)
        for doc in docs:
            for i in range(doc.page_count):
                if marker not in doc[i].get_text():
                    continue
                rows = rows_of_page(doc[i], i + 1)
                hit = _pick_header_row(rows)
                lab_rows.append((rows, hit))
                if hit and len(hit[1]) > len(header_tokens):
                    header_row, header_tokens = hit
                    header_words = header_row.words
        if header_words is None or len(header_tokens) < 3:
            notes.append("没找到表头行（表头词不在收敛词表里）→ 无法起草，"
                         "需人工确认")
            t = Template(org_id=org_id, org_name=org_name, template_version=1,
                         header_anchor_strings=[title] if title else [],
                         required_anchors=[title] if title else [])
            return Draft(t, 0.0, notes, evidence)

        n_item = sum(1 for t_ in header_tokens if t_ in ITEM_TOKENS)
        n_grp = max(1, n_item)
        pattern = []
        for t_ in (ITEM_TOKENS, VALUE_TOKENS, REF_TOKENS, UNIT_TOKENS):
            for cand in t_:
                if cand in header_tokens:
                    pattern.append(cand)
                    break
        pattern = pattern[:3]
        evidence["header_cells"] = (f"{pattern}（表头行共 {len(header_tokens)} 个"
                                    f"表头词，推断 {n_grp} 栏）")

        # ---- 3. 段标题列 vs 项目名列（只看**表头之后的数据行**）
        #
        # 关键：项目名的 x 要取自数据行，不能取表头。表头的「检验项目」常与
        # 段标题列只差几 pt（本机构 57 vs 47），拿表头当项目列会把段标题列
        # 判成"不存在"。
        data_first_xs: list[float] = []
        for rows, hit in lab_rows:
            if hit is None:
                continue
            seen_header = False
            for row in rows:
                if row is hit[0]:
                    seen_header = True
                    continue
                if not seen_header:
                    continue
                head = row.first.strip()
                if (1 <= len(head) <= 10
                        and not re.search(r"[\d:：↑↓]", head)
                        and head not in ALL_HEADER_TOKENS):
                    data_first_xs.append(round(row.x0, 1))
        clusters = _cluster_x(data_first_xs)
        section_x = item_x = None
        if len(clusters) >= 2:
            section_x, item_x = clusters[0], clusters[1]
        elif len(clusters) == 1:
            item_x = clusters[0]
        if section_x is not None and item_x is not None \
                and item_x - section_x >= 4:
            section_x_max = round((section_x + item_x) / 2, 1)
        elif item_x is not None:
            section_x_max = round(max(0.0, item_x - 6), 1)
            notes.append("段标题列与项目列未能分开 → section_x_max 由项目列"
                         "回推，**必须人工确认**")
        else:
            section_x_max = 60.0
            notes.append("没有数据行可分析 → section_x_max 用兜底值，"
                         "**必须人工确认**")
        evidence["section_x_max"] = (f"数据行列中心 {clusters} → 段标题列 "
                                     f"{section_x} / 项目列 {item_x}"
                                     f" → section_x_max={section_x_max}")

        # ---- 4. 噪声标签：只在化验页上收，且要出现在每一张化验页
        n_lab_pages = sum(1 for _ in lab_rows)
        noise = _detect_noise_labels_pages(lab_rows, n_lab_pages)
        base_noise = ["审核医生", "检验者", "审核日期", "操作员", "主检医生"]
        merged_noise = sorted(set(noise) | set(base_noise))
        evidence["noise_labels"] = f"{merged_noise}"

        # ---- 5. 组装
        t = Template(
            org_id=org_id, org_name=org_name, template_version=1,
            valid_from="", header_anchor_strings=[title] + pattern,
            required_anchors=[title] if title else [],
            lab=LabTemplate(
                page_marker=marker, header_cells=pattern,
                column_groups=n_grp, section_x_max=section_x_max,
                section_stop=pattern),
            notes=["自动起草，confirmed=False；使用前必须人工确认几何假设"],
        )
        # 置信度：表头词齐全 + 段标题列独立 + 噪声标签有检出
        conf = 0.0
        conf += 0.5 if len(pattern) == 3 else 0.2
        conf += 0.3 if (section_x is not None
                        and item_x is not None
                        and item_x - section_x >= 4) else 0.0
        conf += 0.2 if noise else 0.0
        # ---- 6. 抽取基线（供漂移监控）：用草案自己回抽一遍
        totals: list[int] = []
        per_page: dict = {}
        for p in pdf_paths:
            if not os.path.exists(p):
                continue
            doc = fitz.open(p)
            try:
                n = len(extract_lab_results(doc, "0000", t))
            finally:
                doc.close()
            if n:
                totals.append(n)
        if totals:
            t.stats = {"items_per_report": totals,
                       "items_per_page": per_page}
            evidence["stats"] = f"items_per_report={totals}（用草案回抽得到）"
        else:
            notes.append("回抽 0 项 → 未能建立抽取基线，漂移监控将不可用")

        return Draft(t, round(conf, 2), notes, evidence)
    finally:
        for d in docs:
            d.close()
