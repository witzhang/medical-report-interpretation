# -*- coding: utf-8 -*-
"""规则库：每条规则 + 可审计出处。

对应框架 D13。规则与代码分离——``compose.py`` 只负责"怎么算"，
这里负责"依据是什么、适用谁、缺项怎么办"。

**出处原则（框架 §二）**：``quote_verbatim`` 必须逐字原文，不能是转述。
取不到原文的留空并标 ``verified=False``，**不编造**——
本文件里凡 ``verified=False`` 的条目都只是拿到了文献题录，没有核到正文。
"""
from __future__ import annotations

from .schema import Provenance, Rule

ACCESSED = "2026-10-01"

# ---------------------------------------------------------------- 出处

P_LIPID2023 = Provenance(
    source_type="guideline",
    source_title="中国血脂管理指南（2023年）",
    source_issuer="中国血脂管理指南修订联合专家委员会",
    source_citation="中华心血管病杂志. 2023;51(3):221-255.",
    doi="10.3760/cma.j.cn112148-20230119-00038",
    url="https://csc.cma.org.cn/module/download/downfile.jsp?classid=0&filename=96b4e603cee74b7fb208b278aa92bd8b.pdf",
    accessed_at=ACCESSED,
    locator="图1 说明（第227页）",
    quote_verbatim=(
        "首先，按照是否患有ASCVD 划分为二级预防和一级预防两类情况。"
        "在已诊断ASCVD 的人群中，将发生过≥2 次严重ASCVD 事件或发生过1 次严重"
        "ASCVD 事件且合并≥2 个高危险因素者列为超高危人群，其他ASCVD 患者列为"
        "极高危人群。在尚无ASCVD 的人群中，符合如下3 个条件之一者，直接列为高危"
        "人群，不需要再进行ASCVD 10 年发病风险评估：（1）LDL⁃C≥4.9 mmol/L 或"
        "TC≥7.2 mmol/L ；（2）年龄≥40 岁的糖尿病患者；（3）CKD 3~4 期。"),
    guideline_pub_date="2023",
    verified=True,
    note="原文由调研实际抓取指南 PDF 第 227 页逐字核对。"
         "另：10 年发病平均风险 <5%、5%~9%、≥10% 分别定义为低危、中危、高危；"
         "中危且年龄<55 岁需评估余生风险",
)

P_LIPID_TABLE7 = Provenance(
    source_type="guideline",
    source_title="中国血脂管理指南（2023年）表7 降脂靶点的目标值",
    source_issuer="中国血脂管理指南修订联合专家委员会",
    source_citation="中华心血管病杂志. 2023;51(3):221-255.",
    doi="10.3760/cma.j.cn112148-20230119-00038",
    url="https://csc.cma.org.cn/module/download/downfile.jsp?classid=0&filename=96b4e603cee74b7fb208b278aa92bd8b.pdf",
    accessed_at=ACCESSED,
    locator="表7",
    quote_verbatim=("低危 <3.4（Ⅱa/B）；中、高危 <2.6（Ⅰ/A）；"
                    "极高危 <1.8 且较基线降低幅度>50%（Ⅰ/A）；"
                    "超高危 <1.4 且较基线降低幅度>50%（Ⅰ/A）。"
                    "注：非 HDL-C 目标水平 = LDL-C + 0.8 mmol/L"),
    recommendation_class="Ⅰ/Ⅱa",
    evidence_level="A/B",
    guideline_pub_date="2023",
    verified=True,
    note="原文由调研实际抓取指南 PDF 逐字核对",
)

P_CHINAPAR = Provenance(
    source_type="formula",
    source_title="China-PAR 10 年 ASCVD 风险预测模型（男性方程）",
    source_issuer="China-PAR Project",
    source_citation="Yang X, Li J, Hu D, et al. Circulation. 2016;134(19):1430-1440.",
    doi="10.1161/CIRCULATIONAHA.116.022367",
    url="https://pubmed.ncbi.nlm.nih.gov/27682885/",
    accessed_at=ACCESSED,
    locator="男性方程系数表",
    quote_verbatim="",
    verified=False,
    note="系数取自论文；卷期页与 DOI 经 Europe PMC API 核实，但**未逐字核到原文系数表**。"
         "年龄适用范围 35–74 岁，超出即为外推",
)

