# -*- coding: utf-8 -*-
"""数据对象定义（对应框架文档 §二）。

本模块只定义结构，不含任何抽取或判定逻辑。原则：

1. **原始值永不改写**——``description_verbatim`` 与 ``Measurement.raw``
   保留报告原文，规范化值另开字段（借鉴 Vital 的 ``canonical_value`` 双列设计）。
2. **缺失不是阴性**——判定结果用五值枚举，不用布尔（框架 §三 G6）。
3. **证据可回溯**——每个对象都带 ``page_no``，能点回原页。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Finding(str, Enum):
    """位置轴的判定结果——**五值枚举，禁用布尔**（框架 §三 G6）。

    依据：USPSTF 的 I Statement 表明「证据不足/无法判定」是一个
    独立的、正当的档位，而不是"阴性"。
    """

    NORMAL = "normal"                  # 有值且在区间内
    ABNORMAL = "abnormal"              # 有值且超区间
    UNDETERMINED = "undetermined"      # 无值 / 值不可解析 → 未检测不等于正常
    NOT_ASSESSABLE = "not_assessable"  # 有值但缺前置条件
    NOT_APPLICABLE = "not_applicable"  # 人群不适用（性别/年龄不符）


class Change(str, Enum):
    """变化轴的判定结果——与位置轴**必须分开**（框架 §一 步骤 5）。"""

    BEYOND_NOISE = "beyond_noise"              # 超出 RCV
    WITHIN_NOISE = "within_noise"              # 无法与随机波动区分
    INSUFFICIENT_POINTS = "insufficient_points"  # 点数不足，不判


@dataclass
class Measurement:
    """一个测量值。

    ``side`` 由**语义**决定，绝不由出现位置决定——这是踩过的坑：
    2025 年原文把左侧写在前面，按位置归侧会凭空造出「先跳升再回落」的假趋势。
    """

    raw: str                      #: 原文写法，如 "0.5x0.3cm"
    value: Optional[float] = None  #: 主维度数值（多维度时取最大维度）
    unit: str = "cm"
    side: Optional[str] = None     #: 右 / 左 / 双 / None
    descriptor: Optional[str] = None  #: 低回声 / 囊实性 / 高回声 …
    dims: list[float] = field(default_factory=list)  #: 各维度数值


@dataclass
class NarrativeItem:
    """一项检查的叙述（对应框架 §二 的 ``RawNarrative``）。

    例：甲状腺彩超「形态规则,大小正常,...」+ 小结「甲状腺双侧叶结节（TI-RADS 2类）」
    """

    year: str
    page_no: int
    modality: Optional[str]        #: 检查方式：彩超 / CT / 心电图 / 经颅多普勒 / 骨密度
    item: str                      #: 检查项目：甲状腺 / 肝脏 / 胆囊 …
    description_verbatim: str      #: **整段原文保留，不裁剪**（证据指针）
    measurements: list[Measurement] = field(default_factory=list)
    grade_system: Optional[str] = None   #: TI-RADS / BI-RADS / Lung-RADS
    grade_value: Optional[str] = None    #: 2类 / 3类 …
    summary_lines: list[str] = field(default_factory=list)
    summary_attributed: bool = False     #: 小结是否成功归到本项
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["measurements"] = [asdict(m) if not isinstance(m, dict) else m
                             for m in self.measurements]
        return d


@dataclass
class RawObservation:
    """一条结构化标量读数（对应框架 §二 的 ``RawObservation``）。

    来源是**通道 A · 结构化表格**：体格检查页（身高/体重/血压/腰围）、
    眼科页、以及将来的化验单。与 ``NarrativeItem`` 的区别是它只有
    「一个值」，没有成段的描述文字。

    为什么必须把它从叙述里分出来：旧 ``combo.py`` / ``ascvd.py`` 用的
    年龄/血压/TC/HDL/腰围是**手工维护的字典**（框架 D12b），要改成读上游
    就得先把这些值抽出来。现已由 ``compose.py`` 消费。
    """

    year: str
    page_no: int
    section: Optional[str]     #: 一般项目 / 眼科 / 血常规 …（双栏表格的段标题）
    item_raw: str              #: 项目名原文
    value_text: str            #: 值原文（含单位与结论），如 "21.9,体重正常"
    value: Optional[float] = None
    unit: Optional[str] = None
    qualifier: Optional[str] = None   #: 值后面的定性结论，如 "体重正常" / "正常" / "无"


@dataclass
class DeptSummary:
    """分科检查的「小结」块（通道 C）。

    眼科/内科/外科/耳鼻咽喉科/口腔科这类**非影像、非功能**科室不产出
    ``NarrativeItem``：它们既没有成段描述，也没有测量值。它们的小结行此前在
    ``_attribute_summaries`` 里因 ``pending`` 为空被**整段丢弃** —— 2025 年
    眼科「散瞳查眼底」就是这样从全部产物里消失的（报告因此漏掉一条机构建议）。

    ``lines`` 按原文顺序保留小结块的全部行：首行通常是「小结：X」的结论，
    其后可能是随附建议（如「散瞳查眼底」）。原文是最高权威，不在这里裁掉。
    """

    year: str
    page_no: int
    section: str                  #: 科室：眼科 / 内科 / 外科 / 耳鼻咽喉科 …
    lines: list[str] = field(default_factory=list)


@dataclass
class InstitutionFinding:
    """机构主检报告里的一条异常项。

    这是**机构自己的结论**（不是原始观测），含标题、说明与**建议原文**——
    输出层的"复查/就诊建议"主要就来自这里。

    对应旧管线里的 ``data/summary_all.csv``。
    """

    year: str
    page_no: int
    section: str               #: 主要疾病诊断 / 其他阳性发现
    no: int
    title: str
    detail: str                #: 说明 + 建议原文


@dataclass
class LabResult:
    """化验单里的一条结果（通道 A · 结构化表格的另一半）。

    ``occurrence`` 是**段内第几次出现**：尿液检查里有两项都叫「白细胞」
    （一个定性"阴性"，一个定量"3.5 个/ul"），只用 (段, 项目) 做键会撞掉一项。
    """

    year: str
    page_no: int
    section: str
    item_raw: str
    occurrence: int                 #: 段内同名第几次（从 1 起）
    result_raw: str
    value: Optional[float] = None
    value_type: str = "numeric"     #: numeric | qualitative
    unit_raw: str = ""
    ref_raw: str = ""
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_op: str = ""                #: "" | ">" | "<" | ">=" | "<="
    flag: Optional[Finding] = None  #: 自算（不信任报告里的箭头）
    pdf_flag: str = ""              #: 报告原始箭头，仅留作交叉比对
    unit_cell: str = ""             #: 「单位」列的文字（列独立时由模板 unit_cells 收进来）


@dataclass
class AnalyteDefinition:
    """规范指标定义（目录层，与观测实例分离）。

    借鉴 health-assistant 的 ``BiomarkerDefinition``，但**多带作用域字段**：
    ``org_id`` + ``section`` + ``specimen``。原项目没有这三个，而它们正是
    「同名撞键」的根因——尿液白细胞与血常规白细胞必须落成两个 analyte。
    """

    analyte_id: str
    name: str
    specimen: str = ""              #: blood | urine | stool | ""
    value_type: str = "numeric"     #: numeric | qualitative
    canonical_unit: str = ""
    loinc_code: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    org_id: Optional[str] = None    #: 机构原始写法的归属；空表示通用
    section: Optional[str] = None
    confirmed: bool = False         #: 人工确认过才可跨机构复用


@dataclass
class NormalizedObservation:
    """归一后的观测——L3/L4 的统一输入。

    这是流水线的**枢纽对象**：左边接四条抽取通道，右边供校验层与判定层消费。
    所有字段都只做搬运与结构化，不含医学判断（``flag`` 例外，它是纯算术结果）。

    ``evidence`` 保留原文片段，任何结论都要能点回来。
    """

    year: str
    page_no: int
    section: str
    item_raw: str
    occurrence: int
    analyte_id: str
    matched_by: str = ""            #: rule | seed | unmapped
    unit_status: str = "unknown"    #: ok | mismatch | unknown
    repaired_by: str = ""           #: 非空表示这条经过了显式修复规则
    source: str = "lab"             #: lab | scalar | narrative
    value: Optional[float] = None
    value_text: str = ""
    value_type: str = "numeric"     #: numeric | qualitative
    unit: str = ""
    ref_raw: str = ""
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_op: str = ""
    flag: Optional[Finding] = None  #: 位置轴（自算，不信任报告箭头）
    pdf_flag: str = ""              #: 报告原始箭头，留作交叉比对
    evidence: str = ""


@dataclass
class DocumentMeta:
    """报告级元信息（对应框架 §二 的 ``Document``）。

    ``exam_date`` **必须取自内页**，不取文件名——已踩：2026 那份文件名为
    ``20260609``，内页 16 处全部是 ``2026-08-20``，相差 72 天。
    """

    doc_id: str
    org_id: Optional[str]
    exam_date: Optional[str]
    page_count: int
    carrier: str = "text"          #: text | scan | mixed
    template_version: Optional[int] = None
    age: Optional[int] = None      #: 从内页表头取，用于 eGFR / FIB-4 / China-PAR
    sex: Optional[str] = None      #: 男 / 女 —— 参考区间与危险因素都分性别
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 规则与出处


@dataclass
class Provenance:
    """一条规则的出处（与 Rule 是 1:N，独立版本化）。

    ``quote_verbatim`` 存**逐字原文**而非摘要 —— 它既是指南更新时判断
    "我的规则是否受影响"的唯一可靠依据，也正是 FDA CDS 第 4 条
    「能独立复核依据」的工程落地。

    **取不到原文时留空并标 ``verified=False``，不编造。**
    """

    source_type: str               #: guideline | consensus | standard | formula | regulation
    source_title: str
    source_issuer: str = ""
    source_citation: str = ""
    doi: str = ""
    url: str = ""
    accessed_at: str = ""
    locator: str = ""              #: 条文级定位：章节号 + 表号/图号 + 页码
    quote_verbatim: str = ""
    recommendation_class: str = "" #: Ⅰ类 / Ⅱa类 …
    evidence_level: str = ""       #: A / B / C
    guideline_pub_date: str = ""
    verified: bool = False         #: 是否实际取到原文
    note: str = ""


@dataclass
class Rule:
    """一条判定规则。规则与代码分离，可单独测试、单独版本化。"""

    rule_id: str
    name: str
    version: str = "1.0"
    assertion_type: str = "informational"   #: informational | advisory | red_flag
    inputs: list[str] = field(default_factory=list)   #: 依赖的 analyte_id
    missing_policy: str = "downgrade"       #: block | downgrade | partial
    formula: str = ""                       #: 人可读的判定式
    population: str = ""                    #: 适用人群
    unit_requirements: str = ""
    provenance: list[Provenance] = field(default_factory=list)


@dataclass
class DerivedResult:
    """L5 组合层的一条结果。"""

    rule_id: str
    name: str
    year: str
    value: Optional[float] = None
    text: str = ""
    unit: str = ""
    band: str = ""                          #: 分层结论（低危/中危/满足/不满足…）
    inputs: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    status: str = "ok"                      #: ok | not_assessable | not_applicable
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 结论


class ConclusionClass(str, Enum):
    """结论分类（框架 D15）。"""

    CONFIRMED = "confirmed"        #: 已确认问题
    TO_VERIFY = "to_verify"        #: 待核查
    TO_FOLLOWUP = "to_followup"    #: 待随访
    DATA_QUALITY = "data_quality"  #: 数据质量疑点


@dataclass
class Conclusion:
    """一条结论。**必须带证据指针**，能点回年份/页码/原文。"""

    conclusion_id: str
    cls: str                       #: ConclusionClass 的值
    text: str
    upstream: str = ""             #: 共同上游标签（归并后的）
    evidence: list[str] = field(default_factory=list)
    uncertainty: str = "medium"    #: high | medium | low
    rule_refs: list[str] = field(default_factory=list)
    assertion_type: str = "informational"   #: informational | advisory | red_flag
    action_hint: str = ""          #: 建议动作（措辞层另行降级，这里只记事实）
