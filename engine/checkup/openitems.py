# -*- coding: utf-8 -*-
"""开放项登记表（Open items）。

**为什么需要它**：这个项目习惯把"没做到的"写进 ``Provenance.note``。
这本身是对的 —— 写下来才有机会被做掉。但注记有个致命弱点：

    做掉之后，**没人会回去改注记**。

本轮就抓到一次：``P_CKDEPI.note`` 一直写着「报告范围 5-150 与结果取整数
本引擎**尚未实施**」，而那条已经在前一轮实现了。注记在**说假话**，
而且没有任何机制会报错。

所以这里做两件事：

1. **登记表** —— 所有已知未完成项集中在 ``OPEN_ITEMS``，每条带：类型、
   声明位置、被什么卡住、严重度
2. **扫描器** —— 扫全仓的"未完成"标记，与登记表对账：
   * 扫到但没登记 → **有东西沉在注记里**，测试失败
   * 登记了但扫不到 → **登记表过期**（要么已修好没删，要么声明被改写），测试失败

判据不是"没有未完成项"（那不可能），而是**未完成项必须可见、有主、对得上账**。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

#: "未完成"标记词
MARK = re.compile(r"尚未实施|未实施|未实现|未做|待做|待核|未取得|未获|暂未")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class OpenItem:
    """一条已知未完成项。"""

    item_id: str
    title: str
    kind: str                 #: source（原文未取得）| implementation | coverage
    severity: str             #: high | medium | low
    detail: str
    #: blocked_on_source（原文没拿到）| ready_to_implement（原文到手，等实现）
    #: | blocked_on_data（等数据）| blocked_on_access（等权限）
    status: str = "blocked_on_source"
    #: 声明处。``rules:P_XXX.note`` 或 ``file.py:行注释``
    declared_at: list[str] = field(default_factory=list)
    #: 扫到时必须命中的特征串（用于把扫描结果与登记项对上）
    marker: str = ""
    #: 同一开放项在别处的其他说法 —— 一个缺口常被声明多次，
    #: 每个表面形式都要能对上账，否则会被误报成"未登记"
    extra_markers: list[str] = field(default_factory=list)
    #: 什么能推进它
    unblocked_by: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


OPEN_ITEMS: list[OpenItem] = [
    OpenItem(
        item_id="SRC.METSYN",
        title="三条代谢综合征标准的分性别阈值未核到原文",
        kind="source", severity="high",
        detail="CDS 2004 女 HDL<1.0、2007 指南女腰围>85/女 HDL<1.29、"
               "IDF 2005 女腰围≥80 —— 均按公认值实现，期刊正文拒抓取。"
               "**原先对女性静默套用男性阈值**，故宁可先用待核的正确值",
        declared_at=["rules:P_CDS2004.note", "rules:P_GD2007.note",
                     "rules:P_IDF2005.note"],
        status="blocked_on_access",
        marker="分性别阈值待核",
        extra_markers=["未取得逐字原文", "逐字原文未取得"],
        unblocked_by="中文期刊数据库权限（CNKI/万方），或纸质版原文",
    ),
    OpenItem(
        item_id="COV.CHINAPAR_FEMALE",
        title="China-PAR 只有男性方程，女性一律不可评估",
        kind="coverage", severity="high",
        detail="女性受检者走不到 10 年风险评估 → 血脂分层只能用直接高危三条，"
               "其余落 not_assessable。已作为交叉核对中的**覆盖缺口**记录"
               "（独立算例给出女性 17.2%，我们给不出）",
        status="blocked_on_source",
        declared_at=["crosschecks.py:女性算例"],
        marker="我们只有男性方程",
        unblocked_by="China-PAR 女性方程系数（同一篇论文的补充材料）",
    ),
    OpenItem(
        item_id="SRC.CHINAPAR_COEF",
        title="China-PAR 系数未逐字核实，与独立实现差 0.3pp",
        kind="source", severity="high",
        detail="引用经 Europe PMC 核实无误（Circulation 2016;134(19):1430-1440, "
               "PMID 27682885），但 15 个系数未逐字核到补充材料。"
               "与独立计算器偏差 0.3pp，会翻转 5% 切点附近 56–57 岁、"
               "10% 切点附近 68 岁人群的分层结论",
        status="blocked_on_access",
        declared_at=["crosschecks.py:ASCVD.CHINAPAR"],
        marker="T/JPMA 024",
        unblocked_by="T/JPMA 024—2024 附录C《China-PAR 计算系数和赋值》"
                     "（结构已确认，正文需登录）",
    ),
    OpenItem(
        item_id="SRC.FIB4_NAFLD",
        title="FIB-4 的 NAFLD 切点（1.30/2.67）未取得逐字原文",
        kind="source", severity="medium",
        status="blocked_on_access",
        detail="代码用 NAFLD 路径切点（`FIB4_CUTOFFS['NAFLD'] = (1.30, 2.67)`），"
               "而已核实的出处 `P_FIB4_UNITS` 只覆盖 **HCV 队列**的 1.45/3.25。"
               "这条是被 `test_fib4_hcv_cutoffs_match_the_quote` **逼出来的** ——"
               "该测试要求实现里的切点能在引文里找到，于是暴露了无出处的那一套",
        declared_at=["checkup/compose.py:FIB4_CUTOFFS",
                     "checkup/rules.py:P_FIB4_UNITS.note"],
        marker="FIB4_NAFLD",
        unblocked_by="NAFLD 纤维化路径的原始文献（如 AGA 通路）"),
    OpenItem(
        item_id="SRC.AIP",
        title="AIP 公式出处未取得逐字原文",
        kind="source", severity="low",
        detail="AIP = log10(TG/HDL-C)，出处标注为原始文献但未核对",
        status="blocked_on_access",
        declared_at=["rules:P_AIP.note"],
        marker="AIP",
        unblocked_by="Dobiášová & Frohlich, Clin Biochem. 2001;34(7):583-588",
    ),
    OpenItem(
        item_id="COV.REAL_MULTI_ORG",
        title="真实第二家机构报告未验证（全部为合成）",
        kind="coverage", severity="high",
        detail="模板注册表、自动起草、词表冷启动、跨机构测试**全部**建立在"
               "合成 PDF 上。能证明「错的时候不会静默」，**证明不了"
               "「对的时候不会误杀」**",
        status="blocked_on_data",
        declared_at=["tests/test_cross_institution.py"],
        marker="合成",
        unblocked_by="任意一份别家机构的真实报告（新版式）",
    ),
    OpenItem(
        item_id="COV.SCANNED",
        title="扫描件（有图无文字层）无图像通道",
        kind="coverage", severity="medium",
        detail="**G0 载体检定已接线**（2026-10-01）：`validate.check_carrier()` 逐页看"
               "「字符数 / 图片数」，**有图无文字层 = 扫描页**会明确报「内容未被分析」；"
               "**无字无图 = 空白页**，正常不报。本机三份报告 35 页无扫描页、6 页空白。"
               "**仍缺**：图像通道本身（v1 明确排除），以及一份真实扫描件来验证判定",
        declared_at=["checkup/layout.py:has_text_layer（载体分支）",
                     "tests/test_cross_institution.py（合成件有文字层）"],
        status="blocked_on_data",
        marker="has_text_layer", extra_markers=["无文字层", "扫描件"],
        unblocked_by="任意一份扫描件报告",
    ),
    OpenItem(
        item_id="COV.STRATIFIED_RANGES",
        title="分层参考区间表为空，机制有数据无",
        kind="coverage", severity="medium",
        detail="D8 三层回落已实现并有测试，但分层表是空的 → 一律回落报告自带区间。"
               "「60 岁女性的正常范围对 25 岁男性不正常」这个问题**仍然存在**，"
               "只是引擎具备了解决它的能力",
        declared_at=["checkup/ranges.py:RangeTable 文档串"],
        status="blocked_on_data",
        marker="空表是合法状态", extra_markers=["分层表为空", "分层表是空的"],
        unblocked_by="WS/T 404/405 或机构公开的分性别/年龄参考区间",
    ),
]


#: 扫到但**属于设计说明、不是未完成项**的允许清单：(文件, 特征串, 理由)
#: 这些命中**不是未完成项**，而是设计说明、文案或已登记项的别名。
#: 每条必须写明理由；理由是给下一个人看的，不是给测试看的。
ALLOWED_NON_ITEMS: list[tuple[str, str, str]] = [
    ("conclude.py", "冲突消解", "「待核查」是结论分类名，指的是位置正常而变化超噪声，不是未完成项"),
    ("conclude.py", "待核查，**不是", "同上：冲突消解逻辑的说明，非未完成项"),
    ("compose.py", "另有一条", "历史说明：记录一条注记因实现而失真被删除，非未完成项"),
    ("conclude.py", "未做 —— 缺失不等于阴性", "缺失项说明，是引擎特性而非待补缺口"),
    ("perturb.py", "模拟该年漏抽或未做该检查", "扰动台文档串，描述一种被模拟的情形，非未完成项"),
    ("render.py", "数值型中未做变化轴判定的", "审计视图的段落标题，指没有 CVi 的数值项"),
    ("schema.py", "TO_VERIFY", "结论分类枚举值定义，与未完成项无关"),
    ("compose.py", "先按公认值实现并显式标未核实", "METSYN 缺口的说明文字，已在 SRC.METSYN 登记"),
    ("compose.py", "女值见", "METSYN 缺口的说明文字，已在 SRC.METSYN 登记"),
    ("rules.py", "比用一个待核对的正确值更糟", "METSYN 缺口的说明文字，已在 SRC.METSYN 登记"),
    ("rules.py", "三条代谢综合征标准的逐字原文", "METSYN 缺口的说明文字，已在 SRC.METSYN 登记"),
    ("rules.py", "分性别阈值待核", "同一缺口的另一处声明，已在 SRC.METSYN 登记"),
]


def _plain(t: str) -> str:
    """去掉 markdown 标记再比对。

    声明里常写成「逐字原文**未取得**」，而特征串写的是「逐字原文未取得」——
    直接子串匹配会漏。把 ``*`` 与反引号去掉就统一了。
    """
    return t.replace("*", "").replace("`", "")


@dataclass
class Hit:
    """一处扫描命中。"""

    where: str          #: 文件:行 或 rules:<出处>.note
    text: str
    marker: str

    def matches(self, item: OpenItem) -> bool:
        return any(m and m in _plain(self.text)
                   for m in [item.marker, *item.extra_markers])


#: 扫描时排除的文件 —— ``openitems.py`` 自己就满是这些词（自指）
SKIP_FILES = {"openitems.py"}


def _iter_py(root: str):
    pkg = os.path.join(root, "checkup")
    for fn in sorted(os.listdir(pkg)):
        if fn.endswith(".py") and fn not in SKIP_FILES:
            yield fn, os.path.join(pkg, fn)


def scan_rule_hits() -> list[Hit]:
    """扫所有规则出处的 ``note``。"""
    import importlib
    rules = importlib.import_module("checkup.rules")
    hits: list[Hit] = []
    for r in rules.RULES:
        for pr in r.provenance:
            if pr.note and MARK.search(pr.note):
                hits.append(Hit(f"rules:{pr.source_title[:28]}.note", pr.note, ""))
    return hits


def scan_file_hits(root: str) -> list[Hit]:
    """扫 ``<root>/checkup/*.py`` 的行注释/文档串。

    单独拆出来是为了**可测**：可以在临时目录里造一条假的未完成声明，
    验证守卫真的会响 —— 否则"当前状态干净"可能只是因为守卫根本不工作。
    """
    hits: list[Hit] = []
    for fn, path in _iter_py(root):
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if MARK.search(line):
                    hits.append(Hit(f"{fn}:{i}", line.strip(), ""))
    return hits


def scan(root: Optional[str] = None) -> list[Hit]:
    """扫全仓的"未完成"标记。"""
    return scan_rule_hits() + scan_file_hits(root or ROOT)


def _all_text(root: str) -> str:
    """``checkup/`` 下全部源码 + 全部测试的拼接文本。

    用于回答"这条开放项的特征串**还**在仓库里出现吗" —— 比只看 MARK 行更可靠，
    因为有些声明是散文式的（如交叉核对里的"我们只有男性方程"）。
    """
    parts: list[str] = []
    for _fn, path in _iter_py(root):
        with open(path, encoding="utf-8") as f:
            parts.append(f.read())
    tdir = os.path.join(root, "tests")
    if os.path.isdir(tdir):
        for fn in sorted(os.listdir(tdir)):
            if fn.endswith(".py"):
                with open(os.path.join(tdir, fn), encoding="utf-8") as f:
                    parts.append(f.read())
    return "\n".join(parts)


def is_registered(h: Hit) -> bool:
    return any(h.matches(it) for it in OPEN_ITEMS)


def is_allowed(h: Hit) -> bool:
    """允许清单按**特征串在命中文本里出现**匹配，不绑死文件名。

    原先要求"文件名相同且特征串在文本里"，结果 ``conclude.py:156`` 的
    「待核查」因为允许清单写的是另一句而漏网。
    """
    return any(sub in h.text for _f, sub, _r in ALLOWED_NON_ITEMS)


def unregistered(root: Optional[str] = None) -> list[Hit]:
    """扫到、既没登记也不在允许清单里的命中 —— **沉在注记里的未完成项**。"""
    return [h for h in scan(root) if not is_registered(h) and not is_allowed(h)]


def stale(root: Optional[str] = None) -> list[OpenItem]:
    """登记了但在仓库里**找不到任何痕迹**的条目 —— 登记表过期。

    多半是"已经做掉但没删条目"，或"声明被改写导致对不上"。
    判据是特征串在 ``checkup/`` 与 ``tests/`` 全文中是否还出现，
    而不是只看带 MARK 的行 —— 否则散文式声明会被误判为过期。
    """
    text = _all_text(root or ROOT)
    hits = scan(root)
    out = []
    plain = _plain(text)
    for it in OPEN_ITEMS:
        if any(m and m in plain for m in [it.marker, *it.extra_markers]):
            continue
        if any(h.matches(it) for h in hits):
            continue
        out.append(it)
    return out


def summary() -> str:
    from collections import Counter
    by_kind = Counter(i.kind for i in OPEN_ITEMS)
    by_sev = Counter(i.severity for i in OPEN_ITEMS)
    return (f"开放项 {len(OPEN_ITEMS)} 条 · "
            f"按类型 {'/'.join(f'{k}×{v}' for k, v in by_kind.items())} · "
            f"按严重度 {'/'.join(f'{k}×{v}' for k, v in by_sev.items())}")


def render() -> str:
    L = [f"# 开放项登记表（{len(OPEN_ITEMS)} 条）", "",
         "未完成项必须可见、有主、对得上账。", ""]
    for sev in ("high", "medium", "low"):
        items = [i for i in OPEN_ITEMS if i.severity == sev]
        if not items:
            continue
        L.append(f"## {'⚠ ' if sev == 'high' else ''}{sev}")
        L.append("")
        for it in items:
            L.append(f"- **{it.item_id}** · {it.title}")
            L.append(f"  - 类型：{it.kind} · 状态：{it.status}")
            L.append(f"  - {it.detail}")
            L.append(f"  - 声明于：{'、'.join(it.declared_at)}")
            L.append(f"  - 卡在：{it.unblocked_by}")
        L.append("")
    return "\n".join(L)
