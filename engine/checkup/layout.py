# -*- coding: utf-8 -*-
"""页面几何层：把 PDF 页面还原成**人类阅读顺序**的行。

只做几何与排序，不做任何语义或医学判断。

为什么需要这一层（已踩过的坑）：
    FastReport 等报表引擎按自己的顺序绘制文本框，``pg.get_text()`` 的
    返回顺序**不等于**阅读顺序。实测「1.高甘油三酯血症」这个标题在数据流里
    排在正文之后。因此必须按 y 坐标重排。
    参考实现：pdfplumber 的维护者也明确 ``extract_words`` 不保证阅读顺序。

本模块是对既有 ``体检分析/extract.py`` 里 ``cluster_rows`` 的提炼（旧脚本已退役，
此处保留出处说明）——原实现
只服务于化验单的双栏表格，这里做成通用能力，供叙述层与表格层共用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import fitz

#: 行内聚类的 y 容差（pt）。同一视觉行的字块中心通常相差 < 1pt，
#: 但箭头等独立绘制元素会漂移，故留 3pt 余量。
DEFAULT_YTOL = 3.0


@dataclass(frozen=True)
class Word:
    """一个文字块及其包围盒。"""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    """一视觉行：同一 y 上的若干文字块，已按 x 升序。"""

    page_no: int
    y: float
    words: list[Word]

    @property
    def text(self) -> str:
        """整行文本（拼接，不插空格——中文场景下插入空格反而破坏词）。"""
        return "".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0 if self.words else 0.0

    @property
    def first(self) -> str:
        return self.words[0].text if self.words else ""

    def rest(self) -> str:
        """除第一个文字块之外的其余部分（用于 ``标签: 内容`` 形态的行）。"""
        return "".join(w.text for w in self.words[1:])


def page_words(page: "fitz.Page") -> list[Word]:
    """取一页的全部文字块（带坐标）。无文字层时返回空列表。"""
    return [Word(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def cluster_rows(words: Sequence[Word], page_no: int = 0,
                 ytol: float = DEFAULT_YTOL) -> list[Row]:
    """按 y 中心聚类成行：行内按 x 升序，行间按 y 升序。"""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w.yc)
    groups: list[list[Word]] = [[ordered[0]]]
    for w in ordered[1:]:
        ref = sum(x.yc for x in groups[-1]) / len(groups[-1])
        if abs(w.yc - ref) <= ytol:
            groups[-1].append(w)
        else:
            groups.append([w])
    rows = []
    for g in groups:
        g.sort(key=lambda w: w.x0)
        rows.append(Row(page_no, sum(x.yc for x in g) / len(g), g))
    return rows


def rows_of_page(page: "fitz.Page", page_no: int,
                 ytol: float = DEFAULT_YTOL) -> list[Row]:
    """一页 → 阅读顺序的行列表。"""
    return cluster_rows(page_words(page), page_no, ytol)


def has_text_layer(page: "fitz.Page", min_chars: int = 40) -> bool:
    """载体判定：该页是否有可用文字层（决定走文本通道还是图像通道）。

    这对应框架 §三 的 G0 关卡。本机三份报告都有文字层，图像通道在 v1 中被
    明确排除（见框架 §七），但判定入口先留出来。
    """
    return len(page.get_text().strip()) >= min_chars
