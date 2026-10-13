#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双跑可复现性检查（P0-1d）：同一份数据跑两遍，级别必须逐项一致。

    python3 scripts/repeatability_check.py [--src <报告目录>] [--keep]

比什么（**只比脚本产出的事实**，不比 agent 写的文字）：
  · `states.json` 的 `项目状态[*].报告级别` / `状态分类` / `分带` / `逐年.位置`
  · `states.json` 的 `影像台账[*].报告级别` / `级别判据`
  · `coverage.json` 的覆盖率四元组

为什么需要它：本轮首测发现**同一份数据两次运行给出不同的影像级别**（甲状腺 🟢 vs 🟡）——
根因是"影像级别由 agent 心算"。级别落脚本（P0-1b）之后，这一条把"可复现"钉死为验收。

退出码：0 = 两遍逐项一致；1 = 有差异（打印差异项）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
ENGINE = os.path.join(SKILL, "engine")
RECONCILE = os.path.join(SKILL, "reconcile")
sys.path.insert(0, RECONCILE)

from paths import FIXTURES  # noqa: E402


from checkup.console import init_console, run_text  # noqa: E402
init_console()

def _produce(src: str, out: str) -> str:
    """跑一遍：引擎三件套 + 四张底稿 → 返回底稿目录。"""
    env = dict(os.environ)
    for cmd in (
        [sys.executable, os.path.join(ENGINE, "run_narrative.py"), "--out", out, "--quiet"],
        [sys.executable, os.path.join(ENGINE, "run_normalize.py"), "--out", out],
        [sys.executable, os.path.join(ENGINE, "run_pipeline.py"), "--src", src,
         "--out", out, "--quiet"],
    ):
        r = run_text(cmd, cwd=ENGINE, timeout=600, env=env)
        if r.returncode != 0:
            raise SystemExit(f"✗ 引擎步骤失败（退出码 {r.returncode}）：{' '.join(cmd[1:3])}\n"
                             f"{r.stdout[-400:]}\n{r.stderr[-400:]}")
    art = os.path.join(out, "artifacts", "repeat")
    r = run_text([sys.executable, os.path.join(RECONCILE, "gen_artifacts.py"),
                  "--engine-out", out, "--case-name", "repeat", "--out", art],
                 cwd=RECONCILE, timeout=600, env=env)
    if r.returncode != 0:
        raise SystemExit(f"✗ gen_artifacts 失败：{r.stdout[-300:]}{r.stderr[-300:]}")
    return art


def _facts(art: str) -> dict:
    st = json.load(open(os.path.join(art, "states.json"), encoding="utf-8"))
    cov = json.load(open(os.path.join(art, "coverage.json"), encoding="utf-8"))
    return {
        "项目状态": {r["项目"]: {"报告级别": r.get("报告级别"), "状态分类": r.get("状态分类"),
                            "分带": {y: v.get("分带") for y, v in r.get("逐年", {}).items()},
                            "位置": {y: v.get("位置") for y, v in r.get("逐年", {}).items()}}
                 for r in st.get("项目状态", [])},
        "影像台账": {e["发现"]: {"报告级别": e.get("报告级别"), "级别判据": e.get("级别判据")}
                 for e in st.get("影像台账", [])},
        "覆盖率": {k: cov.get(k) for k in ("应查", "全覆盖", "部分覆盖", "缺", "覆盖率")},
    }


def _diff(a: dict, b: dict) -> list[str]:
    bad = []
    for grp in ("项目状态", "影像台账"):
        ka, kb = set(a[grp]), set(b[grp])
        for missing in sorted(ka ^ kb):
            bad.append(f"{grp}：只有一遍里有「{missing}」")
        for k in sorted(ka & kb):
            for f in a[grp][k]:
                if a[grp][k][f] != b[grp][k][f]:
                    bad.append(f"{grp}[{k}].{f}：{a[grp][k][f]!r} ≠ {b[grp][k][f]!r}")
    if a["覆盖率"] != b["覆盖率"]:
        bad.append(f"覆盖率：{a['覆盖率']} ≠ {b['覆盖率']}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="双跑可复现性检查")
    ap.add_argument("--src", default="", help="报告目录（缺省 = 数据根里的真实报告目录）")
    ap.add_argument("--keep", action="store_true", help="保留两次运行的产物目录")
    args = ap.parse_args()
    src = args.src or FIXTURES.real_reports
    if not os.path.isdir(src):
        print(f"✗ 报告目录不存在：{src}（设 CHECKUP_FIXTURES 或 --src）")
        return 2
    base = tempfile.mkdtemp(prefix="ck_repeat_")
    a1, a2 = os.path.join(base, "run1"), os.path.join(base, "run2")
    print(f"数据：{src}\n两遍产物：{base}/run1 · run2")
    f1 = _facts(_produce(src, a1))
    f2 = _facts(_produce(src, a2))
    bad = _diff(f1, f2)
    if not args.keep and not bad:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
    if bad:
        print(f"✗ 两次运行**不一致** {len(bad)} 处（可复现性失败）：")
        for b in bad[:20]:
            print("   ·", b)
        return 1
    n_levels = sum(1 for v in f1["项目状态"].values() if v["报告级别"])
    n_img = sum(1 for v in f1["影像台账"].values() if v["报告级别"])
    print(f"✓ 两次运行逐项一致：项目级别 {n_levels} 项 · 影像级别 {n_img} 项 · "
          f"分带/位置/状态分类/覆盖率全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
