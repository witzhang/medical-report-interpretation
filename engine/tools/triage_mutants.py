#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""变异体分类：靠**输出快照**区分「等价」与「真缺口」。

## 为什么需要它

变异测试报出的百分比只说明"测试没抓到"。没抓到的原因有两种：

* **等价变异体** —— 改了也不改变行为（例如某个几何常量在真实数据上没有
  文字块恰好落在阈值上）
* **真缺口** —— 行为确实变了，但**没有测试看着那个行为**

上一轮我在 `narrative.py` 上靠"逐个注入 + 人工看代码"区分这两者，
把 45% 推到 56%。这一轮把它机械化。

## 做法

对每个存活变异体：

1. 在沙箱副本里注入
2. 跑一组**输出快照**（真实数据 + 降级场景 + 扰动），把判定/派生/结论序列化
3. 与基线快照逐字节比对

* **输出不同** → 行为确实变了 → **真缺口**（只是没有测试覆盖它）
* **输出相同** → 在这组输入下等价 → 大概率是**等价变异体**

第二类不是绝对的 —— 快照没覆盖到的输入上仍可能有差别。所以工具把它报成
"快照内等价"，而不是"等价"。**判据要说得比结论窄。**

    python3 tools/triage_mutants.py checkup/conclude.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from checkup.console import init_console
except ImportError:                     # 直跑时把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from checkup.console import init_console
init_console()
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from mutate import (PER_TEST_TIMEOUT, _sandbox, generate,  # noqa: E402
                    relevant_tests)

#: 快照跑在沙箱目录里，用子进程避免污染当前解释器
SNAPSHOT_SCRIPT = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from dataclasses import replace
from checkup.normalize import UNMAPPED, AnalyteCatalog
from checkup.pipeline import build_all
from checkup.narrative import extract_report
from checkup.judge import judge_all
from checkup.compose import Composer, evaluate, load_profile
from checkup.conclude import synthesize
from checkup.validate import validate
from checkup.redflags import check as redflag_check
from checkup import perturb as P

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from devpaths import FIXTURES  # noqa: E402

SRC = os.environ.get("CHECKUP_SRC") or FIXTURES.real_reports
cat = AnalyteCatalog.load(os.path.join(os.getcwd(), "catalog", "analytes.json"))
per = build_all(SRC, cat)
OBS = [o for rows in per.values() for o in rows]
from checkup.pipeline import require_routed_template  # noqa: E402
from checkup.templates import default_registry  # noqa: E402

_REG = default_registry(os.getcwd())
METAS = {y: extract_report(os.path.join(SRC, f"{fn}体检报告.pdf"), y,
                           require_routed_template(
                               os.path.join(SRC, f"{fn}体检报告.pdf"), _REG))[0]
         for y, fn in {"2024": "20240612", "2025": "20250507",
                       "2026": "20260609"}.items()}
PF = load_profile(os.environ.get("CHECKUP_PROFILE") or FIXTURES.profile)
CVI = os.path.join(os.getcwd(), "catalog", "cvi.json")



def snap(obs, metas):
    js = judge_all(obs, CVI, catalog=cat, metas=metas)
    d = evaluate(Composer(js, metas, PF))
    cs = synthesize(js, d, validation=validate(obs, metas=metas), profile=PF)
    rf = redflag_check(obs, profile=PF)
    return {
        "judgements": [[j.analyte_id, j.position, j.change, j.change_pct,
                        j.robustness, j.notes] for j in
                       sorted(js, key=lambda x: x.analyte_id)],
        "derived": [[x.rule_id, x.year, x.status, x.value, x.band, x.text,
                     sorted((str(k), str(v)) for k, v in (x.inputs or {}).items()),
                     x.notes] for x in
                    sorted(d, key=lambda z: (z.rule_id, z.year))],
        "conclusions": [[c.conclusion_id, c.cls, c.text, c.upstream,
                         c.uncertainty, c.assertion_type, c.action_hint]
                        for c in cs],
        "redflags": [[h.rule_id, h.year, h.label, h.value, h.needs_human]
                     for h in rf],
    }


CASES = {}
try:
    CASES["real"] = snap(OBS, METAS)
except Exception as e:
    CASES["real_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["single_year"] = snap([o for o in OBS if o.year == "2026"],
                                {"2026": METAS["2026"]})
except Exception as e:
    CASES["single_year_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["unmapped"] = snap([replace(o, analyte_id=UNMAPPED) for o in OBS],
                             METAS)
except Exception as e:
    CASES["unmapped_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["empty"] = snap([], {})
except Exception as e:
    CASES["empty_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["unit_swap"] = snap(P.replace_unit(OBS, "BLD.白细胞", "10^12/L",
                                             year="2026"), METAS)
