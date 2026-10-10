# -*- coding: utf-8 -*-
"""合成化验单生成器（测试与演练共用）——**不依赖数据根**。

从 ``tests/test_cross_institution.py`` 抽出（P0-5/D5）：`scripts/edge_cases.py`
需要它，但原来 import 那个测试模块会在**模块级**触发"数据未挂载→跳过→
``SystemExit(0)``"，把宿主脚本一起结束（实测：八项演练只跑到第 ④ 项就"0/0 通过"退出）。

放这里后：测试与演练都能在**任何机器**上生成同样的合成 PDF。
"""
from __future__ import annotations

import os
import tempfile

import fitz

#: PyMuPDF 内置中文字体（无需外部字体文件）
CJK = "china-s"

#: 一份"别家机构"的化验数据（项目名沿用通用写法，段名/版式可变）
ROWS = [
    ("白细胞", "7.2", "3.5-9.5 10^9/L"),
    ("红细胞", "4.50", "3.8-5.1 10^12/L"),
    ("血红蛋白浓度", "140", "115-150 g/L"),
    ("血小板", "230", "125-350 10^9/L"),
    ("中性粒细胞绝对值", "4.10", "1.8-6.3 10^9/L"),
]


def _mk(path: str, section: str, columns: int, title: str = "检验报告单",
        section_x: float = 47.0, item_x: float = 61.0) -> str:
    """生成一份合成化验单。

    ``columns=1`` 单栏；``columns=2`` 双栏且带两组表头锚点。

    列坐标复刻真实报告的**两级缩进**：段标题 x≈47、项目名 x≈61、
    结果 x≈170、参考值 x≈240（右栏各 +270）。

    把 ``item_x`` 也设成 47 可模拟"段标题启发式失灵"的版式。
    """
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 60), f"XX市第一人民医院{title}", fontname=CJK, fontsize=11)
    pg.insert_text((50, 85), "体检日期：2025-03-15", fontname=CJK, fontsize=9)
    pg.insert_text((380, 85), "姓名：测试  性别：女  年龄：45岁", fontname=CJK, fontsize=9)

    # (表头x, 项目x, 结果x, 参考值x)
    if columns == 2:
        cols = [(50, item_x, 170, 240), (320, 320 + (item_x - 50), 440, 510)]
    else:
        cols = [(50, item_x, 170, 240)]

    y = 115
    for hx, _nx, vx, rx in cols:
        pg.insert_text((hx, y), "检验项目", fontname=CJK, fontsize=9)
        pg.insert_text((vx, y), "结果", fontname=CJK, fontsize=9)
        pg.insert_text((rx, y), "参考值", fontname=CJK, fontsize=9)
    y += 30
    pg.insert_text((section_x, y), section, fontname=CJK, fontsize=9)
    y += 20

    chunks = [ROWS] if columns == 1 else [
        ROWS[:(len(ROWS) + 1) // 2], ROWS[(len(ROWS) + 1) // 2:]]
    for i in range(max(len(c) for c in chunks)):
        for (_hx, nx, vx, rx), chunk in zip(cols, chunks):
            if i >= len(chunk):
                continue
            nm, v, ref = chunk[i]
            pg.insert_text((nx, y), nm, fontname=CJK, fontsize=9)
            pg.insert_text((vx, y), v, fontsize=9)
            pg.insert_text((rx, y), ref, fontsize=8)
        y += 18

    doc.save(path)
    doc.close()
    return path


def tmp_pdf(name: str, section: str = "血常规", columns: int = 2, **kw) -> str:
    """生成到系统临时区并返回路径（演练/测试的常用形态）。"""
    path = os.path.join(tempfile.mkdtemp(prefix="synth_"), name)
    return _mk(path, section, columns=columns, **kw)
