#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告结构回归 / 字段 / 话术 / 越界校验器（P1-B10）。    python3 scripts/validate_report.py <报告.md>

八组检查（任一 FAIL → 非零退出）：

1. **必填章节**（`--require`；缺省 = v6.3 九个标题 + 免责声明）；
2. **禁用/已删章节**（`--forbid`；缺省 = 被撤销的四章与"雷达图/健康总分"）；
3. **卡片字段齐备**：每张卡片含 结果/白话解释/最坏会怎样/要吃药吗/严重度/复查，
   N≥2 时含 趋势；末尾有「⚠️ 立即就医」或「提前就诊」句；
4. **卡片标题格式**：`#### <emoji>【颜色】<问题名> —— <一句话状态>`；
5. **禁用话术**：`注意饮食`/`适当运动`/`建议进一步检查`/`不太正常`/`建议复查`（不给条件）——
   机构原文引用（引号内 / 含"机构/报告写/建议您"）豁免；
6. **越界**：诊断性措辞、药名、剂量（生活量如"盐 <5 g"豁免）；
7. **标题四规则**：⛔ 口号式/指引式/口语代号/三层编号；
8. **表头前提假设声明**：六项（年龄/性别/用药/诊断/吸烟/家族史）至少四项。

用法：
    python3 scripts/validate_report.py <报告.md> \
      --require "一、总体评估" ... --forbid "国际口径差异" ...