except Exception as e:
    CASES["unit_swap_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["no_range"] = snap(P.drop_ref_range(OBS, "CHEM.甘油三酯"), METAS)
except Exception as e:
    CASES["no_range_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["female"] = snap(OBS, P.flip_sex(METAS))
except Exception as e:
    CASES["female_ERROR"] = f"{type(e).__name__}: {e}"
try:
    CASES["drop_section"] = snap(P.drop_section(OBS, "血常规", "2026"), METAS)
except Exception as e:
    CASES["drop_section_ERROR"] = f"{type(e).__name__}: {e}"

print(json.dumps(CASES, ensure_ascii=False, sort_keys=True, default=str))
'''


def _purge_pycache(root: str) -> None:
    """清掉沙箱里的 ``__pycache__``。

    ⚠ 这是**必须**的：沙箱被反复改同一批 .py，CPython 的字节码失效判据是
    「源文件 mtime + 大小」。若某个变异**不改变文件大小**（例如 ``+`` → ``-``），
    且两次写入落在同一秒内，Python 会**复用上一个变异体的字节码** ——
    于是快照反映的是**上一个变异体**。实测这会让结论在同一处反复横跳。
    """
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)


def snapshot(sandbox: str) -> str:
    import subprocess
    _purge_pycache(sandbox)
    p = subprocess.run([sys.executable, "-c", SNAPSHOT_SCRIPT],
                       cwd=sandbox, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=PER_TEST_TIMEOUT * 6)
    _purge_pycache(sandbox)
    if p.returncode != 0:
        return "ERROR:" + (p.stderr or "")[-300:]
    return p.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default=None, help="只跑标签含该串的")
    ap.add_argument("--line", type=int, default=None,
                    help="只看某一行的变异体（调试用）")
    ap.add_argument("--no-tests", action="store_true",
                    help="不跑测试（快得多）—— 只比对输出快照，"
                         "再拿已有的变异日志交叉对照")
    a = ap.parse_args()

    rel = os.path.relpath(os.path.abspath(a.module), ROOT)

    # 基线快照（未注入）
    sb = _sandbox()
    base = snapshot(sb)
    shutil.rmtree(os.path.dirname(sb), ignore_errors=True)
    print(f"基线快照 {len(base)} 字节")

    sb2 = _sandbox()
    path = os.path.join(sb2, rel)
    original = open(path, encoding="utf-8").read()
    mutants = generate(original)
    if a.line:
        mutants = [m for m in mutants if m[1] == a.line]
    if a.only:
        mutants = [m for m in mutants if a.only in m[0]]
    if a.limit:
        mutants = mutants[:a.limit]

    # 2×2：输出是否变 × 测试是否抓到
    bucket = {"gap": [], "caught": [], "equiv": [], "odd": []}
    tests = relevant_tests(path)
    if a.no_tests:
        print("（--no-tests：不跑测试，只比对输出快照）")
    else:
        print(f"相关测试 {len(tests)} 个：{', '.join(tests)}")
    try:
        for n, (label, lineno, old, new) in enumerate(mutants, 1):
            with open(path, "w", encoding="utf-8") as f:
                f.write(original.replace(old, new, 1))
            try:
                got = snapshot(sb2)
            except Exception as e:  # noqa: BLE001
                got = f"ERROR:{type(e).__name__}"
            changed = (got != base)
            if a.no_tests:
                caught = None
            else:
                # 跑测试要在沙箱里
                import mutate as _m
                _m._SANDBOX = sb2
                caught = not _m.run_tests(tests)
                _m._SANDBOX = ""
            rec = (lineno, label, old.strip()[:60], changed)
            if caught is None:
                bucket["gap" if changed else "equiv"].append(rec)
                print(f"  [{n:>3}/{len(mutants)}] 第{lineno:>4}行 {label:<12} "
                      f"{'**输出变了**' if changed else '输出未变'}")
                continue
            if changed and not caught:
                key = "gap"
            elif changed and caught:
                key = "caught"
            elif not changed and caught:
                key = "odd"
            else:
                key = "equiv"
            bucket[key].append(rec)
            tag = {"gap": "**真缺口**", "caught": "已抓到",
                   "equiv": "快照内等价", "odd": "输出未变却被抓到"}[key]
            print(f"  [{n:>3}/{len(mutants)}] 第{lineno:>4}行 {label:<12} {tag}")
    except Exception as e:
        print("中断:", e)
    gaps, equiv = bucket["gap"], bucket["equiv"] + bucket["odd"]
    shutil.rmtree(os.path.dirname(sb2), ignore_errors=True)

    print()
    print("=" * 84)
    print(("**输出变了**（拿变异日志对照，未被抓到的就是真缺口）："
           if a.no_tests else
           "**真缺口**（输出变了但没有测试抓到）：") + f"{len(gaps)}")
    for rec in gaps:
        lineno, label, src = rec[0], rec[1], rec[2]
        print(f"   {lineno:>4}  {label:<12} {src}")
    print()
    print(f"已抓到（输出变了且测试失败）：{len(bucket['caught'])}")
    print(f"快照内**等价**（输出没变）：{len(equiv)}")
    print()
    print("⚠ 「快照内等价」不等于「等价」—— 只说明这 8 组输入下输出一致。")
    print("   要更强的结论，得扩大快照覆盖。")
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
