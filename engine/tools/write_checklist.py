#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报告**写作前置自检**（C5）：把对账链里的"必须/禁止"规则提成清单，写作前先跑一遍。

    python3 engine/tools/write_checklist.py --report <报告.md> [--out <清单.md>]

为什么需要它：对账（``reconcile.py``）是**事后**核 —— 34 条规则跑完才发现"影像条目
没带检查方式""缺口里写了正常""女性报告出现 PSA"。这三处都是**写作时**就能避免的。
本工具把规则**从 reconcile.py 源码里提出来**（不复制一份，避免两处口径漂移），
在写作前过一遍草稿。

提取方式（AST，不执行 reconcile 的业务逻辑）：

* 全部 ``ck("ID", "说明", …)`` → 规则清单（ID + 说明）；
* ``ck`` 参数里的 ``not re.search(r"…")`` → **禁止正则**（如 G10 的固定频率建议）；
* ``ck`` 说明里 「…」 引起来的字样 → **必须/禁止字样**（如 「调档：<理由>」「无法判定」）；
* 检查函数里的字符串元组/列表常量（如 ``_IMG_TOKENS``）→ **词表**（供邻接检查用）；
* 代码里的切片数字（如 ``[:16]``）→ **容忍度**（邻接窗口）。

运行侧只做"写作前能判"的那几条（其余留作清单供人读），每条都标出处（检查 ID）。
退出码：0 = 无告警；2 = 有告警（写作前应当先处理）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

from checkup.console import init_console  # noqa: E402
init_console()

RECONCILE = os.path.join(SKILL, "reconcile", "reconcile.py")
#: 本案例踩过的那几类（工作单 C5 点名）：影像方式邻接 / 女性禁 PSA /
#: 缺口禁「正常」/ 调档格式 / 肺 CT 频率。前三条会被机器跑，后两条进清单。
NAMED_RULES = ["S05b", "S10", "C04", "I03", "G10"]


# ------------------------------------------------------------------ 提取

