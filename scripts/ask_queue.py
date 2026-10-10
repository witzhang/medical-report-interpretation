#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问诊队列（P0-3d）：把"该问的问题"由脚本推出来，而不是只靠规则 input_fields。

    python3 scripts/ask_queue.py --engine-out <引擎产物目录> [--out <md>] [--check]

两个来源（§2.10 的机制扩写）：

  A. **规则 `input_fields` 里"没有数据"的输入**（原机制）：年龄/性别/吸烟/家族史/饮酒…
  B. **产物推断的"高影响待问项"**（本轮新增，专治三类漏问）：
     ① **机构建议过、但数据里未见执行的检查** ——这是"拖了很久"类问题的
        来源，也最能改变结论；
     ② **能解除/降级现有黄红级的测量**（家庭血压——解除"单次读数偏高"的疑点；
        包年数——肺癌筛查资格；检测方法是否变更/是否重测——数据疑点）；
     ③ 数据质量疑点的确认项。

排序：**能降级最高级项者优先**；仍 **≤5 问**（SKILL.md 硬规矩）。

`--check`：断言"必问项"都在队列里。必问项**按数据推断**：吸烟年数 · 家族史
恒需；家庭血压仅当数据里有高值读数时才要求；机构建议的随访执行仅当数据里
确有"建议过但未见执行"的发现时才要求。缺项 → 非零退出。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "engine"))
sys.path.insert(0, os.path.join(SKILL, "reconcile"))

from paths import FIXTURES, guard_out, product_root  # noqa: E402

from checkup.bands import band_label  # noqa: E402

BP_WATCH = ("正常高值", "重点关注段", "高血压")
FU_PATTERNS = [
    (r"建议[^。\\n]{0,6}增强[^。\\n]{0,8}检查", "机构曾建议「增强进一步检查」"),
    (r"建议[^。\\n]{0,6}进一步检查", "机构曾建议「进一步检查」"),
    (r"建议您?([\u4e00-\u9fff]{2,6}科)[^。\\n]{0,4}随诊", "机构曾建议到 {0} 随诊"),
]


from checkup.console import init_console  # noqa: E402
init_console()

def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


#: 需要追踪"机构建议过、做了没有"的发现（按对结论的影响排序）
FU_TARGETS = [
    ("影像发现的进一步检查", ("低密度影",), 0,
     "机构多次建议进一步检查、数据里一直没见执行 —— 这类「别拖」项优先问"),
    ("化验异常的专科随诊", ("前列腺特异性抗原", "PSA"), 1,
     "曾被机构标「增高」并建议随诊；数据里未见执行记录"),
    ("眼科的「散瞳查眼底」", ("散瞳", "眼底"), 1,
     "分科小结里写明过这条建议（近视人群查眼底周边部需要散瞳）"),
    ("甲状腺超声的复查", ("甲状腺",), 2, "机构三年都建议每半年复查甲状腺超声"),
    ("肝血管瘤 / 胆囊息肉的复查", ("血管瘤", "息肉"), 2, "机构建议定期复查（按年执行）"),
]