P_CKDEPI = Provenance(
    source_type="formula",
    source_title="CKD-EPI 2021 肌酐方程（无种族系数版）",
    source_issuer="NKF-ASN Task Force",
    source_citation="Inker LA, Eneanya ND, Coresh J, et al. N Engl J Med. 2021;385:1737-1749.",
    doi="10.1056/NEJMoa2102953",
    url="https://www.kidney.org/sites/default/files/example_it_ticket-implement_2021_ckd-epi_equation_to_calculate_egfr_from_creatinine_1.pdf",
    accessed_at=ACCESSED,
    locator="Calculation equation（IF 分支表）；WHI 文档 single-equation 段",
    quote_verbatim=(
        "NKF 官方实现指南：Female ≤0.70 → 142 × (Scr/0.7)^-0.241 × 0.9938^Age × 1.012；"
        "Female >0.70 → 142 × (Scr/0.7)^-1.200 × 0.9938^Age × 1.012；"
        "Male ≤0.90 → 142 × (Scr/0.9)^-0.302 × 0.9938^Age；"
        "Male >0.90 → 142 × (Scr/0.9)^-1.200 × 0.9938^Age。"
        "报告范围 5-150 mL/min/1.73m²，结果取整数，参考区间 >60。"
        "｜WHI 单式形式：eGFRcr = 142 × min(Scr/κ,1)^α × max(Scr/κ,1)^-1.200 "
        "× 0.9938^Age × 1.012 [if female]；κ = 0.7(female)/0.9(male)；"
        "α = -0.241(female)/-0.302(male)"),
    guideline_pub_date="2021",
    verified=True,
    note="**两份独立来源逐字核对一致**，且与我们的实现（min/max 单式）代数等价："
         "① NKF《Example IT ticket: implement 2021 CKD-EPI》官方实现指南 "
         "（PDF 存 调研/src/NKF-CKD-EPI-2021-implementation.pdf）；"
         "② WHI《Calculation of estimated glomerular filtration rate》（引 Inker Table S10）。"
         "NKF 规定的「报告范围 5-150」与「结果取整数」已于 2026-10-01 实现"
         "（``compose.egfr_reported``）；注意**取整会跨过 CKD 分期边界**并进而"
         "影响血脂分层的「CKD 3~4 期」条件，详见 ``test_reporting.py``",
)

P_FIB4_UNITS = Provenance(
    source_type="calculator",
    source_title="Hepatitis C Online（University of Washington）FIB-4 Calculator",
    source_issuer="University of Washington National Hepatitis C Training Center",
    source_citation="https://www.hepatitisc.uw.edu/page/clinical-calculators/fib-4",
    url="https://www.hepatitisc.uw.edu/page/clinical-calculators/fib-4",
    accessed_at=ACCESSED,
    locator="FIB-4 计算器输入项与切点说明",
    quote_verbatim=("FIB-4 = 年龄(年) × AST(U/L) / (血小板(10^9/L) × √ALT(U/L))；"
                    "切点：低值 1.45、高值 3.25"),
    verified=True,
    note="公式与**单位**已逐字核对。注意：1.45/3.25 是 HCV 队列的切点；"
         "NAFLD 路径常用 <1.30 排除进展期纤维化 —— **两套切点不可混用**")

P_FIB4 = Provenance(
    source_type="formula",
    source_title="FIB-4 肝纤维化指数",
    source_issuer="",
    source_citation="Sterling RK, Lissen E, Clumeck N, et al. Hepatology. 2006;43:1317-1325.",
    doi="10.1002/hep.21178",
    url="https://aasldpubs.onlinelibrary.wiley.com/doi/10.1002/hep.21178",
    accessed_at=ACCESSED,
    locator="",
    quote_verbatim="",
    verified=False,
    note="切点 <1.30 排除进展期纤维化；AASLD 2023 不建议用血液学指标追踪纤维化进展",
)

