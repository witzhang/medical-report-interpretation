#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从观测值库产出**四张对账底稿**：coverage / states / advice / facts。

这是本轮"数据准确性"机制的核心：**报告里的事实性判断一律从这里取，agent 不
得再看着 CSV 手工数**。四张底稿各自的职责：

* ``coverage.json`` —— 缺口表与覆盖率。对照 ``guideline_2025.json``（卫健委
  2025 版基本体检项目），逐项 × 逐年判定"做过没有"，再聚合成 全覆盖/部分/缺。
* ``states.json``  —— 跨年状态分类。每个项目：位置轴（超没超机构标注的区间）、
  变化轴（是不是超出它平时的正常起伏，用引擎的 CVi 目录 + RCV 公式）、官方
  分带（血压/血糖/血脂有权威过渡带；其余走兜底规则并标注依据级别）、以及
  影像发现的**逐年尺寸序列（带检查方式）**。
* ``advice.json``  —— 机构建议摘录。主检条目 + 分科小结（眼科「散瞳查眼底」
  这类就在分科通道里），逐条带页码；再按主题抽出"复查频率""措辞"这类可对账
  的事实。
* ``facts.json``   —— 数值事实表。报告里每个被引用的数字/派生量/场景，都要能
  在这里指到；每个条目带 ``来源``（文件 + 列/行/公式）。

