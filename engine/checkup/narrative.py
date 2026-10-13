# -*- coding: utf-8 -*-
"""D3 · 叙述层结构化：把影像/心电图/体格检查的页面变成结构化字段。

对应框架文档 §一 步骤 2 的两条通道：

* **通道 A · 结构化表格** —— 体格检查/眼科那类「标签: 值」双栏表
  （身高/体重/血压/腰围…）→ ``RawObservation``
* **通道 B · 叙述文本** —— 彩超/CT/心电图/经颅多普勒/骨密度那类成段描述
  → ``NarrativeItem``

通道 A 看似简单，但它是去手工字典（框架 D12b）的前提。

设计要点
--------
1. **原文整段保留**（``description_verbatim``）——字段化必然丢信息，
   原文永远是最底层证据，任何结论都要能点回来。
2. **测量值按语义归侧，不按出现位置**——已踩过的坑：2025 年原文把左侧
   写在前面，按位置归侧会凭空造出「先跳升再回落」的假趋势。
3. **一行可能是两项**——本机构体格检查页是双栏「标签:值」表，必须按标签
   的位置切段；否则右栏会被并进左栏（体重/收缩压/腰围会整批丢失）。
4. **不做医学判断**——这里只回答"报告上写了什么"，不回答"这严重吗"。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

import fitz

from .layout import Row, rows_of_page
from .schema import (DocumentMeta, DeptSummary, InstitutionFinding, Measurement,
                     NarrativeItem, RawObservation)
from .templates import Template, compile_patterns, require_template

# ---------------------------------------------------------------- 版面常量

#: 标签列的右边界。本机构报告里项目标签的 x0 ≈ 50（右栏 ≈ 305），续行 x0 ≈ 163。
LABEL_X_MAX = 130.0

#: 段标题/项目标签所在的列（x0 ≈ 50）。
SECTION_X_MAX = 70.0

#: 小结列表的缩进列（x0 ≈ 83）。**这是第二级缩进**：
#: 段标题在 ≈51，小结条目在 ≈83，描述续行在 ≈163。
#: 不区分这两级，「脂肪肝（轻度）」「胆囊息肉」这类短小结行会被误判成段标题。
SUMMARY_X = SUMMARY_X_MIN = 70.0

#: 「名称:」形态的标签（整个文字块以冒号结尾）
LABEL_RE = re.compile(r"^([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9\-]{0,11})[:：]$")

#: 成段叙述的检查方式。**不在**此集合的段（一般项目/眼科/口腔…）走通道 A。
NARRATIVE_SECTIONS = {
    "彩超", "CT", "MRI", "磁共振", "超声", "心电图", "经颅多普勒", "TCD",
    "骨密度", "胸片", "钼靶", "DR", "X线", "胃镜", "肠镜", "肺功能",
    "动脉硬化", "人体成分",
}

_PAT_CACHE: dict = {}


def _pat(t: Template) -> dict:
    """按模板缓存编译好的正则。"""
    key = (t.org_id, t.template_version)
    if key not in _PAT_CACHE:
        _PAT_CACHE[key] = compile_patterns(t)
    return _PAT_CACHE[key]


def canonical_modality(section: Optional[str],
                       template: Template) -> Optional[str]:
    """段名归一到规范检查方式。

    报告里同一个检查在不同页/不同年写作「CT」或「CT检查」、「心电图」或
    「心电图检查」。不做这一步，``CT检查`` 段会被判成非叙述段而整段丢失。
    """
    if not section:
        return None
    secs = set(template.narrative.narrative_sections)
    t = section.strip()
    if t in secs:
        return t
    if t.endswith("检查") and t[:-2] in secs:
        return t[:-2]
    # 机构把检查方式写进**项目名**（「甲状腺彩超」「【CT-平扫】胸部」）时，
    # 段名里什么都没有 —— 按模板的别名表折算到规范名（覆盖判定只认规范名）。
    for pat, mod in template.narrative.modality_aliases:
        if re.search(pat, t):
            return mod
    return t if t in secs else None


#: 不是检查项目的标签
NOISE_LABELS = {
    "医师", "检验者", "审核医生", "操作员", "主检医生", "审核日期",
    "姓名", "性别", "年龄", "保健号", "体检号", "单位", "体检日期", "ID",
}

NOISE_ROW_RE = re.compile(
    r"体检日期[:：]|保健号|体检号|^姓名[:：]|^ID[:：]|^第[\d\-]+页|"
    r"体\s*检\s*报\s*告|检验报告单|^单位$|^性别$|^年龄$")

#: 签名行。注意「医师：马斌」是**一个**文字块，不会命中 LABEL_RE，需单独拦。
NOISE_INLINE_RE = re.compile(r"^(医师|检验者|检查者|检查医师|审核医生|操作员|主检医生|审核日期)[:：]")

# ---------------------------------------------------------------- 语义正则

SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[x×X]\s*(\d+(?:\.\d+)?)"
    r"(?:\s*[x×X]\s*(\d+(?:\.\d+)?))?\s*(cm|mm)")

LEN_RE = re.compile(
    r"(?:长径|短径|厚|直径|内径|大小|内膜居中)\s*约?\s*"
    r"(\d+(?:\.\d+)?)\s*(cm|mm)")

#: 裸数值（骨密度「0.8」这类无单位的情形——正是已记录的坑：无单位无部位）
BARE_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([^\d\s]{0,6})$")

SIDE_RE = re.compile(r"(右侧|右叶|右肾|右肺|右乳|左叶|左侧|左肾|左肺|左乳|双侧|两侧|双肾|双肺)")

DESCRIPTOR_RE = re.compile(r"(低回声|高回声|强回声|等回声|无回声|混合回声|囊实性|囊性|实性)")

GRADE_RE = re.compile(r"(TI-?RADS|BI-?RADS|Lung-?RADS|C-?RADS)\s*[：:]?\s*"
                      r"(\d+\s*[a-cA-C]?\s*类|\d+\s*[a-cA-C]?)")

CLAUSE_SPLIT_RE = re.compile(r"[，,。;；]")

#: 标量值：可选的比较符 + 数字 + 单位
SCALAR_VALUE_RE = re.compile(r"^([<>≤≥]?)\s*(\d+(?:\.\d+)?)\s*([^\d\s,，]*)")

#: 小结行 → 检查项目的器官关键词（一条小结可命中多个器官）
ORGAN_KEYS: list[tuple[str, str]] = [
    ("甲状腺", "甲状腺"),
    ("颈部血管", "颈部血管"), ("颈动脉", "颈部血管"),
    ("肝脏", "肝脏"), ("肝", "肝脏"),
    ("胆囊", "胆囊"), ("胆", "胆囊"),
    ("胰腺", "胰腺"), ("胰", "胰腺"),
    ("脾脏", "脾脏"), ("脾", "脾脏"),
    ("双肾", "双肾"), ("肾", "双肾"),
    ("输尿管", "输尿管"),
    ("膀胱", "膀胱"),
    ("前列腺", "前列腺"),
    ("子宫", "子宫"), ("卵巢", "卵巢"),
    ("乳腺", "乳腺"),
    ("肺", "肺"), ("肺", "肺部CT"), ("胸部", "肺部CT"),
]

SIDE_NORMALIZE = {"右侧": "右", "右叶": "右", "右肾": "右", "右肺": "右", "右乳": "右",
                  "左侧": "左", "左叶": "左", "左肾": "左", "左肺": "左", "左乳": "左",
                  "双侧": "双", "两侧": "双", "双肾": "双", "双肺": "双"}


# ---------------------------------------------------------------- 行 → 条目

@dataclass
class _Entry:
    """一段「标签 + 值/描述」的原始条目。"""

    page_no: int
    section: Optional[str]
    name: str
    text: str
    is_summary: bool = False


def _segments(row: Row) -> list[tuple[Optional[str], str, float]]:
    """把一行切成若干 (标签, 其余文本, 标签x0)。

    一行可能有**两个**标签（双栏「标签:值」表），必须按标签位置切段。
    无标签的行返回 ``[(None, 整行文本, x0)]``。
    """
    labs = [i for i, w in enumerate(row.words) if LABEL_RE.match(w.text)]
    if not labs:
        return [(None, row.text.strip(), row.x0)]
    out: list[tuple[Optional[str], str, float]] = []
    for k, i in enumerate(labs):
        end = labs[k + 1] if k + 1 < len(labs) else len(row.words)
        name = LABEL_RE.match(row.words[i].text).group(1)
        rest = "".join(w.text for w in row.words[i + 1:end]).strip()
        out.append((name, rest, row.words[i].x0))
    return out


def _is_section_header(text: str, x0: float, n_words: int,
                       template: Template) -> bool:
    """独立成行的段标题：一般项目 / 眼科 / 心电图检查 / 彩超 …

    必须落在**标签列**（x0 ≤ SECTION_X_MAX）。小结条目缩进在 x0 ≈ 83，
    不属于这一列，否则「胆囊息肉」这类短文本会被误认为段标题。
    """
    if n_words != 1 or x0 > template.narrative.section_x_max:
        return False
    t = text.strip()
    if not t or len(t) > 8 or re.search(r"[:：\d]", t):
        return False
    return True


def _extract_age(text: str) -> Optional[int]:
    """年龄取自内页表头。eGFR / FIB-4 / China-PAR 都要用。

    注意：本案例 2026 报告写 35 岁，而按内页日期（2024-06-12 → 2026-08-20）
    推算应为 34 岁 —— 该疑点是否成立取决于生日，见项目回顾 坑 14。
    """
    m = re.findall(r"年龄[:：]?\s*(\d+)\s*岁", text)
    return int(m[0]) if m else None


def _extract_sex(text: str) -> Optional[str]:
    m = re.search(r"性别[:：]?\s*([男女])", text)
    return m.group(1) if m else None


def classify_page(page: "fitz.Page",
                  template: Template) -> str:
    """页类型判定：cover | main_report | lab | other。

    * **cover** —— 封面信（"您好…感谢…"），整页没有体检数据，必须跳过；
      否则那封 1400 字的信会被当成一条标量读数。
    * **main_report** —— 主检报告页，含机构自报的异常项与建议，单独解析。
    """
    t = page.get_text()
    markers = template.main_report_markers or ["主要疾病诊断", "其他阳性发现"]
    # 封面信（「尊敬的客户…」）上也会出现「建议您」这类主检字样 —— 有强封面
    # 信号时封面优先；否则「您好+感谢」只在不含主检字样时才算封面（守住
    # 原契约：「您好 感谢 主要疾病诊断」是主检报告，不是封面）。
    if ("您好" in t and "感谢" in t
            and ("尊敬的客户" in t or not any(m in t for m in markers))):
        return "cover"
    if any(m in t for m in markers):
        return "main_report"
    if template.lab.page_marker in t:
        return "lab"
    return "other"


def parse_main_report_pages(doc: "fitz.Document", year: str,
                            template: Template) -> list[InstitutionFinding]:
    """解析全部主检报告页。

    **段标题必须跨页继承**：2026 的「主要疾病诊断」在第 5 页列到第 7 条，
    第 6 页继续 8/9/10 条而**不重复标题**。若每页独立解析，续页的条目
    会因 section 为空被整批丢掉。

    注意负向先行断言 ``(?!\\d)``——机构清单里会出现 ``0.94mmol/L(...)``，
    没有它 ``^(\\d+)[.、]`` 会把 "0" 当成序号（已记录的坑）。
    """
    out: list[InstitutionFinding] = []
    section = ""
    #: **当前条目也必须跨页继承**：2026 第 7 条「肝血管瘤」的说明与建议跨页
    #: （p5 末 "CT检查提示：肝内" + p6 首 "多发可见片状稍低密度。建议您肝胆外科随诊…"）。
    #: 若每页重置 cur，续页首行会因"没有当前条目"被静默丢弃——建议原文就被截断。
    cur: Optional[InstitutionFinding] = None
    for pno in range(doc.page_count):
        page = doc[pno]
        if classify_page(page, template) != "main_report":
            continue
        page_no = pno + 1

        pat = _pat(template)
        for row in rows_of_page(page, page_no):
            t = row.text.strip()
            if not t or NOISE_ROW_RE.search(t) or NOISE_INLINE_RE.match(t) \
                    or pat["nar_noise_row"].search(t) \
                    or pat["nar_noise_inline"].match(t):
                continue
            m_sec = re.match(r"^[一二三四五六七八九十]+[、.](.+)$", t)
            if m_sec:
                section, cur = m_sec.group(1).strip(), None
                continue
            m_item = re.match(r"^(\d{1,2})[.、](?!\d)\s*(.+)$", t)
            if m_item and section:
                cur = InstitutionFinding(year=year, page_no=page_no, section=section,
                                         no=int(m_item.group(1)),
                                         title=m_item.group(2).strip(), detail="")
                out.append(cur)
                continue
            if cur is not None:
                cur.detail += t
    return out


def _iter_entries(doc: "fitz.Document",
                  template: Template) -> Iterator[_Entry]:
    """逐页扫出条目。

    段标题（``section``）跨页继承——已踩：化验单/检查页在续页不重复段标题。
    但**当前条目 ``last`` 必须每页重置**，否则上一页的文本会一路累积下去
    （封面信 + 主检报告曾被粘成一条 1400 字的"标量读数"）。
    """
    section: Optional[str] = None

    for pno in range(doc.page_count):
        pg = doc[pno]
        # 化验页由 tables.py 处理；封面信与主检报告各有专门通道。
        if classify_page(pg, template) in ("cover", "main_report", "lab"):
            continue
        if any(m in pg.get_text()
               for m in template.narrative.page_exclude_markers):
            continue
        last: Optional[_Entry] = None
        in_summary = False

        for row in rows_of_page(pg, pno + 1):
            txt = row.text.strip()
            pat = _pat(template)
            if not txt or pat["nar_noise_row"].search(txt) \
                    or pat["nar_noise_inline"].match(txt):
                continue

            head = row.first.strip()
            no_colon_item = (len(row.words) >= 2
                             and row.x0 <= template.narrative.label_x_max
                             and 1 <= len(head) <= 10
                             and not re.search(r"[:：\d]", head)
                             and head not in set(template.narrative.noise_labels))

            for name, rest, x0 in _segments(row):
                if name is None:
                    if _is_section_header(txt, x0, len(row.words), template):
                        section = txt
                        last, in_summary = None, False
                    elif no_colon_item:
                        # 无冒号的项目标签：``肺部CT | 胸廓对称，纵隔气管居中…``
                        # 项目名后面直接跟描述，没有冒号。化验页已在页级跳过，
                        # 否则「血常规|审核医生…」会被误判成项目。
                        in_summary = False
                        last = _Entry(pno + 1, section, head, row.rest().strip())
                        yield last
                    elif in_summary:
                        # 小结块里的每一行是**一条独立小结**，不是上一行的续行。
                        # 若当成续行拼接，「甲状腺双侧叶结节（TI-RADS2类）」与
                        # 「颈部血管未见明显异常」会粘成一行，TI-RADS 就会
                        # 误挂到颈部血管上。
                        last = _Entry(pno + 1, section, "小结", txt, is_summary=True)
                        yield last
                    elif last is not None:
                        last.text += txt
                    continue

                if name in set(template.narrative.noise_labels):
                    continue

                if name == "小结":
                    in_summary = True
                    last = _Entry(pno + 1, section, "小结", rest, is_summary=True)
                    yield last
                    continue

                in_summary = False
                last = _Entry(pno + 1, section, name, rest)
                yield last
                continue


# ---------------------------------------------------------------- 结构化

def _split_measurements(text: str) -> list[Measurement]:
    """从描述文本里抽测量值，**按语义归侧**。

    走法是顺序遍历分句，维护「当前侧别」与「当前描述词」：

    * 2024/2025 形态：``右侧叶实质探及一个低回声结节`` → ``大小约0.3x0.2cm``
      （侧别与尺寸分处两个分句，靠状态传递）
    * 2026 形态：``右侧大小约0.5x0.3cm``（侧别与尺寸同句）
    """
    out: list[Measurement] = []
    cur_side: Optional[str] = None
    cur_desc: Optional[str] = None
    clauses = [c.strip() for c in CLAUSE_SPLIT_RE.split(text) if c.strip()]

    for idx, clause in enumerate(clauses):
        m_side = SIDE_RE.search(clause)
        if m_side:
            cur_side = SIDE_NORMALIZE.get(m_side.group(1), cur_side)
        m_desc = DESCRIPTOR_RE.search(clause)
        if m_desc:
            cur_desc = m_desc.group(1)

        m_size = SIZE_RE.search(clause)
        if m_size:
            dims = [float(g) for g in m_size.groups()[:3] if g]
            out.append(Measurement(
                raw=m_size.group(0).strip(), value=max(dims), unit=m_size.group(4),
                side=cur_side, descriptor=cur_desc, dims=dims))
            continue

        m_len = LEN_RE.search(clause)
        if m_len:
            v = float(m_len.group(1))
            out.append(Measurement(
                raw=m_len.group(0).strip(), value=v, unit=m_len.group(2),
                side=cur_side, descriptor=cur_desc, dims=[v]))
            continue

        # 整段就是一个裸数值（骨密度「0.8」）——只在唯一分句时兜底，
        # 避免把叙述里的其他数字误当测量值。
        if len(clauses) == 1 or (idx == len(clauses) - 1 and len(clause) <= 12):
            m_bare = BARE_NUM_RE.match(clause)
            if m_bare:
                out.append(Measurement(
                    raw=m_bare.group(0), value=float(m_bare.group(1)),
                    unit=m_bare.group(2) or "", side=cur_side,
                    descriptor=cur_desc, dims=[float(m_bare.group(1))]))

    return out


def _parse_scalar(value_text: str) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """解析标量值文本 → (数值, 单位, 定性结论)。

    ``"21.9,体重正常"`` → ``(21.9, None, "体重正常")``
    ``"181.5cm"``       → ``(181.5, "cm", None)``
    ``"正常"``           → ``(None, None, "正常")``
    """
    t = value_text.strip()
    if not t:
        return None, None, None
    m = SCALAR_VALUE_RE.match(t)
    if not m:
        return None, None, t
    value = float(m.group(2))
    unit = m.group(3) or None
    rest = t[m.end():].lstrip(",，、 ")
    return value, unit, rest or None


def _attribute_summaries(items: list[NarrativeItem], summary: list[str],
                         template: Template) -> None:
    """把小结行归回它描述的检查项目（就地修改）。

    两块兜底：

    * 器官关键词匹配（``ORGAN_KEYS``）
    * **单项目块**：该块只有一个项目时，按器官匹配不上的行也归给它。
      胸部 CT 的报告同时覆盖肺与肝（``1.左肺下叶陈旧性病变。2.肝S2、S3、S7段
      片状稍低密度影…``），而该块只有一个项目「肺部CT」——不兜底就会丢一半。
    """
    if not items or not summary:
        return
    unassigned: list[str] = []
    for line in summary:
        hit: set[str] = set()
        for key, item_name in template.narrative.organ_keys:
            if key in line and any(i.item == item_name for i in items):
                hit.add(item_name)
        if not hit:
            unassigned.append(line)
            continue
        for it in items:
            if it.item in hit and line not in it.summary_lines:
                it.summary_lines.append(line)
                it.summary_attributed = True
    if len(items) == 1:
        for line in unassigned:
            if line not in items[0].summary_lines:
                items[0].summary_lines.append(line)
            items[0].summary_attributed = True


def _attach_grades(items: list[NarrativeItem]) -> None:
    """从小结里抽 TI-RADS / BI-RADS / Lung-RADS 分级。"""
    for it in items:
        for line in it.summary_lines:
            m = GRADE_RE.search(line)
            if m:
                it.grade_system = m.group(1).upper()
                it.grade_value = re.sub(r"\s+", "", m.group(2))
                break


# ---------------------------------------------------------------- 主入口

def extract_report(pdf_path: str, year: str,
                   template: Template) -> tuple[
        DocumentMeta, list[NarrativeItem], list[RawObservation],
        list[InstitutionFinding]]:
    """抽一份报告。

    returns (报告元信息, 叙述项[], 标量读数[], 机构自报异常项[])
    """
    template = require_template(template, "narrative.extract_report")
    doc = fitz.open(pdf_path)
    try:
        all_text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
        # 日期一律取自内页，忽略文件名（已踩：20260609 vs 内页 2026-08-20，差 72 天）。
        # 各家机构的标签不一，常见「体检日期/检查日期/报告日期/采样日期」——
        # 只认「体检日期」会把用「检查日期」的机构整批漏掉（合成样例即此）。
        _labels = ("|".join(template.exam_date_labels)
                   or "体检日期|检查日期|报告日期|采样日期|送检日期")
        dates = re.findall(
            rf"(?:{_labels})[:：]\s*"
            r"(\d{4}-\d{2}-\d{2})", all_text)
        # 日期一律取自内页，忽略文件名（已踩：20260609 vs 内页 2026-08-20，差 72 天）
        exam_date = max(set(dates), key=dates.count) if dates else None

        m = re.search(r"([\u4e00-\u9fff]{2,24}?(?:医院|体检中心|保健中心|康复保健中心))",
                      all_text)
        org = m.group(1) if m else None
        # 模板命中时机构名以模板为准：内页除了页眉还有宣传语/抬头句，
        # 正则可能抓到「……均可在某某医院」这类**全称句**（真实案例已踩）。
        if template.org_name:
            org = template.org_name
        meta = DocumentMeta(
            doc_id=os.path.basename(pdf_path),
            org_id=org,
            exam_date=exam_date,
            page_count=doc.page_count,
            carrier="text" if any(len(doc[i].get_text().strip()) >= 40
                                  for i in range(doc.page_count)) else "scan",
            age=_extract_age(all_text),
            sex=_extract_sex(all_text),
            notes=[] if exam_date else ["未从内页取到体检日期"])

        # 一轮：把条目按 (段, 通道) 归组；小结挂到本段。
        narratives: list[NarrativeItem] = []
        observations: list[RawObservation] = []
        pending: list[NarrativeItem] = []
        summary_buf: list[str] = []
        cur_section: Optional[str] = None

        def flush() -> None:
            if summary_buf:
                _attribute_summaries(pending, list(summary_buf), template)
                _attach_grades(pending)
                summary_buf.clear()
            pending.clear()

        # 必须先把生成器耗完再处理：续行是在 yield 之后才追加到同一个
        # _Entry 上的。若边 yield 边处理，读到的会是只有首行的半截文本
        # （甲状腺的尺寸全在续行里，会整批丢失）。
        for e in list(_iter_entries(doc, template)):
            if e.section != cur_section:
                flush()
                cur_section = e.section
            if e.is_summary:
                if e.text:
                    summary_buf.append(e.text)
                continue

            modality = (canonical_modality(e.section, template)
                        or canonical_modality(e.name, template))
            if modality is not None:
                it = NarrativeItem(year=year, page_no=e.page_no, modality=modality,
                                   item=e.name, description_verbatim=e.text)
                it.measurements = _split_measurements(it.description_verbatim)
                if not it.measurements:
                    it.notes.append("未抽到测量值（可能本就是定性描述）")
                narratives.append(it)
                pending.append(it)
            else:
                v, u, q = _parse_scalar(e.text)
                observations.append(RawObservation(
                    year=year, page_no=e.page_no, section=e.section,
                    item_raw=e.name, value_text=e.text, value=v, unit=u,
                    qualifier=q))
        flush()

        # 检查方式证据（模板声明）：机构把检查方式放在项目清单/抬头里、
        # 结果行没有可解析项目名时（心电图只剩清单字样），按模板扫全文补一条；
        # 仅当该年没有任何同方式条目时才补，避免与真正的叙述项重复。
        for ev in template.narrative.modality_evidence:
            pat_s, mod = ev[0], ev[1]
            item_name = ev[2] if len(ev) > 2 else mod
            if any(it.modality == mod or it.item == mod for it in narratives):
                continue
            for pno2 in range(doc.page_count):
                m2 = re.search(pat_s, doc[pno2].get_text())
                if not m2:
                    continue
                it = NarrativeItem(
                    year=year, page_no=pno2 + 1, modality=mod, item=item_name,
                    description_verbatim=f"报告载明「{m2.group(0)}」")
                it.summary_lines = [m2.group(0)]
                it.notes.append("检查方式证据：来自报告文字（清单/抬头），不含测量值")
                narratives.append(it)
                break

        # 主检报告页单独解析：它是**机构自己的结论**（含建议原文），
        # 不是原始观测，混进上面两条通道会污染证据链。
        findings = parse_main_report_pages(doc, year, template)

        return meta, narratives, observations, findings
    finally:
        doc.close()


def extract_dept_summaries(pdf_path: str, year: str,
                           template: Template
                           ) -> list[DeptSummary]:
    """通道 C · 分科小结：眼科/内科/外科/耳鼻咽喉科/口腔科… 的「小结」块。

    这些科室不是影像/功能检查，不产出 ``NarrativeItem``；它们的小结行若只走
    ``_attribute_summaries``，会因该段 ``pending`` 为空被**整段丢弃**。
    已踩的坑：2025 年眼科小结块里的「散瞳查眼底」由此从全部产物里消失，
    下游报告因此漏掉一条机构建议。

    本函数**复用同一套行解析**（``_iter_entries``），不新增版面规则 ——
    保证与叙述层读到的是同一份原文。判定归属的规则只有一条：
    **该段若已有检查方式（彩超/CT/心电图…），小结归影像通道，这里不重复出**。
    """
    doc = fitz.open(pdf_path)
    try:
        out: list[DeptSummary] = []
        cur_section: Optional[str] = None
        has_items = False            #: 本段是否已有检查方式条目
        buf: list[str] = []
        buf_page: Optional[int] = None

        def flush() -> None:
            nonlocal buf, buf_page, has_items
            if buf and cur_section and not has_items:
                out.append(DeptSummary(year=year, page_no=buf_page or 0,
                                       section=cur_section, lines=list(buf)))
            buf, buf_page, has_items = [], None, False

        for e in list(_iter_entries(doc, template)):
            if e.section != cur_section:
                flush()
                cur_section = e.section
            if e.is_summary:
                if e.text:
                    if not buf:
                        buf_page = e.page_no
                    buf.append(e.text)
                continue
            if canonical_modality(e.section, template) is not None:
                has_items = True
        flush()
        return out
    finally:
        doc.close()
