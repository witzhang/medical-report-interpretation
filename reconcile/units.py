# -*- coding: utf-8 -*-
"""单位可比性判定（对账/回溯层共用）——**只判等价、不换算**（与引擎同口径）。

用途：把"数字巧合"挡在跟踪之外。报告里的 ``24.0%`` 不应指回
``GGT 24.0 U/L``（单位不同、语义不同）；两侧任一单位缺失时不拦（无法判断，
宁可保留线索也不静默丢失）。

口径来源：复用引擎 ``checkup.normalize._norm_unit`` 的等价映射（避免双规）；
在其上补一条大小写宽容比较（``U/L`` 与 ``u/l`` 视为同一单位）。
"""
from __future__ import annotations

import paths  # noqa: F401  （把 engine 目录挂上 sys.path，供 checkup.* 导入）
from checkup.normalize import _norm_unit  # noqa: E402

__all__ = ["unit_compat"]


def _loose(u: str) -> str:
    """宽容归一：在引擎等价映射之上再吸收"同一单位的写法差异"。

    * μ（U+03BC）/ µ（U+00B5）→ ``u``：报告正文常用 ``μmol/L``，
      而引擎规范单位是 ASCII ``umol/L``（实测：不做这一步会误伤整类 μ 单位）；
    * ``×`` → ``x``；整体 casefold。
    """
    return (_norm_unit(u).replace("μ", "u").replace("µ", "u")
            .replace("×", "x").casefold())


def unit_compat(report_unit: str, fact_unit: str) -> bool:
    """报告侧单位与产物侧单位是否**可比**（不可比 → 不允许互指）。

    规则：
    * 任一侧为空/缺失 → ``True``（无法判断，不拦）；
    * 其余按引擎等价映射 + 写法宽容（μ/µ/u、×/x、大小写）比较。
    """
    if not report_unit or not fact_unit:
        return True
    return _loose(report_unit) == _loose(fact_unit)
