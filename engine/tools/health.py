#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令看全项目的健康度。

    python3 tools/health.py          # 快速：测试 + 三个检查器
    python3 tools/health.py --full   # 另加变异测试自检

包含六项，每项都有**失败的判据**（不是"跑了就算）：

1. **全部测试** —— 有任何一项不通过即失败
2. **变异工具自检** —— 等价变异须存活、致命变异须杀死；两侧都要对
3. **空转断言审计** —— 静态可判定的零断言/恒真断言
4. **接线审计** —— 声明了却没被引用的函数/类（G0 就是这么发现的）
5. **开放项对账** —— 扫到没登记 / 登记了扫不到
6. **仓库完整性** —— 各模块的变异体计数与基线一致（能暴露残留变异）

第 6 项值得解释：变异测试逐个用 ``original.replace()`` 生成变异体，
所以**只有最后一个会残留**。若某一行已被预置成变异形态，那一款变异体就
不会再生成 —— 计数会变。这是"仓库是否被改坏"的低成本探针。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

#: 变异体计数基线。改动源码后这个数会变 —— 那时同步更新即可，
#: 它的作用是发现**非预期**的变化。
#:
#: 第一次跑它就报了一次：`validate.py 63≠56` —— 那是本轮新增 `check_carrier`
#: 带来的**预期**变化，确认后更新基线即可。**关键是它逼人确认一次**，
#: 而不是把"数字变了"混进"看起来正常"里。
MUTANT_BASELINE = {
    "checkup/compose.py": 198,      # P0-3：Composer 单位拦下（+1）
    "checkup/judge.py": 55,         # 新机构上线：空单位年份不拦（unit_ok +1 分支）
    "checkup/conclude.py": 52,      # P0-3：动态变化键 + 单位随观测（+1）
    "checkup/lipid_table.py": 40,
    "checkup/redflags.py": 40,
    "checkup/tables.py": 52,        # 新机构上线：单位分列（unit_cells）收进 unit_cell（+7）
    "checkup/normalize.py": 29,      # B5：seed 跨机构命中标 cross_org（+4 变异体）
    "checkup/validate.py": 69,      # D3：check_drift 分套餐基线（套餐优先取自己的基线，+2）
    "checkup/render.py": 31,        # P0-3：三视图年份数据驱动（+10）
    "checkup/rules.py": 57,
}


from checkup.console import init_console, run_text  # noqa: E402
init_console()

def _child_env() -> dict:
    """子进程环境：显式 UTF-8，免得 Windows 上子进程打印 ✓ 就崩。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env


def _run_tests() -> tuple[bool, str]:
    tests = sorted(f for f in os.listdir(os.path.join(ROOT, "tests"))
                   if f.startswith("test_") and f.endswith(".py"))
    total = passed = 0
    bad = []
    for fn in tests:
        p = run_text([sys.executable, os.path.join(ROOT, "tests", fn)],
                     cwd=ROOT, timeout=300, env=_child_env())
        line = (p.stdout or "").strip().split("\n")[-1] if p.stdout else ""
        try:
            got, all_ = line.split()[0].split("/")
            total += int(all_)
            passed += int(got)
            if p.returncode != 0:
                bad.append(fn)
        except (ValueError, IndexError):
            bad.append(fn)
    return (not bad), f"{passed}/{total} 项" + (f" · 失败 {bad}" if bad else "")


def _mutant_counts() -> tuple[bool, str]:
    from mutate import generate
    bad = []
    for rel, want in MUTANT_BASELINE.items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            bad.append(f"{rel} 不存在")
            continue
        got = len(generate(open(p, encoding="utf-8").read()))
        if got != want:
            bad.append(f"{os.path.basename(rel)} {got}≠{want}")
    return (not bad), ("与基线一致" if not bad else " · ".join(bad))


def _open_items() -> tuple[bool, str]:
    import importlib
    m = importlib.import_module("checkup.openitems")
    un, st = m.unregistered(), m.stale()
    n = len(m.OPEN_ITEMS)
    if un or st:
        return False, f"{n} 条 · 未登记 {len(un)} · 过期 {len(st)}"
    return True, f"{n} 条 · 无未登记、无过期"


def _purge_pycache() -> int:
    """清掉仓库里的 ``__pycache__``。

    ⚠ 这不是"卫生"问题，是**正确性**问题。实测过两次：

    * 分类工具在沙箱里反复改同一批 .py，CPython 按「mtime + 大小」判字节码是否
      失效。``and`` → ``or`` 与 ``>=`` → ``>`` **都让文件少 1 字节** —— 同一秒内
      写入就会**复用上一个变异体的字节码**，快照反映的是上一个变异体。
    * 同样的机制让**工作仓库**读到了陈旧字节码：一句 `if risk_band == strat`
      明明该为真、输出却说"不一致"。

    所以健康检查开跑前先清一次。**这类问题不会报错，只会给出一个看起来合理的错答案。**
    """
    import shutil as _sh
    n = 0
    for dirpath, dirnames, _ in os.walk(ROOT):
        for d in list(dirnames):
            if d == "__pycache__":
                _sh.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)
                n += 1
    return n


def _parse_args():
    import argparse
    ap = argparse.ArgumentParser(
        prog="health.py",
        description="项目健康检查：测试 + 变异自检 + 三个审计器 + 开放项对账",
        epilog="每一项都有明确的**失败判据**，不是「跑了就算」。")
    ap.add_argument("--full", action="store_true",
                    help="另加变异工具自检（要跑两分钟）")
    return ap.parse_args()


def main() -> int:
    full = _parse_args().full
    purged = _purge_pycache()
    checks = [
        ("全部测试", _run_tests),
        ("开放项对账", _open_items),
        ("仓库完整性（变异体计数）", _mutant_counts),
        ("空转断言审计", lambda: _script("audit_tests.py")),
        ("文档审计", lambda: _script("audit_docs.py")),
        ("分类工具可导入", lambda: _import_ok("triage_mutants")),
        ("接线审计", lambda: _script("audit_wiring.py")),
    ]
    if full:
        checks.insert(1, ("变异工具自检", lambda: _script("mutate.py",
                                                       "--selftest")))
    ok_all = True
    print("=" * 78)
    print("项目健康检查")
    print("=" * 78)
    print(f"  （已清理 {purged} 处 __pycache__ —— 陈旧字节码会给错答案）")
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        ok_all &= ok
        print(f"  {'✓' if ok else '✗'} {name:<24} {detail}")
    print()
    print("✓ 全部通过" if ok_all else "✗ 有项目未通过 —— 上面的数字不可全信")
    return 0 if ok_all else 1


def _import_ok(mod: str) -> tuple[bool, str]:
    """工具至少要能被导入 —— 语法错误会让它静默失效。

    这条是从一次真实事故加的：我改 `triage_mutants.py` 时留下了孤立的
    `finally`，工具直接语法错误。若不是当场发现，它会一直"跑"出空结果。
    """
    import importlib
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    return True, "导入正常"


def _script(name: str, *extra: str) -> tuple[bool, str]:
    p = run_text([sys.executable, os.path.join(ROOT, "tools", name),
                  *extra], cwd=ROOT, timeout=900, env=_child_env())
    tail = [l for l in (p.stdout or "").strip().split("\n") if l.strip()]
    return p.returncode == 0, (tail[-1][:70] if tail else "无输出")


if __name__ == "__main__":
    sys.exit(main())
