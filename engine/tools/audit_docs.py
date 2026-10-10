#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计文档 —— 让「文档说谎」变成立刻能发现的事。

来历：整理交付文档时，我在这几处写了不存在的东西：

* `check_reader` —— 真实函数叫 `check_carrier`
* `draft_catalog.py --src <目录>` —— 实际要 `--org` + 位置参数
* `id` 格式写成 `C-LIPID-001` —— 实际是 `C01`

三处都是**手写记忆**而非核对产物。所以这个检查器做四件事：

1. **相对链接必须可达**
2. **文档里出现的 `python3 xxx.py` 命令必须真实存在**
3. **`--flag` 必须在那个脚本的 `--help` 里**
4. **JSON 示例里的字段名必须真实存在**（拿真实产物对）

判据保守：只报**可机械判定**的。任何需要理解语义的一律不报 ——
误报多了人就不看了，检查器也就废了。

    python3 tools/audit_docs.py
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from checkup.console import init_console
except ImportError:                     # 直跑时把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from checkup.console import init_console
init_console()

WORKSPACE = os.path.dirname(ROOT)

#: 要审计的文档（相对 ROOT）
DOCS = ["AGENTS.md", "docs/数据契约.md",
        os.path.join("..", "README.md")]



_SANDBOX = ""


def _sandbox_tree() -> str:
    """整棵技能树的一次性临时副本（试跑文档里的脚本用）。

    ⚠️ **只读审计绝不能改动仓库**：不是所有脚本都认 ``--help`` ——
    实测 ``build_catalog.py --help`` 会真的执行并重建 ``catalog/analytes.json``，
    把包内已登记的新机构规则覆写没了。所以试跑一律在临时副本里做。
    """
    global _SANDBOX
    if not _SANDBOX:
        import shutil
        import tempfile
        _SANDBOX = os.path.join(tempfile.mkdtemp(prefix="audit_docs_"), "skill")
        shutil.copytree(os.path.dirname(ROOT), _SANDBOX,
                        ignore=shutil.ignore_patterns("__pycache__",
                                                      ".pytest_cache", "*.pyc", ".git"))
    return _SANDBOX


def _child_env() -> dict:
    """子进程环境：显式 UTF-8（Windows 上子进程打印符号不崩）。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env


def _rels(md: str) -> list[str]:
    """文档里的相对链接。"""
    base = os.path.dirname(os.path.join(ROOT, md))
    s = io.open(os.path.join(ROOT, md), encoding="utf-8").read()
    out = []
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", s):
        t = m.group(2)
        if t.startswith(("http", "#", "mailto")):
            continue
        out.append(os.path.normpath(
            os.path.join(base, urllib.parse.unquote(t.split("#")[0]))))
    return out


def _commands(md: str) -> list[str]:
    """文档代码块里的 `python3 xxx.py ...`。"""
    s = io.open(os.path.join(ROOT, md), encoding="utf-8").read()
    return re.findall(r"python3\s+([A-Za-z_][\w/]*\.py)([^\n`]*)", s)


def _flags(cmd_tail: str) -> list[str]:
    return re.findall(r"(--[a-z][\w-]*)", cmd_tail)


def main() -> int:
    problems: list[str] = []
    n_links = n_cmds = n_flags = 0

    # ---- 1. 链接
    for md in DOCS:
        for p in _rels(md):
            n_links += 1
            if not os.path.exists(p):
                problems.append(f"{md}: 链接失效 → "
                                f"{os.path.relpath(p, ROOT)}")

    # ---- 2 & 3. 命令与参数
    helps: dict[str, set] = {}
    for md in DOCS:
        for script, tail in _commands(md):
            # 文档里的命令可能相对**引擎根**（老写法 `python3 run_pipeline.py`）
            # 或相对**技能根**（新写法 `python3 scripts/eval_synth.py`）→ 两处都试
            path = os.path.join(ROOT, script)
            if not os.path.exists(path):
                path = os.path.join(os.path.dirname(ROOT), script)
            n_cmds += 1
            if not os.path.exists(path):
                problems.append(f"{md}: 文档里的命令不存在 → python3 {script}")
                continue
            if script not in helps:
                # ⚠️ 试跑必须在**临时副本**里：这不是所有脚本都认 --help ——
                # 实测 `build_catalog.py --help` 会**真的执行**并重建
                # catalog/analytes.json，把包内已登记的新机构规则覆写没了。
                # 只读审计绝不能改动仓库：先整体拷到临时目录再试跑。
                sandbox = _sandbox_tree()
                probe = os.path.join(sandbox, script)
                if not os.path.exists(probe):
                    probe = os.path.join(sandbox, "engine", script)
                try:
                    r = subprocess.run([sys.executable, probe, "--help"],
                                       cwd=os.path.join(sandbox, "engine"),
                                       capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
                                       env=_child_env())
                    helps[script] = set(re.findall(r"(--[a-z][\w-]*)",
                                                   r.stdout or ""))
                except Exception:  # noqa: BLE001
                    helps[script] = set()
            for f in _flags(tail):
                n_flags += 1
                if f not in helps[script]:
                    problems.append(f"{md}: python3 {script} 没有参数 {f}")

    # ---- 4. JSON 示例里的字段名
    try:
        real = json.load(open(os.path.join(ROOT, "out",
                                           "report_2026.json"),
                              encoding="utf-8"))
        real_keys = set(real.keys()) | set(real["document"].keys())
        if real.get("observations"):
            real_keys |= set(real["observations"][0].keys())
    except (OSError, ValueError, KeyError):
        real_keys = set()
    if real_keys:
        for md in DOCS:
            s = io.open(os.path.join(ROOT, md), encoding="utf-8").read()
            for blk in re.findall(r"```jsonc\n(.*?)```", s, re.S):
                for key in re.findall(r'"(\w+)":', blk):
                    if key not in real_keys and key not in {
                            "level", "checks", "name", "detail", "items"}:
                        problems.append(
                            f"{md}: JSON 示例里的字段 {key!r} 在真实产物里不存在")

    print(f"审计 {len(DOCS)} 份文档 · {n_links} 个链接 · "
          f"{n_cmds} 条命令 · {n_flags} 个参数")
    print("=" * 84)
    if not problems:
        print("✓ 文档里没有可机械判定的不实内容")
        print("  （这只查「写的东西存不存在」，查不了「说的对不对」）")
        return 0
    for p in problems:
        print(f"  ✗ {p}")
    print()
    print(f"共 {len(problems)} 处 —— 文档与产物不符")
    return 1


if __name__ == "__main__":
    sys.exit(main())
