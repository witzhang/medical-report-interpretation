# -*- coding: utf-8 -*-
"""模板自动起草（框架 D1 的下一半）。

上一轮把「改代码」降成了「写配置」，但**写配置仍需要一个懂版式的人**。
本模块把这一步也自动化：丢一份 PDF 进去，扫出锚点、列位、栏数、段标题列、
噪声模式，产出一份**草案**模板（``confirmed=False``），人工确认后即可用。

为什么可行：中文化验单的表头词高度收敛——「检验项目 / 结果 / 参考值」
几乎家家这么写。真正每家不同的是**几何**（栏数、列位、缩进），
而几何是能算出来的。

**判据反转（A1，本次事故的修法）**：化验页的判据从"命中某个**页标记词**"
（一个封闭词表当唯一闸门）改成"**该页存在一行命中 ≥3 个表头词**"，
页标记词降为线索之一。事故经过：某机构 8 张化验页上「报告单/检验单/化验单」
全部 0 命中 → 起草器整批跳过 → 表头分支失败 → ``confidence=0``；
而同一批 PDF 上每张化验页都稳定命中 4 个表头词。**静默回落必须死**：
全 0 命中不再套默认值，而是 ``confidence=0`` + 明确报错。

**产出一定是草案。** 自动起草错了会静默抽错数据，所以：
``confirmed=False`` + ``evidence`` 逐条记明"这一项是从哪里推出来的"；
拿不准的字段**留空**并写进 ``open_questions``（待确认问题），绝不猜。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import fitz

from .layout import rows_of_page
from .tables import extract_lab_results
from .templates import LabTemplate, NarrativeTemplate, Template

#: 中文化验单表头词的收敛词表（这是本方法可行的前提）
ITEM_TOKENS = ["检验项目", "检测项目", "项目名称", "项目"]
VALUE_TOKENS = ["检测结果", "检验结果", "结果"]
REF_TOKENS = ["正常参考值", "参考区间", "参考范围", "参考值"]
UNIT_TOKENS = ["单位"]
#: 「提示/标志/状态」列的表头候选（表头全貌推断第 4/5 列时用）
FLAG_TOKENS = ["提示", "标志", "状态", "异常标志", "提示信息", "结果提示"]

ALL_HEADER_TOKENS = ITEM_TOKENS + VALUE_TOKENS + REF_TOKENS + UNIT_TOKENS

#: 「化验页」判据：一页里存在**一行命中 ≥3 个表头词**的表头行即认定。
MIN_HEADER_HITS = 3

MARKER_RE = re.compile(r"[\u4e00-\u9fffA-Za-z]{2,24}?(报告单|检验单|报告)")

#: 通用页面标记候选（用于"这一页是不是化验页"，跨机构通用）
GENERIC_MARKERS = ["检验报告单", "检验单", "化验单", "报告单"]

#: 机构后缀白名单：机构名**切在第一个后缀词的末尾**。
#: 实测：``…医院健康管理中心超声检查报告`` 与 ``…医院健康体检中心`` 都会裁成
#: ``…医院`` —— 报告标题/部门字样不再混进 ``required_anchors``（本次误取
#: 「…超声检查报告」当机构名，就是没有这一层）。
ORG_SUFFIXES = ["医院", "门诊部", "卫生院", "保健院", "疗养院",
                "卫生服务中心", "健康管理中心", "健康体检中心", "体检中心",
                "检验中心", "影像中心", "中心", "诊所", "医务室"]

#: 只有箭头/星号/连接符的列内容 —— 这类"标记列"不配 ``flag_cells``：
#: 箭头留给 E4 箭头交叉做证据、值内标记（如 ``+-*``）留在值里。
_MARK_ONLY = re.compile(r"^[↑↓*※·\-—+/\s]*$")


@dataclass
class Draft:
    """自动起草的模板草案。

    ``confidence`` / ``notes`` / ``open_questions`` 三件一起表达"部分成功"：

    * ``confidence == 0`` → **无法起草**（``notes`` 写明原因，不假成功）
    * ``confidence > 0`` 且有 ``open_questions`` → **部分成功**（结构已定，
      尚有待确认字段）；问题清单直出到验收报告
    * 否则 → **结构已定**（仍须人工确认几何假设后才能投入使用）
    """

    template: Template
    confidence: float                       #: 0–1，越高越可信
    notes: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)   #: 每一项是从哪里推出来的
    open_questions: list[str] = field(default_factory=list)  #: 待确认问题
    #: 几何候选（A13/A11）：``[(口径名, section_x_max), ...]`` —— 供求解器枚举
    geo_variants: list[tuple[str, float]] = field(default_factory=list)

    @property
    def status(self) -> str:
        """``ok`` = 结构已定 / ``partial`` = 部分成功 / ``fail`` = 无法起草。"""
        if self.confidence <= 0:
            return "fail"
        return "partial" if self.open_questions else "ok"

    @property
    def status_label(self) -> str:
        return {"ok": "结构已定", "partial": "部分成功（待确认字段）",
                "fail": "无法起草"}[self.status]

    def decisions(self) -> list[dict]:
        """**机器默认决定清单**（C1）：把"我替你定的"逐条写清楚。

        每条 = 决定 + 依据 + 不确定标记。依据取自 ``evidence``（"这一项是从哪里
        推出来的"）；不确定 = 有待确认问题落在同一处。**流程里不再有阻塞式提问**
        —— 决定照做、把不确定标出来，人只做事后抽检（见 C3 抽检单）。
        """
        out: list[dict] = []
        for k, v in self.evidence.items():
            if not v:
                continue
            txt = str(v)
            out.append({
                "决定": k, "依据": txt,
                "不确定": any(q[:10] in txt or txt[:10] in q
                              for q in self.open_questions)})
        for q in self.open_questions:
            out.append({"决定": "（待确认项：已按默认代入继续）",
                        "依据": q, "不确定": True})
        return out

    def render(self) -> str:
        lines = [f"草案 {self.template.org_id} v{self.template.template_version}"
                 f" · 置信度 {self.confidence:.2f} · {self.status_label}"
                 f" · confirmed=False", ""]
        for k, v in self.evidence.items():
            lines.append(f"  {k}: {v}")
        for n in self.notes:
            lines.append(f"  ⚠ {n}")
        for q in self.open_questions:
            lines.append(f"  ？ 待确认：{q}")
        return "\n".join(lines)


def _pick_header_row(rows: list, extra_vocab: Optional[set] = None):
    """找出含最多表头词的行，返回 (Row, 按出现顺序的表头词)。

    **返回 Row 而不是它的词表** —— 调用方要靠 ``row is header_row`` 判断
    "数据行从表头之后开始"，返回词表会让这个判断永远为假。

    命中数下限 ``MIN_HEADER_HITS``（3）：一行里能同时出现"检验项目/结果/参考值"
    这类字样，就是中文化验单的表头 —— 这是化验页的**主证据**（A1）。

    ``extra_vocab``：**自举**出来的表头词（A2，不在内置收敛词表里）。
    词表为空/不认识时靠它把表头认出来；调用方需自行分辨哪些词是"认识"的。
    """
    vocab = ALL_HEADER_TOKENS if not extra_vocab else \
        list(ALL_HEADER_TOKENS) + list(extra_vocab)
    best_row, best_hits, best_tokens = None, 0, []
    for row in rows:
        toks = [w.text for w in row.words if w.text in vocab]
        if len(toks) > best_hits:
            best_row, best_hits, best_tokens = row, len(toks), toks
    if best_row is None or best_hits < MIN_HEADER_HITS:
        return None
    return best_row, best_tokens


#: 自举表头词时允许的词形态：1–6 字、无数字/冒号/箭头/标点（表头词都是短名词）
_BOOT_WORD_BAD = re.compile(r"[\d:：↑↓，。、；！？（）()\[\]【】/\\|]")


def bootstrap_header_tokens(docs: list) -> tuple[list[str], str]:
    """**自举表头词**（A2）：一个词表都没有，也能把表头挖出来。

    表头行的形态特征（跨机构通用，不依赖任何词表）：

    ① **跨页重复出现**且**位置稳定** —— 同一行文本在多页上重复，各次的首词 x 一致；
    ② 由 **≥3 个短词**组成（1–6 字、无数字/冒号/箭头/标点）；
    ③ 横向**铺开**（末词明显在首词右侧）—— 表头是一排列名，不是一句话。

    与 A1 的判据互补：A1 用收敛词表认表头（快、准），这一路在**词表失灵**时兜底。

    返回 ``(词集, 依据)``；挖不到 → ``([], 原因)``。
    """
    groups: dict[str, dict] = {}
    for di, doc in enumerate(docs):
        for i in range(doc.page_count):
            for row in rows_of_page(doc[i], i + 1):
                ws = [w.text.strip() for w in row.words]
                if len(ws) < 3 or not all(
                        1 <= len(w) <= 6 and not _BOOT_WORD_BAD.search(w)
                        for w in ws):
                    continue
                if row.words[-1].x0 - row.words[0].x0 < 120:    # ③ 铺开
                    continue
                g = groups.setdefault(row.text.strip(),
                                      {"pages": set(), "x0s": [], "words": ws})
                g["pages"].add((di, i))
                g["x0s"].append(round(row.x0, 1))
    cands = [(len(g["pages"]), len(g["x0s"]), float(max(g["x0s"]) - min(g["x0s"])),
              text, g) for text, g in groups.items() if len(g["pages"]) >= 2]
    if not cands:
        return [], ("扫遍全部页面：没有任何一行同时满足『跨页重复（≥2 页）+ "
                    "≥3 个短词 + 横向铺开』→ 无法自举表头词")
    cands.sort(key=lambda c: (-c[0], -c[1], c[2]))
    n_pages, n_times, spread, text, g = cands[0]
    why = (f"自举：跨页重复行 {text!r}（{n_pages} 页 / {n_times} 次，"
           f"首词 x 漂移 {spread:.1f}pt）→ 表头词 {g['words']}")
    return list(g["words"]), why


def _generic_marker(docs: list) -> tuple[str, int]:
    """挑一个**通用**页面标记：出现在最多页面上的那个候选。

    两个坑：

    * 按出现**次数**计会把封面上的「体检报告」算赢（封面 + 每页页眉），
      要按**页数**去重。
    * 正则用非贪婪，会先匹配短的「…报告」。同一位置要取**最长**匹配
      （「检验报告单」优于「报告」）。

    **全 0 命中 → 返回 ``("", 0)``**（A1）：不回落默认值。静默套一个默认词
    会把"真化验页"整批跳过 —— 这正是本次 ``confidence=0`` 的起点；
    失败必须由调用方大声说出来。
    """
    best, best_pages = "", 0
    for cand in GENERIC_MARKERS:
        pages = sum(1 for doc in docs for i in range(doc.page_count)
                    if cand in doc[i].get_text())
        if pages > best_pages:
            best, best_pages = cand, pages
    return best, best_pages


def _detect_marker(docs: list) -> tuple[str, int]:
    """页面标记：**出现在最多页面**上的「…报告单」字样（机构抬头线索）。

    ⚠️ 这里取到的字样**只用作抬头线索与 required_anchors 候选**（专属字样），
    不能直接当 ``page_marker``（那是"是不是化验页"的通用闸门，见 A4）。
    """
    per_page: dict[str, set] = {}
    for di, doc in enumerate(docs):
        for i in range(doc.page_count):
            text = doc[i].get_text()
            # 同一页同一位置取最长匹配
            spans: list[tuple[int, int, str]] = []
            for m in MARKER_RE.finditer(text):
                spans.append((m.start(), m.end(), m.group(0)))
            keep: list[str] = []
            for a, b, g in spans:
                if any(a >= x and b <= y and (y - x) > (b - a)
                       for x, y, _ in spans):
                    continue
                keep.append(g)
            for g in keep:
                per_page.setdefault(g, set()).add((di, i))
    if not per_page:
        return "", 0
    top = max(per_page, key=lambda k: (len(per_page[k]), len(k)))
    return top, len(per_page[top])


def _page_spans(page) -> list[tuple[float, str]]:
    """页面的 ``(字号, 文本)`` 列表（``layout`` 层不带字号，这里读原始 dict）。"""
    out: list[tuple[float, str]] = []
    try:
        d = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return out
    for b in d.get("blocks", []):
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                t = (s.get("text") or "").strip()
                if t:
                    out.append((round(float(s.get("size") or 0.0), 1), t))
    return out


#: 抬头行形态：6–30 字、无数字、无冒号、无句子标点（排除整句正文/数据行）
_HEADER_LINE_BAD = re.compile(r"[\d:：，。、！？；,.;!?（）()【】\[\]]")


def _looks_like_header_line(text: str) -> bool:
    return bool(6 <= len(text) <= 30 and not _HEADER_LINE_BAD.search(text))


def _cut_org_name(text: str) -> str:
    """把一段抬头文本裁成机构名（**切在第一个机构后缀词的末尾**）。

    ``…医院健康管理中心超声检查报告`` / ``…医院健康体检中心`` → ``…医院``；
    后缀白名单一个都没命中（如封面被截断的 ``某某市干部健康``）→ ``""``
    （不像机构名，不采纳）。
    """
    t = text.strip()
    ends = [t.find(s) + len(s) for s in ORG_SUFFIXES if s in t]
    if not ends:
        return ""
    name = t[:min(ends)].strip(" ：:·-—")
    return name if 4 <= len(name) <= 30 else ""


def _detect_org_name(docs: list) -> tuple[str, str, list[str]]:
    """机构名（专属抬头）：**多路来源 + 后缀白名单 + 样本校验**。

    为什么不能只信一个来源：``MARKER_RE`` 最长匹配单独用会把
    「…医院健康管理中心超声检查报告」整段当机构名（本次实测踩到）——
    它是被**报告标题**污染了。三路互相印证才拦得住：

    ① **页眉固定行** —— 同一行文本在多页重复出现（抬头/页眉）；
    ② **最大字号文本** —— 封面题头（有的机构封面是整图，只剩字号可辨）；
    ③ **报告标题**（``MARKER_RE`` 最长匹配）—— 最后手段。

    同名候选**跨来源合并计票**，再要求"出现在 ≥2 份样本"上（只给 1 份样本时
    退化为"出现在 ≥2 页"，即"多页重复出现"这一条证据本身）。

    返回 ``(机构名, 依据, 待确认问题)``；校验不过 → ``("", 依据+原因, [问题])``。
    """
    texts = [[doc[i].get_text() for i in range(doc.page_count)] for doc in docs]

    def _coverage(name: str) -> tuple[int, int]:
        """(出现的样本份数, 出现的页数)。"""
        nd = sum(1 for per_doc in texts if any(name in t for t in per_doc))
        np_ = sum(1 for per_doc in texts for t in per_doc if name in t)
        return nd, np_

    votes: dict[str, list[str]] = {}      # 机构名 → 来源说明
    for doc in docs:
        for i in range(doc.page_count):
            for row in rows_of_page(doc[i], i + 1):
                t = row.text.strip()
                if not _looks_like_header_line(t):
                    continue
                nm = _cut_org_name(t)
                if nm:
                    votes.setdefault(nm, []).append(f"页眉行 {t!r}")
        for i in range(min(2, doc.page_count)):
            spans = _page_spans(doc[i])
            if not spans:
                continue
            size, text = max(spans, key=lambda x: (x[0], len(x[1])))
            nm = _cut_org_name(text)
            if nm:
                votes.setdefault(nm, []).append(f"最大字号 {text!r}（{size}pt）")
    title, _title_n = _detect_marker(docs)
    if title:
        nm = _cut_org_name(title)
        if nm:
            votes.setdefault(nm, []).append(f"报告标题 {title!r}")
    if not votes:
        return "", "没有任何一路来源给出像机构名的字样（机构后缀白名单未命中）", [
            "机构名（required_anchors）未自动确认：三路来源（页眉行/最大字号/"
            "报告标题）都没裁出机构名 → 路由会**少一道机构专属闸门**，"
            "请人工填一个『这份报告上确实出现』的机构字样"]

    best = None
    for nm, whys in votes.items():
        nd, np_ = _coverage(nm)
        ok = (nd >= 2) if len(docs) >= 2 else (np_ >= 2)
        key = (0 if ok else 1, -nd, -np_, len(nm))
        if best is None or key < best[0]:
            best = (key, nm, sorted(set(whys)), nd, np_, ok)
    _key, nm, whys, nd, np_, ok = best
    why = f"{nm!r}：来源 {whys[:3]}；覆盖 {nd} 份样本 / {np_} 页"
    if not ok:
        return "", why + " —— **未通过『出现在 ≥2 份样本』校验**", [
            f"机构名候选 {nm!r} 只覆盖 {nd}/{len(docs)} 份样本、{np_} 页 → "
            f"**未写入 required_anchors**（少了机构专属闸门，别家报告可能被误配到"
            f"本模板）；补 1–2 份同年样本后重跑，或手工填机构字样"]
    return nm, why, []


def _detect_noise_labels_pages(lab_rows: list, n_lab_pages: int) -> list[str]:
    """噪声标签：以冒号结尾、且**几乎每张化验页都出现**的短词。

    只在化验页上收，并且要求出现在每一张化验页 —— 否则会把体格检查、
    眼科那些「身高:」「心率:」全部收进来（它们本身是数据项，不是噪声）。
    """
    per_page: dict[str, set] = {}
    pat = re.compile(r"^([\u4e00-\u9fff]{2,6})[:：]$")
    for pi, (rows, _hit) in enumerate(lab_rows):
        for row in rows:
            for w in row.words:
                m = pat.match(w.text)
                if m:
                    per_page.setdefault(m.group(1), set()).add(pi)
    need = max(1, n_lab_pages)
    return sorted(t for t, ps in per_page.items() if len(ps) >= need)


def _cluster_x(values: list[float], tol: float = 4.0) -> list[float]:
    """把一堆 x 坐标聚成若干列中心。"""
    return [c for c, _ in _x_hist(values, tol)]


def _x_hist(values: list[float], tol: float = 4.0) -> list[tuple[float, int]]:
    """x 直方图 + 容差聚类：``[(列中心, 出现次数), ...]``（按 x 升序）。

    **跨页合并**统计（A13）：单页的 x 只有几个样本，簇中心容易被偶然值带偏；
    合并后"哪一簇是列"由**出现次数**说话 —— 与调研共识一致（列检测 =
    每行词数众数 + x 聚类，与 pdfplumber/camelot 的 explicit/stream 策略同源）。
    """
    clusters: list[list[float]] = []
    for v in sorted(values):
        if clusters and v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [(round(sum(g) / len(g), 1), len(g)) for g in clusters]


def _pick_section_split(hist: list[tuple[float, int]]
                        ) -> tuple[Optional[float], Optional[float], float, str]:
    """从 x 直方图挑"段标题列 / 项目列"的分界（A13 稳健化）。

    判据：**项目列 = 样本最多的那一簇** —— 每个项目一行，段标题只有十来个；
    分界取在**项目列与其左侧最近簇的正中**（取正中 = 两侧各留最大余量，
    页面 x 轻微漂移仍不会判错）。

    为什么不能用"取最左两簇"（旧实现）：本机构左侧有 3 簇
    （页脚 56.9 / 段标题 66.9 / 项目名 98.8），取前两簇会切在段标题与页脚
    之间（61.9）→ 段标题行全被当数据行抽成假项、页脚被当段标题
    （实测 89 项 → 98 项，多出的 9 项全是"段标题 → 检验者：某某"）。

    返回 ``(段标题列 x, 项目列 x, section_x_max, 依据)``。
    """
    if not hist:
        return None, None, 60.0, "没有数据行可分析 → section_x_max 用兜底值"
    item_x, item_n = max(hist, key=lambda t: (t[1], -t[0]))
    left = [h for h in hist if h[0] < item_x - 1]
    if not left:
        return None, item_x, round(max(0.0, item_x - 6), 1), (
            f"直方图 {hist} → 项目列 x≈{item_x}（{item_n} 次）**左侧没有别的簇**"
            f"（段标题与项目列同列，或本机构没有段标题）→ section_x_max 由项目列回推")
    nearest = max(left, key=lambda t: t[0])
    return nearest[0], item_x, round((nearest[0] + item_x) / 2, 1), (
        f"x 直方图 {hist} → 项目列 x≈{item_x}（{item_n} 次，最密簇）；"
        f"左侧最近簇 x≈{nearest[0]}（{nearest[1]} 次）→ "
        f"section_x_max={(nearest[0] + item_x) / 2:.1f}（取正中，两侧各留最大余量）")


def _column_consistency(lab_pages: list, item_x: Optional[float],
                        tol: float = 6.0) -> tuple[str, list[str]]:
    """列锚点自检（A13）：**每页**的数据行首词是否都落在项目列上。

    列位是跨页继承的，所以"页与页之间列位一致"是模板成立的前提。某页明显
    不落在项目列（续页改版/表格错位/扫描漂移）时必须说出来 —— 否则整页项会
    静默落进段标题或别的列。

    返回 ``(逐页自检说明, 需人工看的问题)``。
    """
    if item_x is None:
        return "无法自检（项目列未推断出）", []
    lines: list[str] = []
    issues: list[str] = []
    for p in lab_pages:
        if p["hit"] is None:
            continue
        seen, xs = False, []
        for row in p["rows"]:
            if row is p["hit"][0]:
                seen = True
                continue
            if not seen:
                continue
            head = row.first.strip()
            if (1 <= len(head) <= 10 and not re.search(r"[\d:：↑↓]", head)
                    and head not in ALL_HEADER_TOKENS):
                xs.append(row.x0)
        if not xs:
            continue
        pct = sum(1 for x in xs if abs(x - item_x) <= tol) / len(xs)
        lines.append(f"第{p['page'] + 1}页 {pct:.0%}")
        if pct < 0.6:
            issues.append(
                f"第 {p['page'] + 1} 页只有 {pct:.0%} 的数据行首词落在项目列 "
                f"(x≈{item_x:.0f}) → **该页列位与其它页不一致，建议人工看一眼**"
                f"（可能续页改版/表格错位；整页项会被判进段标题或别的列）")
    return "；".join(lines), issues


def _safe_noise_labels(detected: list[str]) -> tuple[list[str], list[str]]:
    """把"检出的签名词"过滤成可安全回写的噪声词表（A6 回写前的守卫）。

    危险：``narrative.noise_labels`` 会**整段吞掉**同名的叙述条目 ——
    若把「甲状腺超声:」「腹部超声:」这类**检查方式名**写进去，那项检查的结论
    会整段消失，而且验收链（E1–E6 是化验通道）抓不到。所以与检查方式
    （``NarrativeTemplate.narrative_sections``）沾边的标签一律不写。

    返回 ``(可回写的标签, 被拦下的标签)``。
    """
    mods = NarrativeTemplate().narrative_sections
    keep: list[str] = []
    blocked: list[str] = []
    for label in detected:
        (blocked if any(m and m in label for m in mods) else keep).append(label)
    return keep, blocked


# ------------------------------------------------------------ 页扫描（A1）

def _scan_pages(docs: list, marker: str,
                extra_vocab: Optional[set] = None) -> list[dict]:
    """扫**全部**页面，返回逐页记录。

    每条：``{rows, hit, has_marker, doc, page, text}``。

    * ``hit`` —— 该页的表头行（``_pick_header_row``，主证据）；
    * ``has_marker`` —— 该页是否命中通用页标记词（线索之一）。

    判据反转：**命中 ≥3 个表头词即认定化验页**，不再要求页标记词。
    非候选页也保留（``hit is None and not has_marker``）—— 判断标记词是否
    "越界出现在非化验页"时需要它们的文本。

    ``extra_vocab``：自举出来的表头词（A2），收敛词表失灵时拿来补判据。
    """
    out: list[dict] = []
    for di, doc in enumerate(docs):
        for i in range(doc.page_count):
            text = doc[i].get_text()
            rows = rows_of_page(doc[i], i + 1)
            out.append({
                "rows": rows,
                "hit": _pick_header_row(rows, extra_vocab),
                "has_marker": bool(marker) and marker in text,
                "doc": di, "page": i, "text": text,
            })
    return out


def _pick_page_marker(all_pages: list, lab_pages: list,
                      header_tokens: list[str]) -> tuple[str, str, list[str]]:
    """化验页标记：① 通用标记词 ② 表头词（**必须出现在每一张化验页上**）。

    为什么要求"出现在每一张化验页上"：``page_marker`` 是抽取层的**硬闸门**
    （``tables.extract_lab_results`` 里 ``page_marker not in page_text`` 即整页
    跳过）。标了一个只在少数页出现的词，续页与别的化验页会整页丢数据。

    实测本类机构（化验页通篇没有「报告单」字样）：通用词 0 命中，
    表头词「检验项目」每张化验页都有 —— 这正是手写模板当初选的标记。

    返回 ``(marker, 依据, 待确认问题)``；两个来源都不成立 → ``("", 原因, [])``。
    """
    hp = [p for p in lab_pages if p["hit"] is not None]
    if not hp:
        return "", "没有任何一页出现含 ≥3 个表头词的表头行", []

    def _n_pages_with(word: str) -> int:
        return sum(1 for p in all_pages if word in p["text"])

    inside = {(p["doc"], p["page"]) for p in hp}

    def _n_outside(word: str) -> int:
        return sum(1 for p in all_pages
                   if (p["doc"], p["page"]) not in inside and word in p["text"])

    # ① 通用标记词（优先：它才是"报告单"式的通用闸门）
    ok = [c for c in GENERIC_MARKERS if all(c in p["text"] for p in hp)]
    if ok:
        cand = max(ok, key=lambda c: (_n_pages_with(c), len(c)))
        return cand, (f"通用标记词 {cand!r}：出现在全部 {len(hp)} 张化验页上"
                      f"（全文 {_n_pages_with(cand)} 页）"), []

    # ② 表头词当标记：非化验页出现次数最少、且越靠左越好（最左列最像标记）
    best = None
    for idx, w in enumerate(header_tokens):
        if not all(w in p["text"] for p in hp):
            continue
        out_n = _n_outside(w)
        key = (out_n, idx)
        if best is None or key < best[0]:
            best = (key, w, out_n)
    if best is None:
        return "", (f"通用标记词（{GENERIC_MARKERS}）与表头词都没有出现在全部 "
                    f"{len(hp)} 张化验页上 → 无法确定化验页标记"), []
    (_key, idx), word, out_n = best
    questions: list[str] = []
    if out_n:
        questions.append(
            f"化验页标记用表头词 {word!r}，但它也在 {out_n} 个非化验页上出现 → "
            f"抽取时这些页也会被扫（**判给验收链 E1–E6 仲裁**，必要时手工换一个标记）")
    return word, (f"表头词 {word!r}（表头第 {idx + 1} 列）：出现在全部 {len(hp)} 张"
                  f"化验页上，非化验页 {out_n} 页"), questions


# -------------------------------------------------------- 表头全貌（A5）

#: 表头行里可能出现的"列名"总表：核心三列 + 提示/标志列。
#: （「标志」这类词不在核心表头词表里，但它确实是表头行的列名 —— 只扫
#: ``ALL_HEADER_TOKENS`` 会把它整个漏掉，A5 的"第 4/5 列"也就提不出来。）
ROW_TOKENS = ALL_HEADER_TOKENS + FLAG_TOKENS


def _header_cells_with_x(row) -> list[tuple[str, float]]:
    """表头行里的列名及其 x（按出现顺序；同名词全部保留）。"""
    return [(w.text, w.x0) for w in row.words if w.text in ROW_TOKENS]


def _column_texts(lab_pages: list, x: float, tol: float = 12.0) -> list[str]:
    """取数据行上落在该列 x 附近的词 —— 用于判断"这列装的是什么"。

    只看表头行**之后**的行（表头行本身跳过；续页重复表头里的同名词由调用方
    按词表滤掉）。
    """
    out: list[str] = []
    for p in lab_pages:
        hit = p["hit"]
        if hit is None:
            continue
        seen = False
        for row in p["rows"]:
            if row is hit[0]:
                seen = True
                continue
            if not seen:
                continue
            for w in row.words:
                if abs(w.x0 - x) <= tol:
                    out.append(w.text)
    return out


def _sample(texts: list[str], n: int = 4) -> str:
    """内容样例（去重、排序、截断）—— 供待确认问题里写清"这列装的是什么"。"""
    uniq = sorted({t for t in texts if t.strip()})
    head = "、".join(repr(t) for t in uniq[:n])
    return head + ("…" if len(uniq) > n else "") if uniq else "空"


def _dedup_order(items: list[str]) -> list[str]:
    """去重且保持出现顺序。"""
    return list(dict.fromkeys(items))


def _header_extras(lab_pages: list, header_row,
                   header_tokens: list[str]) -> tuple[list[str], list[str], list[str]]:
    """表头全貌推断（A5）：三列锚点之外的列按词表提名 flag_cells / unit_cells。

    规则：

    * 表头行上除**核心三列**（``pattern``：项目/结果/参考值）之外的列名都是
      候选"额外列"—— 它们可能排在中间（本机构：项目|结果|**标志**|参考值|单位），
      也可能排在末尾；
    * 命中 ``UNIT_TOKENS`` → ``unit_cells``；命中 ``FLAG_TOKENS`` → ``flag_cells``；
    * 都认不出 → **留空**并写进待确认问题（绝不猜）；
    * "提示/标志"列的**数据行内容只有箭头/星号**时也留空：那类列的信息已在
      「值 vs 区间」里，收进 ``flag_cells`` 会把箭头从 E4 箭头交叉里拿走。

    返回 ``(flag_cells, unit_cells, 待确认问题)``。
    """
    core = set(header_tokens[:3])          # 核心三列（三列锚点）
    core_cats = [cats for cats in (ITEM_TOKENS, VALUE_TOKENS, REF_TOKENS, UNIT_TOKENS)
                 if any(p in cats for p in core)]
    extras: list[tuple[str, float]] = []
    for tok, x in _header_cells_with_x(header_row):
        if tok in core:
            continue
        if any(tok in cats for cats in core_cats):
            continue                        # 同类别别名（如同时出现「项目」与「检验项目」）
        extras.append((tok, x))
    flag_cells: list[str] = []
    unit_cells: list[str] = []
    questions: list[str] = []
    for tok, x in extras:
        content = [t for t in _column_texts(lab_pages, x)
                   if t not in ROW_TOKENS]
        if tok in UNIT_TOKENS:
            unit_cells.append(tok)
            continue
        if tok in FLAG_TOKENS:
            meaningful = [t for t in content
                          if t.strip() and not _MARK_ONLY.match(t)]
            if meaningful:
                flag_cells.append(tok)
            else:
                questions.append(
                    f"表头 {tok!r} 列的数据行内容只有箭头/星号（{_sample(content)}）"
                    f"→ **未配置 flag_cells**：箭头留给 E4 箭头交叉做证据、"
                    f"值内标记（如 ``+-*``）留在值里；若该列确有文字提示，"
                    f"请手工补 flag_cells")
            continue
        questions.append(
            f"表头 {tok!r}（x≈{x:.0f}）既不像单位列也不像提示列"
            f"（数据行样例 {_sample(content)}）→ **未配置，需人工确认这列装什么**")
    return _dedup_order(flag_cells), _dedup_order(unit_cells), questions


# ------------------------------------------------- 候选-验证闭环（A3）

def semantic_lab_pages(docs: list) -> list[tuple[int, int, int]]:
    """**内容语义**候选集（A3）：不靠任何标记/表头词，只数"名称 + 数值 + 区间"行。

    **token 级判据**（不看列位、也不要求列顺序）：一行里同时出现
    ① 像项目名的 token（``accept._nameish``）、② 纯数值 token、
    ③ 区间形状的 token（``accept._looks_range``）。

    为什么不用 ``accept.row_items``：那个解析器假定"单位在值之后、区间之前"这一种
    列顺序（E3 的独立第二读者），而多家机构把单位排在**参考值之后**（本机构
    「钙 2.38 2.11-2.52 mmol/L」），用它会把真化验行全漏掉。这里只要形状
    共存，不判顺序 —— 候选集宁宽不窄，判定交给 E1–E6。

    一页里 ≥3 行这样 → 这一页**极可能**是化验页。这是**候选**，不是判定。

    返回 ``[(doc_idx, page_idx, 行数), ...]``（按行数降序）。
    """
    from .accept import _NUM, _looks_range, _nameish  # noqa: PLC0415
    out: list[tuple[int, int, int]] = []
    for di, doc in enumerate(docs):
        for i in range(doc.page_count):
            n = 0
            for row in rows_of_page(doc[i], i + 1):
                toks = [w.text.strip() for w in row.words if w.text.strip()]
                if len(toks) < 3:
                    continue
                has_name = any(_nameish(t) for t in toks)
                has_num = any(_NUM.match(t) for t in toks)
                has_ref = any(_looks_range(t) for t in toks)
                if has_name and has_num and has_ref:
                    n += 1
            if n >= 3:
                out.append((di, i, n))
    return sorted(out, key=lambda t: -t[2])


def verify_extraction(pdf_paths: list[str], template: Template,
                      docs: Optional[list] = None) -> tuple[int, list[str]]:
    """**E2 式回读验证**（A3）：抽一遍，再核对每个数值能不能回原页、与项目名同行。

    失败明细非空 = 这份假设站不住（列位/栏数/分界错），换下一个假设重试。
    """
    owned = docs is None
    docs_ = docs if docs is not None else [
        fitz.open(p) for p in pdf_paths if os.path.exists(p)]
    fails: list[str] = []
    n_ok = 0
    try:
        for path, doc in zip(pdf_paths, docs_):
            sample = os.path.basename(path)
            for r in extract_lab_results(doc, "0000", template):
                m = re.search(r"\d+(?:\.\d+)?", r.result_raw or "")
                if m is None:
                    continue
                page = doc[r.page_no - 1]
                hit_rows = [row for row in rows_of_page(page, r.page_no)
                            if any(m.group(0) in w.text for w in row.words)]
                if not hit_rows:
                    fails.append(f"{sample} p{r.page_no} {r.item_raw}={m.group(0)}"
                                 f"：该数值在声称的页面上找不到")
                    continue
                if not any(r.item_raw[:4] in row.text for row in hit_rows):
                    fails.append(f"{sample} p{r.page_no} {r.item_raw}={m.group(0)}"
                                 f"：含该数值的行里找不到项目名（疑似错行）")
                    continue
                n_ok += 1
    finally:
        if owned:
            for d in docs_:
                d.close()
    return n_ok, fails


def _geo_variants(hist: list[tuple[float, int]]) -> list[tuple[str, float]]:
    """几何候选变体（A3）：分界的几个合理取法，逐个交给验证器仲裁。

    ① ``nearest`` —— 项目列与**左侧最近簇**的正中（默认：两侧余量最大）；
    ② ``densest`` —— 项目列与**左侧最密簇**的正中（左侧簇里最像"段标题列"的）；
    ③ ``tight``   —— 项目列 - 6pt（只把明显更靠左的当段标题）。
    """
    item_x, item_n = (max(hist, key=lambda t: (t[1], -t[0])) if hist else (None, 0))
    out: list[tuple[str, float]] = []
    if item_x is None:
        return out
    left = [h for h in hist if h[0] < item_x - 1]
    if left:
        nearest = max(left, key=lambda t: t[0])
        densest = max(left, key=lambda t: (t[1], t[0]))
        out.append(("nearest", round((nearest[0] + item_x) / 2, 1)))
        if densest[0] != nearest[0]:
            out.append(("densest", round((densest[0] + item_x) / 2, 1)))
    out.append(("tight", round(max(0.0, item_x - 6), 1)))
    return out


def _cover_all_pages(docs: list, need: set, candidates: list[str]) -> str:
    """挑一个**覆盖 ``need`` 里全部页面**的标记候选（A3 页集自愈用）。

    取最长者（更长 = 更专属，越不容易在别的页面上越界命中）。
    """
    ok = [c for c in candidates
          if c and all(c in docs[di][pi].get_text() for di, pi in need)]
    return max(ok, key=len) if ok else ""


def _pick_by_verification(pdf_paths: list[str], docs: list, base: Template,
                          variants: list[tuple[str, float]]
                          ) -> tuple[Template, dict]:
    """候选-验证闭环（A3）：枚举候选变体 → 各跑一遍抽取 + 回读 → 取全过者。

    取优顺序：**失败数最少** → 项数最多（抽得全）→ 变体顺序（默认优先）。
    全部都有失败时返回失败最少的那个，并把明细交给调用方写成待确认问题。
    """
    best: Optional[tuple] = None
    log: list[str] = []
    for name, x_max in variants:
        cand = Template.from_dict(base.to_dict())
        cand.lab.section_x_max = x_max
        n_ok, fails = verify_extraction(pdf_paths, cand, docs)
        log.append(f"{name}(section_x_max={x_max})：回读通过 {n_ok} 个数值，"
                   f"失败 {len(fails)}")
        key = (len(fails), -n_ok)
        if best is None or key < best[0]:
            best = (key, cand, name, x_max, n_ok, fails)
    assert best is not None
    _key, cand, name, x_max, n_ok, fails = best
    return cand, {"chosen": name, "section_x_max": x_max, "n_verified": n_ok,
                  "failures": fails, "trials": log}


# ------------------------------------------------- 求解器化（A11）

#: 签名词（出现在"值"或"项目名"里 = 这一项是假项：段标题行被当数据行了）
SIGNATURE_WORDS = ["审核医生", "检验者", "检查者", "审核日期", "操作员",
                   "主检医生", "初审医师", "主检医师"]
_SIG_RE = re.compile("|".join(SIGNATURE_WORDS))


#: 散文句读：值/项目名里出现句读标点 → 这是**叙述行**被当化验行（不是数据）
_PROSE_RE = re.compile(r"[，。；、！？,;!?]|[:：]")


def quality_of(template: Template, pdf_paths: list[str]
               ) -> tuple[int, int, list[str]]:
    """候选模板的**品质分**（越小越好）：``(假项数, -项数, 疑点清单)``。

    全绿候选之间总得有个先后。两条判别力最强的假项特征（都实测过）：

    ① **签名词当了值/名**：段标题行（``*尿液分析[心脑]检验者：张永华``）被当
       数据行时抽出"项目名 = 段标题、值 = 检验者：某某"；
    ② **值/名是散文句**（含句读标点）：叙述/结论行（``甲状腺超声:…诊断:…``、
       ``1.ALT61U/L、GGT79U/L → 升高，脂肪肝，建议…``）被当化验行。

    真化验项的取值不长成这两种样子。第三项 ``-项数``：同样干净时抽得全的优先。
    """
    bad, n = 0, 0
    notes: list[str] = []
    for path in pdf_paths:
        if not os.path.exists(path):
            continue
        doc = fitz.open(path)
        try:
            for r in extract_lab_results(doc, "0000", template):
                n += 1
                name, val = r.item_raw or "", r.result_raw or ""
                why = ""
                if _SIG_RE.search(name) or _SIG_RE.search(val):
                    why = "签名"
                elif _PROSE_RE.search(val) or _PROSE_RE.search(name):
                    why = "散文"
                if why:
                    bad += 1
                    if len(notes) < 8:
                        notes.append(f"[{why}] {os.path.basename(path)} "
                                     f"p{r.page_no} {name!r}={val!r}")
        finally:
            doc.close()
    return bad, -n, notes


def _signature_noise_pattern(base: str) -> str:
    """在 ``base`` 噪声上追加**签名词**（不加行首锚点）。

    为什么必须"不加锚点"：段标题行常把签名词粘在后面
    （``*尿液分析[心脑]检验者：张永华``），默认 pattern 的 ``^(检验者|…)``
    命中不了 → 这行被当数据行抽成假项。实测加上它：93 → 89 项（与手写模板一致）。

    ⚠️ 必须**基于草稿自己的 pattern**（它可能已含机构名抬头等项），
    不能用 ``LabTemplate()`` 的默认值重建 —— 那会把草稿已有的噪声项丢掉。
    """
    return "(" + "|".join(SIGNATURE_WORDS) + ")|" + base


def _strict_noise_pattern(base: str) -> str:
    """**严格**噪声：签名词 + 任何冒号行 + 编号行 + ``base``。

    再多盖两类"看着像数据、其实是叙述"的行（实测随包样例的合成机构）：

    * 任何含冒号的行（``检查日期:2024年09月21日``、``甲状腺超声:…诊断:…``）；
    * 编号行（``1.ALT61U/L、GGT79U/L``、``2.血脂异常…``、``3.尿酸偏高…``）。
    """
    return _signature_noise_pattern(base) + r"|[:：]|^\d+\s*[.、]"


def variant_space(draft: Draft) -> list[tuple[str, Template]]:
    """候选变体空间（A11）：把草稿的**每个可争议决定**都展开成二元选择。

    维度（只展开真正有争议的，避免无意义组合爆炸）：

    * **噪声词锚点**：默认（行首锚定）vs 签名词不加锚点（见上，本次 89/82 就靠它）；
    * **分界口径**：草稿值 vs A13 的另一档候选（``Draft.geo_variants``）；
    * **单位列**：草稿提名 vs 留空（A5 的两条路）；
    * **标志列**：草稿提名 vs 留空；
    * **页标记**：草稿选择 vs 机构名（A4 的机构专属字样）；
    * **栏数**：草稿推断 vs 另一档（1↔2）。

    变体名只列**非默认**维度；全默认 = ``默认组合``。
    """
    base = draft.template
    dims: list[tuple[str, list[tuple[str, object]]]] = []

    base_noise = base.lab.noise_pattern
    dims.append(("噪声", [("默认", base_noise),
                          ("签名", _signature_noise_pattern(base_noise)),
                          ("严格", _strict_noise_pattern(base_noise))]))
    geo = [(n, x) for n, x in draft.geo_variants
           if abs(x - base.lab.section_x_max) > 0.5]
    if geo:
        dims.append(("分界", [("草稿", base.lab.section_x_max)] + geo))
    if base.lab.unit_cells:
        dims.append(("单位列", [("提名", list(base.lab.unit_cells)), ("空", [])]))
    if base.lab.flag_cells:
        dims.append(("标志列", [("提名", list(base.lab.flag_cells)), ("空", [])]))
    org = (base.required_anchors or [""])[0]
    if org and org != base.lab.page_marker:
        dims.append(("页标记", [("草稿", base.lab.page_marker), ("机构名", org)]))
    other = 2 if base.lab.column_groups == 1 else 1
    dims.append(("栏数", [(str(base.lab.column_groups), base.lab.column_groups),
                          (str(other), other)]))
    defaults = {d: opts[0][0] for d, opts in dims}

    out: list[tuple[str, Template]] = []
    seen: set[str] = set()

    def _apply(t: Template, dim: str, val: object) -> Template:
        t2 = Template.from_dict(t.to_dict())
        if dim == "噪声":
            t2.lab.noise_pattern = str(val)
        elif dim == "分界":
            t2.lab.section_x_max = float(val)         # type: ignore[arg-type]
        elif dim == "单位列":
            t2.lab.unit_cells = list(val)             # type: ignore[arg-type]
        elif dim == "标志列":
            t2.lab.flag_cells = list(val)             # type: ignore[arg-type]
        elif dim == "页标记":
            t2.lab.page_marker = str(val)
        elif dim == "栏数":
            t2.lab.column_groups = int(val)           # type: ignore[arg-type]
        return t2

    def _rec(i: int, chosen: list[tuple[str, str]], t: Template) -> None:
        if i == len(dims):
            key = json.dumps(t.to_dict(), sort_keys=True, ensure_ascii=False)
            if key in seen:
                return
            seen.add(key)
            name = "|".join(f"{d}={v}" for d, v in chosen if v != defaults[d])
            out.append((name or "默认组合", t))
            return
        dim_name, options = dims[i]
        for opt_name, val in options:
            _rec(i + 1, chosen + [(dim_name, opt_name)],
                 _apply(t, dim_name, val))

    _rec(0, [], base)
    return out


@dataclass
class SearchResult:
    """求解结果：全绿者里品质最好的一款 + 全部试跑记录 + 并列时的对照卡。"""

    template: Optional[Template]
    chosen: str
    trials: list[dict] = field(default_factory=list)
    green: list[str] = field(default_factory=list)
    card: str = ""
    draft: Optional["Draft"] = None

    def render(self) -> str:
        L = [f"求解器：试跑 {len(self.trials)} 款 · 全绿 {len(self.green)} 款"
             f" · 采用 {self.chosen}", ""]
        L += ["| 变体 | 项数 | 签名假项 | accept | 备注 |", "|---|---|---|---|---|"]
        for t in self.trials:
            L.append(f"| {t['name'] or '默认组合'} | {t['n_items']} | "
                     f"{t['signature']} | {t.get('verdict', '·')} | "
                     f"{t.get('note', '')} |")
        if self.card:
            L += ["", self.card]
        return "\n".join(L)


def search_template(pdf_paths: list[str], *, org_id: str = "AUTO-001",
                    org_name: str = "", draft: Optional[Draft] = None,
                    accept_fn=None, max_accept: int = 5) -> SearchResult:
    """**求解器化**（A11）：枚举候选变体 → 逐款验收 → 全绿者里取品质最好的一款。

    两段式（省时且不降标准）：

    1. **预筛**：每款跑一遍抽取 + 品质分（签名假项数、项数）→ 取前 ``max_accept`` 款；
    2. **仲裁**：对前几名**真跑** ``accept()``（E1–E6 全链），只认全绿者；
       全绿者之间仍按品质分排序；前两名**品质分并列**时出"对照卡"交人定夺。

    判定标准一格不放松：没有全绿者就返回 ``template=None``（调用方按 REVIEW 处理）。
    """
    if draft is None:
        draft = draft_template(pdf_paths, org_id=org_id, org_name=org_name)
    if draft.confidence <= 0:
        return SearchResult(None, "（草稿 conf=0，无法求解）",
                            trials=[{"name": "起草", "n_items": 0, "signature": 0,
                                     "note": "；".join(draft.notes)[:120]}],
                            draft=draft)
    docs = [fitz.open(p) for p in pdf_paths if os.path.exists(p)]
    try:
        space = variant_space(draft)
    finally:
        for d in docs:
            d.close()
    scored = []
    for name, t in space:
        sig, neg_n, notes = quality_of(t, pdf_paths)
        scored.append((sig, neg_n, name, t, notes))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    trials: list[dict] = [{"name": s[2], "n_items": -s[1], "signature": s[0],
                           "note": ("；".join(s[4])[:90]) if s[4] else ""}
                          for s in scored]
    if accept_fn is None:
        # 延迟导入：避免 autotemplate ↔ accept 循环
        from .accept import accept as accept_fn  # noqa: PLC0415
    green: list[tuple] = []
    for sig, neg_n, name, t, _notes in scored[:max_accept]:
        # 变体自带几何 → **不带上一版几何的基线**参选（基线与几何配套，
        # 见 ``_baseline_of``）；选定后再按选定几何回抽一次。
        t = Template.from_dict(t.to_dict())
        t.stats = {}
        rep = accept_fn(t, pdf_paths)
        row = next(x for x in trials if x["name"] == name)
        row["verdict"] = rep.verdict
        row["n_items"] = rep.n_items
        if rep.verdict == "ok":
            green.append((sig, neg_n, name, t))
    if not green:
        return SearchResult(None, "（没有全绿候选）", trials, draft=draft)
    green.sort(key=lambda x: (x[0], x[1], x[2]))
    sig, neg_n, name, chosen = green[0]
    chosen.stats = _baseline_of(chosen, pdf_paths)
    card = ""
    if len(green) > 1 and (green[1][0], green[1][1]) == (sig, neg_n):
        ties = [g for g in green if (g[0], g[1]) == (sig, neg_n)]
        card = ("\n## 对照卡（多个全绿候选品质并列，需人工定夺）\n\n"
                "> 它们都过了 E1–E6，品质分也一样 —— 需要人看一眼「差在哪一列」。\n\n")
        for _s, _n, nm, t in ties:
            card += (f"- **{nm or '默认组合'}**：section_x_max="
                     f"{t.lab.section_x_max}，page_marker={t.lab.page_marker!r}，"
                     f"unit_cells={t.lab.unit_cells}，flag_cells={t.lab.flag_cells}，"
                     f"column_groups={t.lab.column_groups}\n")
    return SearchResult(chosen, name or "默认组合", trials,
                        [g[2] for g in green], card, draft)


def _baseline_of(template: Template, pdf_paths: list[str]) -> dict:
    """用模板回抽一遍 → ``stats``（漂移监控基线）。

    ⚠️ 基线**必须与当前几何配套**：求解器给每个候选变体都要重算一次自己的基线，
    否则变体带着"上一版几何的基线"去验收，E6 会因为它把自己多抽的假项算进基线
    而把这些变体全打回（实测：随包样例的干净变体 22 项 vs 起草时含 8 个假项的
    基线 30 项 → 偏离 29% → BLOCK）。
    """
    totals: list[int] = []
    for p in pdf_paths:
        if not os.path.exists(p):
            continue
        doc = fitz.open(p)
        try:
            n = len(extract_lab_results(doc, "0000", template))
        finally:
            doc.close()
        if n:
            totals.append(n)
    return {"items_per_report": totals, "items_per_page": {}} if totals else {}


# ---------------------------------------------------------------- 主流程

def draft_template(pdf_paths: list[str], org_id: str = "AUTO-001",
                   org_name: str = "") -> Draft:
    """从一至多份 PDF 起草模板。多份同机构报告能显著提高置信度。"""
    docs = [fitz.open(p) for p in pdf_paths if os.path.exists(p)]
    try:
        if not docs:
            raise ValueError("没有可读的 PDF")
        notes: list[str] = []
        evidence: dict = {}
        questions: list[str] = []

        # ---- 1. 页面线索：专属抬头（required_anchors 候选）与通用页标记
        #
        # 要分开两件事：
        # * ``page_marker`` —— "这一页是不是化验页"，必须是**通用**短标记
        # * ``required_anchors`` —— "这是不是本机构"，用**专属**抬头
        # 混成一个的后果：别家报告因为抬头对不上而整页被跳过。
        #
        # 机构名走**多路来源 + 后缀白名单 + 样本校验**（A4）：单靠 MARKER_RE
        # 最长匹配会取到「…超声检查报告」（本次实测）。
        org, org_why, org_qs = _detect_org_name(docs)
        questions += org_qs
        title, title_n = _detect_marker(docs)
        marker, marker_pages = _generic_marker(docs)
        evidence["required_anchors"] = (f"required_anchors={[org] if org else []}"
                                        f"（{org_why}）"
                                        f"；报告标题候选={title!r}（{title_n} 页）")

        # ---- 2. 化验页判定（A1：主证据 = 表头行命中 ≥3 个表头词）
        all_pages = _scan_pages(docs, marker)
        lab_pages = [p for p in all_pages if p["hit"] is not None or p["has_marker"]]
        header_pages = [p for p in lab_pages if p["hit"] is not None]

        # 自举表头词（A2）：先算出来进证据；收敛词表失灵时改用它再扫一遍。
        boot_tokens, boot_why = bootstrap_header_tokens(docs)
        evidence["bootstrap_header_tokens"] = boot_why
        if not header_pages and boot_tokens:
            extra = set(boot_tokens) - set(ALL_HEADER_TOKENS)
            extra_pages = _scan_pages(docs, marker, extra_vocab=extra)
            header_pages = [p for p in extra_pages if p["hit"] is not None]
            if header_pages:
                lab_pages = [p for p in extra_pages
                             if p["hit"] is not None or p["has_marker"]]
                notes.append(
                    f"表头词不在收敛词表里 → 改用**自举词集**认出 "
                    f"{len(header_pages)} 页化验页（{boot_why}）")

        header_row = None
        header_tokens: list[str] = []
        for p in header_pages:
            row, toks = p["hit"]
            if len(toks) > len(header_tokens):
                header_row, header_tokens = row, toks

        if not header_pages:
            # fail-loud（不再静默回落默认值）
            if not lab_pages:
                notes.append(
                    f"**没找到表头行，也没有识别出化验页**：全 {len(all_pages)} 页中"
                    f"既没有一页出现含 ≥{MIN_HEADER_HITS} 个表头词的表头行"
                    f"（表头词表：{'、'.join(ALL_HEADER_TOKENS)}），也没有一页命中"
                    f"通用页标记词（{GENERIC_MARKERS}）→ confidence=0，**不起草**。"
                    f"请人工确认版式：把该机构的表头叫法登记进词表，"
                    f"或手工写模板后用 --template 验收")
            else:
                notes.append(
                    f"**没找到表头行**：识别出 {len(lab_pages)} 页疑似化验页"
                    f"（命中页标记词 {marker!r}），但没有一页出现含 "
                    f"≥{MIN_HEADER_HITS} 个表头词的表头行（该机构表头叫法可能不在"
                    f"收敛词表里）→ confidence=0，**不起草**。"
                    f"请人工确认表头叫法后登记进词表")
            t = Template(org_id=org_id, org_name=org_name, template_version=1,
                         header_anchor_strings=[org] if org else [],
                         required_anchors=[org] if org else [])
            return Draft(t, 0.0, notes, evidence, questions)

        # ---- 2b. 化验页标记（通用词优先，其次表头词；都不行 → fail-loud）
        marker, marker_why, marker_qs = _pick_page_marker(
            all_pages, lab_pages, header_tokens)
        questions += marker_qs
        if not marker:
            notes.append(f"**无法确定化验页标记**：{marker_why} → confidence=0，"
                         f"**不起草**。请人工指定一个「每张化验页都有」的通用短词")
            t = Template(org_id=org_id, org_name=org_name, template_version=1,
                         header_anchor_strings=[org] if org else [],
                         required_anchors=[org] if org else [])
            return Draft(t, 0.0, notes, evidence, questions)
        evidence["page_marker"] = (
            f"page_marker={marker!r}（{marker_why}）"
            + ("；通用标记词候选全部 0 命中 → 已改用表头词"
               if not marker_pages else ""))

        # ---- 3. 表头词与栏数
        n_item = sum(1 for t_ in header_tokens if t_ in ITEM_TOKENS)
        n_grp = max(1, n_item)
        pattern: list[str] = []
        for t_ in (ITEM_TOKENS, VALUE_TOKENS, REF_TOKENS, UNIT_TOKENS):
            for cand in t_:
                if cand in header_tokens:
                    pattern.append(cand)
                    break
        pattern = pattern[:3]
        unknown = [t_ for t_ in header_tokens if t_ not in ROW_TOKENS]
        if unknown:
            # 自举出来的词不在收敛词表里 → 能认页，但组不出列锚点（列锚点必须
            # 是**已知**的项目/结果/参考值字样）。如实报出来，别硬套。
            questions.append(
                f"自举出的表头词 {unknown} 不在收敛词表里 → 请人工登记进词表"
                f"（登记后列锚点才能组装；现在只认得 {pattern}）")
        if not pattern:
            notes.append(
                f"**表头词一个都不认识**：该机构表头词是 "
                f"{header_tokens or '未识别'}（自举依据：{boot_why}），"
                f"但都不在收敛词表（{'/'.join(ITEM_TOKENS + VALUE_TOKENS + REF_TOKENS)}）"
                f"里 → 组不出列锚点，confidence=0，**不起草**。"
                f"请把该机构的表头叫法登记进词表后重跑")
            t = Template(org_id=org_id, org_name=org_name, template_version=1,
                         header_anchor_strings=[org] if org else [],
                         required_anchors=[org] if org else [])
            return Draft(t, 0.0, notes, evidence, questions)

        # ---- 4. 表头全貌（A5）：三列锚点之外的列提名 flag_cells / unit_cells
        extra_flag, extra_unit, extra_qs = _header_extras(
            header_pages, header_row, header_tokens)
        questions += extra_qs
        evidence["header_cells"] = (
            f"{pattern}（表头行共 {len(header_tokens)} 个表头词，推断 {n_grp} 栏）"
            + (f"；第 4/5 列提名 flag_cells={extra_flag} unit_cells={extra_unit}"
               if (extra_flag or extra_unit) else ""))

        # ---- 5. 段标题列 vs 项目名列（只看**表头之后的数据行**）
        #
        # 关键：项目名的 x 要取自数据行，不能取表头。表头的「检验项目」常与
        # 段标题列只差几 pt（本机构 57 vs 47），拿表头当项目列会把段标题列
        # 判成"不存在"。
        #
        # 稳健化（A13）：跨页合并 x 直方图 + 容差聚类 → **最密簇 = 项目列**，
        # 分界取"项目列与其左侧最近簇的正中"。旧实现"取最左两簇"会在
        # 左侧有 3 簇（页脚/段标题/项目名）时切错（本机构实测 89 → 98 项）。
        data_first_xs: list[float] = []
        for p in header_pages:
            hit = p["hit"]
            seen_header = False
            for row in p["rows"]:
                if row is hit[0]:
                    seen_header = True
                    continue
                if not seen_header:
                    continue
                head = row.first.strip()
                if (1 <= len(head) <= 10
                        and not re.search(r"[\d:：↑↓]", head)
                        and head not in ALL_HEADER_TOKENS):
                    data_first_xs.append(round(row.x0, 1))
        hist = _x_hist(data_first_xs)
        section_x, item_x, section_x_max, why = _pick_section_split(hist)
        if section_x is None:
            notes.append(f"{why}，**必须人工确认**")
        evidence["section_x_max"] = why
        # 列位自检（A13）：每页数据行首词是否都落在项目列上
        consistency, col_issues = _column_consistency(header_pages, item_x)
        evidence["column_consistency"] = consistency
        questions += col_issues

        # ---- 6. 噪声标签：只在化验页上收（要求每张化验页都出现）
        noise_raw = _detect_noise_labels_pages(
            [(p["rows"], p["hit"]) for p in header_pages], len(header_pages))
        noise, blocked = _safe_noise_labels(noise_raw)
        base_noise = ["审核医生", "检验者", "审核日期", "操作员", "主检医生"]
        merged_noise = sorted(set(noise) | set(base_noise))
        # A6：检出结果**回写模板**（原来算完就丢，是纯 bug）。
        # 与默认词表取并集：默认里的身份类标签（姓名/性别/年龄/体检号…）
        # 不能因为"这次没检出"就丢掉。
        noise_labels = sorted(set(NarrativeTemplate().noise_labels)
                              | set(merged_noise))
        evidence["noise_labels"] = (
            f"化验页检出 {noise_raw or '无'}"
            + (f"（拦下检查方式同名标签 {blocked}：写进噪声词表会吞掉叙述条目）"
               if blocked else "")
            + f" + 签名基线 {base_noise} → 模板 narrative.noise_labels"
              f"（{len(noise_labels)} 条）")

        # ---- 7. 组装
        first = docs[0][0]
        page_size = [round(first.rect.width, 1), round(first.rect.height, 1)]
        evidence["page_size"] = f"{page_size}（首份 PDF 首页实测）"
        # 机构名抬头行算**噪声行**：页标记词退到机构名时（续页没有『报告单』字样），
        # 抬头行本身会被当数据行抽成假项（实测："…医院" 变成一项）。
        noise_pattern = LabTemplate().noise_pattern
        if org:
            noise_pattern = f"{org}|{noise_pattern}"
        t = Template(
            org_id=org_id, org_name=org_name, template_version=1,
            valid_from="", page_size=page_size,
            header_anchor_strings=[a for a in [org, *pattern] if a],
            required_anchors=[org] if org else [],
            lab=LabTemplate(
                page_marker=marker, header_cells=pattern,
                column_groups=n_grp, section_x_max=section_x_max,
                flag_cells=extra_flag, unit_cells=extra_unit,
                section_stop=_dedup_order(pattern + extra_flag + extra_unit),
                noise_pattern=noise_pattern),
            narrative=NarrativeTemplate(noise_labels=noise_labels),
            notes=["自动起草，confirmed=False；使用前必须人工确认几何假设"],
        )
        # 置信度：表头词齐全 + 段标题列独立 + 噪声标签有检出
        conf = 0.0
        conf += 0.5 if len(pattern) == 3 else 0.2
        conf += 0.3 if (section_x is not None
                        and item_x is not None
                        and item_x - section_x >= 4) else 0.0
        conf += 0.2 if noise else 0.0

        # ---- 7b. 候选-验证闭环（A3）：几何变体 + 页集自愈
        #
        # 提案层可以聪明，落盘层必须机械：候选变体各跑一遍抽取，**回读验证**
        # （每个数值能回原页、与项目名同行）说了算；失败就换下一个假设重试。
        variants = _geo_variants(hist)
        geo_kept = list(variants)
        if len(variants) > 1:
            t, vinfo = _pick_by_verification(pdf_paths, docs, t, variants)
            evidence["verification"] = (
                f"候选变体逐个回读验证：{'；'.join(vinfo['trials'])}"
                f" → 采用 {vinfo['chosen']}（section_x_max="
                f"{vinfo['section_x_max']}，回读通过 {vinfo['n_verified']} 个数值）")
            if vinfo["failures"]:
                questions.append(
                    f"回读验证有 {len(vinfo['failures'])} 处对不上（"
                    f"{vinfo['failures'][:3]}）→ 模板几何大概率还不对，"
                    f"**必须人工看**这几行")
        # 页集自愈：内容语义候选页（≥3 行"名称+值+区间"）若没被标记词覆盖，
        # 那些页会被整页跳过（静默丢数据）→ 换一个覆盖全部候选页的标记词。
        #
        # ⚠️ 自愈必须**比人工更保守**：回读验证抓得住"值配错行"，抓不住
        # "多扫了非化验页"（那些行的值/名同页同行，回读照样通过 —— 实测
        # 换成「结果」这种通用词后项数 124 → 131，多出来的是体格检查页）。
        # 所以再叠一条硬约束：**新标记词不得扫到候选集之外的页**。
        uncovered = [(di, pi, n) for di, pi, n in semantic_lab_pages(docs)
                     if not (marker and marker in docs[di][pi].get_text())]
        if uncovered:
            need = {(di, pi) for di, pi, _ in uncovered} | {
                (p["doc"], p["page"]) for p in header_pages}
            covered = {(p["doc"], p["page"]) for p in all_pages
                       if marker and marker in p["text"]}
            alt = _cover_all_pages(docs, need,
                                   GENERIC_MARKERS + list(header_tokens)
                                   + ([org] if org else []))
            alt_pages = {(di, i) for di, doc in enumerate(docs)
                         for i in range(doc.page_count)
                         if alt and alt in doc[i].get_text()}
            spilled = sorted(p + 1 for _, p in alt_pages - need - covered)
            if alt and alt != marker and not spilled:
                cand = Template.from_dict(t.to_dict())
                cand.lab.page_marker = alt
                n_ok, fails = verify_extraction(pdf_paths, cand, docs)
                cur_ok, _cur = verify_extraction(pdf_paths, t, docs)
                evidence["page_set_selfheal"] = (
                    f"语义候选页 {sorted(p + 1 for _, p, _ in uncovered)} 未被标记词 "
                    f"{marker!r} 覆盖 → 试换标记词 {alt!r}：回读通过 {n_ok} 个数值"
                    f"（原标记 {cur_ok} 个），失败 {len(fails)}")
                if not fails and n_ok > cur_ok:
                    t = cand
                    marker = alt
                    questions.append(
                        f"**页集自愈**：原标记词覆盖不全（漏了第 "
                        f"{sorted(p + 1 for _, p, _ in uncovered)} 页）→ 已改用 "
                        f"{alt!r} 覆盖全部候选页；请人工确认这些页确为化验页")
                else:
                    questions.append(
                        f"语义候选页 {sorted(p + 1 for _, p, _ in uncovered)} "
                        f"未被标记词覆盖，但换 {alt!r} 未被回读验证接受"
                        f"（失败 {len(fails)} 处）→ **人工确认这些页**")
            else:
                why2 = (f"（候选标记词 {alt!r} 会多扫第 {spilled} 页 → 不接受，"
                        f"宁可报出来让人看）" if alt and alt != marker and spilled
                        else "")
                questions.append(
                    f"语义上是化验页、但没被标记词覆盖的页面："
                    f"{[(p + 1, n) for _, p, n in uncovered]}"
                    f"（各自 ≥3 行『名称+值+区间』）→ 这些页会被整页跳过，"
                    f"**人工确认它们是不是化验页**，是则换一个覆盖它们的标记词"
                    f"{why2}")

        # ---- 8. 抽取基线（供漂移监控）：用草案自己回抽一遍
        t.stats = _baseline_of(t, pdf_paths)
        if t.stats.get("items_per_report"):
            evidence["stats"] = (f"items_per_report="
                                 f"{t.stats['items_per_report']}（用草案回抽得到）")
        else:
            notes.append("回抽 0 项 → 未能建立抽取基线，**漂移监控未启用**"
                         "（先确认 page_marker / column_groups / section_x_max "
                         "是否配对）")

        return Draft(t, round(conf, 2), notes, evidence, questions, geo_kept)
    finally:
        for d in docs:
            d.close()
