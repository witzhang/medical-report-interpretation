#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观测值库载入器 —— 把两个案例读成**同一种形状**，供缺口表/状态分类/对账共用。

两个案例、两个来源，但下游脚本只认一种结构：

* ``real``  —— 引擎产物（``--engine-out``，用户自己的报告跑出来的）。来源：``checkup-engine/out/``（引擎的
  确定性产物；PDF 原文是最高权威，引擎产物是它的结构化投射）。
* ``synth`` —— ``research/testdata/`` 的合成报告（2 机构 3 年）。PDF 由
  ``.txt`` 生成，故 **.txt 是原文**；引擎没有第二家机构的版式模板，跑不动，
  所以这里按固定版式解析 .txt。

单位：**只在这里换算一次**（synth 的 mg/dL → 标准单位），每个换算值都记录
原值、因子与依据，报告引用换算值时必须能指回这里（``换算`` 字段）。
真实案不需要换算（机构用标准单位）。

用法：
    python3 loaders.py --case real   # 转储为 JSON，供人工核对
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from paths import ENGINE_DIR, FIXTURES  # noqa: E402

#: 缺省数据源（可被 CLI 覆盖，P0-8）：引擎产物与合成报告都在**数据根**里
ENGINE_OUT = FIXTURES.engine_out
def _synth_dir() -> str:
    """合成报告目录解析：环境变量 > 数据根 research/testdata（开发布局）
    > **随包 examples/testdata**（分发布局）。"""
    env = os.environ.get("CHECKUP_SYNTH_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    dev = FIXTURES.synth_dir
    if dev and os.path.isdir(dev):
        return dev
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "examples", "testdata")


SYNTH_DIR = _synth_dir()

#: mg/dL → 标准单位的换算因子。来源：临床化学标准换算（SI 换算系数）。
#: 引擎在 CKD-EPI 里用同一族因子（rules.py：「Scr 单位 μmol/L，计算前 ÷88.4」）。
CONVERSIONS = {
    "mg/dL>umol/L":  {"因子": 88.4,   "适用": ["肌酐"], "说明": "1 mg/dL 肌酐 = 88.4 μmol/L"},
    "mg/dL>umol/L#UA": {"因子": 59.48, "适用": ["尿酸"], "说明": "1 mg/dL 尿酸 = 59.48 μmol/L"},
    "mg/dL>mmol/L#CH": {"因子": 1 / 38.67, "适用": ["总胆固醇", "低密度脂蛋白胆固醇", "高密度脂蛋白胆固醇"],
                        "说明": "1 mmol/L 胆固醇 = 38.67 mg/dL"},
    "mg/dL>mmol/L#TG": {"因子": 1 / 88.57, "适用": ["甘油三酯"], "说明": "1 mmol/L 甘油三酯 = 88.57 mg/dL"},
    "mg/dL>mmol/L#GLU": {"因子": 1 / 18.02, "适用": ["葡萄糖"], "说明": "1 mmol/L 葡萄糖 = 18.02 mg/dL"},
}


