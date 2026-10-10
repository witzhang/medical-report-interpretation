#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「检查手段」本身接进测试套件。    python3 tests/test_health.py

`tools/health.py` 是一条命令看全项目健康度，但它是**手动**跑的。
这一套把那几项里**快的**部分接进常规测试，这样 `python3 tests/test_*.py`
就会覆盖它们。

不含变异测试自检（要跑两分钟）—— 那个留在 `health.py --full`。

为什么这几项值得进套件：连续几轮的失效都发生在"检查手段"这一层
（一条不变式永远返回 0、工具改坏仓库、工具报假的 100%、G0 悬空）。
**检查器本身也需要被检查。**
"""
from __future__ import annotations

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

from checkup.console import run_text  # noqa: E402


def _run(name: str, *extra: str) -> subprocess.CompletedProcess:
    return run_text(
        [sys.executable, os.path.join(BASE, "tools", name), *extra],
        cwd=BASE, timeout=300)


def test_wiring_audit_passes():
    """**没有「声明了却没接线」的东西。**

    G0 载体判定（`layout.has_text_layer`）就是这么发现的 —— 定义在引擎里、
    框架里列为关卡，但全仓没有任何地方调用它，悬空了不知道多久。
    """
    p = _run("audit_wiring.py")
    assert p.returncode == 0, p.stdout[-600:]


def test_wiring_audit_would_catch_an_unused_function():
    """**元测试**：种一个无人调用的函数，检查器必须报出来。

    没有这条，上面那条可能只是因为**检查器根本不工作**而通过。
    这里不改仓库文件 —— 直接调用检查器的判定逻辑。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audit_wiring", os.path.join(BASE, "tools", "audit_wiring.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # `_definitions` 要能认出新定义的函数
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write("def planted_unused_function():\n    return 1\n")
        path = f.name
    try:
        names = [n for n, _ in m._definitions(path)]
        assert "planted_unused_function" in names, names
    finally:
        os.unlink(path)


def test_vacuous_assertion_audit_passes():
    """没有静态可判定的空转断言。

    这一类里抓到过一条真的：``assert rcv(x) == rcv(x)`` —— 自己等于自己，
    **不可能失败**。
    """
    p = _run("audit_tests.py")
    assert p.returncode == 0, p.stdout[-600:]


def test_mutant_baseline_matches():
    """各模块的变异体计数与基线一致 —— 能暴露**残留的变异**。

    变异体逐个用 ``original.replace()`` 生成，所以只有最后一个会残留；
    那一行若已被预置成变异形态，那一款就不会再生成 —— 计数会变。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "health", os.path.join(BASE, "tools", "health.py"))
    h = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(h)
    from mutate import generate
    bad = []
    for rel, want in h.MUTANT_BASELINE.items():
        path = os.path.join(BASE, rel)
        if not os.path.exists(path):
            bad.append(f"{rel} 不存在")
            continue
        got = len(generate(open(path, encoding="utf-8").read()))
        if got != want:
            bad.append(f"{os.path.basename(rel)} {got}≠{want}")
    assert not bad, ("计数与基线不符 —— 若是本轮改源码导致的，更新 "
                     f"tools/health.py 的 MUTANT_BASELINE；否则可能被改坏了：{bad}")


def test_open_items_reconcile():
    """开放项对账：扫到没登记 / 登记了扫不到，两者都不允许。"""
    from checkup.openitems import stale, unregistered
    assert not unregistered(), [str(x) for x in unregistered()]
    assert not stale(), [x.item_id for x in stale()]


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