def extract_from_source(path: str) -> dict:
    """从 ``reconcile.py`` 源码提取规则清单（AST，不执行业务逻辑）。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    checks: list[dict] = []
    bans: list[dict] = []           # {id, pattern}
    tables: dict[str, list[str]] = {}   # 字符串常量元组/列表
    tolerances: list[int] = []      # 切片数字（邻接窗口等）

    def _str(node) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):     # f-string：拼接字面量部分
            return "".join(v.value for v in node.values
                           if isinstance(v, ast.Constant)
                           and isinstance(v.value, str))
        return ""

    for node in ast.walk(tree):
        # ① ck("ID", "说明", …)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "ck" and len(node.args) >= 2:
            cid, desc = _str(node.args[0]), _str(node.args[1])
            if cid and desc:
                checks.append({"id": cid, "说明": desc,
                               "行": node.lineno})
                # ② 参数里的 not re.search(r"…") → 禁止正则
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                            and sub.func.attr in ("search", "match") and sub.args:
                        pat = _str(sub.args[0])
                        if pat:
                            bans.append({"id": cid, "pattern": pat,
                                         "行": sub.lineno})
        # ③ 字符串元组/列表常量 → 词表
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            vals = [_str(e) for e in node.value.elts]
            vals = [v for v in vals if v]
            if len(vals) >= 3 and isinstance(node.targets[0], ast.Name):
                tables[node.targets[0].id] = vals
        # ④ 切片上限数字（邻接窗口）
        if isinstance(node, ast.Slice) and isinstance(node.upper, ast.Constant) \
                and isinstance(node.upper.value, int):
            tolerances.append(node.upper.value)

    # 说明里 「…」 引号内的字样 = 必须/禁止字样
    literals: list[dict] = []
    for c in checks:
        for lit in re.findall(r"「([^」]+)」", c["说明"]):
            literals.append({"id": c["id"], "字样": lit, "说明": c["说明"]})
    return {"checks": checks, "bans": bans, "tables": tables,
            "tolerances": sorted(set(tolerances)),
            "literals": literals, "source": path}


# ------------------------------------------------------------------ 运行

#: 影像发现的方式词表（**从源码提取** ``_IMG_TOKENS``，提不到才用兜底）
_FALLBACK_MODALITIES = ["彩超", "超声", "B超", "CT", "MRI", "核磁", "骨密度",
                        "心电图", "经颅多普勒", "TCD", "胸片", "钼靶"]
#: 影像发现的关键词（报告里出现这些词 = 在描述一项影像发现）
_FINDING_KW = ["结节", "息肉", "囊肿", "结石", "脂肪肝", "回声", "斑块", "增生",
               "钙化", "积液", "瘤", "异常信号", "占位"]


def run_checks(report: str, spec: dict, *, specs: Optional[dict] = None,
               female: bool = True) -> list[dict]:
    """跑"写作前能判"的那几条；返回告警清单。

    ``specs``：图规格 JSON（``{名字: 文本}``）—— S05b 只查**图里的结构化条目**
    （与对账一致：图一张张条目、图二①层节点；正文散文不查，否则满屏假告警）。
    """
    out: list[dict] = []
    mods = _FALLBACK_MODALITIES
    for name, vals in spec["tables"].items():
        if "IMG" in name.upper() or "TOKEN" in name.upper():
            mods = vals
            break
    win = 16
    for t in spec["tolerances"]:
        if 8 <= t <= 40:
            win = t
            break

    # ---- ① S05b：图里的影像发现条目必须同句带检查方式（16 字邻接 + 方式词表）
    for name, text in (specs or {}).items():
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            continue
        strings: list[str] = []
        if d.get("layers"):                       # 因果图：只查①层"报告里的发现"
            for n in (d["layers"][0].get("nodes") or []):
                strings.append(str(n.get("text") or ""))
        for sec in (d.get("sections") or []):     # 归并图：items 与 plain
            for g in (sec.get("groups") or []):
                for it in (g.get("items") or []):
                    strings.append(str(it))
            if sec.get("plain"):
                strings += [s.strip() for s in
                            re.split(r"[·；;]", sec["plain"]) if s.strip()]
        for t in strings:
            if any(w in t for w in ("没查", "未查", "缺", "漏做", "没做", "未做",
                                    "没测", "未测", "未见记录", "那一年的")):
                continue                          # "没查/缺项"类陈述不是发现描述
            for kw in _FINDING_KW:
                i = t.find(kw)
                if i < 0:
                    continue
                near = t[max(0, i - win):i + len(kw) + win]
                if any(tok in near for tok in mods):
                    break                         # 方式就在发现附近（两侧都算）✓
                anywhere = next((tok for tok in mods if tok in t), "")
                # "像发现描述" = 含**测量值**（数值 + mm/cm/类/级/%）；纯叙述/建议行降级为 tip
                measured = bool(re.search(r"\d+(?:\.\d+)?\s*(?:mm|cm|ml|类|级|%)", t))
                out.append({
                    "id": "S05b",
                    "级别": "warn" if (measured and not anywhere) else "tip",
                    "规则": "图里的影像发现条目必须带检查方式（名称 +（方式）+ 数值）",
                    "命中": (f"{name}：「{kw}」附近 {win} 字内没有方式词，"
                             f"但同条目里有 {anywhere!r} —— 方式离发现太远，"
                             f"对账 S05b 可能判不过：{t[:44]}"
                             if anywhere else
                             f"{name}：「{kw}」附近与整条里都没有方式词 —— {t[:44]}"),
                    "改法": "在该条目里让方式紧挨发现，如「甲状腺结节 4mm（彩超）」"})
                break

    # ---- ② C04 口径：缺口/未查项不得写成「正常」
    for line in report.splitlines():
        if re.search(r"(缺口|未查|缺项|未做|没做|漏做)", line) and "正常" in line:
            out.append({
                "id": "C04", "级别": "warn",
                "规则": "缺口/未查项：报告口径 = 产物（做过不得写成缺；没做必须写成缺）",
                "命中": f"缺口类行里出现「正常」：{line.strip()[:56]}",
                "改法": "缺口行只写「缺」，不要给结论；「正常」只用于已查且正常"})

    # ---- ③ I03 性别口径：女性报告不得出现 PSA
    if female and re.search(r"PSA|前列腺特异", report):
        out.append({
            "id": "I03", "级别": "warn",
            "规则": "PSA 只对男性适用（女性报告不得出现）",
            "命中": "报告里出现 PSA/前列腺特异抗原，但受检者为女性",
            "改法": "删除 PSA 相关行；若数据里真有该值，按「机构误录」处理并记缺口"})

    # ---- ④ S10：调档必须写格式（**逐处**检查，不是全文一处合规就放过）
    for m in re.finditer(r"调档", report):
        seg = report[m.start():m.start() + 60]
        if re.match(r"调档：.+?）", seg):
            continue
        out.append({
            "id": "S10", "级别": "tip",
            "规则": "影像级别 = 脚本建议；调档必须在卡片写「（调档：<理由>）」",
            "命中": f"「调档」处没有写成「（调档：<理由>）」：…{seg[:40]}…",
            "改法": "改成「（调档：依据问诊——…）」这种带理由的格式（对账 S10 会核）"})
    # ---- ⑤ G10：不得给越界的固定频率建议（用源码里提取的禁止正则）
    for b in spec["bans"]:
        try:
            if re.search(b["pattern"], report):
                out.append({
                    "id": b["id"], "级别": "warn",
                    "规则": (next(c["说明"] for c in spec["checks"]
                                 if c["id"] == b["id"]) if any(
                        c["id"] == b["id"] for c in spec["checks"]) else "禁止正则"),
                    "命中": f"命中禁止正则：{b['pattern'][:60]}…",
                    "改法": "改写该句，别给固定频率/越界断言；频率由医生定"})
        except re.error:
            continue

    # ---- ⑥ A/B 类声明（report-form-v6.4 §4 硬要求）——
    # 来历：这条规则**只在细则里**，一次真实交付就是漏了它、而当时六道门槛全绿通过
    # （对账/结构回归都不查它）。判据**复用** `scripts/validate_report.py` 的实现
    # （单一来源，不复制逻辑，免得两处口径各自漂移）。
    if re.search(r"版本[:：]\s*\**v6\.[4-9]", report):
        try:
            sys.path.insert(0, os.path.join(SKILL, "scripts"))
            from validate_report import ab_decl_line  # noqa: PLC0415
            if ab_decl_line(report) is None:
                out.append({
                    "id": "v6.4·A/B声明", "级别": "warn",
                    "规则": "声明 v6.4+ 的报告**必须**有「A/B 类声明」"
                            "（本报告的\"红\"不等于官方 A/B 类危急值…）",
                    "命中": "报告声明了 v6.4+，但找不到该声明",
                    "改法": "在 §4「问题分级总览」的级别表附近逐字写上："
                            "本报告的\"红\"**不等于**官方 A/B 类危急值"
                            "（血压 ≥180/110 伴急性症状、ALT ≥15 倍上限、"
                            "肌酐 ≥707 μmol/L；ALT 5～15 倍属 B 类）——"
                            "那是机构的紧急召回口径；本报告分级是给个人健康管理用的"})
        except Exception:  # noqa: BLE001
            pass              # 复用失败不影响其余检查（validate_report 会在定稿时兜住）
    return out


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="报告写作前置自检（从对账链提取规则）")
    ap.add_argument("--report", required=True, help="报告 Markdown 草稿")
    ap.add_argument("--specs", nargs="*", default=[],
                    help="图规格 JSON（S05b 只查图里的结构化条目，与对账同口径）")
    ap.add_argument("--out", default="", help="清单输出路径（缺省与报告同目录）")
    ap.add_argument("--reconcile", default=RECONCILE, help="对账脚本（规则来源）")
    ap.add_argument("--male", action="store_true", help="受检者为男性（PSA 检查反向）")
    args = ap.parse_args()

    spec = extract_from_source(args.reconcile)
    report = open(args.report, encoding="utf-8").read()
    specs = {os.path.basename(p): open(p, encoding="utf-8").read()
             for p in args.specs if os.path.exists(p)}
    findings = run_checks(report, spec, specs=specs, female=not args.male)

    L = ["# 报告写作前置自检清单（C5）", "",
         f"- 规则来源：`{os.path.relpath(args.reconcile, SKILL)}`（AST 提取，不复制口径）",
         f"- 规则总数：{len(spec['checks'])} 条检查 · "
         f"{len(spec['bans'])} 条禁止正则 · {len(spec['tables'])} 张词表 · "
         f"容忍度 {spec['tolerances']}",
         f"- 待查报告：`{args.report}`", "",
         "## 一、机器已跑（写作前能判的）", ""]
    if findings:
        L += ["| 检查 | 级别 | 规则 | 命中 | 改法 |", "|---|---|---|---|---|"]
        for f in findings:
            L.append(f"| {f['id']} | {f['级别']} | {f['规则']} | {f['命中']} "
                     f"| {f['改法']} |")
    else:
        L.append("✅ 无告警（本次三条硬规则 + 禁止正则全部通过）")
    L += ["", "## 二、规则清单（写作前请逐条对照）", ""]
    named = [c for c in spec["checks"] if c["id"] in NAMED_RULES]
    others = [c for c in spec["checks"] if c["id"] not in NAMED_RULES]
    for c in named:
        L.append(f"- **{c['id']}**（★本次踩过）：{c['说明']}")
    L += ["", "<details><summary>其余检查（对账链全集）</summary>", ""]
    for c in others:
        L.append(f"- {c['id']}：{c['说明']}")
    L += ["", "</details>", "",
          "## 三、从源码提取到的禁止正则（写作时避开这些句式）", ""]
    for b in spec["bans"]:
        L.append(f"- {b['id']}：`{b['pattern']}`")
    out = args.out or os.path.splitext(args.report)[0] + "_写作前置自检.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print(f"规则来源 {os.path.basename(args.reconcile)}：{len(spec['checks'])} 条检查 · "
          f"{len(spec['bans'])} 条禁止正则 · 邻接窗口 {spec['tolerances'][:4]}")
    warns = [f for f in findings if f["级别"] == "warn"]
    tips = [f for f in findings if f["级别"] != "warn"]
    for f in warns:
        print(f"  ⚠ [{f['id']}] {f['命中']}")
    for f in tips:
        print(f"  · [{f['id']}·提示] {f['命中']}")
    print(f"告警 {len(warns)} 处 · 提示 {len(tips)} 处")
    print(f"清单：{out}")
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
