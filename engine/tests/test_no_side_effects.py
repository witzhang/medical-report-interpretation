#!/usr/bin/env python3
# -*- coding: utf-8 -*-
""""工具不许悄悄改仓库"回归 —— 由一次真实事故换来（2026-10-10）。

事故：`engine/tools/audit_docs.py` 会用 `--help` 去"试跑"文档里提到的脚本；
而 `engine/build_catalog.py` 当时**没有 argparse** → `--help` 被当成普通参数、
脚本**真的执行**了一遍重建，把随包的 `catalog/analytes.json` 覆盖成"未含新机构
登记"的版本 —— 合成样例的映射率从 95% 掉到 5%，且**毫无提示**。

两条锁：
1. `--help` 必须只是打印帮助：四个"写包内文件"的工具跑过 `--help` 后，
   `catalog/analytes.json` 的哈希不得变化；
2. 随包目录必须保留合成机构的映射登记（防任何路径把它静默重建掉）。

    python3 tests/test_no_side_effects.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from checkup.console import run_text  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(BASE, "catalog", "analytes.json")

#: 会写包内文件的工具（"维护工具"）—— 探针必须安全
WRITER_TOOLS = ["build_catalog.py", "build_cvi.py", "draft_catalog.py",
                "draft_template.py"]


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def test_writer_tools_help_is_side_effect_free():
    """① `--help` 只打印帮助：跑完包内 catalog 必须一字未改。"""
    before = _sha(CATALOG)
    for tool in WRITER_TOOLS:
        run_text([sys.executable, os.path.join(BASE, tool), "--help"],
                 timeout=60, cwd=BASE)
    assert _sha(CATALOG) == before, (
        "有工具在 --help 下改动了包内 catalog/analytes.json —— "
        "必须给它加 argparse（让 --help 短路）且默认不写包内")


def test_shipped_catalog_keeps_synthetic_registrations():
    """② 随包目录保留合成机构登记（防被静默重建抹掉）。"""
    with open(CATALOG, encoding="utf-8") as f:
        d = json.load(f)
    syn = [r for r in d.get("match_rules", []) if r.get("org_id") == "SYN-002"]
    assert len(syn) >= 20, f"SYN-002 映射规则只剩 {len(syn)} 条（应 ≥20）"
    ids = {a["analyte_id"] for a in d.get("analytes", [])}
    for aid in ("CHEM.TSH", "CHEM.FT3", "CHEM.FT4", "CHEM.糖化血红蛋白"):
        assert aid in ids, f"随包目录缺 {aid}（合成机构登记被抹掉过）"


def test_documented_probes_run_in_sandbox():
    """③ audit_docs 的"试跑"必须发生在临时副本里（源码级断言）。

    这条防的是"有人把沙箱去掉"——事故的根因就是只读审计直接跑原仓库脚本。
    """
    src = open(os.path.join(BASE, "tools", "audit_docs.py"), encoding="utf-8").read()
    assert "_sandbox_tree()" in src and "shutil.copytree" in src, \
        "audit_docs 的试跑必须先拷临时副本再跑（只读审计不得改动仓库）"


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed, skipped = [], 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except BaseException as e:  # noqa: BLE001
            if type(e).__name__ in ("Skipped", "DataMissing"):
                skipped += 1
                print(f"  · {name} 跳过：{e}")
                continue
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    done = len(tests) - skipped
    print(f"\n{done - len(failed)}/{done} 通过" + (f"（跳过 {skipped}）" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
