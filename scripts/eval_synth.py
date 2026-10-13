#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小随包 eval（合成数据 → 期望输出特征），一条命令。

    python3 scripts/eval_synth.py [--out /tmp/demo]

做的事：

1. 引擎三件套跑随包合成样例（``examples/testdata``）
2. 逐条断言**期望输出特征**（``examples/expected/expected.json``）：
   年份集合、项数、映射率、年份列不空、CSV 列名、单位安全（组合层不可评估）
3. 对账层合成链（``gen_artifacts --case synth`` + ``reconcile --case synth``）全绿

退出码 0 = 全绿；1 = 有断言失败（打印失败项）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine"))

from devpaths import guard_out  # noqa: E402


from checkup.console import init_console, run_text  # noqa: E402
init_console()

def _run(cmd: list[str], quiet: bool = True) -> subprocess.CompletedProcess:
    r = run_text(cmd)
    if r.returncode != 0 and not quiet:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="随包最小 eval（合成数据）")
    import tempfile as _tf
    ap.add_argument("--out", default=os.path.join(_tf.gettempdir(), "eval_synth"),
                    help="产物目录（会被重建；缺省=系统临时目录下 eval_synth）")
    a = ap.parse_args()
    out = guard_out(os.path.abspath(a.out))
    expect = json.load(open(os.path.join(HERE, "examples", "expected",
                                         "expected.json"), encoding="utf-8"))
    src = os.path.join(HERE, "examples", "testdata")

    import shutil
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out, exist_ok=True)

    fails: list[str] = []

    def ok(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'✓' if cond else '✗'} {name}" + (f" —— {detail}" if detail else ""))
        if not cond:
            fails.append(name)

    # ---- 1) 引擎三件套
    print("=" * 72)
    print("① 引擎三件套（合成样例）")
    print("=" * 72)
    for script in ("run_narrative.py", "run_normalize.py"):
        r = _run([sys.executable, os.path.join(HERE, "engine", script),
                  "--src", src, "--out", out], quiet=False)
        ok(f"{script} 退出码 0", r.returncode == 0)
    r = _run([sys.executable, os.path.join(HERE, "engine", "run_pipeline.py"),
              "--src", src, "--out", out, "--quiet"], quiet=False)
    ok("run_pipeline.py 退出码 0", r.returncode == 0)

    # ---- 2) 期望特征
    print()
    print("=" * 72)
    print("② 期望输出特征")
    print("=" * 72)
    norm = list(csv.DictReader(open(os.path.join(out, "normalized_observations.csv"),
                                    encoding="utf-8")))
    years = sorted({r["年份"] for r in norm})
    ok("年份集合 = 数据年份", years == expect["years"], f"{years}（期望 {expect['years']}）")
    per_year = {y: sum(1 for r in norm if r["年份"] == y) for y in years}
    ok("每份报告项数 = 基线", all(per_year.get(y) == expect["items_per_report"]
                                 for y in years), f"{per_year}")
    mapped = [r for r in norm if r["匹配方式"] != "unmapped"]
    rate = len(mapped) / max(1, len(norm))
    ok(f"归一映射率 ≥ {expect['min_mapping_rate']:.0%}", rate >= expect["min_mapping_rate"],
       f"{len(mapped)}/{len(norm)}（{rate:.0%}）")

    jr = list(csv.DictReader(open(os.path.join(out, "judgements.csv"),
                                  encoding="utf-8")))
    header = list(jr[0].keys()) if jr else []
    ok("judgements.csv 含数据年份列", all(y in header for y in years), f"{header[:12]}…")
    ok("judgements.csv 不含" + expect["forbidden_year"], 
       expect["forbidden_year"] not in header)
    empty_col = any(all((r.get(y) or "") == "" for r in jr
                        if r.get("位置" + y) not in (None, "")) for y in years)
    # 位置轴判过的行必须有数值 —— 空列=年份错位的老症状
    ok("年份列无空列（位置判过的行都有值）", not empty_col)

    for view in ("view_clinical.md", "view_patient.md", "view_audit.md"):
        text = open(os.path.join(out, view), encoding="utf-8").read()
        has_years = all(y in text for y in years)
        no_forbidden = expect["forbidden_year"] not in text
        ok(f"{view}：年份列 = 数据年份", has_years and no_forbidden)

    # 单位安全：合成机构用美制单位 → 相关组合项必须"不可评估"，不得给出数值
    vc = open(os.path.join(out, "view_clinical.md"), encoding="utf-8").read()
    if expect.get("unit_safety_composites"):
        ok("单位不等价的组合项不给出数值（不可评估）",
           "不可评估" in vc and "180.0 mmol/L" not in vc)

    # ---- 3) 对账层合成链
    print()
    print("=" * 72)
    print("③ 对账层（--case synth）")
    print("=" * 72)
    art = os.path.join(out, "artifacts", "synth")
    r = _run([sys.executable, os.path.join(HERE, "reconcile", "gen_artifacts.py"),
              "--case", "synth", "--out", art], quiet=False)
    ok("gen_artifacts --case synth 退出码 0", r.returncode == 0)
    rep = os.path.join(HERE, "examples", "report")
    r = _run([sys.executable, os.path.join(HERE, "reconcile", "reconcile.py"),
              "--case", "synth", "--artifacts", art,
              "--report", os.path.join(rep, "样例_体检报告解读_合成数据_v2.md"),
              "--specs", os.path.join(rep, "样例_merge_spec.json"),
              os.path.join(rep, "样例_causal_spec.json"),
              "--out", os.path.join(out, "对账_synth.md")], quiet=False)
    ok("reconcile --case synth 退出码 0", r.returncode == 0)
    if os.path.exists(os.path.join(out, "对账_synth.md")):
        txt = open(os.path.join(out, "对账_synth.md"), encoding="utf-8").read()
        ok("对账记录：FAIL 0", "FAIL 0" in txt.replace("**", ""))

    print()
    print("-" * 72)
    if fails:
        print(f"✗ {len(fails)} 项未通过：" + "、".join(fails))
        return 1
    print("✓ 合成 eval 全绿（引擎 + 期望特征 + 对账链）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
