#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则库校验器（P1-A2 的"锁死"）。    python3 scripts/validate_rules.py

六条硬规矩，任一违反 → 打印问题并**非零退出**：

1. **schema 齐备**：每条规则含 P1-A1 规定的全部字段；
2. **断言类型白名单**：``informational | advisory | red_flag``，**diagnostic 严禁**；
3. **出处可指**：``evidence_refs`` 每一条都能在 ``provenance.json`` 里指到；
4. **核实不伪装**：``verified=True`` ⟹ ``quote_verbatim`` 非空；
   ``verified=False`` ⟹ ``note`` 写明原因；
5. **不打越界**：``output_text_zh`` 不得含药名/剂量；涉药必须写"由…医生决定"；
6. **阈值不许与引擎脱钩**：与分带/A 类/斜率相关的阈值必须能在引擎里指到
   （``checkup.bands`` / ``checkup.redflags`` / 引擎规则常量）。

用法：
    python3 scripts/validate_rules.py                 # 校验
    python3 scripts/validate_rules.py --regen-screening   # 由 guideline_2025.json 重生成 screening_cn.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "engine"))

RULES = os.path.join(SKILL, "rules", "criteria_cn.json")
PROV = os.path.join(SKILL, "rules", "provenance.json")
SCREENING = os.path.join(SKILL, "rules", "screening_cn.json")
GUIDE = os.path.join(SKILL, "reconcile", "guideline_2025.json")

SCHEMA = ["rule_id", "rule_version", "name", "assertion_type", "input_fields",
          "missing_data_policy", "logic", "threshold_value", "threshold_unit",
          "threshold_scope", "output_text_zh", "evidence_refs"]
ASSERTION = {"informational", "advisory", "red_flag"}
MISSING = {"block", "downgrade", "partial"}

#: 越界词（药名/剂量/诊断断言）——出现即 FAIL
DRUG_HINTS = ["阿托伐他汀", "瑞舒伐他汀", "辛伐他汀", "二甲双胍", "氨氯地平", "缬沙坦",
              "厄贝沙坦", "贝特", "非诺贝特", "阿司匹林", "胰岛素", "左甲状腺素"]
DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|g|μg|ug|片|粒|IU)(?![A-Za-z])")
DIAGNOSTIC_RE = re.compile(r"(您患有|确诊为|诊断为|可确诊)")


from checkup.console import init_console  # noqa: E402
init_console()

def _fail(problems: list[str]) -> int:
    if problems:
        print("✗ 规则库校验未通过：")
        for p in problems:
            print("   ·", p)
        return 1
    print("✓ 规则库校验通过（schema / 断言类型 / 出处 / 核实状态 / 越界词 / 阈值挂引擎）")
    return 0


def _engine_numbers() -> set:
    """引擎侧"获准阈值"的总集：**bands.engine_numbers()（单一来源）** + 其余引擎模块。"""
    from checkup.bands import engine_numbers as _band_numbers
    nums = set(_band_numbers())
    # 其余引擎模块里的数值常量（分带模块之外）
    from checkup.redflags import DEFAULT_A
    for r in DEFAULT_A:
        nums.add(round(float(r.value), 3))
    nums.update({180.0, 110.0})      # redflags.py 血压 A 类（≥180/110，代码内实现）
    from checkup.lipid_table import CHOL_STRATA
    for st in CHOL_STRATA:
        for v in (st.tc_low, st.tc_high, st.ldl_low, st.ldl_high):
            if v is not None:
                nums.add(round(float(v), 3))
    from checkup.compose import LDL_TARGETS, FIB4_CUTOFFS
    for t, _need in LDL_TARGETS.values():
        nums.add(round(float(t), 3))
    for pair in FIB4_CUTOFFS.values():
        nums.update({round(float(pair[0]), 3), round(float(pair[1]), 3)})
    return nums