复用而不重写：CVi 目录、RCV 公式、eGFR、China-PAR、21 格危险分层表全部直接
import 引擎（``checkup/catalog/cvi.json`` 与 ``checkup/*.py``）。本工具**不
新增医学规则**；官方分带与兜底阈值**来源 = 引擎** ``checkup/bands.py``（P1-A0 并入），
逐条标依据强度与核实状态（``verified=False`` 不许伪装）。

用法：
    python3 gen_artifacts.py --case real|synth [--out artifacts/<case>]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from paths import ENGINE_DIR, FIXTURES, default_artifacts, guard_out  # noqa: E402

import loaders  # noqa: E402

from checkup.bands import (BAND_SETS, EGFR_SLOPE, TG胰腺炎阈值, band_label, band_of,
                           verify_consistency, 临界判据, 包年数, 早发家族史, 非HDL_C)  # noqa: E402
from checkup.judge import CVA_DEFAULT, rcv  # noqa: E402
from checkup.compose import china_par_male, egfr_ckdepi2021  # noqa: E402
from checkup.compose import LDL_TARGETS  # noqa: E402
from checkup.lipid_table import lookup as lipid_lookup  # noqa: E402
from checkup.lipid_table import lifetime_risk  # noqa: E402

CVI = json.load(open(os.path.join(ENGINE_DIR, "catalog", "cvi.json"),
                     encoding="utf-8"))["entries"]
GUIDE = json.load(open(os.path.join(HERE, "guideline_2025.json"), encoding="utf-8"))

#: 覆盖判定语义（原 bands.json「覆盖语义」；定义不变，逐字保留）
覆盖语义 = {
    "全覆盖": "该项目的全部必查子项在**每一次**体检都有记录",
    "部分覆盖": "有记录但不全：① 缺年份（该年套餐未含或未做）；② 子项被拒查；"
             "③ 子项门槛型项目的必查子项整块缺失",
    "缺": "所有年份都没有记录",
    "覆盖率": "全覆盖项数 ÷ 应查项数（四舍五入为整数百分比）",
    "子项门槛": "仅 一般检查、外科、粪便常规+隐血试验 为 true —— 这三项的'必查子项'"
             "是机构报告里逐条列出的可核查清单（腰围/臀围、直肠指检等）；"
             "实验室项的子项缺失（如肝功能未查碱性磷酸酶）属**套餐深度差异**，"
             "记入备注、不改状态",
}

#: TC「合适/边缘升高」分界（=5.2；情景推演用；同上，来源是引擎分带表）
_TC_EDGE = next(b for b in next(bs for bs in BAND_SETS
                                if bs.name == "总胆固醇").bands
                if b.label == "边缘升高").low

#: 分带来源自检（P1-A0：区间连续性 + verified 不许伪装）
_BAND_ISSUES = verify_consistency()
if _BAND_ISSUES:
    raise SystemExit("✗ 引擎分带自检失败：" + "；".join(_BAND_ISSUES))


from checkup.console import init_console  # noqa: E402
init_console()

def W(path, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print(f"  · {path}")


# ================================================================ coverage

def _obs(case):
    return case["labs"] + case["vitals"]


def _has_section(case, year, sec):
    return any(r["year"] == year and r["section"] == sec for r in _obs(case)) or \
        any(d["year"] == year and d["section"] == sec for d in case["dept"])


def _ids(case, year):
    return {r["analyte_id"] for r in _obs(case) if r["year"] == year}


def _items(case, year):
    return {r["item"] for r in _obs(case) if r["year"] == year}


def _has_modality(case, year, mod):
    return any(n["year"] == year and (n["modality"] == mod or n["item"] == mod)
               for n in case["narr"])


def _refused(case, year, names):
    return [r["item"] for r in case["vitals"]
            if r["year"] == year and r["item"] in names
            and "拒查" in (r.get("qualifier") or "") + (r.get("value_text") or "")]


def _item_year(case, item, year):
    """单项 × 单年 → (状态, 依据, 备注)。状态 ∈ full/partial/missing。"""
    det = item["检测"]
    ev, notes = [], []
    found = False

    if det.get("检查方式"):
        hits = [m for m in det["检查方式"] if _has_modality(case, year, m)]
        if hits:
            found = True
            ev.append("检查方式 " + "、".join(hits))
    if det.get("段落"):
        hits = [s for s in det["段落"] if _has_section(case, year, s)]
        if hits:
            found = True
            ev.append("科室/段落 " + "、".join(hits))
    if det.get("子项"):
        # 子项通道：段落/前缀之外的**可核查清单**（一般检查的腰围/臀围、外科的拒查…）
        ids, its = _ids(case, year), _items(case, year)
        have = []
        for sub, aliases in det["子项"].items():
            if any(a in ids or a in its for a in aliases):
                have.append(sub)
            else:
                notes.append(f"子项「{sub}」本年未见")
        if have:
            found = True
            ev.append("子项 " + "、".join(have))
    elif det.get("id前缀"):
        ids = _ids(case, year)
        hits = [i for i in ids if any(i.startswith(p) for p in det["id前缀"])]
        if hits:
            found = True
            ev.append("项目 " + "、".join(sorted(hits)[:4]) +
                      ("…" if len(hits) > 4 else ""))

    refused = []
    if det.get("拒查项"):
        refused = _refused(case, year, set(det["拒查项"]))
    if refused:
        notes.append("拒查：" + "、".join(refused))

    if not found:
        return "missing", ev, notes
    if refused:
        return "partial", ev, notes
    if item.get("子项门槛") and notes:
        return "partial", ev, notes
    return "full", ev, notes


def _applicable(item, case) -> tuple[bool, str]:
    ap = item.get("适用", {})
    age = case["age_at_last"]
    if ap.get("最小年龄") and age is not None and age < ap["最小年龄"]:
        return False, f"按年龄不适用（{age} 岁 < {ap['最小年龄']} 岁）"
    return True, ""


def build_coverage(case) -> dict:
    items_out, tally = [], {"full": 0, "partial": 0, "missing": 0, "n/a": 0}
    for item in GUIDE["项目"]:
        ok, why = _applicable(item, case)
        if not ok:
            # 适用与否之外，还要看"有没有超指引做过"（骨密度在 35 岁做过）
            done = [y for y in case["years"] if _item_year(case, item, y)[0] != "missing"]
            tally["n/a"] += 1
            items_out.append({"类别": item["类别"], "项目": item["项目"], "适用": False,
                              "不适用原因": why, "状态": "n/a",
                              "实际是否做过": done, "指引原句": item["指引原句"]})
            continue
        per_year, ev_all, notes_all = {}, {}, []
        for y in case["years"]:
            st, ev, notes = _item_year(case, item, y)
            per_year[y] = st
            ev_all[y] = ev
            for n in notes:
                if n not in notes_all:
                    notes_all.append(n)
        if all(v == "missing" for v in per_year.values()):
            status = "missing"
        elif all(v == "full" for v in per_year.values()):
            status = "full"
        else:
            status = "partial"
            if status == "partial" and any(v == "missing" for v in per_year.values()):
                miss_y = [y for y, v in per_year.items() if v == "missing"]
                notes_all.insert(0, "仅 " + "、".join(
                    y for y, v in per_year.items() if v != "missing") + " 有记录")
        tally[status] += 1
        items_out.append({"类别": item["类别"], "项目": item["项目"], "适用": True,
                          "报告用名": item.get("报告用名", []),
                          "状态": status, "按年": per_year, "依据": ev_all,
                          "备注": notes_all, "子项门槛": bool(item.get("子项门槛")),
                          "能发现": item.get("能发现", ""),
                          "指引原句": item["指引原句"]})

    total = tally["full"] + tally["partial"] + tally["missing"]
    rate = round(100 * tally["full"] / total) if total else 0
    gap = [{"项目": i["项目"], "类别": i["类别"], "能发现": i["能发现"]}
           for i in items_out if i["状态"] == "missing"]
    partial = [{"项目": i["项目"], "按年": i["按年"], "备注": i["备注"],
                "指引原句": i["指引原句"]} for i in items_out if i["状态"] == "partial"]

    # 「超出指引要求、而且做得合适」：指引里不适用（或不在指引内）而实际做过的
    extra = []
    for i in items_out:
        if i["状态"] == "n/a" and i["实际是否做过"]:
            extra.append({"项目": i["项目"], "说明": i["不适用原因"],
                          "做过的年份": i["实际是否做过"]})
    mods = {}
    for n in case["narr"]:
        if n["modality"]:
            mods.setdefault(n["modality"], set()).add(n["item"])
    guide_mods = {"心电图", "彩超", "CT", "骨密度", "肺功能", "人体成分"}
    for m, its in sorted(mods.items()):
        for it in sorted(its):
            if m == "彩超" and it in ("肝脏", "胆囊", "胰腺", "脾脏", "双肾"):
                continue      # 腹部超声 = 指引内
            if m == "CT" and it in ("肺部CT",):
                continue      # 胸部 CT = 替代指引的胸部正位片
            extra.append({"项目": f"{it}（{m}）", "说明": "不在基本体检项目内，属加做",
                          "做过的年份": sorted({n["year"] for n in case["narr"]
                                            if n["modality"] == m and n["item"] == it})})
    return {"case": case["case"], "依据": GUIDE["来源"], "适用规则": GUIDE["适用规则"],
            "语义": 覆盖语义,
            "应查": total, "全覆盖": tally["full"], "部分覆盖": tally["partial"],
            "缺": tally["missing"], "不适用": tally["n/a"], "覆盖率": rate,
            "逐项": items_out, "缺口明细": gap, "部分覆盖明细": partial,
            "超出指引要求": extra}


# ================================================================ states

#: 报告里引用的关键项目（项目名 → 归一 id）。顺序 = 报告出现顺序。
KEY = [
    ("甘油三酯", "CHEM.甘油三酯"), ("总胆固醇", "CHEM.总胆固醇"),
    ("坏胆固醇", "CHEM.低密度脂蛋白胆固醇"), ("好胆固醇", "CHEM.高密度脂蛋白胆固醇"),
    ("非好胆固醇", None), ("空腹血糖", "CHEM.葡萄糖"), ("糖化血红蛋白", "CHEM.糖化血红蛋白"),
    ("肌酐", "CHEM.肌酐"), ("肌酸激酶", "CHEM.肌酸激酶"),
    ("谷氨酰转肽酶", "CHEM.谷氨酰转肽酶"), ("谷丙转氨酶", "CHEM.丙氨酸氨基转移酶"),
    ("谷草转氨酶", "CHEM.天门冬氨基转移酶"),
    ("间接胆红素", "CHEM.间接胆红素"), ("总胆红素", "CHEM.总胆红素"),
    ("直接胆红素", "CHEM.直接胆红素"), ("尿酸", "CHEM.尿酸"),
    ("PSA", "IMM.总前列腺特异性抗原"), ("白细胞", "BLD.白细胞"),
    ("血红蛋白", "BLD.血红蛋白浓度"), ("血小板", "BLD.血小板"),
    ("TSH", "CHEM.促甲状腺激素"), ("收缩压", "VITAL.收缩压"), ("舒张压", "VITAL.舒张压"),
    ("体重", "VITAL.体重"), ("腰围", "VITAL.腰围"), ("臀围", "VITAL.臀围"),
    ("身高", "VITAL.身高"), ("体重指数", "VITAL.体重指数"), ("脉搏", "VITAL.脉搏"),
    # —— 贫血/铁代谢（血红蛋白之外的红系三件套 + 铁）
    ("红细胞计数", "BLD.红细胞"), ("红细胞压积", "BLD.血细胞压积"),
    ("平均红细胞HGB浓度", "BLD.平均红细胞血红蛋白浓度"), ("铁", "CHEM.铁"),
    # —— 维生素D / 肝酶 / 甲功明细 / 肿瘤标志物（报告要引用时的底稿值）
    ("25羟维生素D", "CHEM.25羟维生素D"), ("碱性磷酸酶", "CHEM.碱性磷酸酶"),
    ("游离三碘甲状腺原氨酸", "CHEM.游离三碘甲状腺原氨酸"),
    ("游离甲状腺素", "CHEM.游离甲状腺素"), ("甲胎蛋白", "IMM.甲胎蛋白"),
]


def _series(case):
    """id → {year: (值, 单位, ref_low, ref_high, ref_op)}"""
    out: dict = {}
    for r in _obs(case):
        try:
            v = float(r["value"]) if r["value"] not in (None, "") else None
        except ValueError:
            v = None
        out.setdefault(r["analyte_id"], {})[r["year"]] = {
            "值": v, "单位": r["unit"], "ref_low": r["ref_low"],
            "ref_high": r["ref_high"], "ref_op": r["ref_op"], "ref": r["ref"],
            "定性": r.get("qualifier", ""), "换算": r.get("换算"),
            "页": r.get("页", "")}
    return out


def _position(rec):
    """位置轴：超过机构标注的区间吗 → normal/abnormal/unknown/undetermined。"""
    v = rec["值"]
    if v is None:
        return "not_applicable" if rec["定性"] else "unknown"
    lo, hi, op = rec["ref_low"], rec["ref_high"], rec["ref_op"]
    if op == "<":
        return "normal" if v < hi else "abnormal"
    if op == ">":
        return "normal" if v > lo else "abnormal"
    if op == "range":
        return "normal" if lo <= v <= hi else "abnormal"
    return "unknown"


def _robust(case, aid, series):
    e = CVI.get(aid)
    vals = {y: series[y]["值"] for y in series if series[y]["值"] is not None}
    ys = [y for y in case["years"] if y in vals]
    if not e or len(ys) < 2:
        return None
    a, b = vals[ys[0]], vals[ys[-1]]
    if a == 0:
        return None
    chg = 100 * (b - a) / a
    lo, hi = rcv(e["cvi_low"]), rcv(e["cvi_high"])
    band = ("稳健超出" if abs(chg) > hi else
            "稳健在内" if abs(chg) < lo else "取决于CVi取值")
    return {"变化%": round(chg, 1), "RCV区间%": [round(lo, 1), round(hi, 1)],
            "RCV中值%": round(rcv(e["cvi"]), 1), "稳健性": band,
            "CVi": e["cvi"], "CVi区间": [e["cvi_low"], e["cvi_high"]],
            "研究数": e["n_studies"], "来源": "catalog/cvi.json + judge.rcv"}


def _band_of(name, rec, series_all, sex=None):
    """官方分带（P1-A0：**来源 = 引擎** ``checkup.bands``，不再读本地 bands.json）。

    返回 ``{段/强度/核实/依据/适用/备注}``（照抄引擎字段，不许只抄标签）。
    """
    return band_of(name, rec.get("值"), sex)


def _classify(case, name, aid, series, change):
    ys = [y for y in case["years"] if y in series and series[y]["值"] is not None]
    if not ys:
        return "未评估", f"历次都没有「{name}」的记录"
    pos = {y: _position(series[y]) for y in ys}
    missing = [y for y in case["years"] if y not in ys]
    abn = [y for y in ys if pos[y] == "abnormal"]
    band_hit_ = [y for y in ys if (_band_of(name, series[y], series) or {}).get("段")
                 in ("糖尿病前期", "正常高值", "边缘升高", "降低", "升高", "高尿酸血症",
                     "重点关注段")]
    if len(ys) == 1:
        return "单点", "只有一次记录，不构成趋势"
    if missing and ys:
        if abn:
            return "断档·持续异常", f"有记录的年份异常（{'、'.join(abn)}），缺 {'、'.join(missing)}"
        return "断档", f"缺 {'、'.join(missing)} 年，跨该年的比较不可靠"
    if len(abn) == len(ys):
        return "持续异常", f"{'、'.join(ys)} 全部超出区间"
    if abn and ys[-1] not in abn:
        return "曾异常已回落", f"{'、'.join(abn)} 异常，{ys[-1]} 回到区间内"
    if not abn and ys[-1] in band_hit_:
        return "临界上升", f"{ys[-1]} 落在官方过渡带（{_band_of(name, series[ys[-1]], series)['段']}）"
    if not abn:
        last = series[ys[-1]]
        no_ref = all(series[y]["ref_high"] is None and series[y]["ref_low"] is None for y in ys)
        if no_ref and not _band_of(name, last, series):
            return "记录类（无参考区间）", "这项没有区间可判位，只看记录与趋势"
        # 顺序有讲究：先判"变化是否真的发生"（噪声轴），再判"离临界线多近"（余量轴）。
        # 反过来会把"位置正常但真的在变"误标成临界，丢掉更重要的信息。
        if change and change["稳健性"] == "稳健超出":
            return "持续正常·变化超出噪声", f"位置一直正常，但 {change['变化%']}% 超出 RCV 上界"
        if change and change["稳健性"] == "取决于CVi取值":
            return "持续正常·变化取决于噪声取值", f"变化 {change['变化%']}% 落在 RCV 区间内"
        if last["ref_high"] and pos[ys[-1]] == "normal":
            margin = 100 * (last["ref_high"] - last["值"]) / last["ref_high"]
            mono = all(series[ys[i + 1]]["值"] >= series[ys[i]]["值"]
                       for i in range(len(ys) - 1)) or \
                   all(series[ys[i + 1]]["值"] <= series[ys[i]]["值"]
                       for i in range(len(ys) - 1))
            th = 临界判据["贴线余量"]
            th_m = 临界判据["单调余量"]
            if margin < th:
                return "临界上升", (f"贴线：距上限仅 {margin:.1f}%（<{th}% 即入选，不论方向；"
                                    "依据级别=弱，报告须标注）")
            if margin < th_m and mono:
                return "临界上升", (f"未超线但距上限 {margin:.1f}%（<{th_m}%）且方向一致；"
                                    "依据级别=弱，报告须标注）")
        return "持续正常", "位置一直正常，变化也在噪声内"
    if ys[0] in abn:
        return "持续异常", "早期即异常"
    return "新发异常", f"{abn[0]} 首次异常（此前正常）"


def _first_last(s):
    """首末变化%（与 CVi 无关的原始比值，供报告引用'涨了 X%'）。"""
    ys = [y for y in sorted(s) if s[y]["值"] is not None]
    if len(ys) < 2 or not s[ys[0]]["值"]:
        return None
    return round(100 * (s[ys[-1]]["值"] - s[ys[0]]["值"]) / s[ys[0]]["值"], 1)


#: 报告分级的映射表（§2.7；**脚本产出**，agent 不许心算）
#: 🔴 已达诊断或前期标准，需尽快就诊（≠ 官方 A/B 类危急值）
#: 🟡 需要干预或定期复查 ｜ 🟢 正常或轻微，年度复查即可 ｜ ⚪ 未评估（是"不知道"，不是"没问题"）
_LEVEL_RULES = [
    ("🔴", ("高血压", "糖尿病"), "已达诊断标准（官方分带），需尽快就诊"),
    ("🔴", ("糖尿病前期",), "已达前期标准（官方分带）"),
    ("🟡", ("正常高值", "重点关注段", "边缘升高", "升高", "降低", "高尿酸血症",
            "超重", "肥胖"),
     "落在官方过渡带/异常档，需干预或定期复查"),
]


def _suggest_level(rec, band, state) -> tuple:
    """→ (级别, 判据)。纯脚本口径；报告若调档必须写明理由（validate_report 会核）。"""
    ys = sorted(rec["逐年"])
    if not ys or state in ("未评估", "断档"):
        return "⚪", "未评估（是「不知道」，不是「没问题」）"
    if state == "记录类" or state.startswith("记录类"):
        return "🟢", "记录类（无参考区间）：数值本身不判定，随卡片按需呈现"
    last = rec["逐年"][ys[-1]]
    pos = last.get("位置")
    if band:
        for lv, labels, why in _LEVEL_RULES:
            if band in labels:
                return lv, f"{why}（分带：{band}）"
    if pos == "abnormal":
        return "🟡", "位置轴超出参考区间，需干预或定期复查"
    if state == "临界上升":
        return "🟡", "位置未越线但落在官方过渡带/临界判据（临界上升）"
    if "变化超出噪声" in state or "变化取决于噪声取值" in state:
        return "🟡", "位置正常但变化超出/取决于噪声阈值 → 待核查（不是「正常」）"
    if state in ("持续异常", "新发异常"):
        return "🟡", f"跨年状态：{state}"
    if last.get("换算"):
        pass
    return "🟢", "位置正常且变化在噪声内，年度复查即可"


#: 影像类"超出测量误差"的经验阈值（**弱依据**，报告引用须标注——同"临界判据"的纪律）
IMG_SIZE_STEP_MM = 2.0     # 同方式、相邻两次之间：≥2 mm 才算真变化
IMG_SIZE_REL = 20.0        # 且相对增幅 ≥20%
_IMG_MALIGNANT = re.compile(r"(边界不清|边界欠清|形态不规则|形态欠规则|占位|可疑恶性|"
                            r"(?<!未见)(?<!无)钙化|异常血流信号)")
_IMG_EXECUTED = re.compile(r"(增强CT|增强 CT|增强MRI|增强 MRI|已复查|术后|活检|病理|穿刺)")
_GRADE_RE = re.compile(r"(?:TI-?RADS|C-?TIRADS|BI-?RADS|Lung-?RADS)\s*([0-9])")


#: 发现名 → 关键词（用于从整页小结里挑出"本发现相关从句"，以及匹配主检条目）
_IMG_KEYWORDS = {
    "甲状腺右叶结节": ("甲状腺", "结节"), "甲状腺左叶结节": ("甲状腺", "结节"),
    "肝血管瘤": ("血管瘤",), "胆囊息肉": ("息肉",), "肝内低密度影": ("低密度",),
    "肺小结节": ("肺", "结节"), "骨密度测值": ("骨密度",),
    "前列腺（彩超）": ("前列腺",), "脂肪肝（程度）": ("脂肪肝",),
}


def _img_keywords(name: str) -> tuple:
    base = (name or "").split("（")[0]
    for k, v in _IMG_KEYWORDS.items():
        if k.split("（")[0] == base:
            return v
    return (base[:2],) if base else ()


def _relevant_clauses(name: str, text: str) -> str:
    """整页小结里只保留与本发现相关的从句（CT 小结常把肺与肝写在一起，不能整段照搬）。"""
    kws = _img_keywords(name)
    out = []
    for clause in re.split(r"[/／。；;\n]", str(text or "")):
        if any(k in clause for k in kws):
            out.append(clause.strip())
    return "；".join(o for o in out if o)


def _grade(s):
    m = _GRADE_RE.search(str(s or ""))
    return int(m.group(1)) if m else None


def _mm(size, unit):
    try:
        v = float(size)
    except (TypeError, ValueError):
        return None
    return v * 10 if (unit or "").lower() == "cm" else v


def _suggest_imaging_level(entry, case):
    """影像发现的**报告级别**（脚本口径；P0-1b）。

    规则表（与 report-form-v6.4 的级别定义一致）：
      🔴 形态描述含恶性征象
      🟡 机构写过一次性「进一步检查」建议且至今未见执行 · 近次仍增大（≥2 mm 且 ≥20%）
         · 分级升高且未回落
      🟢 近次未继续增大（含"曾长过又稳住"）· 首见且留基线
      ⚪ 不可比（无检查方式/无单位）
    ⚠️ 尺寸阈值是**经验值（弱依据）**；分级/形态优先于 1–2 mm 的尺寸波动。
    """
    yrs = sorted(entry["逐年"])
    kws = _img_keywords(entry["发现"])
    blob = " ".join(
        " ".join(str(v.get(k) or "") for k in ("测量原文", "分级", "原文", "小结"))
        for v in entry["逐年"].values())
    # 机构主检条目（同一发现的原文）也算进来
    for f in case.get("findings", []):
        t = (f.get("title", "") or "") + (f.get("detail", "") or "")
        if any(k in t for k in kws):
            blob += " " + t
    # 否定式描述先清掉（"未见钙化及异常血流信号"不能当恶性征象）
    blob = re.sub(r"(未见|未及|未见明显|无)[^。，；\n]{0,14}", " ", blob)

    if _IMG_MALIGNANT.search(blob):
        return "🔴", "形态描述含恶性征象提示（边界/形态/钙化/血流）——需专科评估"
    adv = None
    for pat in ("建议增强[^。\n]{0,8}检查", "建议[^。\n]{0,6}进一步检查", "性质？"):
        adv = re.search(pat, blob)
        if adv:
            break
    if adv and not _IMG_EXECUTED.search(blob):
        return "🟡", f"机构曾写「{adv.group(0)}」→ 至今**未见执行**（拖了 {len(yrs)} 年记录）"
    if not entry.get("检查方式") or not any((v.get("单位") or "")
                                            for v in entry["逐年"].values()):
        return "⚪", "不可比（无检查方式或无单位/测量部位）——只记录、不判定"

    mm = [(y, _mm(v.get("尺寸"), v.get("单位"))) for y, v in
          ((y, entry["逐年"][y]) for y in yrs)]
    vals = [(y, v) for y, v in mm if v is not None]
    grades = [(y, _grade(entry["逐年"][y].get("分级"))) for y in yrs]
    gvals = [g for _, g in grades if g is not None]
    grade_up = bool(gvals) and gvals[-1] > gvals[0]

    if len(vals) >= 2:
        y0, a = vals[-2]
        y1, b = vals[-1]
        if (b - a) >= IMG_SIZE_STEP_MM and a > 0 and (b - a) / a * 100 >= IMG_SIZE_REL:
            return "🟡", (f"{y1} 较 {y0} 增大 {b - a:.1f} mm（≥{IMG_SIZE_STEP_MM:g} mm 且 "
                          f"≥{IMG_SIZE_REL:g}%，超出测量误差）→ 近次仍在增大")
        hist = (f"（曾从 {vals[0][1]:g} 增至 {max(v for _, v in vals):g} mm，"
                f"{'分级曾升高' if grade_up else '近次稳住'}）"
                if vals[-1][1] > vals[0][1] else "")
        return "🟢", f"近次未继续增大{hist}；按机构给的复查节奏跟即可"
    if grade_up:
        return "🟡", "分级较首次升高且未回落（临床决策依据是分级，不是 1–2 mm 波动）"
    if len(vals) == 1:
        return "🟢", "首见 → 留下基线，按机构时间窗复查"
    return "🟢", "无进展记录，按机构节奏复查"


def _bp_band(sbp, dbp):
    """血压成对分段：取收缩压/舒张压中**较重**的一段（分带来源 = 引擎，P1-A0）。"""
    def seg(v, name):
        return band_label(name, v) or "?"
    a, b = seg(sbp, "收缩压"), seg(dbp, "舒张压")
    order = ["正常", "正常高值", "重点关注段", "高血压"]
    return {"段": max((a, b), key=lambda x: order.index(x) if x in order else 0),
            "收缩压段": a, "舒张压段": b}


def build_states(case) -> dict:
    series = _series(case)
    out = []
    for name, aid in KEY:
        if aid is None:      # 非好胆固醇 = TC − HDL，派生
            tc, hdl = series.get("CHEM.总胆固醇", {}), series.get("CHEM.高密度脂蛋白胆固醇", {})
            s = {}
            for y in case["years"]:
                if y in tc and y in hdl and tc[y]["值"] is not None and hdl[y]["值"] is not None:
                    s[y] = {"值": round(tc[y]["值"] - hdl[y]["值"], 2), "单位": "mmol/L",
                            "ref_low": None, "ref_high": 非HDL_C["治疗目标基数"],
                            "ref_op": "<", "ref": f"<{非HDL_C['治疗目标基数']}",
                            "定性": "", "换算": None}
            rec = {"项目": name, "analyte_id": "派生.非好胆固醇", "逐年": s,
                   "公式": "总胆固醇 − 好胆固醇", "来源": "facts 派生"}
            rec["变化"] = None
            rec["状态分类"] = "派生项"
            _lv, _lv_why = _suggest_level(rec, None, "派生项")
            rec["报告级别"], rec["级别判据"] = _lv, _lv_why
            rec["判据"] = ("= 总胆固醇 − 好胆固醇；上限 4.2 属「要治到什么程度」，"
                          "4.9 才是『升高』分档界线（两者不是一回事）")
            out.append(rec)
            continue
        s = series.get(aid, {})
        change = _robust(case, aid, s)
        states = _classify(case, name, aid, s, change)
        def _margin(rec_y):
            v, hi, lo, op = rec_y["值"], rec_y["ref_high"], rec_y["ref_low"], rec_y["ref_op"]
            if v is None:
                return None
            if op in ("range", "<") and hi:
                return round(100 * (hi - v) / hi, 1)
            if op == ">" and lo:
                return round(100 * (v - lo) / lo, 1)
            return None
        rec = {"项目": name, "analyte_id": aid,
               "逐年": {y: {"值": s[y]["值"], "单位": s[y]["单位"],
                            "参考区间": s[y]["ref"], "位置": _position(s[y]),
                            "分带": (_band_of(name, s[y], s) or {}).get("段"),
                            "余量%": _margin(s[y]), "换算": s[y]["换算"]} for y in s},
               "首末变化%": _first_last(s),
               "变化": change, "状态分类": states[0], "判据": states[1]}
        _lv, _lv_why = _suggest_level(rec, rec["逐年"][sorted(rec["逐年"])[-1]]["分带"]
                                      if rec["逐年"] else None, states[0])
        rec["报告级别"], rec["级别判据"] = _lv, _lv_why
        out.append(rec)

    # ---- 血压：收缩压/舒张压必须成对判定（取较重的一段）
    sbp, dbp = series.get("VITAL.收缩压", {}), series.get("VITAL.舒张压", {})
    bp = {}
    for y in case["years"]:
        if y in sbp and y in dbp and sbp[y]["值"] is not None and dbp[y]["值"] is not None:
            bp[y] = {"值": f"{sbp[y]['值']:.0f}/{dbp[y]['值']:.0f}", "单位": "mmHg",
                     "参考区间": "机构只写「正常血压」，未给区间", "位置": "unknown",
                     "分带": _bp_band(sbp[y]["值"], dbp[y]["值"])["段"], "换算": None}
    if bp:
        hit = [y for y in bp if bp[y]["分带"] == "高血压"]
        mark = [y for y in bp if bp[y]["分带"] in ("重点关注段", "正常高值")]
        ys = sorted(bp)
        if hit and ys[-1] in hit:
            state, why = "新发异常", f"{ys[-1]} 达到高血压段（{bp[ys[-1]]['值']}）"
        elif hit:
            state, why = "曾异常已回落", f"{'、'.join(hit)} 达高血压段，之后回到线下"
        elif mark:
            state, why = ("临界上升",
                          f"{'、'.join(mark)} 落在官方过渡带（{bp[mark[-1]]['分带']}）；"
                          "未越高血压线，但决定血脂分层的『高血压=有/无』前提")
        else:
            state, why = "持续正常", "历次都在正常段"
        _bp_rec = {"逐年": bp}
        # 级别取「最重的一年」：高血压年优先，其次过渡带年（照基准：139/84 也入黄）
        _bp_band_lv = (bp[hit[-1]]["分带"] if hit else
                       (bp[mark[-1]]["分带"] if mark else "正常"))
        _lv, _lv_why = _suggest_level(_bp_rec, _bp_band_lv, state)
        out.append({"项目": "血压", "analyte_id": "VITAL.收缩压+VITAL.舒张压",
                    "逐年": bp, "变化": None, "状态分类": state, "判据": why,
                    "报告级别": _lv, "级别判据": _lv_why,
                    "依据": "；".join(dict.fromkeys(
                        p.source_title for p in
                        next(b for b in BAND_SETS if b.name == "收缩压").provenance))})

    # ---- 影像发现（必须带检查方式；不同方式不得互相推断）
    img = {}
    for n in case["narr"]:
        key = f"{n['item']}｜{n['side'] or '—'}｜{n['modality']}"
        img.setdefault(key, {"检查项目": n["item"], "侧别": n["side"] or "—",
                             "检查方式": n["modality"], "逐年": {}})
        if n["raw"] or n["summary"]:
            prev = img[key]["逐年"].get(n["year"], {})
            img[key]["逐年"][n["year"]] = {
                "测量原文": n["raw"], "尺寸": n["value"], "单位": n["unit"],
                "分级": f"{n['grade_system']}{n['grade']}" if n["grade"] else prev.get("分级", ""),
                "小结": n["summary"]}
    # ---- 派生状态：eGFR 斜率、血压/血糖分带
    derived = []
    ages = {y: (case["meta"][y].get("age") if case["case"] == "real"
                else case["meta"][y].get("age")) for y in case["years"]}
    eg = {}
    for y in case["years"]:
        scr = series.get("CHEM.肌酐", {}).get(y, {}).get("值")
        if scr is not None and ages.get(y):
            eg[y] = round(egfr_ckdepi2021(scr, ages[y], case["sex"] == "男"), 1)
    if len(eg) >= 2:
        ys = sorted(eg)
        months = _months_between(case, ys[0], ys[-1])
        slope = (eg[ys[-1]] - eg[ys[0]]) / (months / 12) if months else None
        derived.append({"项目": "肾脏过滤能力（eGFR）", "逐年": eg,
                        "年均变化": round(slope, 2) if slope is not None else None,
                        "参照": {"自然下降": list(EGFR_SLOPE["自然下降"]),
                               "快速进展线": EGFR_SLOPE["快速进展"],
                               "依据": EGFR_SLOPE["provenance"].source_title,
                               "核实": EGFR_SLOPE["provenance"].verified},
                        "判据": ("健康成人自然下降 0.4～1.0/年；KDIGO 快速进展 >5/年"
                                 "（依据级别 强：队列研究 + KDIGO）"),
                        "来源": "checkup.compose.egfr_ckdepi2021（引擎函数，直接复用）"})
    # 无测量值、无小结的条目（如"检查方式证据"）不进影像发现 —— 空行会
    # 让报告层误以为有一条"没内容的发现"。
    out_img = [v for _, v in sorted(img.items()) if v["逐年"]]

    # ---- 影像台账：把"逐年尺寸"按**报告讨论的发现**归行（口径声明 + 值取自观测值库）
    ledger = []
    def _ledger(name, modality, pick, note=""):
        rows = [n for n in case["narr"] if n["modality"] == modality and pick(n)]
        yr = {}
        for n in rows:
            if n["raw"] or n["value"] is not None:
                yr[n["year"]] = {"测量原文": n["raw"] or n["summary"],
                                 "尺寸": n["value"], "单位": n["unit"],
                                 "分级": f"{n['grade_system']}{n['grade']}" if n["grade"] else "",
                                 "原文": n["verbatim"],
                                 "小结": _relevant_clauses(name, n["summary"])}
        if yr:
            ledger.append({"发现": name, "检查方式": modality, "逐年": yr,
                           "备注": note, "来源": "narrative（引擎叙述通道）"})
    # —— 两种版式分派（按数据特征，不看机构名）：
    #   B）段名即检查方式（「彩超」「CT」段）→ NX-001/合成案的归行口；
    #   A）**项目名里带检查方式**（「甲状腺彩超」「【CT-平扫】胸部」），没有
    #      「彩超/CT」这类段名 → 段名式版式（NX-001/合成案），走 _ledger_by_section。
    if any(n["item"] in ("甲状腺彩超", "乳腺彩超", "【CT-平扫】胸部")
           for n in case["narr"]):
        _ledger_named(case, ledger)
    else:
        _ledger_nx001(ledger, _ledger)

    # 脂肪肝程度（在肝脏小结里，不在尺寸里）——仅 B 式有「肝脏」合并项
    fatty = [n for n in case["narr"] if n["item"] == "肝脏" and "脂肪肝" in n["summary"]]
    if fatty:
        ledger.append({"发现": "脂肪肝（程度）", "检查方式": "彩超",
                       "逐年": {n["year"]: {"小结": n["summary"]} for n in fatty},
                       "备注": "", "来源": "narrative 小结"})

    # ---- P0-1b：影像级别落到脚本（不再由 agent 心算；reconcile S10 会用它对账）
    _IMG_ORDER = {"🔴": 0, "🟡": 1, "🟢": 2, "⚪": 3}
    for e in ledger:
        lv, why = _suggest_imaging_level(e, case)
        e["报告级别"], e["级别判据"] = lv, why
        e["级别依据强度"] = "弱（尺寸阈值 = 经验值，分级/形态优先）" if "mm" in why else "强"
    ledger.sort(key=lambda e: (_IMG_ORDER.get(e["报告级别"], 9), e["发现"]))
    return {"case": case["case"], "项目状态": out, "影像发现": out_img,
            "影像台账": ledger, "派生状态": derived,
            "分带来源": "checkup.bands（引擎；P1-A0 并入后报告层直接引用引擎产物）",
            "分带依据": {b.name: {
                "依据": "；".join(dict.fromkeys(p.source_title for p in b.provenance)),
                "核实": all(p.verified for p in b.provenance),
                "强度": b.strength,
                "备注": b.note} for b in BAND_SETS}}


def _ledger_nx001(ledger, _ledger):
    """B 式（段名即检查方式）归行口 —— NX-001/合成案。"""
    _ledger("甲状腺右叶结节", "彩超", lambda n: n["item"] == "甲状腺" and n["side"] == "右")
    _ledger("甲状腺左叶结节", "彩超", lambda n: n["item"] == "甲状腺" and n["side"] == "左")
    _ledger("肝血管瘤", "彩超", lambda n: n["item"] == "肝脏",
            note="腹部超声；部分年份未标侧别，另一年标了侧别")
    _ledger("胆囊息肉", "彩超", lambda n: n["item"] == "胆囊")
    _ledger("肝内低密度影", "CT", lambda n: n["modality"] == "CT" and (n["unit"] == "cm"))
    _ledger("肺小结节", "CT", lambda n: n["modality"] == "CT" and (n["unit"] == "mm"))
    _ledger("骨密度测值", "骨密度", lambda n: n["modality"] == "骨密度",
            note="原始报告无单位、无测量部位——两次可能不是同一种测量方法，数值不可比")
    _ledger("前列腺（彩超）", "彩超", lambda n: n["item"] == "前列腺")
    _ledger("腹部超声（肝/胆/胰/脾/肾）", "彩超",
            lambda n: n["item"] in ("肝脏", "胆囊", "胰腺", "脾脏", "双肾") and not n["raw"],
            note="未见异常的定性行")


def _ledger_named(case, ledger):
    """A 式（项目名即检查方式）归行口 —— 项目名式版式（某机构 NXYD）。

    与 B 式的差别：规格以**项目名**区分（甲状腺彩超/乳腺彩超/肝/…），
    且定性年份（无测量值）也要入格 —— 只留"有尺寸"的年份会把
    「2026 乳腺未见占位」这类结论整年丢掉。同年**同名**多条后者覆盖
    （汇总页在前、单独报告页在后 → 后者更完整），同年多名并列成句。
    """
    def _row(name, modality, picks, note=""):
        # narrative 表**每个测量值一行**：同一年同一项目会有多条记录行。
        # 描述取该项目**后出现**的那条（汇总页在前、单独报告页在后 → 后者更全），
        # 测量值逐条合并 —— 只留"最后一行"会把 5.4×4.9×4.4 cm 这类主尺寸挤掉。
        by_year: dict = {}
        for n in case["narr"]:
            if n["modality"] == modality and picks(n):
                by_year.setdefault(n["year"], {}).setdefault(n["item"], []).append(n)
        yr: dict = {}
        for y, items in by_year.items():
            multi = len(items) > 1
            cell: dict = {}
            texts, raws, units = [], [], []
            for itname, rows in items.items():
                n = rows[-1]
                if n["verbatim"]:
                    texts.append(f"{itname}：{n['verbatim']}" if multi else n["verbatim"])
                for r in rows:
                    if r["raw"]:
                        raws.append(r["raw"])
                        if r["value"] is not None:
                            _sizes = cell.setdefault("_sizes", [])
                            _sizes.append(r["value"])
                            units.append(r["unit"])
                if n["grade"]:
                    cell["分级"] = f"{n['grade_system']}{n['grade']}"
                if n["summary"]:
                    cell["小结"] = (cell.get("小结", "") + "；" + n["summary"]).strip("；")
            if texts:
                cell["原文"] = "；".join(texts)
            if raws:
                cell["测量原文"] = "；".join(dict.fromkeys(raws))
            if cell.get("_sizes"):
                cell["尺寸"] = max(cell.pop("_sizes"))
                cell["单位"] = next((u for u in units if u), "")
            if cell:
                yr[y] = cell
        if yr:
            ledger.append({"发现": name, "检查方式": modality, "逐年": yr,
                           "备注": note, "来源": "narrative（引擎叙述通道）"})

    _row("乳腺（彩超）", "彩超", lambda n: "乳腺" in n["item"],
         note="2024：左乳低回声结节（主检 p4 #1，BI-RADS 3类，建议 3-6 个月复查）；"
              "2026：哺乳期声像改变（主检 p5 #9）")
    _row("甲状腺（彩超）", "彩超", lambda n: "甲状腺" in n["item"],
         note="2024：回声略增粗（主检 p4 #2）；2026：弥漫性病变（主检 p5 #8）")
    _row("腹部超声（肝/胆/胰/脾/肾/门静脉）", "彩超",
         lambda n: n["item"] in ("肝", "胆", "胰", "脾", "双肾", "门静脉"),
         note="2024 含门静脉；2026 报告仅肝/胆/胰/脾/肾")
    _row("子宫附件（彩超）", "彩超",
         lambda n: n["item"] in ("子宫附件B超", "经阴道扫查", "妇科经腹部检查"))
    _row("心脏（彩超）", "彩超", lambda n: "多普勒" in n["item"],
         note="2026 首次做心脏彩超：二尖瓣返流（轻度），主检 p5 #6")
    _row("肺结节（CT）", "CT", lambda n: n["modality"] == "CT",
         note="2026 首次做胸部 CT；2024 无胸部影像（不评「新增」）")
    _row("宫颈液基细胞学（TCT）", "TCT", lambda n: True,
         note="2024 做过：未见上皮内病变及恶性病变（NILM）；2026 未做")


def _months_between(case, y0, y1):
    def d(y):
        if case["case"] == "real":
            return case["meta"][y]["exam_date"]
        return case["meta"][y]["exam_date"]
    from datetime import date
    a = date.fromisoformat(d(y0))
    b = date.fromisoformat(d(y1))
    return (b - a).days * 12 / 365.25


# ================================================================ advice

TOPICS = {
    "甲状腺超声复查": {"keys": ["甲状腺"], "freq": True, "源": ["findings", "dept"]},
    "肝内低密度影": {"keys": ["低密度"], "flags": ["增强"], "源": ["findings", "dept", "narr"]},
    "肝血管瘤": {"keys": ["肝血管瘤"], "freq": True, "源": ["findings", "dept"]},
    "胆囊息肉": {"keys": ["胆囊息肉"], "源": ["findings"]},
    "脂肪肝": {"keys": ["脂肪肝"], "源": ["findings", "narr"]},
    "前列腺特异抗原": {"keys": ["前列腺特异性抗原", "PSA"], "flags": ["增高"],
                 "源": ["findings"]},
    "肺CT复查": {"keys": ["肺部CT", "胸部CT", "双肺", "肺小结节", "肺结节", "左肺"],
              "freq": True, "源": ["findings", "narr"]},
    "血脂复查": {"keys": ["甘油三酯", "血脂", "高密度脂蛋白"], "源": ["findings"]},
    "血压": {"keys": ["血压"], "源": ["findings"]},
    "眼科": {"keys": ["眼科", "屈光", "散瞳", "视力"], "freq": True,
           "源": ["findings", "dept"]},
    "耳鼻喉": {"keys": ["耳鼻喉", "过敏性鼻炎", "鼻中隔"], "源": ["findings", "dept"]},
    "骨密度": {"keys": ["骨密度"], "源": ["findings", "narr"]},
    "尿酸": {"keys": ["尿酸"], "源": ["findings"]},
    "血糖": {"keys": ["血糖", "糖化"], "源": ["findings"]},
    # —— 通用补充（报告要按主题落机构原话的项；无数据时自动不出现）
    "乳腺复查": {"keys": ["乳腺", "左乳"], "freq": True, "源": ["findings", "narr"]},
    "维生素D": {"keys": ["维生素D", "25羟"], "源": ["findings"]},
    "甲状腺功能复查": {"keys": ["促甲状腺激素", "甲功", "甲状腺功能"], "源": ["findings"]},
    "血红蛋白与红系": {"keys": ["血红蛋白", "血红细胞"], "源": ["findings"]},
    "血清铁": {"keys": ["血清铁"], "源": ["findings"]},
    "碱性磷酸酶": {"keys": ["碱性磷酸酶"], "源": ["findings"]},
    "甲胎蛋白复查": {"keys": ["甲胎蛋白"], "源": ["findings"]},
    "尿隐血复查": {"keys": ["尿隐血"], "源": ["findings"]},
    "宫颈癌筛查": {"keys": ["液基", "TCT", "宫颈"], "源": ["findings", "narr"]},
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _freq(text):
    t = _norm(text)
    for pat, lab in (("每半年", "每半年"), ("半年复查", "每半年"),
                     ("12个月", "12个月"), ("每12个月", "12个月"),
                     ("每年复查", "每年"), ("年度复查", "每年"), ("每年", "每年"),
                     ("定期复查", "定期（未给频率）"), ("三个月到半年", "3～6 个月")):
        if pat in t:
            return lab
    return "未给频率"


def build_advice(case) -> dict:
    by_year_main, by_year_dept = {}, {}
    for f in case["findings"]:
        by_year_main.setdefault(f["year"], []).append(
            {"页": f["page"], "分类": f["section"], "序号": f["no"],
             "标题": f["title"], "原文": f["detail"]})
    for d in case["dept"]:
        by_year_dept.setdefault(d["year"], []).append(
            {"页": d["page"], "科室": d["section"], "小结原文": d["lines"]})

    topics = {}
    for topic, spec in TOPICS.items():
        per = {}
        for y in case["years"]:
            hits, srcs = [], []
            if "findings" in spec["源"]:
                for f in case["findings"]:
                    if f["year"] == y and any(k in f["title"] + f["detail"]
                                              for k in spec["keys"]):
                        hits.append(f["detail"])
                        srcs.append(f"主检 p{f['page']} #{f['no']} {f['title']}")
            if "dept" in spec["源"]:
                for d in case["dept"]:
                    if d["year"] == y and any(k in d["section"] + "".join(d["lines"])
                                              for k in spec["keys"]):
                        hits.append("｜".join(d["lines"]))
                        srcs.append(f"分科小结 p{d['page']} {d['section']}")
            if "narr" in spec["源"]:
                for n in case["narr"]:
                    if n["year"] == y and any(k in (n["verbatim"] + n["summary"])
                                              for k in spec["keys"]):
                        hits.append(f'{n["item"]}（{n["modality"]}）：'
                                    f'{n["verbatim"]}｜{n["summary"]}')
                        srcs.append(f'叙述 p{n["page"]} {n["modality"]}·{n["item"]}')
            if hits:
                joined = " ｜ ".join(hits)
                per[y] = {"原文": hits, "来源": srcs,
                          "复查频率": _freq(joined) if spec.get("freq") else None,
                          "措辞标记": ({f: (f in _norm(joined) or f in joined)
                                    for f in spec.get("flags", [])}
                                   if spec.get("flags") else None)}
        if per:
            topics[topic] = per
    return {"case": case["case"], "主检建议": by_year_main, "分科小结": by_year_dept,
            "主题": topics}


# ================================================================ facts

def build_facts(case, coverage, states, advice, profile_path: str = "") -> dict:
    series = _series(case)
    facts = {"case": case["case"], "年份": case["years"], "来源": case["来源"]}

    # ---- 数值表（报告引用的每个数字都应在这里）
    table = {}
    for name, aid in KEY:
        if aid is None:
            continue
        s = series.get(aid, {})
        if not s:
            continue
        table[name] = {"analyte_id": aid,
                       "逐年": {y: {"值": s[y]["值"], "单位": s[y]["单位"],
                                    "参考区间": s[y]["ref"], "位置": _position(s[y]),
                                    "换算": s[y].get("换算"),
                                    "页": s[y].get("页", "")}
                                for y in sorted(s)},
                       "来源": "观测值库（引擎归一产物 / 合成报告原文）"}
    facts["数值表"] = table
    #: 页码可用性：**所有**数值表行都有页码时才算"可核"（旧产物只有体格页 → 视为不可核）
    _rows = [t for blk in table.values() for t in blk["逐年"].values()]
    facts["页码可用"] = bool(_rows) and all(t.get("页") for t in _rows)
    # 相邻年变化%（纯算术，供"2024→2025 的回落 X%"这类引用）
    for name, blk in table.items():
        ys = sorted(blk["逐年"])
        blk["逐年变化%"] = {}
        for a, b in zip(ys, ys[1:]):
            va, vb = blk["逐年"][a]["值"], blk["逐年"][b]["值"]
            if va and vb is not None:
                blk["逐年变化%"] = dict(blk["逐年变化%"],
                                        **{f"{a}→{b}": round(100 * (vb - va) / va, 1)})

    # ---- 派生量与场景（全部由引擎函数算出）
    der = {}
    ages = {y: case["meta"][y].get("age") for y in case["years"]}
    # eGFR
    eg = {}
    for y in case["years"]:
        scr = series.get("CHEM.肌酐", {}).get(y, {}).get("值")
        if scr is not None and ages.get(y):
            eg[y] = round(egfr_ckdepi2021(scr, ages[y], case["sex"] == "男"), 1)
    der["eGFR"] = {"逐年": eg, "来源": "checkup.compose.egfr_ckdepi2021"}
    # 非好胆固醇
    tc, hdl = series.get("CHEM.总胆固醇", {}), series.get("CHEM.高密度脂蛋白胆固醇", {})
    der["非好胆固醇"] = {"逐年": {y: round(tc[y]["值"] - hdl[y]["值"], 2)
                            for y in case["years"] if y in tc and y in hdl},
                     "公式": "总胆固醇 − 好胆固醇",
                     "上限": 非HDL_C["治疗目标基数"],
                     "来源": "剧本内派生（无引擎规则，属算术）"}
    # 危险分层（现况 + 情景）
    prof = _profile(case, profile_path)
    strat = {}
    for y in case["years"]:
        if not all(series.get(k, {}).get(y, {}).get("值") is not None for k in
                   ("CHEM.总胆固醇", "CHEM.低密度脂蛋白胆固醇",
                    "CHEM.高密度脂蛋白胆固醇")):
            continue
        sbp = series.get("VITAL.收缩压", {}).get(y, {}).get("值")
        dbp = series.get("VITAL.舒张压", {}).get(y, {}).get("值")
        htn = (sbp is not None and dbp is not None and (sbp >= 140 or dbp >= 90))
        r = lipid_lookup(hypertension=htn, age=ages.get(y), male=case["sex"] == "男",
                         tc=tc[y]["值"], ldl=series["CHEM.低密度脂蛋白胆固醇"][y]["值"],
                         hdl=hdl[y]["值"], smoke=bool(prof["smoking"]["current"]))
        strat[y] = {"血压": f"{sbp}/{dbp}", "高血压": htn, "分层": r.band,
                    "危险因素": r.risk_factors if hasattr(r, "risk_factors") else None,
                    "备注": r.notes}
    der["血脂危险分层"] = {"逐年": strat,
                      "来源": "checkup.lipid_table.lookup（21 格表，引擎规则）"}
    # 包年数（问诊输入 → 事实化；**缺输入不出块**，宁缺不猜）
    _py = (prof.get("smoking") or {}).get("pack_years")
    if _py is not None:
        from checkup.bands import 包年数 as _PY
        der["包年数"] = {
            "值": _py, "单位": "包年", "门槛": _PY["高危门槛"],
            "结论": (f"≥{_PY['高危门槛']:g} 包年 → 达中国肺癌筛查讨论门槛"
                     if float(_py) >= _PY["高危门槛"] else
                     f"<{_PY['高危门槛']:g} 包年 → 未达中国肺癌筛查门槛"),
            "来源": "profile.smoking.pack_years（问诊输入；不得由 agent 手算）"}
    # 情景：若高血压成立 / 若 TC 越线（供报告"差两级"类表述引用）
    scen = {}
    y = case["years"][-1]
    if y in strat:
        base = strat[y]
        r2 = lipid_lookup(hypertension=True, age=ages.get(y), male=case["sex"] == "男",
                          tc=tc[y]["值"], ldl=series["CHEM.低密度脂蛋白胆固醇"][y]["值"],
                          hdl=hdl[y]["值"], smoke=bool(prof["smoking"]["current"]))
        scen["若高血压成立"] = {"分层": r2.band,
                             "LDL目标": (LDL_TARGETS.get(r2.band) or (None,))[0],
                             "来源": "checkup.lipid_table.lookup"}
        r3 = lipid_lookup(hypertension=base["高血压"], age=ages.get(y), male=case["sex"] == "男",
                          tc=_TC_EDGE,      # TC 越过分界（引擎「合适/边缘升高」）
                          ldl=series["CHEM.低密度脂蛋白胆固醇"][y]["值"],
                          hdl=hdl[y]["值"], smoke=bool(prof["smoking"]["current"]))
        scen[f"若总胆固醇越过 {_TC_EDGE}"] = {
            "分层": r3.band, "LDL目标": (LDL_TARGETS.get(r3.band) or (None,))[0],
            "来源": "checkup.lipid_table.lookup"}
    der["分层情景"] = {"以年": y, "情景": scen,
                   "来源": "checkup.lipid_table.lookup（若…则…的假设情景，非现状）"}
    # China-PAR（含家族史敏感性）
    cp, cp_miss = {}, {}
    for y in case["years"]:
        sbp = series.get("VITAL.收缩压", {}).get(y, {}).get("值")
        wc = series.get("VITAL.腰围", {}).get(y, {}).get("值")
        need = {"年龄": ages.get(y), "收缩压": sbp, "腰围": wc,
                "总胆固醇": tc.get(y, {}).get("值"), "好胆固醇": hdl.get(y, {}).get("值")}
        miss = [k for k, v in need.items() if v is None]
        if miss:
            cp_miss[y] = miss
            continue
        row = {}
        for fh in (False, True):
            r = china_par_male(ages[y], sbp, tc[y]["值"], hdl[y]["值"], wc,
                               smoke=bool(prof["smoking"]["current"]),
                               dm=bool(prof["diabetes"]),
                               north=bool((prof.get("region") or {}).get("north", True)),
                               urban=bool((prof.get("region") or {}).get("urban", True)), fh=fh,
                               treated=bool(prof["antihypertensive"]))
            row["家族史=True" if fh else "家族史=False"] = round(r * 100, 2)
        cp[y] = row
    # 代谢综合征：直接调引擎内部规则（不重写阈值）
    der["代谢综合征"] = _metsyn_facts(case, series, ages, prof)
    if case["sex"] == "男":
        der["ChinaPAR"] = {"逐年": cp, "单位": "%（10年）", "缺输入年份": cp_miss,
                       "来源": "checkup.compose.china_par_male；家族史敏感性 = 同一函数取 fh=True",
                       "说明": "家族史未知时按『无』计算，敏感性值给出『若有早发家族史』的上界；"
                               "缺腰围等输入时**拒绝计算**（不估）"}
    else:
        # 引擎只实现男性方程 —— 女性不得给出该方程的输出（与 compose 的
        # not_applicable 同口径，别把男性方程的数值留在底稿里）。
        der["ChinaPAR"] = {"状态": "not_applicable",
                       "说明": "本引擎目前只实现了 China-PAR 男性方程，不适用于女性受检者"}
    facts["派生"] = der
    facts["派生"] = der

    # ---- 数据质量与一致性（报告"数据问题"类陈述的事实底稿）
    dq = {}
    # HDL 重复值（按报告显示精度比较：一位小数还是两位小数，由数据自己决定）
    h = hdl
    if all(h.get(y, {}).get("值") is not None for y in case["years"]):
        vals = [h[y]["值"] for y in case["years"]]
        for dec in (1, 2):
            rounded = [round(v, dec) for v in vals]
            if len(set(rounded)) == 1:
                break
        from collections import Counter
        cnt = Counter(round(v, 2) for v in vals)
        top, freq = cnt.most_common(1)[0]
        dq["好胆固醇同值"] = {"值": vals, "精确同值": len(set(vals)) == 1,
                          "两位小数下的重复": f"{top}×{freq}",
                          "是否全部同值": len(set(round(v, 2) for v in vals)) == 1,
                          "判据": "同一个人多年连小数点后两位都一样 → 疑似未重新检测（"                                  "措辞只能用「无法核实」，不得指控）"}
    # 胆红素内部一致
    tbi, dbi, ibi = (series.get("CHEM.总胆红素", {}), series.get("CHEM.直接胆红素", {}),
                     series.get("CHEM.间接胆红素", {}))
    cons = {}
    for y in case["years"]:
        if tbi.get(y, {}).get("值") is not None and dbi.get(y, {}).get("值") is not None \
                and ibi.get(y, {}).get("值") is not None:
            cons[y] = {"总": tbi[y]["值"], "直接": dbi[y]["值"], "间接": ibi[y]["值"],
                       "直接+间接": round(dbi[y]["值"] + ibi[y]["值"], 2),
                       "一致": abs(tbi[y]["值"] - dbi[y]["值"] - ibi[y]["值"]) <= 0.1}
    dq["胆红素内部一致"] = cons
    # 身高差 / BMI
    hh = series.get("VITAL.身高", {})
    if hh:
        vs = [hh[y]["值"] for y in sorted(hh)]
        dq["身高"] = {"逐年": vs, "极差": round(max(vs) - min(vs), 1),
                      "判据": "成年男性身高不变；极差 >1cm 即属记录异常，BMI 相关结论应打折"}
    # 日期与文件名（真实案）
    if case["case"] == "real":
        dq["采样间隔月"] = {f"{a}→{b}": round(_months_between(case, a, b), 1)
                        for a, b in zip(case["years"], case["years"][1:])}
        # 年份**从数据推导**：写死 "2026" 时，任何别的年份集合都会 KeyError
        _last = sorted(case["meta"])[-1] if case["meta"] else ""
        dq["文件名与内页日期"] = {"文件名": "（略，见原始文件）",
                           "内页": (case["meta"].get(_last) or {}).get("exam_date"),
                           "判据": "日期一律取内页"}
    facts["数据质量"] = dq

    # ---- 覆盖率/缺口（从 coverage 取，供报告直接引用）
    facts["覆盖率"] = {"应查": coverage["应查"], "全覆盖": coverage["全覆盖"],
                   "部分覆盖": coverage["部分覆盖"], "缺": coverage["缺"],
                   "覆盖率": coverage["覆盖率"],
                   "缺口项目": [g["项目"] for g in coverage["缺口明细"]],
                   "部分覆盖项目": [p["项目"] for p in coverage["部分覆盖明细"]]}
    # ---- 机构建议主题（可对账：频率/措辞/是否标注）
    facts["机构建议"] = {k: v for k, v in advice["主题"].items()}
    # ---- 影像尺寸序列（带检查方式）
    facts["影像"] = {f"{i['检查项目']}（{i['检查方式']}）{'·'+i['侧别'] if i['侧别'] != '—' else ''}":
                 i["逐年"] for i in states["影像发现"]}
    facts["影像台账"] = {i["发现"]: {"检查方式": i["检查方式"], "逐年": i["逐年"],
                                "备注": i["备注"]} for i in states["影像台账"]}
    return facts


def _profile(case, profile_path: str = "") -> dict:
    """个体信息（吸烟/家族史/地区/用药）—— 来自问诊/报告原文，不猜。

    载入顺序：``--profile`` > ``CHECKUP_PROFILE`` > 数据根 profile.json。
    字段规范见 ``contracts/profile.schema.json``。
    """
    if case["case"] == "real":
        # 个体信息是**运行期输入**（问诊产物），不进 skill 目录（P0-4）
        path = profile_path or os.environ.get("CHECKUP_PROFILE") or FIXTURES.profile
        if not os.path.exists(path):
            raise SystemExit(
                f"✗ 需要个体信息（吸烟/家族史/用药）：{path} 不存在。\n"
                f"  由问诊产生后经 --profile / CHECKUP_PROFILE 指向；涉及这些输入的"
                f"派生量（China-PAR/危险分层）不可猜。\n"
                f"  字段规范：contracts/profile.schema.json")
        p = json.load(open(path, encoding="utf-8"))
        missing = [k for k in ("smoking", "family_history_ascvd",
                              "diabetes", "antihypertensive") if k not in p]
        if not isinstance(p.get("smoking"), dict) or "current" not in p.get("smoking", {}):
            missing.append("smoking.current")
        if missing:
            raise SystemExit(
                f"✗ profile 缺必填字段：{'、'.join(missing)}（{path}）\n"
                f"  缺字段不猜，宁可停下；字段规范：contracts/profile.schema.json")
        return p
    return {"smoking": {"current": True, "note": "合成数据：吸烟每日约 10 支"},
            "family_history_ascvd": {"known": True, "value": False,
                                     "note": "父亲 62 岁脑梗死——按指南'早发'定义（男<55）不满足"},
            "diabetes": False, "antihypertensive": False,
            "region": {"north": False, "urban": True}}


_CATALOG_CACHE = {}


def _catalog():
    """引擎的规范指标目录（懒加载一次）。"""
    if "c" not in _CATALOG_CACHE:
        from checkup.normalize import AnalyteCatalog
        _CATALOG_CACHE["c"] = AnalyteCatalog.load(
            os.path.join(ENGINE_DIR, "catalog", "analytes.json"))
    return _CATALOG_CACHE["c"]


def _metsyn_facts(case, series, ages, prof):
    """代谢综合征三套标准 —— 直接复用引擎的 ``_metsyn``（不重写阈值）。"""
    from checkup.compose import Composer
    from checkup.compose import _metsyn as metsyn_rule

    class _J:
        def __init__(self, aid, series_):
            self.analyte_id = aid
            self.series = {y: r["值"] for y, r in series_.items() if r["值"] is not None}
            # 与规范单位不等价的年份 → 组合评估拦下（与引擎同口径，见 compose.Composer）
            from checkup.normalize import AnalyteCatalog, _same_unit
            a = _catalog().analytes.get(aid)
            self.unit = (a.canonical_unit if a and a.canonical_unit else "")
            self.unit_ok = {}
            if a and a.canonical_unit:
                for y, r in series_.items():
                    u = r.get("单位") or ""
                    if u:
                        self.unit_ok[y] = _same_unit(u, a.canonical_unit)

    class _Meta:
        def __init__(self, age, sex):
            self.age, self.sex = age, sex

    aids = ["CHEM.甘油三酯", "CHEM.高密度脂蛋白胆固醇", "CHEM.葡萄糖",
            "VITAL.体重指数", "VITAL.收缩压", "VITAL.舒张压", "VITAL.腰围"]
    comp = Composer([_J(a, series.get(a, {})) for a in aids],
                    {y: _Meta(ages.get(y), case["sex"]) for y in case["years"]}, prof)
    out = {}
    for y in case["years"]:
        res = metsyn_rule(comp, y, case["sex"] == "男", prof)
        for r in res:
            out.setdefault(r.rule_id, {})[y] = {"分层": r.band, "状态": r.status,
                                              "说明": r.text, "缺失输入": r.missing}
    return {"逐年": out, "来源": "checkup.compose._metsyn（引擎内部规则，直接复用）", "备注": "腰围缺失 → 该标准不可评估（不估）"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="四张底稿产出器（coverage/states/advice/facts）")
    ap.add_argument("--case", choices=["real", "synth"], default=None,
                    help="回归用：real=引擎产物读数；synth=合成 .txt 解析")
    ap.add_argument("--engine-out", default="",
                    help="新案例链（P0-8）：直接吃 run_pipeline --out 的产物目录")
    ap.add_argument("--case-name", default="",
                    help="新案例名（配合 --engine-out；底稿里的 case 标签）")
    ap.add_argument("--synth-dir", default="", help="合成报告目录（synth 用）")
    ap.add_argument("--out", default="",
                    help="底稿输出目录（缺省 <产物根>/artifacts/<名>；⛔ 不写 skill 目录）")
    ap.add_argument("--profile", default="",
                    help="个体信息 JSON（吸烟/家族史/用药；字段规范见 "
                         "contracts/profile.schema.json）；缺省取 CHECKUP_PROFILE "
                         "环境变量，再退数据根 profile.json")
    args = ap.parse_args()
    if not args.case and not args.engine_out:
        ap.error("要么给 --case real|synth（回归），要么给 --engine-out <dir>（新案例）")
    name = args.case_name or args.case or "case"
    if args.engine_out:
        # 新案例：来源类型按"引擎产物"走（case["case"]="real" 语义），标签用 case-name
        case = loaders.load("real", engine_out=args.engine_out, name=name)
    else:
        case = loaders.load(args.case, synth_dir=args.synth_dir, name=name)
    out = guard_out(args.out or default_artifacts(name))
    print(f"[{name}] 观测值库：{case['来源']}")
    cov = build_coverage(case)
    st = build_states(case)
    adv = build_advice(case)
    fct = build_facts(case, cov, st, adv, args.profile)
    W(os.path.join(out, "coverage.json"), cov)
    W(os.path.join(out, "states.json"), st)
    W(os.path.join(out, "advice.json"), adv)
    W(os.path.join(out, "facts.json"), fct)
    print(f"  覆盖率 {cov['覆盖率']}%（全 {cov['全覆盖']} / 部分 {cov['部分覆盖']}"
          f" / 缺 {cov['缺']} / 应查 {cov['应查']}）")
    print(f"  底稿目录：{out}")
    print(f"  缺口：{'、'.join(g['项目'] for g in cov['缺口明细']) or '无'}")
    print(f"  部分覆盖：{'、'.join(p['项目'] for p in cov['部分覆盖明细']) or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
