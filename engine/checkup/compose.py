# -*- coding: utf-8 -*-
"""L5 · 组合与分层层：复合指标 / 综合征（多版本）/ 风险分层。

对应框架 D12b + D14。**全部从 L4 的判定结果读数**——不再有 ``combo.py`` /
``ascvd.py`` 那种手工维护的数值字典。人的属性（吸烟、家族史、用药）来自
``profile.json``，因为报告里根本没有这些字段。

两条硬规则：

* **多版本并存**：代谢综合征在 CDS 2004 / 2007 血脂指南版 / IDF 2005 下
  结论可能不同，三个版本**并列输出**，禁止隐式择一。
* **缺项不推断**：依赖 N 个输入的规则，输入不全时落到 ``not_assessable``，
  严禁按"其他项正常"判成正常。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional
import os
from typing import Optional

from .judge import Judgement
from .lipid_table import (LIFETIME_AGE_LIMIT, LIFETIME_MIN_HITS,
                          lifetime_risk, lookup)
from .rules import RULES
from .schema import DerivedResult, DocumentMeta

MMOL_PER_MGDL = 0.0259


# ---------------------------------------------------------------- 取值器

class Composer:
    """按 (analyte_id, year) 取值的访问器。

    **单位不等价的年份一律不可取值**（设计：只判等价、不换算）——把 mg/dL
    当 mmol/L 代入 SI 阈值会算出错误的分层（已踩：201 mg/dL 被算成
    "201.0 mmol/L" 直接命中高危）。拦下后组合层如实报"不可评估"。
    """

    def __init__(self, judgements: list[Judgement],
                 metas: dict[str, DocumentMeta], profile: dict):
        self.val: dict[tuple[str, str], Optional[float]] = {}
        self.raw: dict[tuple[str, str], str] = {}
        self.judgements = {j.analyte_id: j for j in judgements}
        #: (analyte_id, year) → 该年观测单位（与规范单位不等价者）
        self.unit_conflict: dict[tuple[str, str], str] = {}
        for j in judgements:
            for y, v in j.series.items():
                self.val[(j.analyte_id, y)] = v
            for y, ok in (getattr(j, "unit_ok", None) or {}).items():
                if not ok:
                    self.unit_conflict[(j.analyte_id, y)] = j.unit
        self.metas = metas
        self.profile = profile

    def value(self, aid: str, year: str) -> Optional[float]:
        if (aid, year) in self.unit_conflict:
            return None
        return self.val.get((aid, year))

    def get(self, aids: list[str], year: str) -> tuple[dict[str, float], list[str]]:
        """批量取值，返回 (值字典, 缺失列表)。"""
        got, miss = {}, []
        for a in aids:
            v = self.value(a, year)
            if v is None:
                u = self.unit_conflict.get((a, year))
                miss.append(f"{a}（单位 {u} ≠ 规范单位，按设计不换算）" if u else a)
            else:
                got[a] = v
        return got, miss


# ---------------------------------------------------------------- 算法

#: FIB-4 的切点**按用途分两套**（见 rules.P_FIB4_UNITS）。
#:   HCV  —— 原始验证队列（UW 计算器页）：1.45 / 3.25
#:   NAFLD —— 非酒精性脂肪肝路径常用：<1.30 排除进展期纤维化 / >2.67 高概率
FIB4_CUTOFFS = {"HCV": (1.45, 3.25), "NAFLD": (1.30, 2.67)}

#: 本引擎当前采用的切点集。这是**显式选择**，不是默认值 ——
#: 本案例是体检人群（非 HCV 队列），故取 NAFLD 路径。
FIB4_CONTEXT = "NAFLD"

#: NKF 官方实现指南规定的报告范围与取整方式
EGFR_REPORT_MIN, EGFR_REPORT_MAX = 5, 150


def egfr_reported(value: float) -> tuple[Optional[int], str, bool]:
    """按 NKF 规定把计算值转成**报告值**。

    NKF 原文：「报告范围 5-150 mL/min/1.73m²（<5 报 <5，>150 报 >150）」
    「Number of decimal places: zero, report in whole numbers」。

    返回 ``(报告值或 None, 显示文本, 是否被截断)``。越界时报告值取 None ——
    因为实际报告上写的是 ``<5`` / ``>150``，不是一个数。
    """
    if value < EGFR_REPORT_MIN:
        return None, f"<{EGFR_REPORT_MIN}", True
    if value > EGFR_REPORT_MAX:
        return None, f">{EGFR_REPORT_MAX}", True
    return int(round(value)), str(int(round(value))), False


def egfr_ckdepi2021(scr_umol: float, age: int, male: bool = True) -> float:
    """CKD-EPI 2021（无种族系数）。出处见 rules.P_CKDEPI。"""
    scr = scr_umol / 88.4
    k = 0.9 if male else 0.7
    a = -0.302 if male else -0.241
    r = scr / k
    return (142 * min(r, 1) ** a * max(r, 1) ** -1.200
            * (0.9938 ** age) * (1.0 if male else 1.012))


def fib4(age: int, ast: float, plt: float, alt: float) -> float:
    """FIB-4。出处见 rules.P_FIB4。"""
    return age * ast / (plt * math.sqrt(alt))


#: China-PAR 男性方程系数
_CP = {
    "lage": 31.97, "lsbp_treated": 27.39, "lsbp_untreated": 26.15,
    "ltc": 0.62, "lhdl": -0.69, "lwc": -0.71,
    "smoke": 3.96, "dm": 0.36, "north": 0.48, "urban": -0.16, "fh": 6.22,
    "lage_lsbp_treated": -6.02, "lage_lsbp_untreated": -5.73,
    "lage_smoke": -0.94, "lage_fh": -1.53,
}
_CP_S10, _CP_MEAN = 0.9707, 140.68


def china_par_male(age: float, sbp: float, tc: float, hdl: float, wc: float,
                   smoke: bool, dm: bool = False, north: bool = True,
                   urban: bool = True, fh: bool = False,
                   treated: bool = False) -> float:
    """China-PAR 男性方程 → 10 年 ASCVD 风险（0-1）。出处见 rules.P_CHINAPAR。"""
    lage, lsbp = math.log(age), math.log(sbp)
    ltc, lhdl, lwc = (math.log(tc / MMOL_PER_MGDL), math.log(hdl / MMOL_PER_MGDL),
                      math.log(wc))
    xb = (_CP["lage"] * lage
          + (_CP["lsbp_treated"] if treated else _CP["lsbp_untreated"]) * lsbp
          + _CP["ltc"] * ltc + _CP["lhdl"] * lhdl + _CP["lwc"] * lwc
          + _CP["smoke"] * int(smoke) + _CP["dm"] * int(dm)
          + _CP["north"] * int(north) + _CP["urban"] * int(urban)
          + _CP["fh"] * int(fh)
          + (_CP["lage_lsbp_treated"] if treated
             else _CP["lage_lsbp_untreated"]) * lage * lsbp
          + _CP["lage_smoke"] * lage * int(smoke)
          + _CP["lage_fh"] * lage * int(fh))
    return 1 - _CP_S10 ** math.exp(xb - _CP_MEAN)


# ---------------------------------------------------------------- 评估器

def _res(rule_id: str, year: str, **kw) -> DerivedResult:
    r = next(x for x in RULES if x.rule_id == rule_id)
    return DerivedResult(rule_id=rule_id, name=r.name, year=year, **kw)


def _not_assessable(rule_id: str, year: str, miss: list[str]) -> DerivedResult:
    return _res(rule_id, year, status="not_assessable", missing=miss,
                text=f"因缺少 {'、'.join(miss)}，无法完成该项评估")


def evaluate(c: Composer) -> list[DerivedResult]:
    """跑全部规则。"""
    out: list[DerivedResult] = []
    years = sorted(c.metas)
    male = all((c.metas[y].sex or "男") == "男" for y in years)
    pf = c.profile

    for y in years:
        meta = c.metas[y]
        age = meta.age

        # ---- 复合指标
        got, miss = c.get(["CHEM.总胆固醇", "CHEM.高密度脂蛋白胆固醇"], y)
        if not miss:
            v = round(got["CHEM.总胆固醇"] - got["CHEM.高密度脂蛋白胆固醇"], 2)
            out.append(_res("COMPOSITE.NON_HDL_C", y, value=v, unit="mmol/L",
                            inputs={k.split(".")[-1]: val for k, val in got.items()},
                            text=f"non-HDL-C = {got['CHEM.总胆固醇']} − "
                                 f"{got['CHEM.高密度脂蛋白胆固醇']} = {v} mmol/L"))
        else:
            out.append(_not_assessable("COMPOSITE.NON_HDL_C", y, miss))

        got, miss = c.get(["CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇"], y)
        if not miss and got["CHEM.高密度脂蛋白胆固醇"] > 0:
            v = round(math.log10(got["CHEM.甘油三酯"]
                                 / got["CHEM.高密度脂蛋白胆固醇"]), 3)
            out.append(_res("COMPOSITE.AIP", y, value=v,
                            inputs={k.split(".")[-1]: val for k, val in got.items()},
                            text=f"AIP = log10({got['CHEM.甘油三酯']} / "
                                 f"{got['CHEM.高密度脂蛋白胆固醇']}) = {v}",
                            notes=["AIP 无公认切点，只看方向"]))
        else:
            out.append(_not_assessable("COMPOSITE.AIP", y, miss or ["HDL-C>0"]))

        got, miss = c.get(["CHEM.肌酐"], y)
        if not miss and age:
            v = round(egfr_ckdepi2021(got["CHEM.肌酐"], age, male), 1)
            rep, rep_txt, clipped = egfr_reported(v)
            g_raw = next(n for lo, n in G_STAGES if v >= lo)
            g_rep = (next(n for lo, n in G_STAGES if rep >= lo)
                     if rep is not None else None)
            notes = ["无尿微量白蛋白/ACR，无法完成 CKD 完整分期"]
            notes.append(f"NKF 报告规定：范围 {EGFR_REPORT_MIN}-{EGFR_REPORT_MAX}、"
                         f"取整数 → 报告值 {rep_txt}")
            if rep is not None and g_rep != g_raw:
                notes.append(f"⚠ **取整跨过了分期边界**：计算值 {v} 属 {g_raw}，"
                             f"取整为 {rep} 后属 {g_rep} —— 报告上写的是 {rep}，"
                             f"下游按报告判会得到 {g_rep}")
            if clipped:
                notes.append(f"⚠ 计算值 {v} 超出报告范围 → 报告只写 {rep_txt}，"
                             f"**无法据此做分期**")
            out.append(_res("RENAL.EGFR_CKDEPI2021", y, value=v, unit="mL/min/1.73m²",
                            inputs={"肌酐": got["CHEM.肌酐"], "年龄": age,
                                    "报告值": rep, "报告文本": rep_txt},
                            band=f"{g_raw}(计算)" + (f" / {g_rep}(报告)" if g_rep and g_rep != g_raw else ""),
                            text=f"eGFR = {v} mL/min/1.73m²（计算值；报告值 {rep_txt}）",
                            notes=notes))
        else:
            out.append(_not_assessable("RENAL.EGFR_CKDEPI2021", y, miss or ["年龄"]))

        got, miss = c.get(["CHEM.天门冬氨基转移酶", "BLD.血小板",
                           "CHEM.丙氨酸氨基转移酶"], y)
        if not miss and age:
            v = round(fib4(age, got["CHEM.天门冬氨基转移酶"],
                           got["BLD.血小板"], got["CHEM.丙氨酸氨基转移酶"]), 2)
            lo, hi = FIB4_CUTOFFS[FIB4_CONTEXT]
            band = f"低（<{lo}）" if v < lo else (f"灰区（{lo}–{hi}）" if v <= hi
                                              else f"高（>{hi}）")
            out.append(_res("HEPATIC.FIB4", y, value=v, band=band,
                            inputs={**{k.split(".")[-1]: val for k, val in got.items()},
                                    "年龄": age,
                                    "切点集": FIB4_CONTEXT,
                                    "切点": f"{lo}/{hi}"},
                            text=f"FIB-4 = {v} → {band}（切点集 {FIB4_CONTEXT}）",
                            notes=[
                                "AASLD 2023 不建议用血液学指标追踪纤维化进展",
                                f"**切点必须连同用途一起读**：HCV 队列用 "
                                f"{FIB4_CUTOFFS['HCV'][0]}/{FIB4_CUTOFFS['HCV'][1]}，"
                                f"NAFLD 路径用 {FIB4_CUTOFFS['NAFLD'][0]}/"
                                f"{FIB4_CUTOFFS['NAFLD'][1]}；本引擎用 "
                                f"{FIB4_CONTEXT}。混用会给出不同的临床含义",
                            ]))
        else:
            out.append(_not_assessable("HEPATIC.FIB4", y, miss or ["年龄"]))

        # ---- 代谢综合征：三个版本**并列输出**，禁止静默择一
        out.extend(_ckd_stage(c, y, male))
        out.extend(_metsyn(c, y, male, pf))

        # ---- China-PAR
        out.append(_china_par(c, y, male, pf))

        # ---- 危险分层 + LDL-C 达标
        out.extend(_stratify(c, y, male, pf))

    return out


#: 代谢综合征各标准的分性别阈值。
#:
#: ⚠ 女值**未取得逐字原文**（见 ``rules.py`` 里三条 METSYN 规则的
#: ``Provenance.verified=False``）。但"对女性套用男性阈值"是**确定的错**，
#: 比"用了一个待核对的正确值"更糟——所以先按公认值实现并显式标未核实。
METSYN_CUTS = {
    "CDS2004": {"bmi": 25.0, "fpg": 6.1, "bp": (140, 90), "tg": 1.7,
                "hdl": {"男": 0.9, "女": 1.0}},
    "GD2007": {"wc": {"男": 90, "女": 85}, "fpg": 5.6, "bp": (130, 85),
               "tg": 1.7, "hdl": {"男": 1.03, "女": 1.29}},
    "IDF2005": {"wc": {"男": 90, "女": 80}, "fpg": 5.6, "bp": (130, 85),
                "tg": 1.7, "hdl": {"男": 1.03, "女": 1.29}},
}


def _sex(meta) -> Optional[str]:
    return getattr(meta, "sex", None)


#: KDIGO 2012 G 分期（eGFR 下界含、上界不含）
G_STAGES = [(90, "G1"), (60, "G2"), (45, "G3a"), (30, "G3b"), (15, "G4"), (0, "G5")]


def _ckd_stage(c: Composer, year: str, male: bool) -> list[DerivedResult]:
    """CKD 分期（KDIGO 2012）。

    **A 分期缺尿白蛋白/ACR → 一律不可评估**，不假定为 A1。这是本案例的真实
    缺口：报告里有尿常规但无定量白蛋白，所以只能给 G 分期。
    单凭 G 分期**不足以判定 CKD** —— 白蛋白尿是另一半，必须写清楚。
    """
    scr = c.value("CHEM.肌酐", year)
    age = c.metas[year].age
    if scr is None or not age:
        return [_not_assessable("RENAL.CKD_STAGE", year,
                                ["缺肌酐或年龄 → 无法算 eGFR"])]
    egfr = round(egfr_ckdepi2021(scr, age, male), 1)
    # 分期用**报告值**（取整后）—— 临床上看到的、以及报告上印的就是这个数
    rep, rep_txt, clipped = egfr_reported(egfr)
    base = rep if rep is not None else egfr
    g = next(name for lo, name in G_STAGES if base >= lo)
    band = f"{g} + A?"
    text = (f"eGFR {egfr}（报告 {rep_txt}）→ {g}；"
            f"**A 分期不可评估**（缺尿白蛋白/ACR）→ 无法给出完整 CKD 分期")
    notes = ["只有 G 分期，**不足以判定 CKD**：白蛋白尿是另一半",
             "eGFR 由 CKD-EPI 2021 估算，非实测"]
    if g in ("G3a", "G3b", "G4"):
        notes.append(f"{g} 属「CKD 3~4 期」范畴 → 血脂指南将其列为"
                     f"**直接高危**条件之一（本引擎已实现）")
    if clipped:
        notes.append(f"计算值 {egfr} 超出报告范围 {EGFR_REPORT_MIN}-{EGFR_REPORT_MAX}，"
                     f"报告只写 {rep_txt} → 分期以报告文本无法确定的边界为准，**存疑**")
    return [_res("RENAL.CKD_STAGE", year, value=egfr, unit="mL/min/1.73m²",
                 band=band, inputs={"eGFR 计算值": egfr, "eGFR 报告值": rep,
                                    "报告文本": rep_txt, "G 分期": g,
                                    "A 分期": "不可评估（缺 ACR）"},
                 text=text, notes=notes)]


def _metsyn(c: Composer, year: str, male: bool,
            pf: dict) -> list[DerivedResult]:
    """三个版本的代谢综合征标准**并列**。

    三点必须守住：

    1. **分性别阈值**。原先腰围写死 90cm、HDL 只在 ``male`` 为真时判定 ——
       女性被静默套用男性阈值。女值见 :data:`METSYN_CUTS`（待核）。
    2. **已确诊即计入对应组件**。已确诊高血压/糖尿病者，即使当次血压/血糖
       正常，该组件也成立（各标准原文均含"或已诊断"）。
    3. **性别未知 → 不可评估**，不默认取男性阈值（「未知维度不算命中」）。
    """
    res: list[DerivedResult] = []
    meta = c.metas.get(year)
    sex = _sex(meta)
    if sex not in ("男", "女"):
        return [_not_assessable(f"METSYN.{k}", year,
                                ["性别未知 → 无法选分性别阈值"])
                for k in ("CDS2004", "GD2007", "IDF2005")]
    hx_htn = bool(pf.get("hypertension_diagnosed"))
    hx_dm = bool(pf.get("diabetes"))

    bmi = c.value("VITAL.体重指数", year)
    fpg = c.value("CHEM.葡萄糖", year)
    sbp, dbp = c.value("VITAL.收缩压", year), c.value("VITAL.舒张压", year)
    tg = c.value("CHEM.甘油三酯", year)
    hdl = c.value("CHEM.高密度脂蛋白胆固醇", year)
    wc = c.value("VITAL.腰围", year)
    treat = "已确诊计入" if (hx_htn or hx_dm) else ""

    # ---- CDS 2004：4 项中满足 3 项（无腰围条款）
    cut = METSYN_CUTS["CDS2004"]
    crit = {
        f"BMI≥{cut['bmi']}": (bmi is not None and bmi >= cut["bmi"]),
        f"FPG≥{cut['fpg']}": (hx_dm or (fpg is not None and fpg >= cut["fpg"])),
        f"BP≥{cut['bp'][0]}/{cut['bp'][1]}": (
            hx_htn or (sbp is not None and dbp is not None
                       and (sbp >= cut["bp"][0] or dbp >= cut["bp"][1]))),
        f"TG≥{cut['tg']} 或 HDL{sex}<{cut['hdl'][sex]}": (
            tg is not None and hdl is not None
            and (tg >= cut["tg"] or hdl < cut["hdl"][sex])),
    }
    if any(v is None for v in (bmi, fpg, sbp, dbp, tg, hdl)):
        res.append(_not_assessable("METSYN.CDS2004", year,
                                   [k for k, v in (("BMI", bmi), ("FPG", fpg),
                                                   ("血压", sbp), ("TG", tg),
                                                   ("HDL", hdl)) if v is None]))
    else:
        met = [k for k, v in crit.items() if v]
        res.append(_res("METSYN.CDS2004", year,
                        value=float(len(met)),
                        band="满足" if len(met) >= 3 else "不满足",
                        inputs={"已满足": "、".join(met) or "无", "共满足": len(met),
                                "性别": sex, treat: True} if treat else
                               {"已满足": "、".join(met) or "无", "共满足": len(met),
                                "性别": sex},
                        text=f"{len(met)}/4 项 → "
                             f"{'满足' if len(met) >= 3 else '不满足'}"
                             f"（已满足：{'、'.join(met) or '无'}）"))

    # ---- 2007 血脂指南版：5 项中满足 3 项
    cut = METSYN_CUTS["GD2007"]
    if any(v is None for v in (wc, tg, hdl, sbp, dbp, fpg)):
        res.append(_not_assessable("METSYN.GD2007", year,
                                   ["腰围/血压/血脂/血糖不全"]))
    else:
        crit5 = {
            f"腰围{sex}>{cut['wc'][sex]}cm": wc > cut["wc"][sex],
            f"TG≥{cut['tg']}": tg >= cut["tg"],
            f"HDL{sex}<{cut['hdl'][sex]}": hdl < cut["hdl"][sex],
            f"BP≥{cut['bp'][0]}/{cut['bp'][1]}": (
                hx_htn or sbp >= cut["bp"][0] or dbp >= cut["bp"][1]),
            f"FPG≥{cut['fpg']}": hx_dm or fpg >= cut["fpg"],
        }
        met = [k for k, v in crit5.items() if v]
        res.append(_res("METSYN.GD2007", year,
                        value=float(len(met)),
                        band="满足" if len(met) >= 3 else "不满足",
                        inputs={"已满足": "、".join(met) or "无", "共满足": len(met),
                                "性别": sex},
                        text=f"{len(met)}/5 项 → "
                             f"{'满足' if len(met) >= 3 else '不满足'}"
                             f"（已满足：{'、'.join(met) or '无'}）"))

    # ---- IDF 2005：腰围必备 + 2 项
    cut = METSYN_CUTS["IDF2005"]
    if wc is None or any(v is None for v in (tg, hdl, sbp, dbp, fpg)):
        res.append(_not_assessable("METSYN.IDF2005", year,
                                   ["腰围/血压/血脂/血糖不全"]))
    else:
        must = wc >= cut["wc"][sex]
        extra = sum([tg >= cut["tg"], hdl < cut["hdl"][sex],
                     hx_htn or sbp >= cut["bp"][0] or dbp >= cut["bp"][1],
                     hx_dm or fpg >= cut["fpg"]])
        ok = must and extra >= 2
        res.append(_res("METSYN.IDF2005", year, value=float(extra),
                        band="满足" if ok else "不满足",
                        inputs={"腰围必备": must, "另满足": extra, "性别": sex,
                                "腰围阈值": cut["wc"][sex]},
                        text=f"{'满足' if ok else '不满足'}（{sex}腰围{wc}cm "
                             f"{'达' if must else '未达'}必备条件"
                             f"（阈值 {cut['wc'][sex]}cm），另满足 {extra} 项）"))
    return res


def _china_par(c: Composer, year: str, male: bool, pf: dict) -> DerivedResult:
    # 适用性优先于缺项：人群不适用的规则不该报"缺输入"（not_applicable ≠ not_assessable）
    if not male:
        return _res("ASCVD.CHINAPAR", year, status="not_applicable",
                    text="本引擎目前只实现了 China-PAR 男性方程，不适用于女性受检者")
    got, miss = c.get(["CHEM.总胆固醇", "CHEM.高密度脂蛋白胆固醇",
                       "VITAL.收缩压", "VITAL.腰围"], year)
    age = c.metas[year].age
    if miss or not age:
        return _not_assessable("ASCVD.CHINAPAR", year, miss or ["年龄"])

    smoke = bool(pf.get("smoking", {}).get("current", False))
    fh_known = bool(pf.get("family_history_ascvd", {}).get("known", False))
    fh = bool(pf.get("family_history_ascvd", {}).get("value", False))
    reg = pf.get("region", {})
    r = china_par_male(age, got["VITAL.收缩压"], got["CHEM.总胆固醇"],
                       got["CHEM.高密度脂蛋白胆固醇"], got["VITAL.腰围"],
                       smoke=smoke, dm=bool(pf.get("diabetes", False)),
                       north=bool(reg.get("north", True)),
                       urban=bool(reg.get("urban", True)), fh=fh,
                       treated=bool(pf.get("antihypertensive", False)))
    band = "低危(<5%)" if r < 0.05 else ("中危(5-9.9%)" if r < 0.10 else "高危(≥10%)")
    notes = []
    if age < 35 or age > 74:
        notes.append(f"年龄 {age} 超出模型验证范围 35–74，属外推")
    if not fh_known:
        notes.append("家族史未知，按『无』计算；已做敏感性检验")
    return _res("ASCVD.CHINAPAR", year, value=round(r * 100, 1), unit="%（10年）",
                band=band, inputs={**{k.split(".")[-1]: v for k, v in got.items()},
                                   "年龄": age, "吸烟": smoke, "家族史": fh},
                text=f"China-PAR 10 年 ASCVD 风险 {r*100:.1f}% → {band}",
                notes=notes)


#: 表7 降脂靶点（风险等级 → (LDL-C 目标, 是否要求较基线降幅>50%)）
LDL_TARGETS = {
    "低危": (3.4, False),
    "中危": (2.6, False),
    "高危": (2.6, False),
    "极高危": (1.8, True),
    "超高危": (1.4, True),
}


@dataclass
class LipidInputs:
    """用于判定的血脂值，以及它们的**来源**。"""

    tc: Optional[float]
    ldl: Optional[float]
    hdl: Optional[float]
    source: str            #: current | pretreatment
    caveat: str = ""       #: 需要说明的问题（空 = 无）


def resolve_lipids(c: Composer, year: str, pf: dict) -> LipidInputs:
    """按指南图1 脚注取血脂：**「危险因素的水平均为干预前水平」**。

    三种情形：

    * **未服降脂药** → 当次值即干预前值（本案例属此类）
    * **服药 + 有治疗前记录** → 用治疗前值
    * **服药 + 无治疗前记录** → 只能用当次值，但结果会**低估**：
      他汀会把 TC/LDL-C 压下来，可能让人从 C3 掉到 C2、甚至让
      「LDL-C≥4.9 → 直接高危」这条**根本不触发**

    第三种情形不是"缺个数"那么简单 —— 它会让**结论偏向乐观**，
    所以必须显式带出来，而不是悄悄用当次值。
    """
    tc = c.value("CHEM.总胆固醇", year)
    ldl = c.value("CHEM.低密度脂蛋白胆固醇", year)
    hdl = c.value("CHEM.高密度脂蛋白胆固醇", year)

    if not pf.get("lipid_lowering"):
        return LipidInputs(tc, ldl, hdl, "current")

    pre = pf.get("pretreatment_lipids") or {}
    if pre.get("known") and any(pre.get(k) is not None
                                for k in ("tc", "ldl", "hdl")):
        # 注意：不能写 ``pre.get("tc", tc)`` —— 键存在但值为 None 时，
        # dict.get 会返回 None 而**不**用默认值。必须显式判 None。
        def pick(key, fallback):
            v = pre.get(key)
            return fallback if v is None else v

        return LipidInputs(
            pick("tc", tc), pick("ldl", ldl), pick("hdl", hdl),
            "pretreatment",
            "已按脚注要求改用**干预前水平**；治疗前缺失的项回落到当次值")

    return LipidInputs(
        tc, ldl, hdl, "current",
        "⚠ 受检者**在服降脂药**，但无治疗前血脂记录 → 本项用了**当次（治疗中）值**。"
        "他汀会压低 TC/LDL-C，故：① 胆固醇分层可能偏低一档；"
        "②「LDL-C≥4.9 或 TC≥7.2 → 直接高危」这条**可能被掩盖**。"
        "结论方向偏**乐观**，需向受检者核实用药前的血脂")


def _stratify(c: Composer, year: str, male: bool, pf: dict) -> list[DerivedResult]:
    """血脂指南2023 危险分层 + LDL-C 达标（表7）。

    分层分**三条路径**（依据图1 说明，逐字见 ``rules.P_LIPID2023``）：

    * **二级预防**（已诊断 ASCVD）：≥2 次严重事件，或 1 次事件且合并 ≥2 个
      高危险因素 → **超高危**；其他 ASCVD 患者 → **极高危**
    * **一级预防 · 直接高危**：满足①LDL-C≥4.9 或 TC≥7.2 ②年龄≥40 的糖尿病
      ③CKD 3-4 期 之一者 → **高危**，**不需要再做 10 年风险评估**
    * **一级预防 · 其余**：按 10 年发病风险 <5% / 5-9% / ≥10% 映射 低/中/高危

    第三条路径指南给的是**21 格查表**（按 LDL-C × 有无高血压 × 危险因素个数）；
    该表正文未取到，这里用 China-PAR 的**个体** 10 年风险配上**已验证的切点**
    近似，并在结论里写明这是近似。
    """
    lip = resolve_lipids(c, year, pf)
    ldl, tc, hdl = lip.ldl, lip.tc, lip.hdl
    sbp = c.value("VITAL.收缩压", year)
    dbp = c.value("VITAL.舒张压", year)
    scr = c.value("CHEM.肌酐", year)
    age = c.metas[year].age
    if ldl is None:
        return [_not_assessable("LIPID.RISK_STRATIFICATION", year, ["LDL-C"]),
                _not_assessable("LIPID.LDL_TARGET", year, ["LDL-C"])]

    # 用**报告值**判 CKD 3-4 期 —— 报告上印的是取整后的数，下游按报告判。
    # 这里曾经用计算值，于是 59.8 会被判成 CKD 3 期，而报告上写的是 60（G2）。
    egfr_raw = (round(egfr_ckdepi2021(scr, age, male), 1)
                if (scr is not None and age) else None)
    egfr = None
    if egfr_raw is not None:
        rep, _txt, _clip = egfr_reported(egfr_raw)
        egfr = float(rep) if rep is not None else egfr_raw
    # 其他 ASCVD 危险因素个数 —— 21 格表的第二维。取指南该表常用的三项。
    rf_list = []
    if pf.get("smoking", {}).get("current"):
        rf_list.append("吸烟")
    if hdl is not None and hdl < 1.04:
        rf_list.append(f"低HDL-C（{hdl}）")
    if age and ((male and age >= 45) or (not male and age >= 55)):
        rf_list.append(f"年龄（{age}）")

    inputs: dict = {
        "LDL-C": ldl, "TC": tc, "eGFR": egfr,
        "血脂取值来源": lip.source,
        # 这两项**不驱动**下面的三条路径，但必须记录：
        # 它们是 21 格表的两个维度，缺了它们就无法核对近似是否合理。
        "危险因素个数": len(rf_list), "危险因素": rf_list,
        "高血压": pf.get("hypertension_diagnosed"),
    }

    # ---- 路径 1：二级预防
    if pf.get("ascvd_diagnosed"):
        events = int(pf.get("severe_ascvd_events") or 0)
        hrf = int(pf.get("ascvd_high_risk_factors") or 0)
        if events >= 2 or (events == 1 and hrf >= 2):
            strat = "超高危"
            basis = f"已诊断 ASCVD；严重事件 {events} 次、高危险因素 {hrf} 个"
        else:
            strat = "极高危"
            basis = "已诊断 ASCVD，其他患者"
        inputs.update({"路径": "二级预防", "严重事件数": events, "高危因素数": hrf})

    # ---- 路径 2：一级预防 · 直接高危
    else:
        direct = []
        ckd_used = False
        ckd_rounding = False
        if ldl >= 4.9:
            direct.append(f"LDL-C≥4.9（{ldl}）")
        if tc is not None and tc >= 7.2:
            direct.append(f"TC≥7.2（{tc}）")
        if pf.get("diabetes") and age and age >= 40:
            direct.append(f"年龄≥40 的糖尿病（{age} 岁）")
        if egfr is not None and egfr < 60:
            direct.append(f"eGFR {egfr}<60 → CKD 3-4 期（仅按 eGFR 判）")
            ckd_used = True
        elif egfr_raw is not None and egfr_raw < 60:
            # 计算值 <60 但报告值 ≥60 → 这个条件**不成立**，但必须说出来
            ckd_rounding = True
        if direct:
            strat, basis = "高危", "**直接高危**（无需 10 年评估）：" + "；".join(direct)
            inputs["路径"] = "一级预防 · 直接高危"
        else:
            # ---- 路径 3：一级预防 · 10 年风险 → **查 21 格表**
            #
            # 原先是拿 China-PAR 的**个体**风险配切点近似。指南图1 给的是一张
            # 21 格查表（人群平均风险），两者不等价。表已从原文渲染图逐字转录
            # （见 checkup/lipid_table.py），故改为**照表查**。
            hdl_v = c.value("CHEM.高密度脂蛋白胆固醇", year)
            non_hdl_v = (round(tc - hdl_v, 2)
                         if (tc is not None and hdl_v is not None) else None)
            smoke = bool(pf.get("smoking", {}).get("current", False))
            res = lookup(
                hypertension=pf.get("hypertension_diagnosed"),
                age=age, male=male, tc=tc, ldl=ldl, hdl=hdl_v, smoke=smoke)

            if res.status == "not_assessable":
                # 表查不了 → **不可评估**，不再退回近似。用近似顶替会把
                # 「缺一个维度」悄悄变成「有一个结论」。
                extra = list(res.notes)
                if pf.get("hypertension_diagnosed") is None:
                    extra.append("profile.json 的 hypertension_diagnosed 未确认；"
                                 "相关年份报告只写『正常血压』，未给可判读的数值区间")
                return [_not_assessable("LIPID.RISK_STRATIFICATION", year,
                                        extra or ["21 格表所需输入不全"]),
                        _not_assessable("LIPID.LDL_TARGET", year, ["分层未定"])]

            strat = res.band
            basis = (f"21 格表：高血压={'有' if pf.get('hypertension_diagnosed') else '无'}"
                     f" × 危险因素 {res.row_key} 个 × {res.stratum_key} → {strat}")
            # 注意：**不要覆盖** "危险因素个数" —— 它在函数上方已存了数值。
            # 这里曾把它改成一句显示文本，测试立刻发现接口变了。
            inputs.update({"路径": "一级预防 · 21 格表",
                           "21格行": res.row_key, "21格列": res.stratum_key,
                           "21格依据": res.reasons})
            notes_extra = list(res.notes)

            # China-PAR 个体风险作为**旁证**保留（不再作为分层依据）
            if all(x is not None for x in (tc, sbp, hdl_v)) and age and \
                    c.value("VITAL.腰围", year) is not None:
                risk = round(china_par_male(
                    age, sbp, tc, hdl_v, c.value("VITAL.腰围", year),
                    smoke=smoke, dm=bool(pf.get("diabetes", False)),
                    north=bool(pf.get("region", {}).get("north", True)),
                    urban=bool(pf.get("region", {}).get("urban", True)),
                    fh=bool(pf.get("family_history_ascvd", {}).get("value", False)),
                    treated=bool(pf.get("antihypertensive", False))) * 100, 1)
                risk_band = "低危" if risk < 5 else ("中危" if risk < 10 else "高危")
                inputs["ChinaPAR 10年风险%"] = risk
                notes_extra.append(
                    f"旁证：China-PAR **个体** 10 年风险 {risk}% → {risk_band}；"
                    f"与查表结果{'' if risk_band == strat else '**不一致**'}"
                    f"（表是人群平均风险，模型是个体风险，两者不等价，"
                    f"**分层以查表为准**）")

            # 余生风险：中危且年龄<55 时评估
            if strat == "中危" and age and age < LIFETIME_AGE_LIMIT:
                lt = lifetime_risk(sbp, dbp, non_hdl_v, hdl_v,
                                   c.value("VITAL.体重指数", year), smoke)
                inputs["余生风险"] = lt.band
                if lt.status == "not_assessable":
                    notes_extra.append(
                        f"中危且年龄<{LIFETIME_AGE_LIMIT} → 指南要求评估**余生风险**，"
                        f"但缺 {('、'.join(lt.missing))} → 不可评估")
                else:
                    notes_extra.append(
                        f"中危且年龄<{LIFETIME_AGE_LIMIT} → 评估余生风险："
                        f"命中 {len(lt.hits)}/5 项"
                        f"（{'、'.join(lt.hits) or '无'}）→ **{lt.band}**"
                        f"（≥{LIFETIME_MIN_HITS} 项即为高危人群）")
            elif strat == "中危":
                notes_extra.append(
                    f"中危但年龄≥{LIFETIME_AGE_LIMIT} → 指南未要求评估余生风险")

    notes = ["一级预防" if not pf.get("ascvd_diagnosed") else "二级预防"]
    if lip.caveat:
        notes.append(lip.caveat)
    elif lip.source == "pretreatment":
        notes.append("已按脚注要求使用**干预前**血脂值（来源 "
                     "profile.pretreatment_lipids）")
    notes.extend(locals().get("notes_extra") or [])
    if locals().get("ckd_used"):
        notes.append("CKD 分期只用了 eGFR 一侧 —— 缺尿白蛋白/ACR，"
                     "无法判断白蛋白尿分期，**可能低估 CKD 分期**")
    if locals().get("ckd_rounding"):
        notes.append(f"⚠ eGFR 计算值 {egfr_raw} <60，但**报告值 {egfr}**（取整后）"
                     f"≥60 → 按报告不构成 CKD 3-4 期，故**不列入直接高危**。"
                     f"若以计算值为准则应列入 —— 这是一个四舍五入跨越的结论差异")
    # 余生风险的评估已在路径 3 内完成（见上方 lifetime_risk 调用）。
    # 原先这里另有一条"本引擎未实现"的注记，实现之后它就失真了，故删除。
    if egfr is None:
        notes.append("缺肌酐或年龄 → 无法用 eGFR 判断 CKD 3-4 期")
    # 21 格表正文已于 2026-10-01 取得（由渲染图逐字转录，见 lipid_table.py），
    # 第三条路径改为**照表查**。此处只说明本案例走的是哪一格。
    notes.append(
        f"21 格表：高血压状态="
        f"{'已确诊' if pf.get('hypertension_diagnosed') else '未确诊' if pf.get('hypertension_diagnosed') is not None else '未确认'}"
        f"；危险因素 {len(rf_list)} 个"
        f"{'：' + '、'.join(rf_list) if rf_list else ''}")

    strat_res = _res("LIPID.RISK_STRATIFICATION", year, band=strat,
                     inputs=inputs, text=f"{basis} → {strat}", notes=notes)

    # ---- 表7：LDL-C 目标与达标
    target, need_drop = LDL_TARGETS[strat]
    series = [v for v in (c.value("CHEM.低密度脂蛋白胆固醇", y)
                          for y in sorted(c.metas)) if v is not None]
    baseline = max(series) if series else ldl      # 以历年最高值近似"基线"
    drop = (baseline - ldl) / baseline * 100 if baseline else 0.0
    ok = ldl < target and (not need_drop or drop > 50)
    why = f"LDL-C={ldl} vs 目标 <{target}"
    if need_drop:
        why += (f" 且需较基线降幅>50%（以历年最高 {baseline} 近似基线，"
                f"实际降幅 {drop:.0f}%）")
    target_res = _res("LIPID.LDL_TARGET", year, value=ldl, unit="mmol/L",
                      band=f"{strat} 目标<{target}{'+降幅>50%' if need_drop else ''}"
                           f" → {'达标' if ok else '未达标'}",
                      inputs={"LDL-C": ldl, "分层": strat, "目标值": target,
                              "需降幅>50%": need_drop, "近似基线": baseline},
                      text=f"{why} → {'达标' if ok else '未达标'}",
                      notes=["基线用历年最高 LDL-C 近似；真实基线应是"
                             "治疗前的水平"] if need_drop else [])
    # 非 HDL-C 目标 = LDL-C + 0.8（表7 注）
    #
    # ⚠ 这个量必须**既能被断言、又能被显示** —— 不能只在文案里算出来。
    # 曾经写成 ``v = round(non_hdl - hdl, 2)`` 且只在 f-string 里用，于是一次
    # 被打断的变异测试把 ``-`` 留成 ``+``（算出 TC+HDL = 5.98 而非 4.20），
    # 而**全部 416 项测试照过** —— 因为没有任何测试看得到这个数。
    # 现在它进 ``inputs``，与其它派生量一视同仁。
    tc_v = c.value("CHEM.总胆固醇", year)
    hdl_v = c.value("CHEM.高密度脂蛋白胆固醇", year)
    if tc_v is not None and hdl_v is not None:
        non_hdl_c = round(tc_v - hdl_v, 2)
        non_hdl_target = round(target + 0.8, 2)
        target_res.inputs["non-HDL-C"] = non_hdl_c
        target_res.inputs["non-HDL-C 目标"] = non_hdl_target
        target_res.notes.append(
            f"非 HDL-C 目标 = LDL-C 目标 + 0.8 = {non_hdl_target}；"
            f"本次 non-HDL-C = {non_hdl_c} → "
            f"{'达标' if non_hdl_c < non_hdl_target else '未达标'}")
    return [strat_res, target_res]


def load_profile(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
