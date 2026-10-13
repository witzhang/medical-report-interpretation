#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出全部已知未完成项，并与全仓的"未完成"标记对账。

    python3 open_items.py

判据不是"没有未完成项"，而是**未完成项必须可见、有主、对得上账**。

为什么需要它：这个项目习惯把"没做到的"写进 `Provenance.note`。写下来是对的，
但**做掉之后没人会回去改注记**。本工具就是那次事故的产物 —— `P_CKDEPI.note`
一直写着「本引擎尚未实施」，而那条早已实现。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checkup.openitems import (OPEN_ITEMS, render, scan, stale,  # noqa: E402
                               summary, unregistered)


def main() -> int:
    print("=" * 96)
    print(summary())
    print("=" * 96)
    print()
    print(render())

    hits = scan()
    un = unregistered()
    st = stale()
    print("=" * 96)
    print("对账")
    print("=" * 96)
    print(f"  扫描命中 {len(hits)} 处 · 开放项 {len(OPEN_ITEMS)} 条")
    if not un:
        print("  ✓ 无未登记标记 —— 没有东西沉在注记里")
    else:
        print(f"  ✗ **{len(un)} 处未登记**（有人承认没做，但没人认领）：")
        for h in un:
            print(f"      {h.where}: {h.text[:90]}")
    if not st:
        print("  ✓ 无过期条目 —— 登记表没有说假话")
    else:
        print(f"  ✗ **{len(st)} 条登记已过期**（多半是做掉了没删）：")
        for it in st:
            print(f"      {it.item_id}（特征串 {it.marker!r} 已不存在）")

    if un or st:
        print()
        print("  → 修法：把未登记项加进 checkup/openitems.py 的 OPEN_ITEMS，")
        print("          或把过期条目删掉（若确实已做，同时更新对应的 note）")
        return 1
    print()
    print("  按严重度摘要：")
    for sev in ("high", "medium", "low"):
        n = sum(1 for i in OPEN_ITEMS if i.severity == sev)
        if n:
            print(f"    {sev:<7} {n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
