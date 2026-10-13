# -*- coding: utf-8 -*-
"""《中国血脂管理指南（2023年）》图1 的 21 格危险分层表 + 余生风险判据。

**原文来源与转录方式**：表在图1 **内部**，`lipid2023.pdf` 第 7 页（期刊页码 227）
的文字层字体编码损坏（抽出为 ``$6&9'`` 之类乱码），故由**渲染图逐字转录**。
转录件与渲染图见 ``体检分析/调研/src/lipid2023-fig1-21cell.md`` 与
``lipid2023-fig1-*.png``。出处见 :data:`checkup.rules.P_LIPID2023`。

    列 = 血清胆固醇水平分层（mmol/L）
        C1  3.1≤TC<4.1  或 1.8≤LDL-C<2.6
        C2  4.1≤TC<5.2  或 2.6≤LDL-C<3.4
        C3  5.2≤TC<7.2  或 3.4≤LDL-C<4.9
    行 = 危险因素ᵃ 个数 × 有无高血压
        ᵃ 危险因素 = 吸烟、低 HDL-C、年龄≥45（男）/≥55（女）

设计取舍（**都是引擎的选择，不是指南写的**，逐条写明）：

* TC 与 LDL-C 落进不同列时取**较重的那列**（保守）
* 两个判据都够不到 C1 时按 C1 处理（并标注为外推）
* 危险因素个数上限为 3（脚注只给了 3 项，不可能更多）
* **高血压状态未知 → 不可评估**，不默认取"无高血压"块
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

#: 危险因素ᵃ 的三项（脚注逐字：「危险因素包括吸烟、低 HDL-C、年龄≥45/55 岁（男性/女性）」）
RF_ITEMS = ("吸烟", "低HDL-C", "年龄")


@dataclass(frozen=True)
class CholStratum:
    key: str
    tc_low: float
    tc_high: float
    ldl_low: float
    ldl_high: float


#: 三个胆固醇分层，顺序 = 从轻到重
CHOL_STRATA = (
    CholStratum("C1", 3.1, 4.1, 1.8, 2.6),
    CholStratum("C2", 4.1, 5.2, 2.6, 3.4),
    CholStratum("C3", 5.2, 7.2, 3.4, 4.9),
)

#: **21 格本体**（逐字转录）。索引 = 胆固醇分层 C1/C2/C3 的顺序。
#: 无高血压块的 "0~1" 是原文的合并行 —— 0 与 1 个危险因素同判。
NO_HYPERTENSION = {
    "0~1": ("低危", "低危", "低危"),
    "2": ("低危", "低危", "中危"),
    "3": ("低危", "中危", "中危"),
}
HYPERTENSION = {
    "0": ("低危", "低危", "低危"),
    "1": ("低危", "中危", "中危"),
    "2": ("中危", "高危", "高危"),
    "3": ("高危", "高危", "高危"),
}

#: 余生风险判据（图1 下半部分，逐字）
#: 「10年ASCVD发病危险为中危且年龄<55岁者，评估余生危险。具有以下任意2个及以上
#:   危险因素者，定义为ASCVD高危人群」
LIFETIME_CRITERIA = (
    ("收缩压≥160 或舒张压≥100 mmHg", "bp"),
    ("非 HDL-C≥5.2 mmol/L（200 mg/dl）", "non_hdl"),
    ("HDL-C<1.0 mmol/L（40 mg/dl）", "hdl"),
    ("BMI≥28 kg/m²", "bmi"),
    ("吸烟", "smoke"),
)
LIFETIME_MIN_HITS = 2
LIFETIME_AGE_LIMIT = 55


def _stratum_of(tc: Optional[float], ldl: Optional[float]
                ) -> tuple[int, list[str], list[str]]:
    """定胆固醇分层。返回 ``(列索引, 依据, 提示)``。

    取 TC 与 LDL-C 给出的**较重**那列；两者都够不到 C1 时按 C1 处理并标注外推。
    """
    why: list[str] = []
    notes: list[str] = []
    idxs: list[int] = []

    if tc is not None:
        hit = next((i for i, s in enumerate(CHOL_STRATA)
                    if s.tc_low <= tc < s.tc_high), None)
        if hit is None:
            if tc < CHOL_STRATA[0].tc_low:
                hit = 0
                notes.append(f"TC {tc} 低于 C1 下界 {CHOL_STRATA[0].tc_low} "
                             f"→ 按 C1 处理（**表未覆盖，属外推**）")
            else:
                # TC ≥7.2 应已在「直接高危」那一步拦下
                notes.append(f"TC {tc} 超出 C3 上界 —— 本应已被「直接高危」"
                             f"条件①拦下，此处按 C3 处理")
                hit = len(CHOL_STRATA) - 1
        idxs.append(hit)
        why.append(f"TC {tc} → {CHOL_STRATA[hit].key}")

    if ldl is not None:
        hit = next((i for i, s in enumerate(CHOL_STRATA)
                    if s.ldl_low <= ldl < s.ldl_high), None)
        if hit is None:
            if ldl < CHOL_STRATA[0].ldl_low:
                hit = 0
                notes.append(f"LDL-C {ldl} 低于 C1 下界 {CHOL_STRATA[0].ldl_low} "
                             f"→ 按 C1 处理（**表未覆盖，属外推**）")
            else:
                notes.append(f"LDL-C {ldl} 超出 C3 上界 —— 本应已被「直接高危」"
                             f"条件①拦下，此处按 C3 处理")
                hit = len(CHOL_STRATA) - 1
        idxs.append(hit)
        why.append(f"LDL-C {ldl} → {CHOL_STRATA[hit].key}")

    if not idxs:
        return -1, [], ["TC 与 LDL-C 都缺 → 无法定胆固醇分层"]

    worst = max(idxs)
    if len(set(idxs)) > 1:
        notes.append(f"TC 与 LDL-C 指向不同列（{'、'.join(why)}）→ "
                     f"**取较重的一列** {CHOL_STRATA[worst].key}（引擎的保守选择，"
                     f"指南未规定不一致时怎么办）")
    return worst, why, notes


def risk_factors(age: Optional[float], male: bool, hdl: Optional[float],
                 smoke: bool) -> tuple[int, list[str], list[str]]:
    """数危险因素ᵃ。返回 ``(个数, 命中项, 缺项)``。

    脚注逐字：「危险因素包括吸烟、低 HDL-C、年龄≥45/55 岁（男性/女性）」
    —— 恰好三项，所以个数上限是 3。
    """
    hits: list[str] = []
    missing: list[str] = []
    if smoke:
        hits.append("吸烟")
    if hdl is None:
        missing.append("低HDL-C")
    elif hdl < 1.04:
        hits.append(f"低HDL-C（{hdl}）")
    if age is None:
        missing.append("年龄")
    elif (male and age >= 45) or (not male and age >= 55):
        hits.append(f"年龄（{age}）")
    return len(hits), hits, missing


@dataclass
class StratumResult:
    band: Optional[str]
    row_key: str
    stratum_key: str
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    status: str = "ok"          #: ok | not_assessable


def lookup(hypertension: Optional[bool], age: Optional[float], male: bool,
           tc: Optional[float], ldl: Optional[float], hdl: Optional[float],
           smoke: bool) -> StratumResult:
    """查 21 格表。

    **高血压状态未知时返回不可评估** —— 两个块的同一格结果不同
    （如 2 个危险因素 + C3：无高血压是中危、有高血压是高危），
    不能默认取"无高血压"块。
    """
    if hypertension is None:
        return StratumResult(
            None, "", "", status="not_assessable",
            notes=["高血压状态未知 → **不能查表**：无高血压块与有高血压块"
                   "在多数格子上结果不同，默认取无高血压会低估风险"])

    n_rf, hit_items, miss_rf = risk_factors(age, male, hdl, smoke)
    idx, why, notes = _stratum_of(tc, ldl)
    if idx < 0:
        return StratumResult(None, "", "", notes=notes, status="not_assessable")

    table = HYPERTENSION if hypertension else NO_HYPERTENSION
    if hypertension:
        row_key = str(min(n_rf, 3))
    else:
        row_key = "0~1" if n_rf <= 1 else str(min(n_rf, 3))
    band = table[row_key][idx]

    reasons = [f"高血压={'有' if hypertension else '无'}",
               f"危险因素 {n_rf} 个（{'、'.join(hit_items) or '无'}）",
               f"胆固醇分层 {CHOL_STRATA[idx].key}"] + why
    if miss_rf:
        notes.append(f"危险因素ᵃ 中缺 {('、'.join(miss_rf))} → "
                     f"实际个数可能更多，**结果偏乐观**")
    if n_rf > 3:
        notes.append(f"危险因素 {n_rf} 个超出表的最大行 3 → 按 3 行处理")
    notes.append("危险因素的水平应为**干预前水平**（脚注逐字）—— "
                 "若在服降脂药，应取治疗前数值")
    return StratumResult(band, row_key, CHOL_STRATA[idx].key, reasons, notes)


# ------------------------------------------------------------ 余生风险

@dataclass
class LifetimeResult:
    band: str
    hits: list[str]
    missing: list[str]
    status: str = "ok"          #: ok | evaluate | not_assessable


def lifetime_risk(sbp: Optional[float], dbp: Optional[float],
                  non_hdl: Optional[float], hdl: Optional[float],
                  bmi: Optional[float], smoke: bool) -> LifetimeResult:
    """余生风险：**任意 2 项及以上** → ASCVD 高危人群。

    只在 10 年风险为中危且年龄<55 时适用（调用方负责判这个前提）。
    """
    vals = {"bp": (sbp, dbp), "non_hdl": non_hdl, "hdl": hdl, "bmi": bmi,
            "smoke": smoke}
    hits: list[str] = []
    missing: list[str] = []
    for label, key in LIFETIME_CRITERIA:
        v = vals[key]
        if key == "smoke":
            if v:
                hits.append(label)
            continue
        if key == "bp":
            s, d = v
            if s is None or d is None:
                missing.append(label)
            elif s >= 160 or d >= 100:
                hits.append(f"{label}（{s}/{d}）")
            continue
        if v is None:
            missing.append(label)
            continue
        if key == "non_hdl" and v >= 5.2:
            hits.append(f"{label} → {v}")
        elif key == "hdl" and v < 1.0:
            hits.append(f"{label} → {v}")
        elif key == "bmi" and v >= 28:
            hits.append(f"{label} → {v}")

    if missing:
        return LifetimeResult("不可评估", hits, missing, status="not_assessable")
    band = "高危人群" if len(hits) >= LIFETIME_MIN_HITS else "非高危人群"
    return LifetimeResult(band, hits, missing)
