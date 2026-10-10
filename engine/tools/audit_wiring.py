#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查「声明了却没接线」的东西。

来历：G0 载体判定（`layout.has_text_layer`）定义在引擎里、框架里列为关卡，
但 `grep` 全仓**没有任何地方调用它** —— 一关悬空了不知道多久。
那次是靠手工 grep 发现的，这个工具把它变成可重复的检查。

判据保守：只报**模块级函数/类**在 ``checkup/`` 之外**完全没有被引用**的。
被自己模块内的其他代码用到就算接线了。

排除项（否则噪声淹没信号）：
* 下划线开头的（内部实现）
* ``dataclass`` 生成的（``__init__`` 之类）
* 被 ``__all__`` 显式导出的（那是有意的 API 面）
* Protocol/抽象基类里的方法（由实现方提供）

    python3 tools/audit_wiring.py
"""
from __future__ import annotations

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from checkup.console import init_console
except ImportError:                     # 直跑时把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from checkup.console import init_console
init_console()

PKG = os.path.join(ROOT, "checkup")
#: 除包自身外，还去这些地方找引用
SEARCH_DIRS = [os.path.join(ROOT, "tests"), ROOT, os.path.join(ROOT, "tools")]
SEARCH_FILES = ["run_pipeline.py", "run_narrative.py", "run_normalize.py",
                "build_catalog.py", "build_cvi.py", "draft_template.py",
                "draft_catalog.py", "open_items.py"]

#: 明确属于"对外 API / 由外部按约定调用"的名字，不报
ALLOW = {
    # 协议/接口
    "ModelAdapter", "answer",
    # 供测试与调用方按名字取的常量
    "EXPECTATIONS", "OPEN_ITEMS", "CROSS_CHECKS", "DEFAULT_A",
    "G_STAGES", "LDL_TARGETS", "METSYN_CUTS", "FIB4_CUTOFFS",
    "CHOL_STRATA", "NO_HYPERTENSION", "HYPERTENSION", "LIFETIME_CRITERIA",
    "POSITION_VALUES", "CONCLUSION_CLASSES", "RF_ITEMS",
    # 供**报告核对层**（skill/reconcile）核对"分带来源=引擎"与边界值登记（P1-A0）
    "engine_numbers", "BAND_SETS", "band_of", "band_label", "band_set",
    "verify_consistency", "临界判据", "EGFR_SLOPE", "TG胰腺炎阈值", "非HDL_C",
    "包年数", "早发家族史",
}



def _definitions(path: str) -> list[tuple[str, int]]:
    """模块级函数与类（不含下划线开头的）。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            if not n.name.startswith("_"):
                out.append((n.name, n.lineno))
    return out


def _all_sources() -> dict[str, str]:
    """所有用于"找引用"的文本：文件路径 → 内容。"""
    out: dict[str, str] = {}
    for d in SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                p = os.path.join(d, fn)
                try:
                    out[p] = open(p, encoding="utf-8").read()
                except OSError:
                    pass
    for fn in SEARCH_FILES:
        p = os.path.join(ROOT, fn)
        if os.path.exists(p):
            out[p] = open(p, encoding="utf-8").read()
    # **也要包含 checkup/ 自身** —— 模块内自用同样算接线
    for fn in os.listdir(PKG):
        if fn.endswith(".py"):
            p = os.path.join(PKG, fn)
            out[p] = open(p, encoding="utf-8").read()
    return out


def main() -> int:
    sources = _all_sources()
    pkg_files = {}
    for fn in sorted(os.listdir(PKG)):
        if fn.endswith(".py"):
            p = os.path.join(PKG, fn)
            pkg_files[p] = open(p, encoding="utf-8").read()

    # **也扫顶层脚本与 tools/** —— 它们的辅助函数同样可能悬空
    extra_files = {}
    for d, pat in ((ROOT, ".py"), (os.path.join(ROOT, "tools"), ".py")):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(pat):
                p = os.path.join(d, fn)
                if os.path.isfile(p) and p not in pkg_files:
                    extra_files[p] = open(p, encoding="utf-8").read()
    scan_files = {**pkg_files, **extra_files}

    unwired = []
    total = 0
    for path, src in scan_files.items():
        for name, lineno in _definitions(path):
            if name in ALLOW:
                continue
            total += 1
            # 在**全部**源码里数这个名字出现几次：
            # 定义处 1 次 + 至少 1 次使用 = 接线。
            # 早先的写法排除了本模块，于是"模块内自用"被误判成未接线 ——
            # 这类检查的假阳性一旦多了，人就不看了。
            pat = re.compile(rf"\b{re.escape(name)}\b")
            n = sum(len(pat.findall(txt)) for txt in sources.values())
            # 本模块自己的定义也算在 sources 里（ROOT 下的 .py 用 glob 覆盖不到
            # checkup/ 时，至少 src 里有一份）
            if pat.search(src):
                n = max(n, 1)
            if n <= 1:
                rel = os.path.relpath(path, ROOT)
                unwired.append((rel, name, lineno))

    print(f"检查 {total} 个模块级函数/类"
          f"（checkup {len(pkg_files)} 个文件 + 顶层/tools {len(extra_files)} 个）")
    print("=" * 84)
    if not unwired:
        print("✓ 没有「声明了却没接线」的东西")
        print("  （定义之外至少还有一次引用即算接线，模块内自用也算）")
        return 0
    print("以下定义在 `checkup/` 之外**完全没有被引用**：")
    print()
    for fn, name, lineno in unwired:
        print(f"  ⚠ {fn}:{lineno}  {name}")
    print()
    print("判断方式：它是不是一关悬空的关卡？还是一条只在文档里存在的通路？")
    print("若确实是有意的对外 API，加进 `ALLOW` 并写明理由。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