def _rng(s: str):
    """解析参考区间文本 → (low, high, op)。op ∈ {'', '<', '>', 'range'}。"""
    s = s.strip()
    m = re.search(r"([<>≤≥]?)\s*(\d+(?:\.\d+)?)\s*[-–~]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(2)), float(m.group(3)), "range"
    m = re.search(r"([<>≤≥])\s*(\d+(?:\.\d+)?)", s)
    if m:
        v = float(m.group(2))
        return (None, v, "<") if m.group(1) in "<≤" else (v, None, ">")
    return None, None, ""


# ---------------------------------------------------------------- 真实案

def load_real(engine_out: str = "") -> dict:
    """引擎产物目录 → Case（默认数据根下 ``checkup-engine/out/``）。

    ``engine_out`` 可指向任意一次 ``run_pipeline --out`` / ``run_narrative --out``
    / ``run_normalize --out`` 的产物目录（新案例链，P0-8）。

    只用**归一后的观测**（``normalized_observations.csv``）：项目名、单位、
    参考区间都经过 L2 归一与 L3 校验；未归一的行（匹配方式=unmapped）不静默
    丢弃，进 ``unmatched`` 供审计。
    """
    engine_out = engine_out or ENGINE_OUT
    if not os.path.isdir(engine_out):
        raise SystemExit(
            f"✗ 引擎产物目录不存在：{engine_out}\n"
            f"  先跑：run_narrative --out <dir> && run_normalize --out <dir> && "
            f"run_pipeline --src <报告目录> --out <dir>")
    case = {"case": "real", "years": [], "labs": [], "vitals": [], "narr": [],
            "findings": [], "dept": [], "unmatched": [], "meta": {}}

    # 体格/标量页码：抽取层产物里有（老新产物都有这列）
    page_map: dict = {}
    oa = os.path.join(engine_out, "observations_all.csv")
    if os.path.exists(oa):
        with open(oa, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                page_map.setdefault((r["年份"], r["项目"]), r.get("页", ""))

    with open(os.path.join(engine_out, "normalized_observations.csv"),
              encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rec = {"year": r["年份"], "section": r["段落"], "item": r["项目原文"],
                   "analyte_id": r["analyte_id"], "value": r["数值"] or None,
                   "value_text": r["值原文"], "unit": r["单位"],
                   "ref": r["参考区间"] or "", "qualifier": r["备注"] or "",
                   "source": "normalized_observations.csv"}
            # 页码（P1-B12 附录二用）：新产物带「页」列；否则用 observations_all 兜底
            rec["页"] = r.get("页", "") or page_map.get((r["年份"], r["项目原文"]), "")
            if r["匹配方式"] == "unmapped":
                case["unmatched"].append(rec)
                continue
            if r["来源"] == "标量":
                rec["ref_low"], rec["ref_high"], rec["ref_op"] = _rng(rec["ref"])
                case["vitals"].append(rec)
            else:
                rec["ref_low"], rec["ref_high"], rec["ref_op"] = _rng(rec["ref"])
                case["labs"].append(rec)

    with open(os.path.join(engine_out, "narrative_all.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            case["narr"].append({
                "year": r["年份"], "page": int(r["页"]), "modality": r["检查方式"],
                "item": r["检查项目"], "side": r["侧别"], "descriptor": r["描述词"],
                "raw": r["测量原文"], "value": r["数值"] or None, "unit": r["单位"],
                "grade_system": r["分级体系"], "grade": r["分级"],
                "summary": r["小结"], "verbatim": r["描述原文"]})

    with open(os.path.join(engine_out, "institution_findings.csv"),
              encoding="utf-8") as f:
        for r in csv.DictReader(f):
            case["findings"].append({
                "year": r["年份"], "page": int(r["页"]), "section": r["分类"],
                "no": int(r["序号"]), "title": r["标题"], "detail": r["说明与建议"]})

    with open(os.path.join(engine_out, "dept_summaries.csv"), encoding="utf-8") as f:
        buf: dict = {}
        for r in csv.DictReader(f):
            key = (r["年份"], int(r["页"]), r["科室"])
            buf.setdefault(key, []).append(r["小结原文"])
        for (y, p, sec), lines in sorted(buf.items()):
            case["dept"].append({"year": y, "page": p, "section": sec, "lines": lines})

    # 年份**从产物推导**（不再写死三年）：report_<年>.json 即该年的抽取结果
    import glob as _glob
    for rp in sorted(_glob.glob(os.path.join(engine_out, "report_*.json"))):
        y = os.path.basename(rp)[len("report_"):-len(".json")]
        with open(rp, encoding="utf-8") as f:
            d = json.load(f)
        case["meta"][y] = d["document"]
    if not case["meta"]:
        raise SystemExit(f"✗ {engine_out} 里没有 report_<年>.json —— "
                         f"先跑 run_narrative.py --out {engine_out}")

    case["years"] = sorted(case["meta"])
    case["age_at_last"] = case["meta"][case["years"][-1]]["age"]
    case["sex"] = case["meta"][case["years"][-1]]["sex"]
    case["来源"] = f"{engine_out}/（引擎产物；原文为报告目录里的 PDF）"
    return case


# ---------------------------------------------------------------- 合成案

#: 合成报告的段名 → 本工具的内部段名（与真实案对齐）
SYNTH_SECTIONS = {
    "一般检查": "一般项目", "一般项目": "一般项目", "血常规": "血常规",
    "血细胞分析": "血常规", "肝功能": "肝功能", "肾功能": "肾功能",
    "血脂": "血脂", "血糖": "血糖", "甲状腺": "甲状腺功能",
    "甲状腺功能": "甲状腺功能", "尿常规": "尿常规",
}

#: 合成报告的项目名（原文）→ 归一 id。跨机构命名差异（ALT/丙氨酸氨基转移酶…）
#: 在这里对齐；**不做医学判断**，只做同一性映射。
SYNTH_ANALYTES = {
    # 一般项目
    "血压": ("VITAL.收缩压", "VITAL.舒张压"), "血压 BP": ("VITAL.收缩压", "VITAL.舒张压"),
    "脉搏": ("VITAL.脉搏",), "静息心率": ("VITAL.脉搏",),
    # 血细胞分析
    "白细胞": ("BLD.白细胞",), "WBC 白细胞": ("BLD.白细胞",), "白细胞 WBC": ("BLD.白细胞",),
    "血红蛋白": ("BLD.血红蛋白浓度",), "HGB 血红蛋白": ("BLD.血红蛋白浓度",),
    "血红蛋白 HGB": ("BLD.血红蛋白浓度",),
    "血小板": ("BLD.血小板",), "PLT 血小板": ("BLD.血小板",), "血小板 PLT": ("BLD.血小板",),
    # 肝功能
    "谷丙转氨酶": ("CHEM.丙氨酸氨基转移酶",),
    "丙氨酸氨基转移酶 ALT": ("CHEM.丙氨酸氨基转移酶",),
    "谷草转氨酶": ("CHEM.天门冬氨基转移酶",),
    "天门冬氨酸氨基转移酶 AST": ("CHEM.天门冬氨基转移酶",),
    "谷氨酰转肽酶": ("CHEM.谷氨酰转肽酶",), "谷氨酰转肽酶 GGT": ("CHEM.谷氨酰转肽酶",),
    "r-谷氨酰转移酶 GGT": ("CHEM.谷氨酰转肽酶",),
    # 肾功能
    "肌酐": ("CHEM.肌酐",), "CREA 肌酐": ("CHEM.肌酐",),
    "尿素氮": ("CHEM.尿素",), "BUN 尿素氮": ("CHEM.尿素",),
    "尿酸": ("CHEM.尿酸",), "UA 尿酸": ("CHEM.尿酸",),
    # 血脂
    "总胆固醇": ("CHEM.总胆固醇",), "CHOL 总胆固醇": ("CHEM.总胆固醇",),
    "甘油三酯": ("CHEM.甘油三酯",), "TG 甘油三酯": ("CHEM.甘油三酯",),
    "低密度脂蛋白": ("CHEM.低密度脂蛋白胆固醇",), "LDL-C 低密度脂蛋白": ("CHEM.低密度脂蛋白胆固醇",),
    "低密度脂蛋白 LDL-C": ("CHEM.低密度脂蛋白胆固醇",),
    "高密度脂蛋白": ("CHEM.高密度脂蛋白胆固醇",), "HDL-C 高密度脂蛋白": ("CHEM.高密度脂蛋白胆固醇",),
    "高密度脂蛋白 HDL-C": ("CHEM.高密度脂蛋白胆固醇",),
    # 血糖
    "空腹血糖": ("CHEM.葡萄糖",), "GLU 葡萄糖(空腹)": ("CHEM.葡萄糖",),
    "HBA1C 糖化血红蛋白": ("CHEM.糖化血红蛋白",),
    # 甲状腺
    "TSH": ("CHEM.促甲状腺激素",), "FT3": ("CHEM.游离三碘甲状腺原氨酸",),
    "FT4": ("CHEM.游离甲状腺素",),
    # 尿常规
    "尿蛋白": ("URI.蛋白质.qual",), "尿糖": ("URI.尿糖.qual",),
}

RAW_RANGE_RE = re.compile(
    r"([<>]\s*\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?|\d+(?:\.\d+)?\s*[-–~]\s*\d+(?:\.\d+)?)")


def _conv(name: str, value: float, unit: str):
    """按 CONVERSIONS 把 mg/dL 换成标准单位；返回 (值, 单位, 换算记录|None)。"""
    if unit != "mg/dL":
        return value, unit, None
    for key, spec in CONVERSIONS.items():
        if name in spec["适用"]:
            v = round(value * spec["因子"], 3)
            out_unit = "umol/L" if "umol/L" in key else "mmol/L"
            return v, out_unit, {"原值": value, "原单位": "mg/dL", "因子": spec["因子"],
                                 "换算值": v, "换算单位": out_unit,
                                 "依据": spec["说明"]}
    return value, unit, None


def _conv_ref(name: str, low, high, unit: str):
    """**参考区间必须与数值一起换算** —— 只换值不换区间会造出假异常
    （肌酐 90 μmol/L 配 0.60–1.20 mg/dL 的上限 → 判成超标 7500%）。
    这正是本工具要防的那类错误，所以在这里显式成对处理。"""
    if unit != "mg/dL" or (low is None and high is None):
        return low, high
    for spec in CONVERSIONS.values():
        if name in spec["适用"]:
            f = spec["因子"]
            return (round(low * f, 3) if low is not None else None,
                    round(high * f, 3) if high is not None else None)
    return low, high


def _bp_ref(ref: str, part: int):
    """血压区间「<130/85」「<140/90」→ 按收缩压/舒张压取对应那一半。"""
    m = re.match(r"([<>]?)\s*(\d+)\s*/\s*(\d+)", ref or "")
    if not m:
        return None, None, ""
    v = float(m.group(2) if part == 1 else m.group(3))
    op = m.group(1) or "<"
    return (None, v, op) if op in ("<", "≤") else (v, None, op)


#: 数值 token（含血压「138/88」与带比较符的「<5.2」）
_NUM_TOK = re.compile(r"^[<>]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?$")


def _parse_synth_rows(text: str) -> list[dict]:
    """解析「名称 结果 参考值 提示」版式（列宽不严格对齐，**不能按列切片**）。

    定位规则：**第一个数值 token 之前是项目名，之后是结果/参考值/提示**。
    项目名里可以带字母与数字（FT3 / HBA1C / LDL-C），所以不能用"取前两个
    token"这类写法（已踩：`天门冬氨酸氨基转移酶 AST 36 U/L` 会被整行跳过）。
    定性行（尿蛋白 阴性(-)）没有数值 token，单独走末 token 作结论。
    """
    out = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip() or s.lstrip().startswith(("项目", "====", "----", "第", "ID", "（")):
            continue
        toks = s.split()
        idx = next((i for i, t in enumerate(toks) if _NUM_TOK.match(t)), None)
        if idx is None:
            if len(toks) < 2:      # 定性行：名称 + 结果[ + 结论]
                continue
            name, rest = toks[0], " ".join(toks[1:])
            flag = rest.split()[-1] if rest.split() and rest.split()[-1] in (
                "正常", "阴性", "阳性", " -", "-") else ""
            rngs = list(RAW_RANGE_RE.finditer(rest))
            out.append({"name": name, "raw": rest,
                        "before": rest[:-len(flag)].strip() if flag else rest,
                        "ref": rngs[-1].group(1) if rngs else "", "flag": flag})
            continue
        name = " ".join(toks[:idx]).rstrip(":：")
        rest = " ".join(toks[idx:])
        rngs = list(RAW_RANGE_RE.finditer(rest))
        if rngs:
            last = rngs[-1]
            ref, after, before = last.group(1), rest[last.end():].strip(), rest[:last.start()].strip()
        else:
            ref, after, before = "", "", rest
        toks_a = after.split()
        flag = toks_a[-1] if toks_a and toks_a[-1] in (
            "正常", "H", "L", "偏高", "↓偏低", "↑偏高") else ""
        out.append({"name": name, "raw": rest, "before": before, "ref": ref, "flag": flag})
    return out


def load_synth(synth_dir: str = "") -> dict:
    """合成报告目录（默认数据根下 ``research/testdata``）→ Case。

    原文即 .txt；PDF 由它生成。
    """
    synth_dir = synth_dir or SYNTH_DIR
    case = {"case": "synth", "years": [], "labs": [], "vitals": [], "narr": [],
            "findings": [], "dept": [], "unmatched": [], "meta": {}}
    # 目录探测：``YYYY-MM-DD_机构.txt`` —— **不写死文件名**（随包样例即此格式）
    files = {}
    for fn in sorted(os.listdir(synth_dir)) if os.path.isdir(synth_dir) else []:
        m = re.match(r"(\d{4})-\d{2}-\d{2}_.+\.txt$", fn)
        if m:
            files[m.group(1)] = fn
    for year, fn in files.items():
        path = os.path.join(synth_dir, fn)
        if not os.path.exists(path):
            raise SystemExit(f"✗ 合成报告不存在：{path}\n"
                             f"  由 CHECKUP_FIXTURES 指向的数据根提供（skill 不含数据）。")
        text = open(path, encoding="utf-8").read()
        org = text.splitlines()[0].strip()
        date = re.search(r"(?:体检日期|检查日期)[:：]?\s*(\d{4}-\d{2}-\d{2})", text)
        age = re.search(r"年\s*龄[:：]?\s*(\d+)", text)
        sex = re.search(r"性\s*别[:：]?\s*([男女])", text)
        height = re.search(r"身\s*高[:：]?\s*([\d.]+)\s*cm", text)
        weight = re.search(r"体\s*重[:：]?\s*([\d.]+)\s*kg", text)
        case["meta"][year] = {"org": org, "exam_date": date.group(1) if date else None,
                              "age": int(age.group(1)) if age else None,
                              "sex": sex.group(1) if sex else None,
                              "身高": float(height.group(1)) if height else None,
                              "体重": float(weight.group(1)) if weight else None}

        # ---- 分节
        blocks = re.split(r"^(?:={4,}|-{4,})\s*(.+?)\s*(?:={4,}|-{4,})$", text, flags=re.M)
        # blocks = [前言, 节名1, 正文1, 节名2, 正文2, ...]
        for i in range(1, len(blocks), 2):
            sec_raw, body = blocks[i].strip(), blocks[i + 1]
            sec = SYNTH_SECTIONS.get(sec_raw, sec_raw)
            if sec_raw == "影像检查" or sec_raw == "超声检查":
                for m in re.finditer(r"((?:腹部|甲状腺)(?:B超|超声))[:：]\s*(.+?)(?=诊断[:：])诊断[:：]\s*(.+?)(?=\n\S|\Z)",
                                     body, re.S):
                    item = {"腹部B超": "肝脏", "腹部超声": "肝脏", "甲状腺B超": "甲状腺",
                            "甲状腺超声": "甲状腺"}[m.group(1)]
                    verb = re.sub(r"\s+", "", m.group(2))
                    summ = re.sub(r"\s+", "", m.group(3))
                    size = re.search(r"(\d+(?:\.\d+)?)\s*mm", verb)
                    case["narr"].append({
                        "year": year, "page": 0, "modality": "彩超", "item": item,
                        "side": "右" if "右叶" in verb else "",
                        "descriptor": "低回声" if "低回声" in verb else "",
                        "raw": f"{size.group(1)}mm" if size else "",
                        "value": float(size.group(1)) if size else None,
                        "unit": "mm" if size else "",
                        "grade_system": ("C-TIRADS" if "C-TIRADS" in summ or "C-TIRADS" in verb
                                         else "TI-RADS" if "TI-RADS" in summ or "TIRADS" in summ
                                         else ""),
                        "grade": (re.search(r"(\d)\s*类", summ).group(1) + "类"
                                  if re.search(r"(\d)\s*类", summ) else ""),
                        "summary": summ, "verbatim": verb})
                continue
            if sec_raw in ("异常指标汇总及建议", "结论与建议"):
                for m in re.finditer(r"^(\d+)[.、]\s*(.+)$", body, re.M):
                    case["findings"].append({
                        "year": year, "page": 0, "section": "结论与建议",
                        "no": int(m.group(1)), "title": m.group(2).strip()[:24],
                        "detail": m.group(2).strip()})
                continue
            if sec_raw == "家族史":
                case["meta"][year]["家族史"] = re.sub(r"\s+", "", body)
                continue
            if sec_raw == "尿常规":
                for row in _parse_synth_rows(body):
                    ids = SYNTH_ANALYTES.get(row["name"])
                    if not ids:
                        case["unmatched"].append({"year": year, "section": sec, "name": row["name"]})
                        continue
                    case["labs"].append({
                        "year": year, "section": "尿常规", "item": row["name"],
                        "analyte_id": ids[0], "value": None, "value_text": row["before"],
                        "unit": "", "ref": "", "ref_low": None, "ref_high": None,
                        "ref_op": "",
                        "qualifier": "阴性" if "阴性" in row["before"] else (
                            "阳性" if "阳性" in row["before"] else row["flag"])})
                continue

            for row in _parse_synth_rows(body):
                ids = SYNTH_ANALYTES.get(row["name"])
                if not ids:
                    case["unmatched"].append({"year": year, "section": sec, "name": row["name"]})
                    continue
                before = row["before"]
                nums = re.findall(r"[<>]?\d+(?:\.\d+)?", before)
                unit_m = re.search(r"([A-Za-z%/^0-9]+/[A-Za-z]+|mmHg|bpm|cm|kg|%)", before)
                unit = unit_m.group(1) if unit_m else ""
                for k, aid in enumerate(ids):
                    raw_v = nums[k] if k < len(nums) else (nums[0] if nums else None)
                    if raw_v is None:
                        continue
                    v = float(raw_v)
                    v, out_unit, conv = _conv(aid.split(".")[-1], v, unit)
                    low, high, op = _rng(row["ref"])
                    if aid.startswith("VITAL.收缩压"):
                        low, high, op = _bp_ref(row["ref"], 1)
                    elif aid.startswith("VITAL.舒张压"):
                        low, high, op = _bp_ref(row["ref"], 2)
                    else:
                        low, high = _conv_ref(aid.split(".")[-1], low, high, unit)
                    rec = {"year": year, "section": sec, "item": row["name"],
                           "analyte_id": aid, "value": v, "value_text": before,
                           "unit": out_unit or unit, "ref": row["ref"],
                           "ref_low": low, "ref_high": high, "ref_op": op,
                           "qualifier": row["flag"]}
                    if conv:
                        rec["换算"] = conv
                    (case["vitals"] if aid.startswith("VITAL.") else case["labs"]).append(rec)

        # 身高/体重/BMI（在页头，不在表格里）
        bmi = re.search(r"BMI[:：]?\s*([\d.]+)", text)
        if bmi:
            case["vitals"].append({"year": year, "section": "一般项目", "item": "体重指数",
                                   "analyte_id": "VITAL.体重指数", "value": float(bmi.group(1)),
                                   "value_text": bmi.group(1), "unit": "", "ref": "",
                                   "ref_low": None, "ref_high": None, "ref_op": "",
                                   "qualifier": ""})
        for aid, key in (("VITAL.身高", "身高"), ("VITAL.体重", "体重")):
            if case["meta"][year].get(key):
                case["vitals"].append({"year": year, "section": "一般项目", "item": key,
                                       "analyte_id": aid, "value": case["meta"][year][key],
                                       "value_text": f"{case['meta'][year][key]}", "unit": "cm" if key == "身高" else "kg",
                                       "ref": "", "ref_low": None, "ref_high": None,
                                       "ref_op": "", "qualifier": ""})

    case["years"] = sorted(case["meta"])
    case["age_at_last"] = case["meta"][case["years"][-1]]["age"]
    case["sex"] = case["meta"][case["years"][-1]]["sex"]
    case["来源"] = f"{synth_dir}/*.txt（合成数据；PDF 由 make_reports.py 生成）"
    return case


def load(case: str, engine_out: str = "", synth_dir: str = "",
         name: str = "") -> dict:
    """按来源类型载入；``name`` 用于报告标签（新案例可自定义，逻辑仍按来源类型走）。"""
    c = load_real(engine_out) if case == "real" else load_synth(synth_dir)
    c["name"] = name or c["case"]
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", choices=["real", "synth"], required=True)
    ap.add_argument("--engine-out", default="", help="引擎产物目录（real 用）")
    ap.add_argument("--synth-dir", default="", help="合成报告目录（synth 用）")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    c = load(args.case, engine_out=args.engine_out, synth_dir=args.synth_dir)
    txt = json.dumps(c, ensure_ascii=False, indent=1)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(txt)
        print(f"已写入 {args.out}")
    else:
        print(txt)
    print(f"\n[{args.case}] 年 {c['years']} · 化验 {len(c['labs'])} · 体格 {len(c['vitals'])}"
          f" · 影像 {len(c['narr'])} · 机构条目 {len(c['findings'])}"
          f" · 分科小结 {len(c['dept'])} · 未归一 {len(c['unmatched'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
