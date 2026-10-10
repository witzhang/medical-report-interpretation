# -*- coding: utf-8 -*-
"""官方分带与临界判据（P1-A0）：从"机制层私有阈值"并入引擎。

**为什么并入**（消解 R-8 双轨）：这些阈值原先只存在于机制层
（`报告核对/bands.json`），报告层引用的是"工具自己"的判定，与引擎判定
形成两条轨道。并入后**报告层直接引用引擎产物**，两条轨道合成一条。

本模块只做三件事，不新增医学规则：

1. **官方分带**（`BAND_SETS`）—— 血压正常高值/重点关注段、血糖与 HbA1c 前期、
   血脂四项边缘升高、HDL 降低、尿酸高尿酸血症。逐条带 ``Provenance`` 与
   ``strength``（强/中/弱）＋ ``verified``（是否**实际核到逐字原文**）。
2. **临界判据参数**（`临界判据`）—— 「临界上升」三级判据里第③级的兜底阈值
   （贴线 <5% / 单调 <10%）；①②级的数据源就是上面的分带与 ``EGFR_SLOPE``。
3. **非分带常量**（`TG胰腺炎阈值` / `包年数` / `早发家族史` / `非HDL_C`）——
   报告引用时同样必须能指回引擎（数字追踪口径不变）。

**诚实纪律**：``verified=True`` 只给**本仓库能核到逐字原文**的条目
（`quote_verbatim` 非空）。第一阶段 `bands.json` 标的"强/中"来自更早轮次的
抓取，本轮未在本地库复核到的**一律降为 False + 写明原因**；测试锁死
"verified=True 必须有逐字原文、False 必须写明原因"。依据强度（``strength``）
与核实状态（``verified``）**是两个字段，不许互相顶替**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schema import Provenance

ACCESSED = "2026-10-04"


# ---------------------------------------------------------------- 出处

P_LIPID_T3 = Provenance(
    source_type="guideline",
    source_title="中国血脂管理指南（2023年）表3 血脂合适水平和异常分层标准",
    source_issuer="中国血脂管理指南修订联合专家委员会",
    source_citation="中华心血管病杂志. 2023;51(3):221-255.",
    doi="10.3760/cma.j.cn112148-20230119-00038",
    accessed_at=ACCESSED,
    locator="表3（适用于 ASCVD 低危人群）",
    quote_verbatim="",
    guideline_pub_date="2023",
    verified=False,
    note="阈值（TC 5.2/6.2、LDL-C 3.4/4.1、TG 1.7/2.3、HDL-C 男<1.0）第一阶段"
         "bands.json 记核实=强（报告规格 v4 已逐条引用）；本地指引库未存该 PDF，"
         "本轮**未复核到逐字原文** → 如实标 False，待补原文后升 true。"
         "⚠️ 适用范围：表3 只适用于 ASCVD **低危**人群，中高危阈值更严（报告必须写明）。",
)

P_LIPID_T7_NONHDL = Provenance(
    source_type="guideline",
    source_title="中国血脂管理指南（2023年）表7 降脂靶点的目标值",
    source_issuer="中国血脂管理指南修订联合专家委员会",
    source_citation="中华心血管病杂志. 2023;51(3):221-255.",
    doi="10.3760/cma.j.cn112148-20230119-00038",
    accessed_at=ACCESSED,
    locator="表7 注",
    quote_verbatim="非 HDL-C 目标水平 = LDL-C + 0.8 mmol/L",
    recommendation_class="Ⅰ/Ⅱa",
    evidence_level="A/B",
    guideline_pub_date="2023",
    verified=True,
    note="非 HDL-C 治疗目标基数 4.2 = 低危 LDL-C 目标 3.4 + 0.8，由本句原文推得"
         "（引擎 P_LIPID_TABLE7 同源）。",
)

P_HTN2024 = Provenance(
    source_type="guideline",
    source_title="中国高血压防治指南（2024年修订版）",
    source_issuer="中国高血压防治指南修订委员会",
    accessed_at=ACCESSED,
    locator="血压分类（正常高值 120–139/80–89；130–139/85–89 重点关注）",
    quote_verbatim="",
    guideline_pub_date="2024",
    verified=False,
    note="阈值第一阶段 bands.json 记核实=强（报告规格 v4 已逐条引用）；本地指引库"
         "**未收集该指南 PDF**，本轮未复核到逐字原文 → 如实标 False。",
)

P_DM = Provenance(
    source_type="guideline",
    source_title="中国2型糖尿病防治指南（2020/2024）· 中国成人糖尿病前期干预的专家共识（2023年版）",
    source_issuer="中华医学会糖尿病学分会",
    accessed_at=ACCESSED,
    locator="血糖正常/糖尿病前期/糖尿病 判定界值；HbA1c 前期判定",
    quote_verbatim="",
    guideline_pub_date="2020/2023/2024",
    verified=False,
    note="空腹血糖 6.1–6.9 为前期、HbA1c 5.7–6.4 为前期。**版本差异必须并列标注**："
         "2020 版未纳入 HbA1c 作前期诊断、2023 共识首提、2024 版纳入 ≥5.7%；"
         "ADA 口径为 5.6 起。本地未收集指南 PDF → 本轮未核到逐字原文 → 标 False。",
)

P_UA = Provenance(
    source_type="guideline",
    source_title="中国高尿酸血症与痛风诊疗指南",
    source_issuer="中华医学会内分泌学分会",
    accessed_at=ACCESSED,
    locator="高尿酸血症诊断（男性 >420 μmol/L）",
    quote_verbatim="",
    guideline_pub_date="2019",
    verified=False,
    note="第一阶段记核实=强；本地未存原文 → 标 False。"
         "⚠️「临界高值 360–420」只有科普来源，**不采用**（已查证）。",
)

P_EGFR_SLOPE = Provenance(
    source_type="formula",
    source_title="健康成人 eGFR 自然下降率（四队列）· KDIGO 2012 CKD 快速进展定义",
    source_issuer="Kidney International 2024 / BMJ Open 2024 系统综述 / KDIGO",
    accessed_at=ACCESSED,
    locator="自然下降 0.4–1.0 mL/min/1.73m²/年；KDIGO 快速进展 >5/年",
    quote_verbatim="",
    guideline_pub_date="2024/2012",
    verified=False,
    note="第一阶段记核实=强（报告规格 v4 已引用）；本轮未复核原文 → 标 False。",
)

P_TG_PANCREATITIS = Provenance(
    source_type="consensus",
    source_title="重度高甘油三酯血症与急性胰腺炎风险共识（阈值 5.6 mmol/L）",
    source_issuer="—",
    accessed_at=ACCESSED,
    locator="TG ≥5.6 mmol/L 才纳入胰腺炎风险讨论",
    quote_verbatim="",
    guideline_pub_date="",
    verified=False,
    note="第一阶段记核实=强；本地未存原文 → 标 False。报告中引用时必须带阈值背景"
         "（对账 G11 锁）。",
)

P_PACKYEAR_LUNG = Provenance(
    source_type="guideline",
    source_title="中国肺癌筛查与早诊早治指南（2021，北京）问题9 推荐意见",
    source_issuer="国家癌症中心 / 中华医学会等",
    accessed_at=ACCESSED,
    locator="问题9「如何判定肺癌高风险人群」推荐意见（A）",
    quote_verbatim=("（A）吸烟：吸烟包年数≥30包年，包括曾经吸烟包年数≥30包年，"
                    "但戒烟不足15年；……注1：吸烟包年数=每天吸烟的包数"
                    "（每包20支）×吸烟年数（强推荐，证据分级：中）"),
    guideline_pub_date="2021",
    verified=True,
    note="⚠️ **更正**（本轮逐字核对本地库）：中国指南的吸烟门槛是 **≥30 包年**，"
         "不是 20 —— 20 是 NCCN（美国）口径；指南里 ≥20 说的是**被动吸烟 ≥20 年**。"
         "第一阶段 bands.json 的「≥20 包年」归因有误，已按原文更正。"
         "吸烟年数未知时仍**不得断言筛查资格**（对账 G10 锁）。",
)

P_EARLY_FH = Provenance(
    source_type="consensus",
    source_title="脂蛋白(a)与心血管疾病风险关系及临床管理的专家科学建议 · 中国血脂管理指南（2023年）危险因素定义",
    source_issuer="—",
    accessed_at=ACCESSED,
    locator="早发心血管病家族史：一级亲属发病年龄 男<55 岁 / 女<65 岁",
    quote_verbatim="",
    guideline_pub_date="2023",
    verified=False,
    note="第一阶段记核实=强（本地指引库已落原文的说法在 bands.json 中）；"
         "本轮未复核到逐字原文 → 标 False。",
)

P_BONE_T = Provenance(
    source_type="standard",
    source_title="WHO 骨密度 T 值判读口径",
    source_issuer="WHO",
    accessed_at=ACCESSED,
    locator="T ≥ −1.0 正常；−2.5 < T < −1.0 骨量减少；T ≤ −2.5 骨质疏松",
    quote_verbatim="",
    verified=False,
    note="WHO 口径为通行做法；本地未存原文 → 标 False（报告以机构自带结论为准）。",
)

#: 2025 年卫健委《常见慢性病风险评估指标》——本轮**实际读到**的原文（本地 docx）
P_CDR2025 = Provenance(
    source_type="regulation",
    source_title="5.常见慢性病风险评估指标（2025年版）",
    source_issuer="国家卫生健康委（成人健康体检项目推荐指引配套文件）",
    accessed_at=ACCESSED,
    locator="一、心血管病风险人群（一）；二、糖尿病风险人群（三）（十）",
    quote_verbatim=(
        "对于没有动脉粥样硬化性心血管疾病（ASCVD）的人群，符合如下3个条件之一者，"
        "直接列为高风险人群：1.低密度脂蛋白胆固醇≥4.9mmol/L或总胆固醇≥7.2mmol/L；"
        "2.年龄≥40岁的糖尿病患者；3.慢性肾脏病3-4期。"),
    guideline_pub_date="2025",
    verified=True,
    note="同日文档交叉验证：糖尿病高风险条件含「高密度脂蛋白胆固醇<0.90mmol/L"
         "和（或）甘油三酯>2.22mmol/L」「体重指数≥24kg/m²和（或）中心型肥胖"
         "（男性腰围≥90cm，女性腰围≥85cm）」。",
)


# ---------------------------------------------------------------- 结构

@dataclass
class Band:
    """分带的一段。区间语义：**左闭右开** ``[low, high)``；``None`` 表示无界。"""

    label: str
    low: Optional[float] = None
    high: Optional[float] = None

    def contains(self, value: float) -> bool:
        if self.low is not None and value < self.low:
            return False
        if self.high is not None and value >= self.high:
            return False
        return True


@dataclass
class BandSet:
    """一个指标的一套官方分带。"""

    name: str
    analyte_id: str
    unit: str
    bands: list[Band]
    strength: str = ""            #: 强 | 中 | 弱（依据强度）
    provenance: list[Provenance] = field(default_factory=list)
    sex: str = ""                 #: 限定性别（空 = 通用）
    note: str = ""

    def of(self, value: Optional[float], sex: Optional[str] = None) -> Optional[Band]:
        """落在哪一段。``sex`` 与 ``self.sex`` 不符（且双方都非空）→ 不适用。"""
        if value is None:
            return None
        if self.sex and sex and self.sex != sex:
            return None
        for b in self.bands:
            if b.contains(value):
                return b
        return None

    def hit(self, value: Optional[float], sex: Optional[str] = None) -> Optional[dict]:
        """判定结果（带依据与核实状态；报告引用时**照抄**这些字段，不许只抄标签）。"""
        b = self.of(value, sex)
        if b is None:
            return None
        return {"段": b.label, "强度": self.strength,
                "核实": all(p.verified for p in self.provenance),
                "依据": "；".join(dict.fromkeys(
                    p.source_title for p in self.provenance)) ,
                "适用": (f"仅{self.sex}" if self.sex else ""),
                "备注": self.note}


# ---------------------------------------------------------------- 分带表

BAND_SETS: list[BandSet] = [
    BandSet("收缩压", "VITAL.收缩压", "mmHg",
            [Band("正常", None, 120), Band("正常高值", 120, 130),
             Band("重点关注段", 130, 140), Band("高血压", 140, None)],
            strength="强", provenance=[P_HTN2024],
            note="130–139 即指南的「重点关注」段；≥140/90 为高血压"),
    BandSet("舒张压", "VITAL.舒张压", "mmHg",
            [Band("正常", None, 80), Band("正常高值", 80, 85),
             Band("重点关注段", 85, 90), Band("高血压", 90, None)],
            strength="强", provenance=[P_HTN2024],
            note="85–89 为「重点关注」段；≥90 为高血压"),
    BandSet("空腹血糖", "CHEM.葡萄糖", "mmol/L",
            [Band("正常", None, 6.1), Band("糖尿病前期", 6.1, 7.0),
             Band("糖尿病", 7.0, None)],
            strength="中", provenance=[P_DM],
            note="版本差异：ADA 及疑似 2024 版从 5.6 起算前期 → 报告并列标注"),
    BandSet("糖化血红蛋白", "CHEM.糖化血红蛋白", "%",
            [Band("正常", None, 5.7), Band("糖尿病前期", 5.7, 6.5),
             Band("糖尿病", 6.5, None)],
            strength="中", provenance=[P_DM],
            note="版本差异：2020 版未纳入 HbA1c 作前期诊断、2023 共识首提、2024 版纳入；报告须写明「取决于采用哪一版」"),
    BandSet("总胆固醇", "CHEM.总胆固醇", "mmol/L",
            [Band("合适", None, 5.2), Band("边缘升高", 5.2, 6.2),
             Band("升高", 6.2, None)],
            strength="强", provenance=[P_LIPID_T3, P_CDR2025],
            note="只适用于 ASCVD 低危人群（中高危阈值更严）；TC≥7.2 直接高危"),
    BandSet("坏胆固醇", "CHEM.低密度脂蛋白胆固醇", "mmol/L",
            [Band("合适", None, 3.4), Band("边缘升高", 3.4, 4.1),
             Band("升高", 4.1, None)],
            strength="强", provenance=[P_LIPID_T3, P_CDR2025],
            note="只适用于 ASCVD 低危人群；LDL-C≥4.9 直接高危"),
    BandSet("甘油三酯", "CHEM.甘油三酯", "mmol/L",
            [Band("合适", None, 1.7), Band("边缘升高", 1.7, 2.3),
             Band("升高", 2.3, None)],
            strength="强", provenance=[P_LIPID_T3],
            note="只适用于 ASCVD 低危人群；≥5.6 才纳入胰腺炎风险讨论"),
    BandSet("好胆固醇", "CHEM.高密度脂蛋白胆固醇", "mmol/L",
            [Band("降低", None, 1.0), Band("正常", 1.0, None)],
            strength="强", provenance=[P_LIPID_T3], sex="男",
            note="降低界值 <1.0（男）；女性界值见指南"),
    BandSet("体重指数", "VITAL.体重指数", "kg/m²",
            [Band("偏瘦", None, 18.5), Band("正常", 18.5, 24),
             Band("超重", 24, 28), Band("肥胖", 28, None)],
            strength="强", provenance=[P_CDR2025],
            note="中国成人标准：18.5–24 正常；24–28 超重；≥28 肥胖"
                 "（≥24 这条与 2025 年慢性病风险评估文件的糖尿病风险条件逐字一致；"
                 "完整分段出处 WS/T 428-2013 未存本地 → 该条 verified 依赖 P_CDR2025）"),
    BandSet("骨密度T值", "影像.骨密度.T值", "T 值",
            [Band("骨质疏松", None, -2.5), Band("骨量减少", -2.5, -1.0),
             Band("正常", -1.0, None)],
            strength="强", provenance=[P_BONE_T],
            note="WHO 口径：T ≤ −2.5 骨质疏松；−2.5 < T < −1.0 骨量减少；T ≥ −1.0 正常"),
    BandSet("尿酸", "CHEM.尿酸", "umol/L",
            [Band("正常", None, 420), Band("高尿酸血症", 420, None)],
            strength="强", provenance=[P_UA],
            note="男性 >420 μmol/L 为高尿酸血症；≥540 考虑药物治疗"),
]


def band_set(name: str) -> Optional[BandSet]:
    """按报告用名取分带（"坏胆固醇"是报告层用词，对应 LDL-C）。"""
    return next((b for b in BAND_SETS if b.name == name), None)


def band_of(name: str, value: Optional[float],
            sex: Optional[str] = None) -> Optional[dict]:
    """判定入口：值 → {段/强度/核实/依据/适用/备注}；表外项目返回 None。

    报告层/机制层**一律走这里**（P1-A0：不再读本地 bands.json）。
    """
    bs = band_set(name)
    return bs.hit(value, sex) if bs else None


def band_label(name: str, value: Optional[float],
               sex: Optional[str] = None) -> Optional[str]:
    """只要标签（供 states.json 的「分带」字段用）。"""
    hit = band_of(name, value, sex)
    return hit["段"] if hit else None


# ---------------------------------------------------------------- 判据与常量

#: 「临界上升」第③级兜底阈值（弱依据，报告引用必须标注"无权威依据"）
临界判据 = {
    "贴线余量": 5,        #: 距上限 <5%（不论方向）→ 入选
    "单调余量": 10,       #: 方向一致且距上限 <10% → 入选
    "依据": "经验性兜底（报告规格收紧轮：20% → 10%，宁可漏报不可误报）",
    "强度": "弱",
    "顺序": "先判噪声轴、再判余量轴（否则会把「位置正常但真的在变」误标成临界）",
}

#: eGFR 斜率（「临界上升」第②级）
EGFR_SLOPE = {
    "自然下降": (0.4, 1.0),     #: mL/min/1.73m²/年（健康成人）
    "快速进展": 5.0,            #: KDIGO「快速进展」>5/年（判紧急度）
    "合并肥胖": 0.85, "合并糖尿病": 1.2,
    "强度": "强",
    "provenance": P_EGFR_SLOPE,
}

#: 甘油三酯相关胰腺炎阈值（报告必须给阈值背景，对账 G11 锁）
TG胰腺炎阈值 = {"值": 5.6, "单位": "mmol/L", "provenance": P_TG_PANCREATITIS}

#: 非 HDL-C：4.2 = 低危 LDL 目标 3.4 + 0.8（表7 原文推得）；4.9 = 分档界线
非HDL_C = {"治疗目标基数": 4.2, "分档界线": 4.9, "偏移": 0.8,
           "provenance": [P_LIPID_T7_NONHDL, P_LIPID_T3],
           "说明": "4.2 是「治疗目标」，4.9 是「分档界线」，两者不是一回事"}

#: 吸烟包年数（肺癌筛查资格）——**中国指南门槛 30 包年**（本轮逐字更正）
包年数 = {"高危门槛": 30, "单位": "包年",
          "定义": "每天吸烟的包数（每包20支）× 吸烟年数",
          "provenance": P_PACKYEAR_LUNG,
          "备注": "吸烟年数未知时不得断言筛查资格（对账 G10）"}

#: 家庭血压（家庭自测 7 天平均）——用于**解除**「单次诊室血压偏高」的疑点。
#: 家庭血压标准比诊室更严：≥135/85 就要就诊；<130/80 视为未达高血压。
家庭血压 = {"就诊线": (135, 85), "目标": (130, 80), "provenance": P_HTN2024,
            "强度": "强",
            "备注": "上臂式电子血压计、坐位静息 5 分钟后、早晚各 1 次连测 7 天；"
                    "7 天平均 ≥135/85 → 就诊；<130/80 → 按未达高血压处理（2025 那次按偶发读数）"}

#: 尿酸：考虑药物治疗线（是否用药由医师决定）
尿酸治疗阈值 = {"考虑药物治疗": 540, "单位": "umol/L", "provenance": P_UA,
                "备注": "≥540 才考虑药物治疗；用药由风湿免疫科/内分泌科医师决定"}

#: 早发心血管病家族史定义
早发家族史 = {"男性": 55, "女性": 65, "provenance": P_EARLY_FH,
              "备注": "一级亲属发病年龄小于此值才算「早发」"}

#: 跨年**记录口径变化**的指标：Delta 数值不可直接比较（标注原因，不静默）。
#: 用途：validate.check_delta 命中这些指标时在条目里写明"记录口径不同 → 不可比"，
#: 供报告按『不可比』声明（禁止据此下"视力下降"类结论）。
跨年记录口径 = {
    "EYE.右眼矫正视力": "五分记录法（如 4.6）与小数记录法（如 0.8）跨年混用",
    "EYE.左眼矫正视力": "同上",
}


def engine_numbers() -> set:
    """**本模块全部数值常量**（对账与规则校验的单一来源）。

    与 ``报告核对/constants.json`` 的交叉核对方向：引擎有 → 登记簿不许缺
    （改了引擎阈值却忘了登记，对账会报出来）。
    """
    out = set()
    for bs in BAND_SETS:
        for b in bs.bands:
            for v in (b.low, b.high):
                if v is not None:
                    out.add(float(v))
    for pair in (家庭血压["就诊线"], 家庭血压["目标"]):
        out.update(float(v) for v in pair)
    out.add(float(尿酸治疗阈值["考虑药物治疗"]))
    out.add(float(TG胰腺炎阈值["值"]))
    out.add(float(包年数["高危门槛"]))
    out.update({float(早发家族史["男性"]), float(早发家族史["女性"])})
    out.update({float(非HDL_C["治疗目标基数"]), float(非HDL_C["分档界线"]),
                float(非HDL_C["偏移"])})
    out.update(float(v) for v in EGFR_SLOPE["自然下降"])
    out.update({float(EGFR_SLOPE["快速进展"]), float(EGFR_SLOPE["合并肥胖"]),
                float(EGFR_SLOPE["合并糖尿病"])})
    return out


def verify_consistency() -> list[str]:
    """自检分带表：区间无重叠、无空隙（每个 BandSet 的分段必须覆盖全实数轴）。"""
    bad: list[str] = []
    for bs in BAND_SETS:
        cur: Optional[float] = None
        for b in bs.bands:
            if b.low != cur:
                bad.append(f"{bs.name}：分段不连续（{cur} → {b.low}）")
            cur = b.high
        if cur is not None:
            bad.append(f"{bs.name}：最后一段没有上界（{cur}）")
    for bs in BAND_SETS:
        for p in bs.provenance:
            if p.verified and not p.quote_verbatim:
                bad.append(f"{bs.name}：verified=True 但没有逐字原文（不许伪装）")
            if not p.verified and not p.note:
                bad.append(f"{bs.name}：verified=False 但没写原因")
    return bad