def build(engine_out: str, facts: dict | None = None) -> list[dict]:
    """推断问诊队列。``facts`` 传入时记录"数据条件"，供 --check 推必问项。"""
    q: list[dict] = []

    # ---- B①：机构建议过、数据里未见执行的检查（**按发现去重**，只问一遍）
    texts = []
    for r in _rows(os.path.join(engine_out, "institution_findings.csv")):
        texts.append((r.get("年份", ""), f"{r.get('标题', '')}：{r.get('说明与建议', '')}"))
    for r in _rows(os.path.join(engine_out, "dept_summaries.csv")):
        texts.append((r.get("年份", ""), f"{r.get('科室', '')}：{r.get('小结原文', '')}"))
    for r in _rows(os.path.join(engine_out, "narrative_all.csv")):
        texts.append((r.get("年份", ""), f"{r.get('检查项目', '')}：{r.get('小结', '')}"))
    _advised = re.compile(r"(建议[^。\n]{0,8}(随诊|复查|进一步检查|增强[^。\n]{0,6}检查)|性质？)")
    _executed = re.compile(r"(增强CT|增强 CT|增强MRI|增强 MRI|已复查|术后|活检|病理|穿刺)")
    for label, kws, pri, why in FU_TARGETS:
        hit_years, clause = [], ""
        executed = False
        for year, t in texts:
            if not any(k in t for k in kws):
                continue
            # 分科小结通道：科室小结本身就是机构的建议（如"散瞳查眼底"），命中即算
            dept_hit = t.startswith(("眼科", "耳鼻喉科", "内科", "外科", "神经内科", "一般项目"))
            if _advised.search(t) or dept_hit:
                hit_years.append(year)
            if _executed.search(t):
                executed = True
        if hit_years and not executed:
            if facts is not None:
                facts["B①"] = facts.get("B①", 0) + 1
            ys = "、".join(sorted(set(hit_years)))
            q.append({"问题": f"机构 {ys} 建议过「{label}」——后来做了吗？结果如何？",
                      "为什么问": f"{why}；数据里没有执行记录",
                      "影响": "直接影响该卡片要不要继续挂着", "优先级": pri, "来源": "B①"})

    # ---- B②：家庭血压（能解除"单次诊室血压偏高"）
    bp = {(r["年份"], r["项目原文"]): r for r in _rows(
        os.path.join(engine_out, "normalized_observations.csv"))
        if r.get("项目原文") in ("收缩压", "舒张压")}
    high = []
    for (y, item), r in bp.items():
        lab = band_label(item, float(r["数值"]) if r.get("数值") else None, "男")
        if lab in ("重点关注段", "高血压"):
            high.append((y, r["数值"], lab))
    if high:
        if facts is not None:
            facts["B②"] = facts.get("B②", 0) + 1
        y, v, lab = high[-1]
        q.append({"问题": f"你平时自己测血压吗？若测过，最近 7 天的平均大概多少？"
                          f"（{y} 年那次体检是 {v}，落在「{lab}」）",
                  "为什么问": "家庭血压 7 天平均 <130/80 就能把这次「临界上升」解除；"
                            "≥135/85 则要认真对待",
                  "影响": "决定血压项是 🟡 还是 🟢，并影响血脂分层的前提", "优先级": 0,
                  "来源": "B②"})

    # ---- A：规则 input_fields 里"没有数据"的标准项
    q.append({"问题": "吸烟吗？多少年、每天大概多少支？→ 用来算包年数",
              "为什么问": "包年数决定肺癌筛查资格（中国指南 ≥30 包年）；也影响血管风险解释",
              "影响": "肺癌筛查资格 + 危险因素计数", "优先级": 1, "来源": "A"})
    q.append({"问题": "家里直系亲属（父母、兄弟姐妹、子女）有没有早发心脑血管病"
                      "（男 <55 岁、女 <65 岁发病）？",
              "为什么问": "家族史直接影响危险分层与心血管风险模型；未知时只能按「无」代入并做敏感性",
              "影响": "心血管风险值（家族史阳性会抬高）", "优先级": 1, "来源": "A"})
    q.append({"问题": "喝酒吗？大概频次和量（血脂/肝酶偏高时的常见解释项）",
              "为什么问": "饮酒是甘油三酯与谷氨酰转肽酶上升的常见解释项",
              "影响": "血脂/肝酶卡片的可能原因", "优先级": 1, "来源": "A"})
    q.append({"问题": "被标为异常的那几项，有没有在别的医院重测过？"
                      "上次体检是不是换过检测方法？",
              "为什么问": "数据疑点只能靠复查或向机构确认（措辞锁「无法核实」）",
              "影响": "危险分层的依据是否成立", "优先级": 1, "来源": "B③"})

    q.sort(key=lambda x: (x["优先级"], x["来源"]))
    for i, item in enumerate(q, 1):
        item["序号"] = i
    return q


def main() -> int:
    ap = argparse.ArgumentParser(description="问诊队列（脚本推断）")
    ap.add_argument("--engine-out", default="", help="引擎产物目录（缺省取数据根的 out/）")
    ap.add_argument("--out", default="", help="输出 md（缺省 <产物根>/待问诊.md）")
    ap.add_argument("--check", action="store_true",
                    help="断言必问项都在队列里（缺 → 非零退出）")
    args = ap.parse_args()
    engine_out = args.engine_out or FIXTURES.engine_out
    facts: dict = {}
    q = build(engine_out, facts)

    # 必问项**按数据推断**：恒需（吸烟年数/家族史）+ 条件需（有高值血压读数
    # 才要求家庭血压；确有"建议过未见执行"才要求随访）——写死四项会让别人
    # 的数据永远 --check 失败。
    REQUIRED = {"包年数": "包年数", "家族史": "家族史"}
    if facts.get("B②"):
        REQUIRED["家庭血压"] = "家庭血压"
    if facts.get("B①"):
        REQUIRED["随访执行（机构建议未做）"] = "建议过"
    miss = [k for k, kw in REQUIRED.items()
            if not any(kw in (i["问题"] + i["为什么问"]) for i in q)]
    L = ["# 待问诊队列（脚本推断，≤5 问由 agent 取前几条）", "",
         f"- 数据源：`{engine_out}`", f"- 候选 {len(q)} 条；排序 = 能降级最高级项者优先", "",
         "| # | 问题 | 为什么问 | 影响 | 来源 |", "|---|---|---|---|---|"]
    for i in q:
        L.append(f"| {i['序号']} | {i['问题']} | {i['为什么问']} | {i['影响']} | {i['来源']} |")
    if miss:
        L += ["", f"**✗ 必问项缺失：{miss}**"]
    out = guard_out(args.out or os.path.join(product_root(), "待问诊.md"))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"待问诊 {len(q)} 条（详见 {out}）")
    for i in q:
        print(f"  {i['序号']}. [{i['优先级']}] {i['问题'][:52]}")
    if args.check:
        if miss:
            print(f"✗ 必问项缺失：{miss}")
            return 1
        print(f"✓ 必问项齐备（{(' · '.join(REQUIRED))}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