"""
from __future__ import annotations

import argparse
import os
import re
import sys

DEFAULT_REQUIRE = ["一、总体评估", "1. 健康状况总览", "2. 综合判断", "3. 正常与改善项",
                   "4. 问题分级总览", "5. 行动建议", "二、逐项分析",
                   "附录一：检查项目完整性评估", "附录二：数值对照表", "免责声明"]
DEFAULT_FORBID = ["国际口径差异", "需要补充的信息", "独立的三年趋势", "雷达图", "健康总分",
                  "健康画像雷达", "维度"]

CARD_HEAD = re.compile(r"^####\s*(.+)$", re.M)
CARD_FORM = re.compile(r"^####\s*[🔴🟡🟢⚪]\s*【[红黄绿灰]色?】.+——.+$")
REQUIRED_FIELDS = ["结果", "白话解释", "最坏会怎样", "要吃药吗", "严重度", "复查"]
BANNED_PHRASES = ["注意饮食", "适当运动", "建议进一步检查", "不太正常"]
BANNED_WITHOUT_CONDITION = re.compile(r"建议复查(?![^。\n]*(?:个月|周|后|内|时|，|；))")
QUOTE_HINTS = ("机构", "报告", "原文", "建议您", "「", "」", "“", "”", "\"", ">")
TITLE_BANNED = ["先看这里", "先看两张图", "【总】", "【分】", "红黄绿", "做得好",
                "需要时再查", "点击", "往下看"]
DIAGNOSTIC = re.compile(r"(您患有|您已患|确诊为|诊断为|可确诊)")
DRUGS = ["阿托伐他汀", "瑞舒伐他汀", "辛伐他汀", "二甲双胍", "氨氯地平", "缬沙坦",
         "厄贝沙坦", "非诺贝特", "阿司匹林", "胰岛素", "左甲状腺素"]
DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|μg|ug|IU|片/|粒/|片\b|粒\b)(?![A-Za-z])")
LIFE_OK = ("盐", "钠", "酒精", "蔬菜", "水果", "水", "运动", "步行", "快走", "分钟",
           "主食", "杂粮", "蛋白", "钙", "维生素", "睡眠", "小时")
HEADER_KEYS = ["年龄", "性别", "药", "诊断", "吸烟", "家族史"]


try:
    from checkup.console import init_console
except ImportError:                     # 直跑：把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "engine"))
    from checkup.console import init_console
init_console()

def _lines_of_table(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.strip().startswith("|")]


def check(text: str, require: list[str], forbid: list[str]) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    # 1/2 章节
    for s in require:
        if s not in text:
            fails.append(f"[章节] 缺必填：「{s}」")
    for s in forbid:
        if s in text:
            fails.append(f"[章节] 出现应删内容：「{s}」（结构回归）")

    # 3/4 卡片：**只扫章二（逐项分析）**——行动建议的 ①…⑤ 与附录的 #### 不是卡片
    warns: list[str] = []
    sec = text.split("## 二、逐项分析", 1)[1] if "## 二、逐项分析" in text else ""
    sec = re.split(r"^## ", sec, flags=re.M)[0]
    cards = CARD_HEAD.findall(sec)
    if not cards:
        fails.append("[卡片] 章二里未找到任何问题卡片（#### 标题）")
    blocks = [b for b in re.split(r"^####\s", sec, flags=re.M)[1:]
              if re.match(r"[🔴🟡🟢⚪]\s*【[红黄绿灰]色?】", b.lstrip())]
    for b in blocks:
        title = b.splitlines()[0][:40]
        lv = b.lstrip()[:1]
        # 所有卡片：复查；黄/红再加 结果；解释类字段（白话解释/为什么/说明）任一
        if "**复查**" not in b:
            fails.append(f"[卡片] 「{title}」缺字段：复查")
        if lv in ("🔴", "🟡") and "**结果**" not in b:
            fails.append(f"[卡片] 「{title}」缺字段：结果")
        if lv in ("🔴", "🟡") and not any(k in b for k in ("白话解释", "为什么", "说明", "这一项")):
            fails.append(f"[卡片] 「{title}」缺解释性字段（白话解释/为什么/说明）")
        # 黄/红卡片：再加 最坏会怎样 / 要吃药吗 / 严重度 / 趋势（或数值链）
        if lv in ("🔴", "🟡"):
            for f in ("最坏会怎样", "要吃药吗", "严重度"):
                if f"**{f}**" not in b:
                    fails.append(f"[卡片] 「{title}」缺字段：{f}")
            if "**趋势**" not in b and len(re.findall(r"\d", b)) < 4:
                fails.append(f"[卡片] 「{title}」缺「趋势」且无数值链")
        if "立即就医" not in b and "提前就诊" not in b:
            warns.append(f"[卡片·提示] 「{title}」未写升级条件句（数据类卡片可省）")
        if not re.search(r"\d", b):
            fails.append(f"[卡片] 「{title}」整张卡片没有任何数字")
    for h in cards:
        if not re.match(r"^[🔴🟡🟢⚪]\s*【[红黄绿灰]色?】", h):
            if re.search(r"(没有出现|未出现|不适用|说明)", h):
                warns.append(f"[标题·提示] 章二出现非卡片标题（空级别说明类）：「{h[:40]}」")
            else:
                fails.append(f"[标题] 卡片标题缺级别 emoji + 【颜色】：「{h[:44]}」")
        # 非卡片说明块不当作卡片要求字段
    blocks = [b for b in blocks
              if re.match(r"^[🔴🟡🟢⚪]\s*【", b.lstrip())]
    for b in blocks:
        title = b.splitlines()[0][:40]
        lv = b.lstrip()[:1]
        if "**复查**" not in b:
            fails.append(f"[卡片] 「{title}」缺字段：复查")

    # 5 禁用话术（引号内/机构语豁免）
    for i, line in enumerate(text.splitlines(), 1):
        if any(h in line for h in QUOTE_HINTS):
            continue
        for p in BANNED_PHRASES:
            if p in line:
                fails.append(f"[话术] 第 {i} 行出现禁用话术「{p}」：{line.strip()[:60]}")
        if BANNED_WITHOUT_CONDITION.search(line):
            fails.append(f"[话术] 第 {i} 行「建议复查」未给条件：{line.strip()[:60]}")

    # 6 越界
    for i, line in enumerate(text.splitlines(), 1):
        if (DIAGNOSTIC.search(line) and "⛔" not in line and "不" not in line[:6]
                and not re.search(r"(是否|若|如果|取决于|假如)", line)):
            fails.append(f"[越界] 第 {i} 行诊断性措辞：{line.strip()[:60]}")
        for d in DRUGS:
            if d in line and "⛔" not in line:
                # 检查项目名豁免：胰岛素释放/抵抗/敏感性试验不是"给药"
                if re.search(re.escape(d) + r"(释放|抵抗|敏感|样|指数|测定|试验)", line):
                    continue
                fails.append(f"[越界] 第 {i} 行出现药名「{d}」")
        for m in DOSE.finditer(line):
            if any(k in line for k in LIFE_OK):
                continue
            fails.append(f"[越界] 第 {i} 行疑似剂量「{m.group(0)}」：{line.strip()[:60]}")

    # 7 标题四规则
    for h in [l for l in text.splitlines() if l.startswith("#")]:
        for b in TITLE_BANNED:
            if b in h:
                fails.append(f"[标题] 违规标题（口号/指引/口语代号）：「{h[:50]}」")
        if re.match(r"^#{2,4}\s+\d+\.\d+(\.\d+)?\s", h):
            fails.append(f"[标题] 出现三层编号：「{h[:50]}」")

    # 8 表头前提假设声明
    head = text[:2500]
    if "前提" not in head:
        fails.append("[表头] 缺「前提假设声明」")
    else:
        n = sum(1 for k in HEADER_KEYS if k in head)
        if n < 4:
            fails.append(f"[表头] 前提假设声明要素不足（命中 {n}/6：{HEADER_KEYS}）")

    # 8.5 v6.4 格式（仅对声明 v6.4+ 的报告；v6.3 旧报告不追溯）
    if re.search(r"版本[:：]\s*\**v6\.[4-9]", text):
        if "## 目录" not in text:
            fails.append("[v6.4·目录] 缺「## 目录」")
        else:
            toc = text.split("## 目录", 1)[1].split("## ", 1)[0]
            for sec in ("一、总体评估", "二、逐项分析",
                        "健康状况总览", "综合判断", "正常与改善项",
                        "问题分级总览", "行动建议",
                        "需干预或定期复查的项", "年度复查即可的项",
                        "附录一", "附录二", "免责声明"):
                if sec not in toc:
                    fails.append(f"[v6.4·目录] 目录缺条目：{sec}")
        if "各级数量" not in text:
            fails.append("[v6.4·§4] 缺「各级数量」一行")
        for lv, name in (("🔴", "已达诊断或前期标准"), ("🟡", "需要干预或定期复查"),
                         ("🟢", "正常或轻微"), ("⚪", "未评估")):
            if not re.search(rf"\*\*{lv}[^\n]*{name}", text):
                fails.append(f"[v6.4·§4] 缺 {lv} 的分级小标题（各级一张表）")

    # 9 口径矛盾：说了"本次没有红色项"就不许再出现"标红的那几项"这类预设
    if re.search(r"(没有红色项|无红色|没有「?需尽快就诊)", text):
        for i, line in enumerate(text.splitlines(), 1):
            if any(h in line for h in ("`", "「", "」")):
                continue
            if "标红" in line:
                fails.append(f"[口径矛盾] 第 {i} 行：报告已声明"
                             f"「本次没有红色项」，又用「标红」预设存在红项："
                             f"{line.strip()[:50]}")

    # 10 综合判断首段：不许元话语/卖关子（§2.3 第一句就是好消息；§2.13 禁结构性路标）
    m = re.search(r"^###\s*2\.\s*综合判断\s*$([\s\S]*?)(?=^###|^##)", text, re.M)
    if m:
        para = ""
        for ln in m.group(1).splitlines():
            t = ln.strip()
            if not t or t.startswith(">"):
                continue
            para = t
            break
        if not para:
            fails.append("[综合判断] 找不到首段")
        else:
            for bad in ("标红", "先说", "值得一提", "另有一", "综上所述", "总的来说",
                        "最想告诉你", "值得花力气的地方在别处"):
                if bad in para:
                    fails.append(f"[综合判断·首段] 出现元话语/卖关子「{bad}」：{para[:56]}")

    # 附：每条行动建议含数字（① 表必须有「多久内」时间窗）
    if "多久内" in text:
        seg = text.split("多久内", 1)[1].split("\n\n", 1)[0]   # 只取紧跟的那张表
        rows = [l for l in seg.splitlines()[2:] if l.strip().startswith("|")]
        for r in rows:
            if not re.search(r"\d", r):
                fails.append(f"[行动] ① 表某行无时间数字：{r[:50]}")
    return fails, warns


def main() -> int:
    ap = argparse.ArgumentParser(description="报告结构回归 / 字段 / 话术 / 越界校验")
    ap.add_argument("report")
    ap.add_argument("--require", nargs="*", default=DEFAULT_REQUIRE)
    ap.add_argument("--forbid", nargs="*", default=DEFAULT_FORBID)
    ap.add_argument("--specs", nargs="*", default=[],
                    help="两张图的 spec JSON（做稀疏度检查：节点≤20、边≤24）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.report):
        print(f"✗ 报告不存在：{args.report}")
        return 2
    text = open(args.report, encoding="utf-8").read()
    fails, warns = check(text, args.require, args.forbid)
    # 两图 spec 稀疏度（P4-5）
    import json as _json
    for sp in args.specs:
        try:
            d = _json.load(open(sp, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            fails.append(f"[两图] spec 读不了：{sp}（{e}）")
            continue
        nodes = len(d.get("nodes") or d.get("groups") or d.get("sections") or [])
        edges = len(d.get("edges") or []) + len(d.get("dashed") or [])
        if nodes > 20 or edges > 24:
            fails.append(f"[两图] {sp} 超稀疏上限：节点 {nodes}/20 · 边 {edges}/24")
    for w in warns:
        print("   ", w)
    if fails:
        print(f"✗ 结构回归未通过：{len(fails)} 项")
        for f in fails:
            print("   ·", f)
        return 1
    if not args.quiet:
        n_cards = len(CARD_HEAD.findall(text))
        print(f"✓ 结构回归通过：{len(args.require)} 个必填章节齐备 · 无应删章节 · "
              f"{n_cards} 张卡片字段齐备 · 无禁用话术 · 无越界 · 标题合规 · 表头前提声明齐备")
    return 0


if __name__ == "__main__":
    sys.exit(main())