P_AIP = Provenance(
    source_type="formula",
    source_title="致动脉粥样硬化指数 AIP = log10(TG/HDL-C)",
    source_issuer="",
    source_citation="Dobiásová M, Frohlich J. Atherosclerosis. 2000;151(1):313.",
    doi="10.1016/S0021-9150(00)80016-4",
    url="",
    accessed_at=ACCESSED,
    locator="",
    quote_verbatim="",
    verified=False,
    note="**无公认切点**，只看方向——不得据此下风险结论",
)

#: 三条代谢综合征标准的逐字原文**未取得**（期刊正文拒抓取，本地无 PDF）。
#: 分性别阈值（女性腰围 / HDL）按公认值实现，待有原文后逐条核对。
#: 之所以仍要实现：原先对女性**静默套用男性阈值**——那是确定的错，
#: 比用一个待核对的正确值更糟。
_METS_UNVERIFIED = (
    "⚠ 逐字原文未取得，分性别阈值待核；原先对女性静默套用男性阈值，属确定的错")

P_CDS2004 = Provenance(
    source_type="consensus",
    source_title="代谢综合征诊断标准（CDS 2004）",
    source_issuer="中华医学会糖尿病学分会",
    source_citation="中华糖尿病杂志. 2004;12(3):156-161.",
    doi="",
    url="",
    accessed_at=ACCESSED,
    locator="",
    quote_verbatim="",
    verified=False,
    note="4 项中满足 3 项；**无腰围条款**",
)

P_GD2007 = Provenance(
    source_type="guideline",
    source_title="中国成人血脂异常防治指南（2007年）代谢综合征诊断标准",
    source_issuer="中国成人血脂异常防治指南制订联合委员会",
    source_citation="中华心血管病杂志. 2007;35(5):390-419.",
    doi="",
    url="",
    accessed_at=ACCESSED,
    locator="",
    quote_verbatim="",
    verified=False,
    note="5 项中满足 3 项",
)

P_IDF2005 = Provenance(
    source_type="consensus",
    source_title="IDF 代谢综合征定义（2005，华人腰围切点）",
    source_issuer="International Diabetes Federation",
    source_citation="Alberti KG, Zimmet P, Shaw J. Lancet. 2005;366(9491):1059-1062.",
    doi="10.1016/S0140-6736(05)67402-8",
    url="",
    accessed_at=ACCESSED,
    locator="",
    quote_verbatim="",
    verified=False,
    note="腰围男≥90cm 为**必备条件**，另加 2 项",
)

P_KDIGO2012 = Provenance(
    source_type="guideline",
    source_title="KDIGO 2012 Clinical Practice Guideline for the Evaluation and "
                 "Management of Chronic Kidney Disease（CKD 分期 G/A）",
    source_issuer="Kidney Disease: Improving Global Outcomes (KDIGO) CKD Work Group",
    source_citation="Kidney Int Suppl. 2013;3(1):1-150.",
    url="https://kdigo.org/wp-content/uploads/2017/02/KDIGO_2012_CKD_GL.pdf",
    accessed_at=ACCESSED,
    locator="Table 2 GFR categories（p.19 区）；Table 6 Albuminuria categories in CKD（p.28 区）",
    quote_verbatim=(
        "GFR categories (ml/min/1.73 m2) — G1 Normal or high ≥90；"
        "G2 Mildly decreased 60-89；G3a Mildly to moderately decreased 45-59；"
        "G3b Moderately to severely decreased 30-44；"
        "G4 Severely decreased 15-29；G5 Kidney failure <15。"
        "Table 6 Albuminuria categories in CKD (mg/g) — "
        "A1 <30 Normal to mildly increased；"
        "A2 30-300 Moderately increased；"
        "A3 >300 Severely increased"),
    guideline_pub_date="2012",
    verified=True,
    note="原文 PDF（163 页）已下载至 调研/src/KDIGO-2012-CKD-GL.pdf 并逐字核对。"
         "⚠ 抽取时字体编码把「<」渲染成「o」、「>」渲染成「4」"
         "（原文作 o30 / 4300 / 430），上引为按上下文还原后的读法。"
         "**本引擎 A 分期缺尿白蛋白/ACR，一律判不可评估**，不假定为 A1")

