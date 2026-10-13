# -*- coding: utf-8 -*-
"""模板验收（accept）：把"这批 PDF 用这份模板**能不能读对**"变成一条有证据的检查链。

背景：模板是抽取层对某机构版式的**显式假设**（列位/页标记/段标题列/噪声）。假设错了
会**静默抽错**（错抽比抽不到危险得多）。本模块用**独立于列位假设**的证据来证伪它，
让"要不要人工看"从"逐项看"变成"只看异常项"。

证据分级（``ok`` 不阻断 / ``review`` 需人工看对照卡 / ``block`` 直接打回）：

=  ==================  ==================================================
E1 结构自洽            路由命中本模板、识别出化验页、判为化验页却 0 项 = 阻断
E2 回读（页码/行）      每个数值能在**它声称的那一页**找到，且与该行项目名同行
E3 双实现交叉          几何列归属 vs **行内语义模式**（只看形状与顺序）：名称/值 必须一致
E4 箭头交叉            报告画的旗标 vs 引擎按「值+区间」复算的旗标（弱证据，只提示）
E5 语义形状            区间列像区间、值不越数量级、同项目单位一致
E6 基线                多份样本项数一致性；任一份 0 项 = 阻断
E7 VLM 第二读者        可选：渲染行图给 VLM 读，与引擎读数比对（未配置 → skip）
=  ==================  ==================================================

**注意**：E3/E4 是"独立第二读者"，不是"再跑一遍同样的算法"。E3 刻意**不用 x 列位**，
只用"值长什么样、区间长什么样、在行内的先后顺序"——两套方法同时错成一样的概率极低。

产出：`:class:`Report``（markdown 报告 + 对照卡 + 行图），可直接落盘归档
（``<模板>.accept.json``），供审计与复现。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import fitz

from .layout import Row, rows_of_page
from .tables import (_header_anchors, _section_of, extract_lab_results,
                     lab_page_audit)
from .templates import Template, TemplateRegistry, compile_patterns

LEVEL_RANK = {"ok": 0, "skip": 0, "review": 1, "block": 2}
ICON = {"ok": "✅", "review": "⚠️", "block": "⛔", "skip": "·"}


# ---------------------------------------------------------------- 数据结构

@dataclass
class Card:
    """对照卡：一行"引擎读数 vs 独立复核"的并排对照。"""

    sample: str
    page: int
    row_text: str
    engine: str
    independent: str
    diff: str
    clip: str = ""            # 行区域 PNG（回图核对材料；可空）

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass
class Evidence:
    key: str
    title: str
    level: str                 # ok | review | block | skip
    detail: str
    cards: list[Card] = field(default_factory=list)
    advice: str = ""           #: 可执行的修法建议（"出现问题时给到提示"）

    def to_dict(self) -> dict:
        d = {"key": self.key, "title": self.title, "level": self.level,
             "detail": self.detail}
        if self.advice:
            d["advice"] = self.advice
        if self.cards:
            d["cards"] = [c.to_dict() for c in self.cards]
        return d


@dataclass
class Report:
    org_id: str
    template_id: str
    samples: list[str]
    n_items: int
    evidences: list[Evidence]
    vlm: str = ""
    #: 起草说明（A9）：草稿 notes 分级 + 待确认问题。手写模板验收时为空。
    #: 直出到验收报告与档案 —— "部分成功"必须看得见，不能被当成"全绿"。
    draft_notes: list[str] = field(default_factory=list)
    #: **机器默认决定清单**（C1）：``[{决定, 依据, 不确定}]`` —— 流程里没有阻塞式
    #: 提问，这些"我替你定的"事项逐条留痕，供事后抽检。
    decisions: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        lv = max((LEVEL_RANK[e.level] for e in self.evidences), default=0)
        return {0: "ok", 1: "review", 2: "block"}[lv]

    @property
    def n_review(self) -> int:
        return sum(len(e.cards) for e in self.evidences if e.level == "review")

    def to_dict(self) -> dict:
        d = {"org_id": self.org_id, "template_id": self.template_id,
             "samples": self.samples, "n_items": self.n_items,
             "verdict": self.verdict, "vlm": self.vlm,
             "evidences": [e.to_dict() for e in self.evidences]}
        if self.draft_notes:
            d["draft_notes"] = list(self.draft_notes)
        if self.decisions:
            d["decisions"] = [dict(x) for x in self.decisions]
        return d

    def render(self) -> str:
        v = self.verdict
        head = {"ok": "**OK —— 可落盘（无需人工）**",
                "review": f"**REVIEW —— 有 {self.n_review} 项需人工看一眼（见对照卡）**",
                "block": "**BLOCK —— 不予落盘（模板与这批 PDF 不匹配）**"}[v]
        L = [f"# 模板验收报告 · {self.template_id}", "",
             f"- 样本：{len(self.samples)} 份（{', '.join(self.samples)}）",
             f"- 抽取：{self.n_items} 项",
             f"- 结论：{head}"]
        if self.vlm:
            L.append(f"- VLM：{self.vlm}")
        L += ["", "| 证据 | 级别 | 说明 |", "|---|---|---|"]
        for e in self.evidences:
            L.append(f"| {e.key} {e.title} | {ICON.get(e.level, '')} {e.level} "
                     f"| {e.detail} |")
        if self.draft_notes:
            # A9：草稿"部分成功"分级直出验收报告 —— 全绿的证据链 + 待确认字段
            # 同时在场，读的人才知道"绿的是结构，没绿的是哪些字段"。
            L += ["", "## 起草说明（分级，A9）", "",
                  "> 模板由自动起草产出时的分级结论与待确认问题。"
                  "**证据链全绿 ≠ 草稿无需确认**：待确认字段按下面的清单逐条看。", ""]
            for n in self.draft_notes:
                L.append(f"- {n}")
        if self.decisions:
            # C1：确认点降级 —— 不再阻塞式提问，改成"机器默认决定清单"留痕
            L += ["", "## 机器默认决定清单（C1）", "",
                  "> 流程里没有阻塞式提问；下面这些是**我替你定的**（决定 + 依据），"
                  "标 ⚠ 的是不确定项（已按默认代入继续）。是否查看不影响正确性 —— "
                  "正确性由 E1–E6 机械验收保证；想看就看这几条。", "",
                  "| 决定 | 依据 | 不确定 |", "|---|---|---|"]
            for x in self.decisions:
                L.append(f"| {x['决定']} | {str(x['依据'])[:160]} "
                         f"| {'⚠ 是' if x.get('不确定') else '否'} |")
        seen, advices = set(), []
        for e in self.evidences:
            if e.advice and e.advice not in seen:
                seen.add(e.advice)
                advices.append(e)
        if advices:
            L += ["", "## 修法建议（可执行）", ""]
            for e in advices:
                L.append(f"- **{e.key} {e.title}**：{e.advice}")
        cards = [c for e in self.evidences for c in e.cards]
        if cards:
            L += ["", "## 对照卡（需人工看一眼的项）", "",
                  "> 每张卡：左边是 PDF 那一行的原文，右边是引擎与独立复核各自的读数。",
                  "> 你只需要回答『这两个读数一样吗』，**不需要任何医学知识**。", ""]
            for i, c in enumerate(cards, 1):
                L += [f"### {i}. `{c.sample}` 第 {c.page} 页 —— {c.diff}", "",
                      f"- PDF 行原文：`{c.row_text}`",
                      f"- 引擎抽取：{c.engine}",
                      f"- 独立复核：{c.independent}"]
                if c.clip:
                    L.append(f"- 行图（回图核对）：`{c.clip}`")
                L.append("")
        return "\n".join(L)


# ---------------------------------------------------------------- 小工具

#: 区间**头部**（允许后面跟单位，如 3.5-9.510^9/L、0-28个/ul）
_RANGE_HEAD = re.compile(
    r"^(?:[<>≤≥]\s*\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?"
    r"|\d+(?:\.\d+)?\s*[-~—]\s*\d+(?:\.\d+)?)")


def _looks_range(tok: str) -> bool:
    return bool(_RANGE_HEAD.match(_norm_ref(tok)))


def _looks_unit(tok: str) -> bool:
    """单位长什么样：10^9/L、mmol/L、%、个/ul、次/分…（**不是**裸数字/区间）。"""
    t = tok.strip()
    if not t or _NUM.match(t) or _looks_range(t):
        return False
    if re.match(r"^\d+[\^*]\d+/\S+$", t):
        return True
    if re.search(r"[A-Za-z%μµ℃°]", t):
        return True
    if re.match(r"^[次个分秒钟][/·]?\S*$", t):
        return True
    return False
_NUM = re.compile(r"^\d+(?:\.\d+)?$")
_FLAG_WORDS = {"↑", "↓", "H", "L", "h", "l", "正常", "偏高", "偏低", "正常高值",
               "阴性", "阳性", "阴性(-)", "阳性(+)", "-", "—", "normal", "high", "low"}
#: 方向词一律"偏离参考区间"（↑↓/H/L/偏高/偏低/阳性）；只有"正常/阴性"才是 normal。
#: 曾把 ↓/L 误放进 normal —— 高密度脂蛋白 0.94 复算 abnormal、报告标 ↓，
#: 被当成"不一致"假报（真实案例实测）。
_ABNORMAL_WORDS = {"↑", "↓", "H", "h", "L", "l", "偏高", "偏低", "正常高值",
                   "阳性", "阳性(+)", "high", "low"}
_NORMAL_WORDS = {"正常", "阴性", "阴性(-)", "(-)", "normal"}


def _norm_name(s: str) -> str:
    return re.sub(r"[\s·]+", "", s or "").strip("↑↓ ")


def _norm_ref(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _split_tokens(row: Row) -> list[str]:
    return [w.text for w in row.words]


#: 名称里常见的拉丁缩写（防止把 "fL"/"pg" 这类单位吞进名称）
_LATIN_ITEM = re.compile(r"^[A-Za-z][A-Za-z0-9\-]{0,7}$")
#: 常见单位词（短、全字母，与名称缩写形似）——名称吞噬时在此停止
_UNIT_WHITELIST = {"fL", "pg", "mL", "L", "g", "mg", "ug", "U", "IU", "min",
                   "mmol", "umol", "μmol", "pmol", "mIU", "gL", "mmolL"}


def _nameish(tok: str) -> bool:
    """这个 token 像"项目名"吗（用于名称向左吞噬时判断停在哪）。

    含 CJK → 是；纯拉丁短串 → 是（WBC/T3/HBA1C/LDL-C…）。
    排除：单位特征（含 ``/ ^ %``）、单位白名单（fL/pg…）、旗标词（正常/阴性…）。
    曾把 "WBC" 当单位拒掉 → 名称只剩"白细胞"、与引擎的 "WBC白细胞" 对不上。
    """
    t = tok.strip()
    if not t:
        return False
    if t in _FLAG_WORDS:
        return False
    if re.search(r"[\u4e00-\u9fff]", t):
        return True
    if re.search(r"[/^%％]", t):
        return False
    if t in _UNIT_WHITELIST:
        return False
    return bool(_LATIN_ITEM.match(t))


def _parse_rightmost(toks: list[str]) -> Optional[dict]:
    """从右往左解析**最右侧那一组**：[名] [值] [旗标] [单位] [区间]（顺序宽松）。

    列序在真实机构里至少有三种（都实测过）：

    * ``名 值 区间``（区间自带单位）；
    * ``名 值 单位 区间 旗标``（随包样例：值后紧跟单位，旗标在最右）；
    * ``名 值 旗标 区间 单位``（本机构：**箭头夹在值与区间之间**，单位在最右）。

    所以单位与区间**谁在右都可能**（用两次"先单位、后区间"的贪心消费），
    旗标允许出现在**区间右侧或左侧**（B10 加宽）。放宽的只有"形状"，
    判定标准（E1–E6）与比对口径一个字没动 —— 加宽的直接收益是 E3 能多比几行。
    """
    if len(toks) < 2:
        return None
    i = len(toks) - 1
    flag = ""

    def _is_flag(t: str) -> bool:
        return t in _FLAG_WORDS or t.endswith(("(-)", "(+)"))

    if _is_flag(toks[i]):
        flag = toks[i]
        i -= 1
    ref = ""
    unit = ""
    for _ in range(2):          # 单位/区间最多各消费一个，谁在右都行
        if not unit and i >= 1 and _looks_unit(toks[i]):
            unit = toks[i]
            i -= 1
            continue
        if not ref and i >= 1 and _looks_range(toks[i]):
            ref = toks[i]
            i -= 1
            continue
        break
    if not flag and i >= 1 and _is_flag(toks[i]):
        # 旗标在**区间左侧**（值与区间之间）——本机构的箭头就在这里
        flag = toks[i]
        i -= 1
    val = ""
    if i >= 1 and _NUM.match(toks[i]):
        val = toks[i]
        i -= 1
    if not val and not ref and not flag:
        return None
    # 名称：从 i 向左吞"像名字"的 token；遇到值/单位/区间/旗标（上一组的尾巴）就停
    j = i
    while j >= 0 and _nameish(toks[j]):
        j -= 1
    name_toks = toks[j + 1:i + 1]
    if not name_toks:
        return None
    return {"name": "".join(name_toks), "value": val, "unit": unit,
            "ref": ref, "flag": flag, "start": j + 1}


def row_items(tokens: list[str], max_groups: int = 4) -> list[dict]:
    """一行里**并排的多组**（双栏报告一行两个项目）→ 逐组解析（左→右）。"""
    rest = [t.strip() for t in tokens if t and t.strip()]
    out: list[dict] = []
    while rest and len(out) < max_groups:
        pat = _parse_rightmost(rest)
        if pat is None:
            break
        out.append({k: pat[k] for k in ("name", "value", "unit", "ref", "flag")})
        rest = rest[:pat["start"]]
    return list(reversed(out))


def row_pattern(tokens: list[str]) -> Optional[dict]:
    """**行内语义模式**（单组）：只看"长什么样"和顺序，**不看列位 x**。

    形态不典型时返回 ``None``（**不猜**）。双栏行请用 :func:`row_items`。
    """
    items = row_items(tokens, max_groups=1)
    return items[0] if items else None


def _row_png(page: "fitz.Page", row: Row) -> bytes:
    """整行区域渲染成 PNG（**内存**，回图核对材料）。失败返回空 bytes。"""
    try:
        rect = fitz.Rect(min(w.x0 for w in row.words) - 6,
                         min(w.y0 for w in row.words) - 3,
                         max(w.x1 for w in row.words) + 6,
                         max(w.y1 for w in row.words) + 3)
        return page.get_pixmap(clip=rect, dpi=200).tobytes("png")
    except Exception:  # noqa: BLE001
        return b""


def _row_clip(page: "fitz.Page", row: Row, out_dir: str, tag: str) -> str:
    """把整行区域渲染成 PNG 并落盘（回图核对材料）。失败返回空串。"""
    if not out_dir:
        return ""
    png = _row_png(page, row)
    if not png:
        return ""
    try:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{tag}.png")
        with open(path, "wb") as f:
            f.write(png)
        return path
    except Exception:  # noqa: BLE001
        return ""


def _route_ok(doc, template: Template) -> tuple[bool, str]:
    reg = TemplateRegistry([template])
    m = reg.match(doc)
    if m.template is None:
        return False, f"路由未命中：{m.reason}"
    return True, f"路由命中（{m.score:.2f}：{m.reason}）"


# ---------------------------------------------------------------- 各项证据

def _e1_structure(doc, template: Template, sample: str) -> Evidence:
    ok, why = _route_ok(doc, template)
    if not ok:
        return Evidence("E1", "结构自洽", "block",
                        f"{sample}：{why} —— 这份 PDF 根本路由不到本模板",
                        advice="检查 required_anchors（机构专属字样）是否与这份 PDF 一致；"
                               "若这是另一家机构/另一种版式，应当另起一份模板")
    audit = lab_page_audit(doc, template)
    pages = [a for a in audit]
    items = sum(a["items"] for a in pages)
    if not pages:
        return Evidence("E1", "结构自洽", "block",
                        f"{sample}：没有识别出任何化验页（页标记 {template.lab.page_marker!r} 未命中）",
                        advice=f"page_marker 要选『这份 PDF 的化验页上确实出现』的通用短词"
                               f"（现在写的 {template.lab.page_marker!r} 一页都没命中）；"
                               f"可用 draft_template.py 的推断结果，或改为该报告页眉上的短语")
    no_header = [a["page_no"] for a in pages if not a.get("has_header")]
    if no_header:
        return Evidence("E1", "结构自洽", "block",
                        f"{sample}：第 {no_header} 页判为化验页但没有表头行 —— 列位假设不成立",
                        advice="header_cells 要与该机构表头词**逐字一致**"
                               "（如「参考值」vs「参考范围」）；对照 PDF 表头行改模板")
    if items == 0:
        return Evidence("E1", "结构自洽", "block",
                        f"{sample}：判为化验页 {len(pages)} 页却抽到 0 项")
    return Evidence("E1", "结构自洽", "ok",
                    f"{sample}：{len(pages)} 页化验页 · {items} 项 · {why}")


def _token_has(text: str, needle: str) -> bool:
    """``text`` 里是否含**独立的**数字 token ``needle``。

    不能做子串匹配：needle="14" 会命中 "149"、"140/90"、"134" 等别的项
    （实测踩过：BUN=14 被匹配到 HGB=149 那一行 → 假报"错行"）。
    """
    return bool(re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d.])", text))


def _e2_readback(doc, template: Template, results, sample: str) -> Evidence:
    cards: list[Card] = []
    n_checked = 0
    # **页级缓存**（B6）：同一页的行聚类只做一次。原来对每个数值都重跑一遍
    # ``rows_of_page``（实测某机构 171 项里 124 次是重复解析同一页），
    # 页数多、项数大时是纯浪费 —— 判定逻辑一个字没动（输出不变）。
    rows_cache: dict[int, list] = {}
    for r in results:
        if r.value is None:
            continue
        page = doc[r.page_no - 1]
        m = re.search(r"\d+(?:\.\d+)?", r.result_raw or "")
        if not m:
            continue
        needle = m.group(0)
        name_compact = _norm_name(r.item_raw)
        # 用**行身份**判断（rows_of_page 已按 y 分好行）：值与项目名必须在同一行
        rows = rows_cache.get(r.page_no)
        if rows is None:
            rows = rows_cache[r.page_no] = rows_of_page(page, r.page_no)
        hit_rows = [row for row in rows
                    if any(_token_has(w.text, needle) for w in row.words)]
        if not hit_rows:
            cards.append(Card(sample, r.page_no, "",
                              f"{r.item_raw} = {needle}",
                              "该数值在声称的页面上找不到（独立 token 匹配）",
                              "页码/值原文可疑"))
            continue
        n_checked += 1
        found = any(_norm_name(w.text) and _norm_name(w.text) in name_compact
                    for row in hit_rows for w in row.words)
        if not found:
            row_txt = " ⏐ ".join(row.text for row in hit_rows[:2])
            cards.append(Card(sample, r.page_no, row_txt,
                              f"{r.item_raw} = {needle}",
                              "含该数值的行里找不到这个项目名",
                              "疑似错行（行切分/段归属错）"))
    lv = "block" if cards else "ok"
    return Evidence("E2", "回读（页码/行）", lv,
                    f"{sample}：核对 {n_checked} 个数值，"
                    + (f"{len(cards)} 个可疑" if cards else "全部能回原页、且与项目名同行"),
                    cards)


def _e3_crosscheck(doc, template: Template, results, sample: str,
                   clip_dir: str = "") -> Evidence:
    """双实现交叉：几何列归属 vs 行内语义模式。"""
    noise = compile_patterns(template)["lab_noise"]
    by_name: dict[str, list] = {}
    for r in results:
        by_name.setdefault(_norm_name(r.item_raw), []).append(r)
    cards: list[Card] = []
    n_checked = 0
    for pno in range(doc.page_count):
        page = doc[pno]
        if template.lab.page_marker not in page.get_text():
            continue
        for row in rows_of_page(page, pno + 1):
            if _section_of(row, template, noise):
                continue
            for pat in row_items(_split_tokens(row)):
                if not (pat["value"] or pat["ref"]):
                    continue
                cands = by_name.get(_norm_name(pat["name"])) or []
                if not cands:
                    continue
                # 同名多条（同段同名、值类型不同的"同名撞键"）→ 先按**值**配对，
                # 配不上再按顺序取（否则会把定性条与定量条错配 → 假报不一致）
                r = None
                if pat["value"]:
                    for k, c in enumerate(cands):
                        try:
                            if c.value is not None and \
                                    abs(float(pat["value"]) - c.value) < 1e-9:
                                r = cands.pop(k)
                                break
                        except ValueError:
                            continue
                if r is None:
                    r = cands.pop(0)
                n_checked += 1
                diffs = []
                if _norm_name(pat["name"]) != _norm_name(r.item_raw):
                    diffs.append(f"名称：模式={pat['name']!r} vs 引擎={r.item_raw!r}")
                if pat["value"] and r.value is not None:
                    try:
                        if abs(float(pat["value"]) - r.value) > 1e-9:
                            diffs.append(f"值：模式={pat['value']} vs 引擎={r.value}")
                    except ValueError:
                        pass
                if pat["value"] and r.value is None:
                    diffs.append(f"值：模式读到 {pat['value']}，引擎没读到数值")
                if pat["ref"] and r.ref_raw and \
                        _norm_ref(pat["ref"]) != _norm_ref(r.ref_raw):
                    diffs.append(f"区间：模式={pat['ref']!r} vs 引擎={r.ref_raw!r}")
                if not diffs:
                    continue
                clip = _row_clip(page, row, clip_dir,
                                 f"{os.path.splitext(sample)[0]}_p{pno+1}_"
                                 f"r{row.words[0].y0:.0f}") if clip_dir else ""
                cards.append(Card(
                    sample, pno + 1, row.text,
                    f"名={r.item_raw} 值={r.value} 单位={r.unit_raw} 区间={r.ref_raw}",
                    f"名={pat['name']} 值={pat['value'] or '—'} "
                    f"单位={pat['unit'] or '—'} 区间={pat['ref'] or '—'}",
                    "；".join(diffs), clip))
    lv = "review" if cards else "ok"
    return Evidence("E3", "双实现交叉", lv,
                    f"{sample}：比对 {n_checked} 行，"
                    + (f"{len(cards)} 行两套读数不一致" if cards else "两套读数完全一致"),
                    cards, _e3_advice(cards))


def _e3_advice(cards: list[Card]) -> str:
    """从差异的**系统性模式**推一条可执行的修法（只推最像的一条，不猜）。"""
    if not cards:
        return ""
    ref_extra = 0
    extra_words: dict[str, int] = {}
    for c in cards:
        m = re.search(r"区间：模式='([^']*)' vs 引擎='([^']*)'", c.diff)
        if m and m.group(2).startswith(m.group(1)) and \
                len(m.group(2)) > len(m.group(1)):
            ref_extra += 1
            w = m.group(2)[len(m.group(1)):]
            extra_words[w] = extra_words.get(w, 0) + 1
    if ref_extra >= max(3, int(0.5 * len(cards))):
        w = max(extra_words, key=extra_words.get)
        return (f"参考值里混进了提示列的旗标（当前混入的取值是 {w!r}）→ 在模板里加 "
                f"\"flag_cells\": [\"<该列表头词>\"]（表头行里那一列的词，"
                f"常见如「提示」「状态」），再重跑验收")
    if any("值：模式读到" in c.diff and "引擎没读到" in c.diff for c in cards):
        return ("引擎没把值列读成数值（可能是『值列与提示列/参考列错位』）→ "
                "检查 header_cells 与该机构表头是否逐字一致")
    return ("逐条看对照卡：两侧读数不一致时**以 PDF 原文为准**修模板"
            "（先看是哪一列错位，再改 header_cells / section_x_max）")


def _e4_flag_crosscheck(doc, template: Template, results, sample: str) -> Evidence:
    """弱证据：报告画的旗标 vs 引擎复算的旗标（不一致只提示，不阻断）。"""
    mismatch = 0
    n = 0
    cards: list[Card] = []
    for r in results:
        printed = (r.pdf_flag or "").strip()
        if not printed or r.flag is None:
            continue
        n += 1
        mapped = ("abnormal" if printed in _ABNORMAL_WORDS else
                  "normal" if printed in _NORMAL_WORDS else None)
        if mapped and mapped != r.flag.value:
            mismatch += 1
            if len(cards) < 5:
                cards.append(Card(sample, r.page_no, "",
                                  f"{r.item_raw}={r.value} 区间={r.ref_raw} → 复算 {r.flag.value}",
                                  f"报告标注 {printed!r}",
                                  "印刷旗标与复算不一致"))
    if n == 0:
        return Evidence("E4", "箭头交叉", "skip", f"{sample}：行内没有可交叉的旗标（正常）")
    # 弱证据：小样本不做判据（真实案例里 3–4 行、1 行不一致是"机构标注口径不同"，
    # 不是列位错）；要 >=10 行且 >=3 行不一致且 >5% 才提示。
    ratio = mismatch / n
    if n < 10 or mismatch < max(3, int(0.05 * n)):
        return Evidence("E4", "箭头交叉", "ok",
                        f"{sample}：{n} 行有旗标，{mismatch} 行不一致"
                        f"（样本太小/量级不足，不作为判据；仅供知情）")
    return Evidence("E4", "箭头交叉", "review",
                    f"{sample}：{n} 行有旗标，{mismatch} 行不一致（{ratio:.0%}）"
                    f"→ 列位或机构标注可疑", cards)


def _e5_shape(doc, template: Template, results, sample: str) -> Evidence:
    cards: list[Card] = []
    units: dict[tuple, set] = {}
    for r in results:
        if r.value_type == "numeric":
            # 只对数值项分组：同名撞键（同段同名、值类型不同）是**合法**情形
            key = (r.section, _norm_name(r.item_raw), r.value_type)
            units.setdefault(key, set()).add(r.unit_raw or "")
        if r.value_type != "numeric":
            continue          # 定性项的"参考值"本来就是词（阴性/淡黄），不查形状
        # ① 参考列是个**纯裸数字** → 像"值"而不像"区间"
        if r.ref_raw and _NUM.match(_norm_ref(r.ref_raw)) \
                and r.ref_low is None and r.ref_high is None and not r.ref_op:
            cards.append(Card(sample, r.page_no, "",
                              f"{r.item_raw} 区间={r.ref_raw!r}",
                              "参考列内容像单个数值",
                              "参考列与值列可能错位"))
        # ② 值列里装的是一段**区间** → 两列多半错位
        if r.value is None and r.result_raw and _looks_range(r.result_raw):
            cards.append(Card(sample, r.page_no, "",
                              f"{r.item_raw} 值列={r.result_raw!r}",
                              "值列内容像参考区间",
                              "参考列与值列可能错位"))
        # ③ 值与区间的量级差得太离谱（保守阈值，避免误报）
        if r.value is not None and (r.ref_low is not None or r.ref_high is not None):
            lo = r.ref_low if r.ref_low is not None else 0.0
            hi = r.ref_high if r.ref_high is not None else max(r.value, 1.0)
            if hi > 0 and r.value > 0 and (r.value > hi * 100 or (lo > 0 and r.value < lo / 100)):
                cards.append(Card(sample, r.page_no, "",
                                  f"{r.item_raw}={r.value} 区间={r.ref_raw}",
                                  "值偏离区间 100 倍以上", "值与区间可能不同列/不同行"))
    dup_unit = [k for k, v in units.items() if len(v) > 1]
    if dup_unit:
        return Evidence("E5", "语义形状", "block",
                        f"{sample}：同项目单位不一致 {dup_unit[:3]}"
                        f"（抽取列位可疑）", cards)
    lv = "review" if cards else "ok"
    return Evidence("E5", "语义形状", lv,
                    f"{sample}：{len(results)} 项形状检查"
                    + (f"，{len(cards)} 项可疑" if cards else "，无异常"),
                    cards)


def _e6_baseline(per_sample: dict[str, int], template: Template) -> Evidence:
    """项数基线：与**模板自带基线**比（抓"抽漏一大半"），再看样本间一致性。

    ⛔ 只有样本间比较会漏掉"所有样本一起抽漏"的情形（同一天花板 → 彼此一致）：
    实测把 ``section_x_max`` 放宽到全页后 22 项掉到个位数，样本间仍"一致"，
    基线检查必须对着模板的 ``stats.items_per_report`` 比。
    """
    counts = {k: v for k, v in per_sample.items()}
    if any(v == 0 for v in counts.values()):
        zero = [k for k, v in counts.items() if v == 0]
        return Evidence("E6", "基线", "block", f"{zero} 抽到 0 项（化验页识别或列位出错）")
    base = (template.stats or {}).get("items_per_report") or []
    pkg_bases = (template.stats or {}).get("package_baseline") or {}
    if not base and pkg_bases:
        # **分套餐基线**（D3）：整体基线为空但记了各套餐的项数区间 → 用它们的合并
        # 当基线。否则"套餐多变"的机构会一直停在"漂移监控未启用"（A10 告警）上，
        # 改版照样发现不了 —— 实测把 section_x_max 放到 590（89 项 → 34 项）无人拦。
        base = sorted({n for vs in pkg_bases.values() for n in vs})
    if base:
        import statistics
        med = statistics.median(base)
        bad = {k: v for k, v in counts.items()
               if med and abs(v - med) / med > 0.15}
        if bad:
            extra = (f"（基线来自 {len(pkg_bases)} 个套餐的合并：{pkg_bases}）"
                     if pkg_bases and not (template.stats or {}).get(
                         "items_per_report") else "")
            return Evidence("E6", "基线", "block",
                            f"项数偏离模板基线 {med:.0f}：{bad}{extra} —— 疑似抽漏/抽多"
                            f"（≥15% 偏离即打回）",
                            advice="项数掉很多通常是 section_x_max 过宽（把数据行当段标题丢掉）"
                                   "或 header_cells 不匹配；先对着 PDF 抽查被丢的那几行")
    if len(counts) >= 2:
        lo, hi = min(counts.values()), max(counts.values())
        if hi > lo * 1.3:
            return Evidence("E6", "基线", "review",
                            f"各样本项数差异偏大：{counts}（可能某份版式不同或抽漏）")
    if not base:
        # A10：空基线**显式告警**（原来只写"模板未带基线"，读起来像备注）。
        # ⚠️ 只加呈现层：level 仍是 ok —— E1–E6 判定标准不动（铁律①）。
        # 为什么值得大声说：没有基线时"所有样本一起抽漏"（同一天花板、彼此一致）
        # 这条通道是**关闭**的，而它在界面上看不出来。
        return Evidence(
            "E6", "基线", "ok",
            f"项数：{counts} —— ⚠ **模板未带基线 → 漂移监控未启用**"
            f"（改版/抽漏只能靠样本间一致性发现）",
            advice="给模板补 stats.items_per_report（用 draft_template.py 自动回抽，"
                   "或按本报告的项数手工填）—— 没有基线时『所有样本一起抽漏』"
                   "不会再被 E6 抓到")
    base_txt = f"（模板基线 {base}）" if base else "（模板未带基线）"
    return Evidence("E6", "基线", "ok",
                    f"项数：{counts}{base_txt}" if len(counts) > 1 else
                    f"单份样本，项数 {list(counts.values())[0]}{base_txt}")


def vlm_row_tasks(doc, template: Template, results, sample: str,
                  max_rows: int = 12) -> list[dict]:
    """挑出"值得请第二读者复核"的行 → 任务清单（key/png/row_text/engine）。

    key 形如 ``p1_r593``（页-纵坐标），用作行图文件名与作答键；
    E7 与"给 agent 的工作单"共用同一套选择逻辑（两边必须看到同样的行）。
    """
    noise = compile_patterns(template)["lab_noise"]
    by_name: dict[str, list] = {}
    for r in results:
        by_name.setdefault(_norm_name(r.item_raw), []).append(r)
    tasks: list[dict] = []
    stem = os.path.splitext(sample)[0]
    for pno in range(doc.page_count):
        page = doc[pno]
        if template.lab.page_marker not in page.get_text():
            continue
        for row in rows_of_page(page, pno + 1):
            if len(tasks) >= max_rows:
                break
            if _section_of(row, template, noise):
                continue
            pat = row_pattern(_split_tokens(row))
            if not pat or not (pat["value"] or pat["ref"]):
                continue
            cands = by_name.get(_norm_name(pat["name"]))
            if not cands:
                continue
            png = _row_png(page, row)
            if not png:
                continue
            r = cands[0]
            tasks.append({
                "key": f"{stem}_p{pno+1}_r{row.words[0].y0:.0f}",
                "png": png, "row_text": row.text,
                "engine": f"名={r.item_raw} 值={r.value} 单位={r.unit_raw} "
                          f"区间={r.ref_raw}",
                "sample": sample, "page": pno + 1,
                "_result": r, "_row": row, "_pno": pno + 1,
                "_page": doc[pno]})
    return tasks


def _e7_vlm(doc, template: Template, results, sample: str, vlm,
            clip_dir: str, required: bool = False,
            degraded: bool = False) -> Evidence:
    """VLM 第二读者：渲染行图 → 让 VLM 独立读一遍 → 与引擎比对。

    **VLM 只当第二读者**：它的读数只用于"两套读数是否一致"，永不写进产物。
    默认的 VLM 就是**跑本 skill 的 agent 本人**（agent 模式：答案来自
    ``answers.json``；读不了图必须提示用户换模型，见 SKILL.md —— 不许静默跳过）。
    """
    if vlm is None or not getattr(vlm, "available", False):
        if required:
            # 明确请求了 VLM 却拿不到通道 → **必须提示，不许静默跳过**
            return Evidence("E7", "VLM 第二读者", "review",
                            "已请求 VLM 第二读者，但没有可用通道",
                            advice="请告知用户：当前模型不支持图像输入时，需要**切换到"
                                   "支持视觉的模型**，或配置 CHECKUP_VLM_* 外部端点"
                                   "（--allow-network 才会联网）")
        if degraded:
            return Evidence("E7", "VLM 第二读者", "skip",
                            "**降级输入**（OCR 叠层/扫描件类）未启用第二读者",
                            advice="本输入的数字可能来自图像识别（文本层 ≠ 纸面），"
                                   "E3 双实现交叉在文本层里查不出这类错 —— "
                                   "**强烈建议**加第二读者：--vlm（agent 读行图作答，"
                                   "图为准）")
        return Evidence("E7", "VLM 第二读者", "skip",
                        "未启用（可选；干净文字层输入默认不需要）",
                        advice="干净文字层输入：列位核对已由 E1–E6（尤其 E3 双实现"
                               "交叉）在文本层内覆盖；如需**视觉**独立复核再启用 "
                               "--vlm（agent 读行图作答）或配 CHECKUP_VLM_* 端点")
    cards: list[Card] = []
    n = 0
    max_rows = int(os.environ.get("CHECKUP_VLM_MAX_ROWS", "12"))
    for t in vlm_row_tasks(doc, template, results, sample, max_rows):
        r = t["_result"]
        got = vlm.read_row(t["png"], key=t["key"])
        n += 1
        if not got:
            continue
        clip = _row_clip(t["_page"], t["_row"], clip_dir, t["key"])
        gv = got.get("value")
        diffs = []
        if got.get("name") and _norm_name(str(got["name"])) != _norm_name(r.item_raw):
            diffs.append(f"名称：VLM={got['name']!r} vs 引擎={r.item_raw!r}")
        try:
            if gv is not None and r.value is not None and \
                    abs(float(gv) - r.value) > 1e-9:
                diffs.append(f"值：VLM={gv} vs 引擎={r.value}")
        except (TypeError, ValueError):
            pass
        if diffs:
            cards.append(Card(sample, t["_pno"], t["row_text"],
                              f"名={r.item_raw} 值={r.value}",
                              f"VLM：名={got.get('name')} 值={gv} "
                              f"区间={got.get('ref')}",
                              "；".join(diffs), clip))
    return Evidence("E7", "VLM 第二读者", "review" if cards else "ok",
                    f"{sample}：VLM 复核 {n} 行"
                    + (f"，{len(cards)} 行不一致" if cards else "，全部一致"),
                    cards)


# ---------------------------------------------------------------- 主入口

def accept(template: Template, pdf_paths: list[str], *, vlm: Any = None,
           clip_dir: str = "", vlm_required: bool = False,
           degraded: bool = False, draft_notes: Optional[list[str]] = None,
           decisions: Optional[list[dict]] = None) -> Report:
    """对（模板 × 一批 PDF）跑完整验收链 → :class:`Report`。

    ``draft_notes``：模板来自**自动起草**时的分级说明与待确认问题（A9），
    原样落到报告与档案；手写模板传空即可。
    """
    evidences: list[Evidence] = []
    per_sample: dict[str, int] = {}
    all_results = []
    for path in pdf_paths:
        sample = os.path.basename(path)
        doc = fitz.open(path)
        try:
            e1 = _e1_structure(doc, template, sample)
            evidences.append(e1)
            if e1.level == "block":
                per_sample[sample] = 0
                continue
            results = extract_lab_results(doc, _year_of(sample), template)
            per_sample[sample] = len(results)
            all_results.extend((sample, r) for r in results)
            evidences.append(_e2_readback(doc, template, results, sample))
            evidences.append(_e3_crosscheck(doc, template, results, sample,
                                            clip_dir=clip_dir))
            evidences.append(_e4_flag_crosscheck(doc, template, results, sample))
            evidences.append(_e5_shape(doc, template, results, sample))
            evidences.append(_e7_vlm(doc, template, results, sample, vlm,
                                     clip_dir, required=vlm_required,
                                     degraded=degraded))
        finally:
            doc.close()
    evidences.append(_e6_baseline(per_sample, template))
    return Report(org_id=template.org_id,
                  template_id=f"{template.org_id} v{template.template_version}",
                  samples=[os.path.basename(p) for p in pdf_paths],
                  n_items=sum(per_sample.values()),
                  evidences=evidences,
                  vlm=(getattr(vlm, "describe", lambda: "")() if vlm else ""),
                  draft_notes=list(draft_notes or []),
                  decisions=[dict(x) for x in (decisions or [])])


def _year_of(sample: str) -> str:
    m = re.search(r"(20\d{2})", sample)
    return m.group(1) if m else "0000"
