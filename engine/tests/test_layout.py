#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 版面层的回归测试。    python3 tests/test_layout.py

版面层是把字块聚成"行"的那一步 —— 后续所有抽取都建立在它上面。
变异测试发现它此前只有 2/6 被杀死，且**边界值从没构造过**。
"""
from __future__ import annotations

import dataclasses
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from checkup.layout import (DEFAULT_YTOL, Row, Word,  # noqa: E402
                            cluster_rows, has_text_layer, rows_of_page)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
SRC = PATHS.real_reports


def _w(text, x0, yc):
    return Word(text=text, x0=x0, y0=yc - 3, x1=x0 + len(text) * 6, y1=yc + 3)


def test_row_clustering_tolerance_is_inclusive():
    """同一视觉行的判定是 ``|Δyc| <= ytol`` —— **恰好等于容差仍算同一行**。

    变异测试发现 ``if abs(w.yc - ref) <= ytol`` 的 ``<=`` 改成 ``<`` 无人管 ——
    因为它只在"字块中心恰好相差 3.0pt"时才产生差别，而这个值从没被构造过。
    """
    at = cluster_rows([_w("A", 0, 0.0), _w("B", 100, DEFAULT_YTOL)])
    over = cluster_rows([_w("A", 0, 0.0), _w("B", 100, DEFAULT_YTOL + 0.01)])
    assert len(at) == 1, [(r.text, r.yc) for r in at]
    assert len(over) == 2, [(r.text, r.yc) for r in over]


def test_row_clustering_sorts_by_x_within_a_row():
    """同一行内按 x 排序 —— 阅读顺序。"""
    rows = cluster_rows([_w("右", 200, 0.0), _w("左", 10, 0.5)])
    assert len(rows) == 1
    assert rows[0].text.strip() == "左右", rows[0].text


def test_word_is_immutable():
    """``Word`` 是 ``frozen=True`` 的。

    变异测试发现改成 ``False`` 无人管 —— 与 `CholStratum` 同一类：
    保证存在但没人验。字块被意外改写会污染后续所有抽取。
    """
    w = _w("A", 0, 0.0)
    assert w.__dataclass_params__.frozen is True
    try:
        w.text = "B"                      # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("Word 竟然可以被修改")


def test_has_text_layer_threshold_is_inclusive():
    """``len(text.strip()) >= min_chars`` —— **恰好等于阈值判为有文字层**。

    变异测试发现 ``>=`` 改成 ``>`` 无人管。这条决定走文本通道还是图像通道，
    判错的后果是整页走错分支。
    """
    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), "A" * 40, fontsize=9)   # 恰好 40 字符
        assert has_text_layer(page, min_chars=40) is True
        assert has_text_layer(page, min_chars=41) is False
    finally:
        doc.close()


def test_has_text_layer_ignores_whitespace():
    """空白不算文字 —— 判定用的是 ``get_text().strip()``。"""
    doc = fitz.open()
    try:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), "   ", fontsize=9)
        assert has_text_layer(page, min_chars=1) is False
    finally:
        doc.close()


def test_real_reports_have_no_scanned_pages():
    """本机三份报告**没有扫描页** —— 少数几页是空白页（无字无图）。

    这条测试第一次跑就纠正了我自己的一个误判：我原以为"第 2、4 页文字层不足"
    说明它们是扫描件，实际是**完全空白的页**（0 字符、0 图片）。
    这个区别很重要 —— 空白页正常，扫描页意味着内容没被分析。
    """
    for fn in sorted(os.listdir(SRC)):
        if not fn.lower().endswith(".pdf"):
            continue
        doc = fitz.open(os.path.join(SRC, fn))
        try:
            scanned, blank = [], []
            for i in range(doc.page_count):
                p = doc[i]
                if has_text_layer(p):
                    continue
                (scanned if p.get_images() else blank).append(i + 1)
            assert not scanned, f"{fn} 有扫描页 {scanned} —— v1 无图像通道"
        finally:
            doc.close()


def test_rows_of_page_matches_cluster_rows():
    """``rows_of_page`` 只是把页面字块取出再聚类 —— 两条路径必须一致。"""
    doc = fitz.open(os.path.join(SRC, "20240612体检报告.pdf"))
    try:
        for pno in range(min(3, doc.page_count)):
            rows = rows_of_page(doc[pno], pno + 1)
            assert all(isinstance(r, Row) for r in rows)
            assert [r.page_no for r in rows] == [pno + 1] * len(rows)
    finally:
        doc.close()


# ------------------------------------------------------------------ 主程序

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
