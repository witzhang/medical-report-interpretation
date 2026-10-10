#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对账层单位可比性（reconcile/units.py）的回归。

背景（2026-10-10 实测）：报告正文写 ``μmol/L``、引擎规范单位是 ``umol/L``；
单位比较若不做写法宽容，会把整类 μ 单位误判"不可比"→ 数字追踪误伤。
另一条：单位交替必须最长优先，否则 ``mmHg`` 被 ``mm`` 截断。
"""
from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
SKILL = os.path.dirname(BASE)
sys.path.insert(0, os.path.join(SKILL, "reconcile"))

from units import unit_compat  # noqa: E402


def test_greek_mu_equals_ascii_u():
    """μmol/L（报告）≡ umol/L（引擎规范单位）。"""
    assert unit_compat("μmol/L", "umol/L")
    assert unit_compat("umol/L", "μmol/L")
    assert unit_compat("µmol/L", "umol/L")      # 微符号 U+00B5 也算


def test_percent_family_and_case():
    assert unit_compat("％", "%")
    assert unit_compat("U/L", "u/l")
    assert not unit_compat("%", "mmol/L")
    assert not unit_compat("mm", "mmHg")


def test_missing_side_is_not_blocked():
    """任一侧缺单位 → 无法判断，不拦（保持召回）。"""
    assert unit_compat("", "mmol/L")
    assert unit_compat("mmol/L", "")
    assert unit_compat("mmol/L", None)


def test_tok_prefers_longest_unit():
    """数字抽取以最长单位优先：'119/78 mmHg' 的 78 必须带 mmHg，不是 mm。"""
    import reconcile as R  # noqa: PLC0415
    m = R.TOK.search("119/78 mmHg")
    assert m and m.group(2) == "mmHg", m.group(2) if m else None


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
        except BaseException as e:  # noqa: BLE001
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
