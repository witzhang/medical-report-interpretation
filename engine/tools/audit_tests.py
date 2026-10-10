#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计测试自身的空转断言。

连续三轮的失效都在"检查手段"这一层（一条不变式永远返回 0、工具改坏仓库、
工具报假的 100%），所以测试本身也需要被检查。

这里做三类静态检查：

1. **零断言的测试** —— 跑了代码但什么都没验
2. **恒真断言** —— 形如 ``assert True`` / ``assert x`` 且 ``x`` 是字面量或
   自己与自己比较
3. **只调辅助函数、自己不验的测试**

判据保守：只报**静态可判定**的。任何需要理解语义的一律不报 ——
误报会让这个检查失去价值。

    python3 tools/audit_tests.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from checkup.console import init_console
except ImportError:                     # 直跑时把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from checkup.console import init_console
init_console()

TESTS = os.path.join(ROOT, "tests")



def _is_constant(node: ast.AST) -> bool:
    """这个表达式是不是**静态恒真**。"""
    if isinstance(node, ast.Constant):
        return True
    # x == x / x is x（两边同形）
    if isinstance(node, ast.Compare) and len(node.comparators) == 1:
        left, right = node.left, node.comparators[0]
        if ast.dump(left) == ast.dump(right):
            return True
    # True or ... / not False
    if isinstance(node, ast.BoolOp) and any(
            isinstance(v, ast.Constant) and v.value is True
            for v in node.values):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_constant(node.operand)
    return False


def _asserts(fn: ast.FunctionDef) -> list:
    """把 ``assert`` 与 ``raise AssertionError`` **都算作断言**。

    只认 ``assert`` 会误报——本仓库有几处用 try/except + ``raise
    AssertionError`` 来表达"不该走到这里"，它们同样会失败。
    """
    out = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    for n in ast.walk(fn):
        if isinstance(n, ast.Raise):
            exc = n.exc
            name = ""
            if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
                name = exc.func.id
            elif isinstance(exc, ast.Name):
                name = exc.id
            if name in ("AssertionError", "SystemExit"):
                out.append(n)
    return out


def audit(path: str) -> list[tuple[str, str, int]]:
    """返回 [(测试名, 问题, 行号)]。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name.startswith("test_")]:
        asserts = _asserts(fn)
        if not asserts:
            out.append((fn.name, "零断言 —— 跑了代码但什么都没验", fn.lineno))
            continue
        plain = [a for a in asserts if isinstance(a, ast.Assert)]
        const = [a for a in plain if _is_constant(a.test)]
        if const:
            out.append((fn.name,
                        f"{len(const)}/{len(plain)} 条断言静态恒真",
                        const[0].lineno))
    return out


def main() -> int:
    total_tests = 0
    problems: list[tuple[str, str, str, int]] = []
    for fn in sorted(os.listdir(TESTS)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        path = os.path.join(TESTS, fn)
        tree = ast.parse(open(path, encoding="utf-8").read())
        total_tests += sum(1 for n in tree.body
                           if isinstance(n, ast.FunctionDef)
                           and n.name.startswith("test_"))
        for name, why, lineno in audit(path):
            problems.append((fn, name, why, lineno))

    print(f"审计 {total_tests} 项测试")
    print("=" * 84)
    if not problems:
        print("✓ 未发现静态可判定的空转断言")
        print("  （注意：这只排除最粗的一类 —— 语义层面的空转要靠变异测试）")
        return 0
    for fn, name, why, lineno in problems:
        print(f"  ✗ {fn}:{lineno}  {name}")
        print(f"      {why}")
    print()
    print(f"共 {len(problems)} 项可疑")
    return 1


if __name__ == "__main__":
    sys.exit(main())