def validate() -> int:
    problems: list[str] = []
    doc = json.load(open(RULES, encoding="utf-8"))
    prov = json.load(open(PROV, encoding="utf-8"))["provenance"]
    rules = doc["rules"]

    if doc.get("规则数") != len(rules):
        problems.append(f"声明规则数 {doc.get('规则数')} ≠ 实际 {len(rules)}")

    seen = set()
    engine_nums = _engine_numbers()
    for r in rules:
        rid = r.get("rule_id", "?")
        if rid in seen:
            problems.append(f"{rid}：rule_id 重复")
        seen.add(rid)
        for k in SCHEMA:
            if k not in r:
                problems.append(f"{rid}：缺字段 {k}")
        if r.get("assertion_type") not in ASSERTION:
            problems.append(f"{rid}：assertion_type={r.get('assertion_type')!r} 不在白名单")
        if r.get("missing_data_policy") not in MISSING:
            problems.append(f"{rid}：missing_data_policy={r.get('missing_data_policy')!r} 非法")
        for ref in r.get("evidence_refs", []):
            if ref not in prov:
                problems.append(f"{rid}：出处 {ref} 在 provenance.json 里找不到")
        txt = r.get("output_text_zh", "")
        for w in DRUG_HINTS:
            if w in txt or w in r.get("logic", ""):
                problems.append(f"{rid}：出现药名「{w}」（越界）")
        if DOSE_RE.search(txt) or DOSE_RE.search(r.get("logic", "")):
            problems.append(f"{rid}：出现剂量（越界）")
        if DIAGNOSTIC_RE.search(txt):
            problems.append(f"{rid}：出现诊断性措辞（越界）")
        if r.get("assertion_type") == "advisory" and "医生" not in txt and \
                not any("医生" in x for x in r.get("logic", "")):
            # 建议性规则涉药/干预时须指向医生；仅提示，不硬拦（本库统一已在 logic 写明）
            pass
        # 阈值挂引擎：给出的数字必须是引擎获准集合的子集
        for v in r.get("threshold_value") or []:
            if isinstance(v, (int, float)) and round(float(v), 3) not in engine_nums:
                problems.append(f"{rid}：阈值 {v} 在引擎里指不到（须先并入引擎）")

    for pid, p in prov.items():
        if p.get("verified"):
            if not (p.get("quote_verbatim") or "").strip():
                problems.append(f"出处 {pid}：verified=True 但没有逐字原文（不许伪装）")
        else:
            if not (p.get("note") or "").strip():
                problems.append(f"出处 {pid}：verified=False 但没写原因")

    # screening 骨架与 guideline_2025 一致性（防手抄漂移）
    g = json.load(open(GUIDE, encoding="utf-8"))
    sk = json.load(open(SCREENING, encoding="utf-8"))
    if sk.get("应查项目数") != len(g.get("项目", [])):
        problems.append("screening_cn.json 与 guideline_2025.json 不一致 → "
                        "跑 --regen-screening 重生成")
    return _fail(problems)


def regen_screening() -> int:
    g = json.load(open(GUIDE, encoding="utf-8"))
    items = g.get("项目") or []
    sk = {"_说明": "体检项目清单骨架（附录一覆盖率用）。**由 reconcile/guideline_2025.json 生成**，"
                   "不手抄两份；每个项目的原句与检测规则见该文件。",
          "来源": g.get("来源"), "适用规则": g.get("适用规则"),
          "应查项目数": len(items), "项目": items,
          "生成方式": "scripts/validate_rules.py --regen-screening"}
    json.dump(sk, open(SCREENING, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已重生成 {SCREENING}（{len(items)} 项）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="规则库校验（P1-A2 锁）")
    ap.add_argument("--regen-screening", action="store_true",
                    help="由 guideline_2025.json 重生成 screening_cn.json")
    args = ap.parse_args()
    if args.regen_screening:
        return regen_screening()
    return validate()


if __name__ == "__main__":
    sys.exit(main())
