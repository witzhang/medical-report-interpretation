#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异测试：找出**空转的断言**。

上一轮发现一条不变式永远返回 0 条（按一个不存在的字段找），它给人一种
"查过了"的错觉。这个项目有 300+ 项测试，同类问题可能还有。

**怎么系统性找**：往源码里注入故障（mutant），跑测试。测试**没抓住**的
故障叫"存活变异体"，那里就是覆盖空洞 —— 要么没测，要么测了但断言恒真。

    python3 tools/mutate.py checkup/lipid_table.py
    python3 tools/mutate.py checkup/judge.py --limit 40

安全措施：只改一个文件，且**无论如何都会还原**（``try/finally`` + 启动时备份）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

#: 跨界测试 —— 它们对任何模块都该有效，故每次都跑
ALWAYS = ["test_invariants.py", "test_degradation.py"]


# ---------------------------------------------------------------- 变异算子


def _child_env() -> dict:
    """子进程环境：显式 UTF-8（Windows 上子进程打印符号不崩）。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env

def _mutations_for_line(line: str) -> list[tuple[str, str]]:
    """给一行生成候选变异（标签, 替换后的行）。"""
    out: list[tuple[str, str]] = []
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return out

    # 比较运算符互换
    for a, b in ((" <= ", " < "), (" >= ", " > "), (" < ", " <= "),
                 (" > ", " >= "), (" == ", " != "), (" != ", " == ")):
        if a in line:
            out.append((f"{a.strip()}→{b.strip()}", line.replace(a, b, 1)))

    # 逻辑运算
    for a, b in ((" and ", " or "), (" or ", " and ")):
        if a in line:
            out.append((f"{a.strip()}→{b.strip()}", line.replace(a, b, 1)))

    # 布尔字面量
    if "True" in line:
        out.append(("True→False", line.replace("True", "False", 1)))
    if "False" in line:
        out.append(("False→True", line.replace("False", "True", 1)))

    # 加减号
    if " - " in line:
        out.append(("- → +", line.replace(" - ", " + ", 1)))
    if " + " in line:
        out.append(("+ → -", line.replace(" + ", " - ", 1)))

    # 数值字面量 ±1（只挑有意义的：0/1 不动，容易造成语法错或等价）
    def bump(m):
        v = m.group(0)
        try:
            f = float(v)
        except ValueError:
            return v
        if f in (0.0, 1.0):
            return v
        nv = f + 1 if f >= 0 else f - 1
        return (f"{nv:g}" if "." in v or "e" in v.lower() else f"{int(nv)}")
    if re.search(r"\b\d+(\.\d+)?\b", line):
        new = re.sub(r"\b\d+\.\d+\b", bump, line, count=1)
        if new != line:
            out.append(("数值+1", new))
    return out


def _in_docstring_mask(lines: list[str]) -> list[bool]:
    """标出哪些行处在三引号字符串（docstring）内。

    不排除的话会产出大量**假阳性存活**：文档里的 ``C1 3.1<=TC<4.1``
    被改数字当然没人抓 —— 那不是覆盖空洞，那是文档。
    """
    DQ = '"' * 3
    SQ = "'" * 3
    mask, inside = [], False
    for line in lines:
        n = line.count(DQ) + line.count(SQ)
        mask.append(inside or n > 0)
        if n % 2 == 1:
            inside = not inside
    return mask


def generate(src: str) -> list[tuple[str, int, str, str]]:
    """生成全部变异体：(标签, 行号, 原行, 变异行)。"""
    lines = src.split("\n")
    skip = _in_docstring_mask(lines)
    out = []
    for i, line in enumerate(lines, 1):
        if skip[i - 1]:
            continue
        for label, new in _mutations_for_line(line):
            if new != line:
                out.append((label, i, line, new))
    return out


# ---------------------------------------------------------------- 跑测试

def relevant_tests(module: str) -> list[str]:
    """挑出与目标模块有关的测试文件。

    判据：测试文件里出现模块名，或 import 了它。太窄会漏（存活的变异体
    其实被别的测试抓到了），太宽会慢。这里取"提到就算"。
    """
    stem = os.path.splitext(os.path.basename(module))[0]
    picked = []
    for fn in sorted(os.listdir(TESTS)):
        if not fn.endswith(".py") or not fn.startswith("test_"):
            continue
        txt = open(os.path.join(TESTS, fn), encoding="utf-8").read()
        if stem in txt or fn in ALWAYS:
            picked.append(fn)
    return picked or [f for f in ALWAYS if os.path.exists(os.path.join(TESTS, f))]


#: 单个测试文件的超时。原先 180 秒 × 5 个文件 = 一个卡死的变异体要烧 15 分钟。
#: 实测所有测试文件都在 2 秒内跑完，20 秒足够，卡住就判它"杀死"并继续。
PER_TEST_TIMEOUT = 20

#: 当前沙箱目录（由 ``mutate_file`` 设置）。测试在**副本**里跑。
_SANDBOX = ""


def _purge_pycache(root: str) -> None:
    """清掉 ``__pycache__`` —— 否则会**读到上一个变异体的字节码**。

    CPython 按「源文件 mtime + 大小」判字节码是否失效。很多变异**不改变文件大小**
    （``+`` → ``-``、``and`` → ``or``、``>=`` → ``>`` 都是），若两次写入落在同一秒内，
    Python 会复用陈旧字节码 —— 于是变异体 N 的测试结果其实是变异体 N-1 的。

    ⚠ 这个 bug 不报错，只会给一个**看起来合理的错判定**。实测让同一处变异体在
    两次运行里一次"存活"、一次"杀死"。
    """
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


def run_tests(files: list[str]) -> bool:
    """全过返回 True。超时按"杀死"处理（变异体导致挂起 = 测试察觉到异常）。"""
    root = _SANDBOX or ROOT
    _purge_pycache(root)
    for fn in files:
        try:
            p = subprocess.run([sys.executable,
                                os.path.join(root, "tests", fn)],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=PER_TEST_TIMEOUT,
                               env=_child_env())
        except subprocess.TimeoutExpired:
            return False
        if p.returncode != 0:
            return False
    return True


# ---------------------------------------------------------------- 主程序

#: 测试会去数据根（``CHECKUP_FIXTURES``，缺省 = 引擎目录的上一级）找这些同级
#: 目录 —— 沙箱必须把它们挂上，否则测试在沙箱里**基线就不过**，
#: 于是每个变异体都被"假杀"。
#: 报告目录名由数据根**探测**（``reports/`` 或 ``*体检报告*``），不写死。
def _sibling_dirs() -> list[str]:
    try:
        from devpaths import FIXTURES
    except ImportError:                 # 直跑 tools/ 时把引擎根加进 sys.path
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from devpaths import FIXTURES
    dirs = ["体检分析"]
    rr = FIXTURES.real_reports
    if rr:
        dirs.insert(0, os.path.basename(rr.rstrip("/\\")))
    return dirs


SIBLING_DIRS = _sibling_dirs()


def _sandbox() -> str:
    """复制工程到临时目录，并**保留工作区结构** —— 变异只发生在副本里。

    两件事都是事故换来的：

    1. **副本而非原地**：变异测试原本直接改仓库文件、跑完再还原。进程被强杀
       （SIGKILL 捕不到）时还原不发生，仓库留在损坏状态；更糟的是那次损坏
       只改了一句 note，**416 项测试全过**，没人发现。
    2. **保留同级目录**：测试用 ``dirname(BASE)`` 找报告 PDF 与旧管线 CSV。
       只复制引擎目录的话，测试在沙箱里**基线就失败** → 每个变异体都被报成
       "杀死" → 工具输出一个**假的 100%**。这比没有工具更糟。

    所以同级目录用**符号链接**挂进来（只读使用，不复制几十 MB）。
    """
    tmp = tempfile.mkdtemp(prefix="mutate_")
    work = os.path.join(tmp, "Zcode")
    os.makedirs(work)
    dst = os.path.join(work, os.path.basename(ROOT))
    # **必须带上 ``out/``** —— 有测试要读它的产物
    # （`test_normalize.py` 读 `out/normalized_observations.csv`）。
    # 漏掉它会让沙箱基线失败，进而触发守卫中止（这次是守卫救的场）。
    shutil.copytree(ROOT, dst,
                    ignore=shutil.ignore_patterns("__pycache__", ".git"))
    for name in SIBLING_DIRS:
        real = os.path.join(os.path.dirname(ROOT), name)
        if os.path.exists(real):
            os.symlink(real, os.path.join(work, name))
    return dst


def mutate_file(path: str, limit: int | None = None, only: str | None = None,
                skip: int = 0) -> tuple[int, int, list[tuple[str, int]]]:
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    sandbox = _sandbox()
    path = os.path.join(sandbox, rel)
    original = open(path, encoding="utf-8").read()
    mutants = generate(original)
    if only:
        mutants = [m for m in mutants if only in m[0]]
    if skip:
        mutants = mutants[skip:]
    if limit:
        mutants = mutants[:limit]

    tests = relevant_tests(path)
    # **必须在这里就设**：基线检查也要在沙箱里跑。设晚了的话，基线在真仓库
    # 里通过、变异体在沙箱里全失败 —— 工具会报一个假的 100%。
    global _SANDBOX
    _SANDBOX = sandbox
    print(f"目标 {rel} · 变异体 {len(mutants)} 个 · "
          f"相关测试 {len(tests)} 个：{', '.join(tests)}")
    print(f"（在副本里跑：{sandbox} —— **仓库不会被改动**）")
    print("（先确认**副本里**的基线通过）", end=" ")
    if not run_tests(tests):
        print("✗ 副本里基线就没过 —— 中止，"
              "**否则每个变异体都会被假杀**")
        shutil.rmtree(os.path.dirname(sandbox), ignore_errors=True)
        _SANDBOX = ""
        return 0, 0, []
    # 二次确认：真仓库的基线也必须过（防止副本漏挂了什么）
    _SANDBOX = ""
    if not run_tests(tests):
        print("✗ 真仓库基线没过，中止")
        shutil.rmtree(os.path.dirname(sandbox), ignore_errors=True)
        return 0, 0, []
    _SANDBOX = sandbox
    print("✓（真仓库基线亦通过）\n")

    killed, survived = 0, []

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, lambda *_a: sys.exit(1))
        except (ValueError, OSError):
            pass
    try:
        for n, (label, lineno, old, new) in enumerate(mutants, 1):
            open(path, "w", encoding="utf-8").write(
                original.replace(old, new, 1))
            try:
                ok = run_tests(tests)
            except subprocess.TimeoutExpired:
                ok = False
            if ok:
                survived.append((label, lineno))
                mark = "存活 ←"
            else:
                killed += 1
                mark = "杀死"
            print(f"  [{n:>3}/{len(mutants)}] 第{lineno:>4}行 {label:<14} {mark}")
    finally:
        # 副本直接删掉即可 —— **仓库从头到尾没被碰过**
        shutil.rmtree(os.path.dirname(sandbox), ignore_errors=True)
        _SANDBOX = ""
    return killed, len(mutants), survived


#: 自检用的两个**已知判定**。
#:
#: 工具连续两轮"报假"（一次把仓库改坏还让 416 项测试全过，一次报出假的 100%），
#: 所以它本身需要一个校准：**等价变异必须报存活、致命变异必须报杀死**。
#: 只验一侧不够 —— 一个"永远报存活"的工具看起来也能抓住致命变异。
SELFTEST = [
    # (模块, 找什么, 换成什么, 期望判定, 为什么这个判定是已知的)
    ("checkup/lipid_table.py",
     "if tc < CHOL_STRATA[0].tc_low:",
     "if tc <= CHOL_STRATA[0].tc_low:",
     "存活",
     "这一行只在 hit is None 时求值，而 tc==3.1 时 hit=0（本来就属 C1），"
     "该行**根本不会求值** —— 改动无可观察效果"),
    ("checkup/judge.py",
     "Z = 1.96",
     "Z = 2.96",
     "杀死",
     "Z 进入每一个 RCV 阈值，改了之后所有变化轴判定都会变"),
]


def _apply(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"自检锚点不唯一：{old!r} 出现 {text.count(old)} 次"
    return text.replace(old, new, 1)


def selftest() -> int:
    """校准工具本身。返回 0 = 通过。"""
    print("=" * 72)
    print("工具自检：等价变异须存活、致命变异须杀死")
    print("=" * 72)
    failures = []
    for rel, old, new, want, why in SELFTEST:
        sandbox = _sandbox()
        global _SANDBOX
        _SANDBOX = sandbox
        path = os.path.join(sandbox, rel)
        try:
            original = open(path, encoding="utf-8").read()
            tests = relevant_tests(path)
            if not run_tests(tests):
                failures.append((rel, "沙箱基线未通过 —— 结论不可信"))
                print(f"  ✗ {rel}: 沙箱基线未通过")
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(_apply(original, old, new))
            got = "杀死" if not run_tests(tests) else "存活"
            mark = "✓" if got == want else "✗"
            if got != want:
                failures.append((rel, f"期望 {want}，实得 {got}"))
            print(f"  {mark} {rel}")
            print(f"      改动：{old.strip()}  →  {new.strip()}")
            print(f"      期望 {want} · 实得 {got} · 理由：{why[:52]}…")
        finally:
            shutil.rmtree(os.path.dirname(sandbox), ignore_errors=True)
            _SANDBOX = ""
    print()
    if failures:
        print(f"✗ 自检未通过：{failures}")
        print("  **在此状态下，任何杀死率数字都不可信。**")
        return 1
    print("✓ 自检通过：等价变异报存活、致命变异报杀死 —— 两侧都对")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default="")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="只跑标签含该串的变异")
    ap.add_argument("--skip", type=int, default=0,
                    help="跳过前 N 个变异体（续跑用）")
    ap.add_argument("--selftest", action="store_true",
                    help="校准工具本身（等价变异须存活、致命变异须杀死）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not os.path.exists(a.module):
        a.module = os.path.join(ROOT, a.module)
    killed, total, survived = mutate_file(a.module, a.limit, a.only, a.skip)
    print()
    print("=" * 72)
    if not total:
        print("没有可用的变异体")
        return 1
    rate = killed / total * 100
    print(f"杀死 {killed}/{total}（{rate:.0f}%）· 存活 {len(survived)}")
    if survived:
        print()
        print("**存活 = 覆盖空洞**，逐个看：")
        for label, lineno in survived:
            print(f"  第 {lineno} 行  {label}")
    return 0 if not survived else 2


if __name__ == "__main__":
    sys.exit(main())
