#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开放项对账测试。    python3 tests/test_openitems.py

判据不是"没有未完成项"（那不可能），而是**未完成项必须可见、有主、对得上账**。

这条测试存在的理由是一个真实事故：``P_CKDEPI.note`` 一直写着
「报告范围 5-150 与结果取整数本引擎**尚未实施**」，而那条早已实现 ——
注记在**说假话**，没有任何机制会报错。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.openitems import (ALLOWED_NON_ITEMS, MARK, OPEN_ITEMS,  # noqa: E402
                               ROOT, Hit, is_allowed, is_registered,
                               render, scan, scan_file_hits, stale, summary,
                               unregistered)


# =============================================== 对账：两边的账都要平

def test_no_unregistered_open_marks():
    """**核心保证**：扫到的每一处未完成标记都已登记或在允许清单里。

    这条挂了意味着：有人在代码或出处里承认了某件事没做，但**没人认领它**。
    """
    un = unregistered()
    assert not un, "有未完成项沉在注记里：\n" + "\n".join(
        f"  {h.where}: {h.text[:100]}" for h in un)


def test_no_stale_open_items():
    """反向：登记的每一条在仓库里都还找得到痕迹。

    找不到多半意味着**已经做掉了但没删条目** —— 登记表开始说假话。
    """
    st = stale()
    assert not st, "登记表过期：\n" + "\n".join(
        f"  {it.item_id}（特征串 {it.marker!r} 已不存在）" for it in st)


# =============================================== 守卫本身要有效

def test_scanner_actually_catches_a_new_undeclared_item():
    """**元测试**：在临时目录里造一条假的未完成声明，守卫必须报出来。

    没有这条，``test_no_unregistered_open_marks`` 可能只是因为
    **扫描器根本不工作**而"通过"。
    """
    root = tempfile.mkdtemp()
    pkg = os.path.join(root, "checkup")
    os.makedirs(pkg)
    with open(os.path.join(pkg, "fake.py"), "w", encoding="utf-8") as f:
        f.write("# -*- coding: utf-8 -*-\n"
                "# 这个功能本引擎尚未实施，且没登记\n"
                "def f():\n    return 1\n")
    hits = scan_file_hits(root)
    assert len(hits) == 1, hits
    assert not is_registered(hits[0])
    assert not is_allowed(hits[0])


def test_scanner_ignores_itself():
    """``openitems.py`` 自己满是这些词（自指），必须排除，否则永远报错。"""
    assert all("openitems.py" not in h.where for h in scan())


def test_mark_pattern_covers_the_words_we_actually_use():
    for w in ("尚未实施", "未实现", "待核", "未取得", "暂未"):
        assert MARK.search(w), w


# =============================================== 登记表自身的卫生

def test_item_ids_are_unique():
    ids = [i.item_id for i in OPEN_ITEMS]
    assert len(ids) == len(set(ids)), ids


def test_every_item_is_actionable():
    """每条都要说清：什么类型、多严重、**卡在什么上**。"""
    for it in OPEN_ITEMS:
        assert it.kind in ("source", "implementation", "coverage"), it.item_id
        assert it.severity in ("high", "medium", "low"), it.item_id
        assert it.unblocked_by, f"{it.item_id} 没说卡在什么上"
        assert it.declared_at, f"{it.item_id} 没说声明在哪"


def test_every_item_has_a_findable_marker():
    """每条的 marker 必须能在仓库里找到 —— 否则这条登记是空挂的。"""
    import io
    text = ""
    pkg = os.path.join(ROOT, "checkup")
    for fn in os.listdir(pkg):
        if fn.endswith(".py"):
            with open(os.path.join(pkg, fn), encoding="utf-8") as f:
                text += f.read().replace("*", "").replace("`", "")
    tdir = os.path.join(ROOT, "tests")
    for fn in os.listdir(tdir):
        if fn.endswith(".py"):
            with open(os.path.join(tdir, fn), encoding="utf-8") as f:
                text += f.read().replace("*", "").replace("`", "")
    for it in OPEN_ITEMS:
        ms = [it.marker, *it.extra_markers]
        assert any(m and m in text for m in ms), \
            f"{it.item_id}: 特征串 {ms} 在仓库里找不到"


def test_allowlist_entries_are_not_orphans():
    """允许清单里每条都必须**还能匹配到东西**。

    这条是本轮血的教训：允许清单里曾有一条 "本引擎未实现"，对应的功能后来
    实现了，注记也删了，**但允许清单还留着** —— 于是它继续吞掉未来任何
    含相同字样的命中。允许清单会变成藏谎的地方，必须能被清理。
    """
    hits = scan()
    orphans = []
    for f, sub, _r in ALLOWED_NON_ITEMS:
        if not any(sub in h.text for h in hits):
            orphans.append((f, sub))
    assert not orphans, f"允许清单有孤儿条目（已无对应命中）：{orphans}"


def test_allowlist_entries_all_have_reasons():
    for f, sub, reason in ALLOWED_NON_ITEMS:
        assert reason and len(reason) >= 6, (f, sub, reason)
        assert sub, (f, reason)


def test_high_severity_items_are_the_ones_that_block_the_objective():
    """high 必须真的是"挡住目标"的那些 —— 否则严重度会通胀。"""
    high = {i.item_id for i in OPEN_ITEMS if i.severity == "high"}
    assert "COV.REAL_MULTI_ORG" in high      # 真实多机构验证
    assert "SRC.METSYN" in high              # 性别阈值靠待核值
    assert "COV.CHINAPAR_FEMALE" in high     # 女性走不到 10 年评估
    assert "SRC.CHINAPAR_COEF" in high       # 0.3pp 会翻转分层


def test_render_and_summary_work():
    r = render()
    assert "开放项登记表" in r
    for it in OPEN_ITEMS:
        assert it.item_id in r, it.item_id
    assert "开放项" in summary()


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