P_GUIDE2025 = Provenance(
    source_type="regulation",
    source_title="成人健康体检项目推荐指引（2025年版）附件5《常见慢性病风险评估指标》",
    source_issuer="国家卫生健康委办公厅",
    source_citation="国卫办医政函〔2025〕412号",
    doi="",
    url="http://www.chtv.com.cn/index.php/index/view/id/7176",
    accessed_at=ACCESSED,
    locator="附件5",
    quote_verbatim="符合如下3个条件之一者，直接列为高风险人群：1.低密度脂蛋白胆固醇≥4.9mmol/L"
                   "或总胆固醇≥7.2mmol/L；2.年龄≥40岁的糖尿病患者；3.慢性肾脏病3-4期。",
    guideline_pub_date="2025-10-28",
    verified=True,
    note="用「高风险人群」而非「高危患者」，本工具照抄该措辞",
)


# ---------------------------------------------------------------- 规则

# 三条 METSYN 规则的出处统一标注"未核实 + 分性别阈值待核"
for _p in (P_CDS2004, P_GD2007, P_IDF2005):
    _p.verified = False
    if _METS_UNVERIFIED not in _p.note:
        _p.note = (_p.note + "；" if _p.note else "") + _METS_UNVERIFIED


RULES: list[Rule] = [
    Rule(rule_id="COMPOSITE.NON_HDL_C", name="non-HDL-C", version="1.0",
         assertion_type="informational",
         inputs=["CHEM.总胆固醇", "CHEM.高密度脂蛋白胆固醇"],
         missing_policy="downgrade",
         formula="non-HDL-C = TC − HDL-C",
         population="一般人群",
         unit_requirements="两项同为 mmol/L",
         provenance=[P_LIPID2023]),

    Rule(rule_id="COMPOSITE.AIP", name="致动脉粥样硬化指数 AIP", version="1.0",
         assertion_type="informational",
         inputs=["CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇"],
         missing_policy="downgrade",
         formula="AIP = log10(TG / HDL-C)",
         population="一般人群",
         unit_requirements="两项同为 mmol/L",
         provenance=[P_AIP]),

    Rule(rule_id="RENAL.EGFR_CKDEPI2021", name="eGFR (CKD-EPI 2021)", version="1.0",
         assertion_type="informational",
         inputs=["CHEM.肌酐"],
         missing_policy="downgrade",
         formula="eGFR = 142 × min(Scr/κ,1)^α × max(Scr/κ,1)^-1.200 × 0.9938^age × [1.012 若女]",
         population="成人；需年龄",
         unit_requirements="Scr 单位 μmol/L，计算前 ÷88.4 转 mg/dL",
         provenance=[P_CKDEPI]),

    Rule(rule_id="HEPATIC.FIB4", name="FIB-4", version="1.0",
         assertion_type="informational",
         inputs=["CHEM.天门冬氨基转移酶", "BLD.血小板", "CHEM.丙氨酸氨基转移酶"],
         missing_policy="downgrade",
         formula="FIB-4 = 年龄 × AST / (血小板 × √ALT)",
         population="成人；需年龄",
         unit_requirements="AST/ALT: U/L；血小板: 10^9/L",
         provenance=[P_FIB4_UNITS, P_FIB4]),

    Rule(rule_id="RENAL.CKD_STAGE", name="CKD 分期（KDIGO 2012 G/A）", version="1.0",
         assertion_type="informational",
         inputs=["CHEM.肌酐", "URI.微量白蛋白", "VITAL.年龄"],
         missing_policy="downgrade",
         formula=("G：eGFR ≥90=G1 / 60-89=G2 / 45-59=G3a / 30-44=G3b / 15-29=G4 / "
                  "<15=G5。A：ACR <30=A1 / 30-300=A2 / >300=A3"),
         population="成人；需年龄与性别",
         unit_requirements="eGFR mL/min/1.73m²；ACR mg/g",
         provenance=[P_KDIGO2012]),

    Rule(rule_id="METSYN.CDS2004", name="代谢综合征（CDS 2004）", version="1.0",
         assertion_type="informational",
         inputs=["VITAL.体重指数", "CHEM.葡萄糖", "VITAL.收缩压", "VITAL.舒张压",
                 "CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇"],
         missing_policy="block",
         formula=("4 项中满足 ≥3 项：BMI≥25 / FPG≥6.1（或已确诊糖尿病）/ "
                  "BP≥140/90（或已确诊高血压）/ TG≥1.7 或 HDL 男<0.9、女<1.0"),
         population="中国成人",
         unit_requirements="BMI kg/m²；FPG/TG/HDL mmol/L；BP mmHg",
         provenance=[P_CDS2004]),

    Rule(rule_id="METSYN.GD2007", name="代谢综合征（2007 血脂指南版）", version="1.0",
         assertion_type="informational",
         inputs=["VITAL.腰围", "CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇",
                 "VITAL.收缩压", "VITAL.舒张压", "CHEM.葡萄糖"],
         missing_policy="block",
         formula=("5 项中满足 ≥3 项：腰围 男>90、女>85cm / TG≥1.7 / "
                  "HDL 男<1.03、女<1.29 / BP≥130/85（或已确诊高血压）/ "
                  "FPG≥5.6（或已确诊糖尿病）"),
         population="中国成人",
         unit_requirements="腰围 cm；TG/HDL/FPG mmol/L；BP mmHg",
         provenance=[P_GD2007]),

    Rule(rule_id="METSYN.IDF2005", name="代谢综合征（IDF 2005 华人标准）", version="1.0",
         assertion_type="informational",
         inputs=["VITAL.腰围", "CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇",
                 "VITAL.收缩压", "VITAL.舒张压", "CHEM.葡萄糖"],
         missing_policy="block",
         formula=("腰围必备（男≥90、女≥80cm），另加 ≥2 项：TG≥1.7 / "
                  "HDL 男<1.03、女<1.29 / BP≥130/85（或已治疗）/ "
                  "FPG≥5.6（或已确诊糖尿病）"),
         population="华人（IDF 2005 华人腰围切点）",
         unit_requirements="腰围 cm；TG/HDL/FPG mmol/L；BP mmHg",
         provenance=[P_IDF2005]),

    Rule(rule_id="ASCVD.CHINAPAR", name="China-PAR 10 年 ASCVD 风险", version="1.0",
         assertion_type="advisory",
         inputs=["CHEM.总胆固醇", "CHEM.高密度脂蛋白胆固醇", "VITAL.收缩压", "VITAL.腰围"],
         missing_policy="block",
         formula="China-PAR 男性方程；需年龄、性别、吸烟、地区、城乡、家族史",
         population="中国人群，年龄 35–74 岁（范围外为外推）",
         unit_requirements="TC/HDL: mmol/L；SBP: mmHg；腰围: cm",
         provenance=[P_CHINAPAR, P_GUIDE2025]),

    Rule(rule_id="LIPID.RISK_STRATIFICATION", name="血脂指南2023 危险分层（一级预防）",
         version="1.0", assertion_type="advisory",
         inputs=["CHEM.低密度脂蛋白胆固醇"],
         missing_policy="block",
         formula=(
             "二级预防：≥2 次严重事件或 1 次事件+≥2 高危因素 → 超高危；"
             "其他 ASCVD → 极高危。"
             "一级预防：满足①LDL-C≥4.9 或 TC≥7.2 ②年龄≥40 糖尿病 ③CKD 3-4 期 "
             "之一者直接高危（不做 10 年评估）；其余按 10 年风险 <5%/5-9%/≥10% "
             "映射 低/中/高危"),
         population="成人；需 ASCVD 病史、糖尿病、CKD、血压、血脂",
         unit_requirements="LDL-C/TC: mmol/L；eGFR: mL/min/1.73m²",
         provenance=[P_LIPID2023]),

    Rule(rule_id="LIPID.LDL_TARGET", name="LDL-C 达标判断", version="1.0",
         assertion_type="advisory",
         inputs=["CHEM.低密度脂蛋白胆固醇"],
         missing_policy="block",
         formula=("低危 <3.4；中、高危 <2.6；极高危 <1.8 且较基线降幅>50%；"
                  "超高危 <1.4 且较基线降幅>50%。非 HDL-C 目标 = LDL-C + 0.8"),
         population="按分层结果取对应目标",
         unit_requirements="mmol/L",
         provenance=[P_LIPID_TABLE7, P_LIPID2023]),
]

RULES_BY_ID = {r.rule_id: r for r in RULES}
