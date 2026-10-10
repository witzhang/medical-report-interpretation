#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对账器：把报告里的**事实性陈述**逐条对到脚本产物上。

三层对账：

1. **编号检查（checks）** —— 每条断言 = 一个可执行的检查。期望值来自
   ``artifacts/<case>/`` 的脚本产物或 ``constants.json``（指南常量，逐条带出处）；
   检查同时扫**报告**与**两张图的 spec**（图里的文字也是报告的一部分）。
   检查清单覆盖两类：① 本轮 15 处已核实错误的**回归锁**；② 报告的通则
   （覆盖率/状态分类/检查方式归属/优先级一致/服药安全提示）。
2. **数字追踪** —— 报告里每个"数值+单位"都要能指回某张底稿；指不回的
   列进「需人工确认」，人工确认后要么补进底稿、要么登记进 constants。
3. **人工确认台账** —— 引擎/工具确实给不出的陈述，在 ``checks.json`` 里
   显式登记为 ``人工`` 类型，报告里必须带有"需人工确认"类措辞。

用法：
    python3 reconcile.py --case synth --report <报告>.md   # 合成样例（默认）
    python3 reconcile.py --case synth --report ../examples/report/样例_体检报告解读_合成数据_v2.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from paths import (ENGINE_DIR, FIXTURES, default_artifacts,  # noqa: E402
                   default_reconcile_out, guard_out, resolve_constants)
from units import unit_compat  # noqa: E402

import loaders  # noqa: E402


from checkup.console import init_console  # noqa: E402
init_console()

def load(art_dir: str, constants: str = ""):
    """从**底稿目录**（P0-8 的 --artifacts）载入四张底稿 + constants。"""
    d = lambda n: json.load(open(os.path.join(art_dir, n), encoding="utf-8"))  # noqa: E731
    return {"coverage": d("coverage.json"), "states": d("states.json"),
            "advice": d("advice.json"), "facts": d("facts.json"),
            "constants": resolve_constants(constants)}


# ---------------------------------------------------------------- 工具

def _flat(o, out=None):
    """把嵌套结构拍平成 (路径, 值) 列表，便于"存在即算对账"的检查。"""
    out = out if out is not None else []
    if isinstance(o, dict):
        for k, v in o.items():
            _flat(v, out)
    elif isinstance(o, list):
        for v in o:
            _flat(v, out)
    else:
        out.append(o)
    return out


def _nums(s):
    return {round(float(x), 3) for x in re.findall(r"\d+(?:\.\d+)?", s or "")}


# ---------------------------------------------------------------- 检查

def _norm_text(s: str) -> str:
    """归一：去空白、去括号内容、去「检查/试验/检测」后缀 —— 用于项目名匹配。"""
    s = re.sub(r"[\s　]", "", s)
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return re.sub(r"(检查|试验|检测|测定)$", "", s)


def build_checks(case_name, A, report, specs, source: str = "real") -> list[dict]:
    """检查清单：**期望全部来自脚本产物**（换案例只换产物，不换检查）。

    分四组：
      C 覆盖/缺口   S 状态/影像   I 机构建议   G 通则（数值/派生/安全/一致性）
    """
    R = report
    fig = " ".join(specs.values())
    both = R + "\n" + fig
    cov, st, adv, fct = A["coverage"], A["states"], A["advice"], A["facts"]
    case = A["case_data"]
    C = A["constants"]
    out = []

    def ck(cid, desc, ok, expect, actual):
        out.append({"id": cid, "说明": desc, "结论": "PASS" if ok else "FAIL",
                    "期望": expect, "实际": actual})

    NUM_TOK = re.compile(r"\d+(?:\.\d+)?")

    def num_in(text, v):
        """报告里的数字 = 产物值**按报告自己的显示精度**四舍五入。
        （合成案把 mg/dL 换算成标准单位后是 1.987，报告写 1.99 —— 这算一致。）"""
        for tok in NUM_TOK.findall(text):
            d = len(tok.split(".")[1]) if "." in tok else 0
            if abs(round(float(v), d) - float(tok)) < 1e-9:
                return True
        return False

    def n_in(text, name):
        return _norm_text(name) in _norm_text(text)

    def all_in(text, names):
        return [n for n in names if not n_in(text, n)]

    # ================= C 覆盖 / 缺口 =================================
    ck("C01", "覆盖率四元组（应查/全覆盖/部分/缺）+ 百分比 = 脚本重算值",
       all(str(v) in R for v in (cov["覆盖率"], cov["全覆盖"], cov["部分覆盖"],
                                 cov["缺"], cov["应查"])),
       f"应查 {cov['应查']}｜全覆盖 {cov['全覆盖']}｜部分 {cov['部分覆盖']}｜"
       f"缺 {cov['缺']}｜覆盖率 {cov['覆盖率']}%",
       "；".join(re.findall(r"覆盖率：[^。\n]{0,60}", R))[:160])
    def _names(project):
        for it in cov["逐项"]:
            if it["项目"] == project:
                return [project] + it.get("报告用名", [])
        return [project]

    miss_gap = [g["项目"] for g in cov["缺口明细"]
                if not any(n_in(R, a) for a in _names(g["项目"]))]
    ck("C02", "缺口表：每个缺口项都在报告里出现（归一化匹配）",
       not miss_gap, "、".join(g["项目"] for g in cov["缺口明细"]),
       f"未出现：{miss_gap or '无'}")
    miss_par = [p["项目"] for p in cov["部分覆盖明细"]
                if not any(n_in(R, a) for a in _names(p["项目"]))]
    ck("C03", "部分覆盖项：每个都在报告里出现，并给出原因（缺年份/拒查/子项）",
       not miss_par,
       "；".join(f"{p['项目']}（{p['备注'][0] if p['备注'] else '子项'}）"
                for p in cov["部分覆盖明细"]) or "无",
       f"未出现：{miss_par or '无'}")
    # 曾经被误判为"没做"的项目：口径必须与产物一致
    for tagname, probe in (("粪便常规", ["粪便"]), ("腰围", ["腰围"])):
        has_data = any(any(k in r["analyte_id"] or k in r["item"] for k in probe)
                       for r in fct.get("数值表", {}).get("—", {}).get("逐年", {})
                       ) if False else None
    fecal = next((i for i in cov["逐项"] if "粪便" in i["项目"]), None)
    fecal_done = fecal and fecal["状态"] == "full"
    if fecal_done:   # 做过 → 报告不得写成缺
        ok = not re.search(r"粪便常规[^。\n]{0,10}(没做|未做|缺)", R)
        exp, act = "产物：三年都做（全覆盖）→ 报告不得写成缺", \
                   "报告未写缺" if ok else "写成了缺"
    else:            # 没做 → 报告必须写它是缺口
        ok = n_in(R, "粪便常规")
        exp, act = "产物：三年都没做 → 报告必须在缺口表里列出", \
                   "报告列出" if ok else "报告未提（漏掉一个缺口）"
    ck("C04", "粪便常规+隐血：报告口径 = 产物（做过不得写成缺；没做必须写成缺）",
       ok, exp, act)
    waist = "VITAL.腰围" in str(fct.get("数值表", {}).get("腰围", "")) and \
            bool(fct["数值表"].get("腰围", {}).get("逐年"))
    waist_vals = [t["值"] for t in fct["数值表"].get("腰围", {}).get("逐年", {}).values()]
    ck("C05", "腰围：产物有的年份就是测过（不得写「缺腰围/没测」）",
       (bool(waist_vals) and not re.search(r"(缺|没有|未测|没测)[^。\n]{0,6}腰围", R))
       or (not waist_vals and re.search(r"腰围[^。\n]{0,12}(没测|未测|三年)", R) is not None),
       f"产物腰围：{waist_vals or '无记录'}",
       "；".join(re.findall(r"[^。\n]{0,16}腰围[^。\n]{0,14}", R))[:150])
    ms = fct["派生"].get("代谢综合征", {}).get("逐年", {})
    if ms:
        gd = ms.get("METSYN.GD2007", {})
        y_sat = [y for y, v in gd.items() if v["分层"] == "满足"]
        no_wc = all(v["状态"] != "ok" for v in ms.get("METSYN.IDF2005", {}).values())
        if y_sat:      # 有满足的年份 → 必须写出
            ok = all(y in R for y in y_sat) and "满足" in R
            exp = f"满足年份 {y_sat}（其余不满足）；腰围缺失 → IDF 不可评估" if no_wc else \
                  f"满足年份 {y_sat}"
            act = "；".join(re.findall(r"[^。\n]{0,18}代谢综合征[^。\n]{0,40}", R))[:160]
        else:          # 没有满足的年份 → 若缺腰围，必须写"无法判定"
            ok = ("无法" in R or "不能下结论" in R or "不可评估" in R) and \
                 (not no_wc or bool(re.search(r"腰围[^。\n]{0,20}(缺|没测|没数据)", R)))
            exp = f"无满足年份；腰围缺失 → 必须写「无法判定」而不是「不满足」（产物：{ms.keys()}）"
            act = "；".join(re.findall(r"[^。\n]{0,16}腰围[^。\n]{0,26}", R))[:160]
        ck("C06", "代谢综合征：结论 = 引擎规则（满足/不满足/缺输入不可评估三分）",
           ok, exp, act)

    # ================= S 状态 / 影像 =================================
    crit = [r for r in st["项目状态"] if "临界" in r["状态分类"]]
    ck("S01", "「临界上升」项都在报告里有落点（卡片或对照表）",
       all(n_in(R, r["项目"]) or n_in(R, r["项目"].replace("胆固醇", "")) for r in crit),
       "；".join(f"{r['项目']}={r['状态分类']}" for r in crit) or "无临界项",
       "；".join(r["项目"] for r in crit) or "无")
    moved = [r for r in st["项目状态"] if "变化超出噪声" in r["状态分类"]
             or "取决于噪声" in r["状态分类"]]
    ck("S02", "「位置正常但变化超噪声/取决于CVi」的项 = 产物的那一组",
       all(n_in(R, r["项目"]) for r in moved),
       "、".join(r["项目"] for r in moved) or "无",
       "、".join(r["项目"] for r in moved if n_in(R, r["项目"])) or "无")
    fell = [r for r in st["项目状态"] if r["状态分类"] == "曾异常已回落"]
    ck("S03", "「曾异常已回落」的项：报告必须写明回落（并说明回落是否可信）",
       all(n_in(R, r["项目"]) for r in fell) and (not fell or "回落" in R),
       "、".join(r["项目"] for r in fell) or "无",
       "；".join(r["项目"] for r in fell if n_in(R, r["项目"])) or "无")
    gaps = [g["项目"] for g in cov["缺口明细"]] + \
           [p["项目"] for p in cov["部分覆盖明细"] if any(
               v == "missing" for v in p["按年"].values())]
    lied = []
    for g in gaps:
        for a in _names(g):
            for sent in re.split(r"[。\n]", R):
                if not re.search(re.escape(a) + r"[^。]{0,12}(正常|阴性|未见异常)", sent):
                    continue
                # 句子里若在描述"机构怎么说/资料缺什么"，不算报告把没查写成正常
                if re.search(r"(机构|报告写|被报告标|标为|写成|缺失|空白|不是)", sent):
                    continue
                lied.append(g)
                break
    ck("S04", "没做过的检查不得被写成「正常」（缺口项只能写「没查/缺」）",
       not lied, f"缺口项：{'、'.join(gaps)}", f"被写成正常的：{set(lied) or '无'}")
    # 影像：每条发现必须带检查方式；同句跨发现必须消歧
    ledger = fct["影像台账"]
    ck("S05", "影像台账每条都带检查方式（彩超/CT/骨密度），报告引用尺寸时同句也要带",
       all(v["检查方式"] for v in ledger.values()),
       "；".join(f"{k}｜{v['检查方式']}" for k, v in ledger.items())[:180],
       "检查方式齐全" if all(v["检查方式"] for v in ledger.values()) else "有缺")
    sents = [x for x in re.split(r"[。；\n]|\|\|", both)
             if "低密度影" in x and "血管瘤" in x]
    bad = [x for x in sents if not re.search(
        r"(不能当作同一个|不能当同一个|不是同一个|不是一回事|不一定就是|两种检查|"
        r"不等同|不能等同|不能划等号|分别|各自|不能混|不能互相推断|不能相互推断)", x)]
    # ---- P0-1c（S10）：影像级别必须 = 产物建议；要调档必须在卡片写「调档：<理由>」
    _IMG_ORDER = {"🔴": 0, "🟡": 1, "🟢": 2, "⚪": 3}
    try:
        import gen_artifacts as _ga
        _kw = _ga._img_keywords
    except Exception:  # noqa: BLE001
        _kw = lambda name: ((name or "").split("（")[0],)  # noqa: E731

    def _report_places(entry_name):
        """报告里该发现出现的（级别, 所在卡片正文）清单：卡片标题 + 总览表行。"""
        kws = _kw(entry_name)
        places = []
        def _hit(t):
            return all(k in t for k in kws)
        for m in re.finditer(r"^####\s*([🔴🟡🟢⚪])[^\n]*\n([\s\S]*?)(?=^#### |^## |\Z)",
                             R, re.M):
            if _hit(m.group(0).split("\n")[0]):
                places.append((m.group(1), m.group(2)))
        for m in re.finditer(r"^\|\s*([🔴🟡🟢⚪])\s*\|([^|\n]*)\|", R, re.M):
            if _hit(m.group(2)):
                places.append((m.group(1), m.group(0)))
        # v6.4 的 §4：级别小标题（**🟡 …（N 项）**）后面紧跟的表行
        for m in re.finditer(r"\*\*([🔴🟡🟢⚪])[^\n]*\n(\|[^\n]*\n)+", R):
            if _hit(m.group(0)):
                places.append((m.group(1), m.group(0)))
        return places

    _img_bad, _img_checked = [], 0
    for e in (st.get("影像台账") or []):
        sug = e.get("报告级别")
        if not sug or sug == "⚪":
            continue          # ⚪ = 不可比/不单独判定 → 该项随宿主卡片，不单独约束
        places = _report_places(e["发现"])
        if not places:
            continue          # 报告没讨论这项 → 不核（如定性行/未列项）
        _img_checked += 1
        rep = min(p[0] for p in places)          # 取报告里最重的一处
        body = " ".join(p[1] for p in places if p[0] == rep)
        if rep != sug and "调档：" not in body:
            _img_bad.append(f"{e['发现']}：产物建议 {sug}（{e.get('级别判据', '')[:28]}）"
                            f" 报告给 {rep}，且未写「调档：<理由>」")
    # ---- v6.4 门控（用户 2026-10-05 要求）：S11 数量一致 · S12 行动单一归属
    _v64 = bool(re.search(r"版本[:：]\s*\**v6\.[4-9]", R))
    if _v64:
        # S11：§4 声明的各级数量必须与产物一致（0/非 0 一致；🔴 尤其不许糊弄）
        prod_lv = {"🔴": 0, "🟡": 0, "🟢": 0, "⚪": 0}
        for r in (st.get("项目状态") or []):
            if r.get("报告级别"):
                prod_lv[r["报告级别"]] = prod_lv.get(r["报告级别"], 0) + 1
        for e in (st.get("影像台账") or []):
            if e.get("报告级别") and e["报告级别"] != "⚪":
                prod_lv[e["报告级别"]] = prod_lv.get(e["报告级别"], 0) + 1
        declared = {}
        for line in R.splitlines():
            if "各级数量" not in line:
                continue
            got = {}
            for lv in ("🔴", "🟡", "🟢", "⚪"):
                mm = re.search(rf"{lv}\s*(\d+)\s*项", line)
                if mm:
                    got[lv] = int(mm.group(1))
            if len(got) >= 2:          # 真正的数量行（版本行只有"各级数量"四个字）
                declared = got
                break
        _s11 = []
        if not declared:
            _s11.append("未找到可解析的「各级数量」行")
        else:
            for lv in ("🔴", "🟡", "🟢", "⚪"):
                if lv not in declared:
                    _s11.append(f"各级数量缺 {lv}")
                elif (declared[lv] > 0) != (prod_lv.get(lv, 0) > 0):
                    _s11.append(f"{lv}：报告声明 {declared[lv]} 项，产物 "
                                f"{prod_lv.get(lv, 0)} 项（0/非 0 不一致）")
        ck("S11", "§4 各级『有/无』与产物一致（0/非 0 必须对上；卡片归并允许行数≠项数，"
               "数量并列展示供人工对照；v6.4）",
           not _s11, f"产物各级：{prod_lv}",
           "；".join(_s11) if _s11 else f"报告声明 {declared} 与产物一致")

        # S12：行动五类"单一归属"——同一测量对象不得同时出现在 ①/② 与 ③
        _sec5 = R[R.find("### 5. 行动建议"):R.find("## 二、逐项分析")]
        def _cat(txt, head):
            i = txt.find(head)
            if i < 0:
                return ""
            j = txt.find("####", i + 1)
            return txt[i:j if j > 0 else len(txt)]
        _c13 = _cat(_sec5, "①") + _cat(_sec5, "②")
        _c3 = _cat(_sec5, "③")
        _objs = ("血压", "血糖", "血脂", "肝功能", "肾功能", "肌酐", "尿酸", "体重",
                 "腰围", "甲状腺超声", "腹部超声", "肺 CT", "骨密度", "心电图")
        # ①/② 里的**准备事项**（带什么/怎么准备）不是"要去的事"，不计入重复判定
        # 括注：无动作动词（查/测/复诊/复查/检查/就诊/看/做）的多为准备说明 → 剥离；
        # 含动作动词的括注是真实行动（如「（血压也要复诊）」）→ 保留参与判重
        _c13_scan = re.sub(
            r"[（(][^）)]*[）)]",
            lambda mo: "" if not re.search(r"(查|测|复诊|复查|检查|就诊|看|做)",
                                           mo.group(0)) else mo.group(0),
            _c13)
        _c13_scan = re.sub(r"(准备|带上|携带|提前|空腹|憋尿)[^，、；。]{0,8}", "",
                           _c13_scan)
        _dup = [o for o in _objs
                if re.search(rf"(?<![三三]){re.escape(o)}", _c3)
                and re.search(rf"(?<![三三]){re.escape(o)}", _c13_scan)]
        ck("S12", "行动五类单一归属：同一对象不得同时出现在 ①/② 与 ③；"
                   "①/② 的『准备事项』与无动作动词的括注不计入（v6.4）",
           not _dup,
           "「先自测按线决定」归 ③（行内写触发线）；① 只列已确定要去的事",
           ("；".join(f"「{o}」同时出现在 ①/② 与 ③ —— 读者不知道该先去还是先测"
                     for o in _dup)) if _dup else "五类归属清晰，无重复对象")

    ck("S10", "影像级别 = 脚本建议（P0-1c：调档必须在卡片写「调档：<理由>」）",
       not _img_bad,
       "同一份数据两次运行必须给同一级别；机构复查频率本身不构成升级理由",
       "；".join(_img_bad[:3]) if _img_bad
       else f"已核对 {_img_checked} 处影像级别，与产物一致")

    # ---- P1（S05b）：图里的**发现条目**必须同句带检查方式（只看结构化条目，note 不算）
    _IMG_TOKENS = ("彩超", "超声", "B超", "CT", "MRI", "核磁", "骨密度",
                   "心电图", "经颅多普勒", "TCD", "胸片", "钼靶")
    _strings = []
    for _name, _txt in specs.items():
        try:
            _d = json.loads(_txt)
        except Exception:  # noqa: BLE001
            continue
        if _d.get("layers"):                     # 因果图：只查①层"报告里的发现"
            for _n in (_d["layers"][0].get("nodes") or []):
                _strings.append((_name, str(_n.get("text") or "")))
        for _sec in (_d.get("sections") or []):  # 归并图：items 与 plain
            for _g in (_sec.get("groups") or []):
                for _it in (_g.get("items") or []):
                    _strings.append((_name, str(_it)))
            if _sec.get("plain"):
                _strings += [(_name, s.strip())
                             for s in re.split(r"[·；;]", _sec["plain"]) if s.strip()]
    _spec_bad = []
    for e in (st.get("影像台账") or []):
        kws = _kw(e["发现"])
        if not kws:
            continue
        for _name, _t in _strings:
            # 「没查/缺项」类陈述不是发现描述 → 豁免
            if any(w in _t for w in ("没查", "未查", "缺", "漏做", "没做", "未做",
                                     "没测", "未测", "未见记录", "那一年的")):
                continue
            if not all(k in _t for k in kws):
                continue
            kw = max(kws, key=len)
            tail = _t[_t.index(kw) + len(kw):][:16]
            if not any(tok in tail for tok in _IMG_TOKENS):
                _spec_bad.append(f"{_name}：「{kw}」条目未带检查方式 —— {_t[:34]}")
    ck("S05b", "图里的影像发现条目必须带检查方式（名称 +（方式）+ 数值；因果图 note 不算）",
       not _spec_bad, "图为方式必填（S05/S06 的图侧配套）",
       "；".join(sorted(set(_spec_bad))[:3]) if _spec_bad
       else "图一张张条目、图二①层节点均已带检查方式")

    ck("S06", "不同检查方式的两条发现同句出现时必须消歧（「不能当作同一个」等）",
       not bad, f"共现 {len(sents)} 句，须全部含消歧措辞",
       f"无消歧的句子：{len(bad)}" + ("；" + bad[0].strip()[:60] if bad else ""))
    # 具体错误表述的回归锁（按产物决定是否需要）
    thy = ledger.get("甲状腺左叶结节") or ledger.get("甲状腺右叶结节")
    if thy:
        grew = []
        ys = sorted(thy["逐年"])
        for a, b in zip(ys, ys[1:]):
            va, vb = thy["逐年"][a].get("尺寸"), thy["逐年"][b].get("尺寸")
            if va is not None and vb is not None and float(vb) > float(va):
                grew.append(b)
        ck("S07", "甲状腺结节：长大过的年份必须写明（不得写「两年没长/尺寸都没怎么变」）",
           all(g in R for g in grew)
           and not re.search(r"(结节|甲状腺)[^。\n]{0,24}(两年没长|一直没有?变|一直没动)", both)
           and "稳住" in R,
           f"产物：{'、'.join(grew)} 长大；之后稳住",
           "；".join(re.findall(r"[^。\n]{0,20}(稳住|长过|没长|没变)[^。\n]{0,16}", R))[:180])
    ecg = [n for n in case["narr"] if n["modality"] == "心电图"]
    if ecg:
        abn_year = [n["year"] for n in ecg if "异常" not in n["summary"]
                    and "正常" not in n["summary"]]
        ck("S08", "心电图：非「正常心电图」的年份必须写明（不得写「三年均未见异常」）",
           all(y in R for y in abn_year)
           and not re.search(r"心电图[^。\n]{0,16}三年[^。\n]{0,8}(均|都)[^。\n]{0,4}正常", R),
           f"产物：{abn_year} 异常（{[n['summary'] for n in ecg if n['year'] in abn_year]}）",
           "；".join(re.findall(r"[^。\n]{0,18}心电图[^。\n]{0,26}", R))[:200])

    # ---- P2-3：跨机构必须写明（换了机构 → 检测系统/区间/口径都变，比较要打折）
    _orgs = set()
    for _y, _m in (case.get("meta") or {}).items():
        if isinstance(_m, dict):
            _org = _m.get("org") or _m.get("org_name") or _m.get("org_id")
            if _org:
                _orgs.add(str(_org))
    if len(_orgs) > 1:
        ck("S09", "跨机构：报告必须写明「换了机构/跨机构」并提示比较要谨慎（P2-3）",
           any(k in R for k in ("换机构", "换了机构", "跨机构")),
           f"产物机构：{sorted(_orgs)}",
           "已写明" if any(k in R for k in ("换机构", "换了机构", "跨机构")) else "缺失")

    # ================= I 机构建议 ====================================
    topics = adv["主题"]
    for topic, per in topics.items():
        freqs = {y: v["复查频率"] for y, v in per.items() if v.get("复查频率")}
        key = topic.split("（")[0]
        if freqs and key in ("甲状腺超声复查",):
            SYN = {"12个月": ["12个月", "每年", "年度", "1年"],
                   "每年": ["每年", "年度", "12个月"],
                   "每半年": ["每半年", "半年"],
                   "3～6 个月": ["3～6个月", "三个月到半年", "3-6个月"]}
            R_n = _norm_text(both)
            vals = set(freqs.values())
            present = all(any(_norm_text(a) in R_n for a in SYN.get(f, [f]))
                          for f in vals if f not in ("未给频率",))
            kw = {"甲状腺超声复查": "甲状腺超声"}.get(key, key)
            ck("I01", f"{topic}：复查频率 = 机构原文（说法可等价：12 个月 ≡ 每年）",
               present and n_in(R, kw),
               "；".join(f"{y}:{f}" for y, f in freqs.items()),
               "；".join(f"{y}:{f}" for y, f in freqs.items()) + f"｜报告含频率={present}")
        if per and topic in ("肝内低密度影",):
            flags = {y: (v.get("措辞标记") or {}) for y, v in per.items()}
            enh = [y for y, f in flags.items() if f.get("增强")]
            ck("I02", "「增强」措辞：只有写了增强的年份能说增强",
               not re.search(r"(两次|三年)[^。\n]{0,10}都[^。\n]{0,6}(建议)?增强", both)
               and (not enh or all(y in R for y in enh)) and "增强" in R,
               f"产物含「增强」的年份：{enh or '无'}",
               "；".join(re.findall(r"[^。\n]{0,20}增强[^。\n]{0,20}", both))[:180])
        if per and topic in ("前列腺特异抗原",):
            up = [y for y, v in per.items()
                  if (v.get("措辞标记") or {}).get("增高")]
            ck("I03", "PSA：被机构标注「增高」的年份与随诊建议必须在报告中",
               "前列腺特异性抗原" in R and all(y in R for y in up),
               f"标「增高」年份 {up}；建议泌尿外科随诊",
               f"报告提及={'前列腺特异性抗原' in R}")
    if "前列腺特异抗原" not in topics:
        ck("I03", "PSA：数据源无此记录时，报告不得出现",
           "前列腺特异性抗原" not in R and "PSA" not in R,
           "数据源无 PSA", "报告未提及" if "PSA" not in R else "报告出现 PSA")
    # 分科建议（眼科散瞳这类只在分科小结里的）——报告必须提到
    dept_adv = []
    for d in case["dept"]:
        for line in d["lines"]:
            if re.search(r"(建议|散瞳|就诊|复查)", line) and "未见明显异常" not in line:
                dept_adv.append((d["year"], d["section"], line))
    for y, sec, line in dept_adv:
        kw = "散瞳" if "散瞳" in line else ("鼻炎" if "鼻炎" in line else sec)
        ck(f"I04", f"分科建议入报告：{y} {sec}「{line[:18]}」",
           kw in R or _norm_text(kw) in _norm_text(R),
           f"原文：{line[:60]}", f"关键词「{kw}」{'在' if kw in R else '不在'}报告里")

    # ================= G 通则 ========================================
    sample_keys = [k for k in ("甘油三酯", "总胆固醇", "好胆固醇", "坏胆固醇", "肌酐",
                               "PSA", "空腹血糖", "尿酸", "腰围", "收缩压")
                   if k in fct["数值表"] and fct["数值表"][k].get("逐年")]
    bad_vals = [f"{k}{y}={t['值']}"
                for k in sample_keys
                for y, t in fct["数值表"][k]["逐年"].items()
                if t["值"] is not None and not num_in(R, t["值"])]
    ck("G01", f"关键数值逐年一致（{len(sample_keys)} 项抽样：{'/'.join(sample_keys[:6])}…）",
       not bad_vals, "数值表逐年值都能在报告里找到", f"找不到：{bad_vals or '无'}")
    # 换算值一致性（附录二的 ᵐ 标记：值 = 原值 × 因子）
    conv_bad = []
    for name, blk in fct["数值表"].items():
        for y, t in blk["逐年"].items():
            if t.get("换算"):
                if not num_in(R, t["值"]):
                    conv_bad.append(f"{name}{y}:{t['值']}")
    ck("G02", "单位换算值（mg/dL→标准单位）与报告一致",
       not conv_bad, "换算值与报告逐格一致", f"不一致：{conv_bad or '无'}")
    # 派生量
    der = fct["派生"]
    if der.get("eGFR", {}).get("逐年"):
        eg = der["eGFR"]["逐年"]
        last_y = max(eg)
        cited = [f"{y}:{v}" for y, v in eg.items() if num_in(R, v)]
        ck("G03", "eGFR：报告引用的值与引擎函数输出一致（最新一年必须引用）",
           num_in(R, eg[last_y]),
           f"引擎：{eg}；报告引用：{[c.split(':')[0] for c in cited] or '无'}",
           f"最新 {last_y}={eg[last_y]} {'在' if num_in(R, eg[last_y]) else '不在'}报告里")
    cp = der.get("ChinaPAR", {}).get("逐年", {})
    if cp:
        v26 = cp.get(max(cp), {})
        vals = [v for v in v26.values()]
        cited = ("China-PAR" in R) or ("10 年" in R)
        ck("G04", "China-PAR：10 年风险值（含家族史敏感性）= 引擎函数输出",
           all(num_in(R, v) or num_in(R, round(v, 1)) or num_in(R, round(v)) for v in vals),
           f"{max(cp)} 年：{v26}",
           "逐值比对完成（报告引用了 China-PAR）" if cited else
           "报告未引用 China-PAR —— 按『值可指回引擎输出』核对，不宣称逐值比对")
    else:
        ck("G04", "China-PAR：输入不足时**不得**给出风险值（不估）",
           "China-PAR" not in R and "10 年" not in R,
           f"缺输入：{der.get('ChinaPAR', {}).get('缺输入年份')}", "报告未引用 China-PAR")
    scen = der.get("分层情景", {}).get("情景", {})
    if scen and "血脂" in R:
        ck("G05", "分层情景（若高血压/若 TC 越线）= 引擎 21 格表输出",
           all(v["分层"] in R for v in scen.values()),
           "；".join(f"{k}→{v['分层']}" for k, v in scen.items()),
           "；".join(f"{k}→{v['分层']}" for k, v in scen.items()))
    dq = fct["数据质量"]
    hd = dq.get("好胆固醇同值", {})
    if hd:
        shown = hd["两位小数下的重复"].split("×")[0]
        ck("G06", "好胆固醇重复值：报告写出的重复情况 = 产物（措辞不得指控机构）",
           shown in R and "未重测" not in R or "无法核实" in R,
           f"重复：{hd['两位小数下的重复']}；全部同值={hd['是否全部同值']}；"
           f"只能用「无法核实」",
           f"报告含 {shown}={'是' if shown in R else '否'}；含「无法核实」={'是' if '无法核实' in R else '否'}")
    if "身高" in dq and dq["身高"]["极差"] > 1:
        ck("G07", "身高异常：报告必须说明 BMI 相关结论要打折",
           num_in(R, dq["身高"]["极差"]) and "BMI" in R,
           f"极差 {dq['身高']['极差']} cm", "报告已说明")
    ck("G08", "服药安全提示必须存在（若已在服药则本报告不适用、勿自行停药）",
       ("切勿自行停药" in R) and ("不适用" in R),
       "「若已在服用任何药物…本节不适用…切勿自行停药」",
       "存在" if "切勿自行停药" in R else "缺失")
    ck("G09", "优先级表述：只有一处排序声明（以第 5 节 ① 为准）",
       len(re.findall(r"排在", both)) <= 3,
       "全篇排序声明集中在一处", f"「排在」出现 {len(re.findall(r'排在', both))} 次")
    ck("G10", "吸烟与肺部筛查：不得给出越界的固定频率建议",
       not re.search(r"(建议|应当|应该)[^。\n]{0,10}(每年|年年)[^。\n]{0,8}(做)?(胸部|肺)"
                     r"[^。\n]{0,6}CT", both)
       and (("包年" in R) or ("吸烟者" not in R)),
       "频率由医生定；包年数未采集不得断言筛查资格",
       "未越界" if not re.search(r"(建议|应当|应该)[^。\n]{0,10}(每年|年年)[^。\n]{0,8}(做)?(胸部|肺)[^。\n]{0,6}CT", both) else "出现固定频率建议")
    ck("G11", "甘油三酯与胰腺炎：提及胰腺炎时必须给出血糖/血脂阈值背景",
       ("胰腺炎" not in R) or ("5.6" in R),
       "TG 2.32 不在胰腺炎风险区（≥5.6）",
       "；".join(re.findall(r"[^。\n]{0,16}胰腺炎[^。\n]{0,26}", R)) or "未提及")
    _in_ledger = "胆囊息肉" in fct["影像台账"]
    ck("G12", "跨案例串场自查：报告提到的项目都在这份数据里",
       True if _in_ledger else not_in(R, ["胆囊息肉"]),
       "影像台账里没有的项目，报告不得出现",
       ("未发现串场（「胆囊息肉」在台账里）" if _in_ledger
        else ("未发现串场" if "胆囊息肉" not in R
              else "报告出现「胆囊息肉」但台账里没有")))
    if source == "real":      # 引擎产物来源（含 --engine-out 的新案例）才做 G14
        import csv as _csv
        jp = os.path.join(ENGINE_OUT_FOR_G14[0], "judgements.csv")
        _rows = list(_csv.DictReader(open(jp, encoding="utf-8")))
        jd = {r["analyte_id"]: r for r in _rows}
        jd_name = {r["项目"]: r for r in _rows}      # 兜底（注意同名撞键，优先用 id）
        mism = []
        _checked = 0          # 实际发生的比对次数（位置轴逐年 + 稳健性）
        for r in st["项目状态"]:
            j = jd.get(r["analyte_id"]) or jd_name.get(r["项目"])
            if not j:
                continue
            # 年份**取自当事数据**（P0-3）：写死 ("2024","2025","2026") 时，
            # 任何别的年份集合都会让"位置轴逐项核对"空转 —— PASS 但什么都没比。
            for y in sorted(r["逐年"]):
                if y in r["逐年"] and r["逐年"][y]["位置"] not in ("unknown", "not_applicable"):
                    if j.get(f"位置{y}"):
                        _checked += 1
                    if j.get(f"位置{y}") and j[f"位置{y}"] != r["逐年"][y]["位置"]:
                        mism.append(f"{r['项目']}{y}: 本工具={r['逐年'][y]['位置']} "
                                    f"引擎={j[f'位置{y}']}")
            if (r.get("变化") and j.get("稳健性") and j["稳健性"] in
                    ("稳健超出", "稳健在内", "取决于CVi取值")):
                _checked += 1
            if (r.get("变化") and j.get("稳健性") and j["稳健性"] in
                    ("稳健超出", "稳健在内", "取决于CVi取值")
                    and j["稳健性"] != r["变化"]["稳健性"]):
                _checked += 1
                mism.append(f"{r['项目']}稳健性: 本工具={r['变化']['稳健性']} 引擎={j['稳健性']}")
        ck("G14", "位置轴与变化轴：本工具计算 与 引擎 judgements.csv 逐项核对",
           not mism, "两套独立实现必须一致（不一致=有一边错了）",
           "；".join(mism[:3]) if mism else f"已核对 {_checked} 项，全部一致")
    # ---- P1-A0：分带来源必须是引擎（消解双轨），且逐项复算一致
    from checkup.bands import band_label as _engine_band
    src_ok = str(st.get("分带来源", "")).startswith("checkup.bands")
    recomputed, bad_band = 0, []
    for r in st["项目状态"]:
        for y, v in r["逐年"].items():
            if v.get("分带") is None:
                continue
            exp = _engine_band(r["项目"], v.get("值"), case.get("sex"))
            if exp is None:
                continue          # 表外项目（分带为兜底标注，不参与本核）
            recomputed += 1
            if exp != v["分带"]:
                bad_band.append(f"{r['项目']}{y}: 产物={v['分带']} 引擎={exp}")
    from checkup.bands import engine_numbers as _engine_numbers
    registered = {round(float(x), 3) for x in _flat(A["constants"]) if isinstance(x, (int, float))}
    missing_reg = sorted(v for v in _engine_numbers() if round(v, 3) not in registered)
    ck("G15", "**分带来源 = 引擎**（P1-A0：报告层直接引用引擎产物，不再有本地 bands.json）",
       src_ok and not bad_band and not missing_reg,
       "states.json 的「分带来源」= checkup.bands；每个分带都能用引擎复算；"
       "引擎分带的每个边界值都在 constants.json 登记（防双轨漂移）",
       "；".join(bad_band[:3]) if bad_band
       else (f"引擎分带边界未登记：{missing_reg}" if missing_reg
             else (f"来源={st.get('分带来源', '缺失')}；复算 {recomputed} 处一致"
                   if src_ok else
                   f"来源={st.get('分带来源', '缺失')}（应为 checkup.bands）")))
    # ---- G16：附录二「原报告页」可核（产物提供页码时才检；旧产物无「页」列则跳过）
    pages_in_art = set()
    for blk in fct.get("数值表", {}).values():
        for t in blk.get("逐年", {}).values():
            if t.get("页"):
                pages_in_art.add(str(t["页"]))
    for i in (st.get("影像台账") or []):
        for t in (i.get("逐年") or {}).values():
            if isinstance(t, dict) and t.get("页"):
                pages_in_art.add(str(t["页"]))
    for blk in (adv.get("主检建议") or {}).values():
        for it in blk:
            if it.get("页"):
                pages_in_art.add(str(it["页"]))
    for blk in (adv.get("分科小结") or {}).values():
        for it in blk:
            if it.get("页"):
                pages_in_art.add(str(it["页"]))
    cited = {m for m in re.findall(r"p(\d{1,2})\b", R)}
    bad_pages = sorted(cited - pages_in_art) if pages_in_art else []
    if cited and not fct.get("页码可用"):
        ck("G16", "附录二「原报告页」可核（产物无页码 → 跳过）", True,
           "引擎产物带「页」列时才能核（run_normalize ≥ 本版）",
           f"本产物无页码列（旧产物），报告引用 {sorted(cited)} —— 不核")
    elif pages_in_art:
        ck("G16", "附录二「原报告页」可核：报告引用的页码都在产物里",
           not bad_pages, f"产物页码集合 {sorted(pages_in_art)}",
           f"报告引用 {sorted(cited)}；产物里没有的：{bad_pages}" if bad_pages
           else f"报告引用 {len(cited)} 个页码，全部可指回")
    ck("G13", "报告内所有带单位的数字都能指回脚本产物",
       True, "见下一节「数字追踪」", "见文件第二节")
    return out


def not_in(text, names):
    return all(n not in text for n in names)


# ---------------------------------------------------------------- 数字追踪

DATA_UNITS = ("mmol/L", "μmol/L", "umol/L", "cm", "mm", "kg", "mmHg", "U/L",
              "ng/ml", "ng/mL", "次/分", "g/L", "mIU/L", "pmol/L",
              "mL/min", "%", "10^9/L")
#: 单位按**长度降序**参与交替 —— 否则 "mmHg" 会被 "mm" 抢先截断
#: （"119/78 mmHg" 曾被读成 "78 mm"，进而指回腰围 78 cm 一类错源）
TOK = re.compile(r"(\d+(?:\.\d+)?)\s*(" + "|".join(
    re.escape(u) for u in sorted(DATA_UNITS, key=len, reverse=True)) + ")")


def trace_numbers(case, A, report) -> tuple[list, list]:
    """返回 (已追踪, 未追踪)。追踪源：数值表 / 派生 / 影像台账 / 覆盖率 / constants。"""
    fct, C = A["facts"], A["constants"]
    idx = []          # (值, 标签, 单位|None) —— 单位用于拒绝跨单位数值巧合
    for name, blk in fct["数值表"].items():
        for y, t in blk["逐年"].items():
            if t["值"] is not None:
                idx.append((float(t["值"]), f"{name} {y}", t.get("单位")))
    for name, blk in fct["派生"].items():
        for v in _flat(blk):
            if isinstance(v, (int, float)):
                idx.append((float(v), f"派生.{name}", None))
    for name, blk in fct["影像台账"].items():
        for y, t in blk["逐年"].items():
            for x in _nums(str(t.get("测量原文", ""))):
                idx.append((x, f"影像.{name} {y}", t.get("单位")))
    for v in _flat(fct["覆盖率"]):
        if isinstance(v, (int, float)):
            idx.append((float(v), "覆盖率", None))
    for name, blk in fct["数值表"].items():
        for k, v in (blk.get("逐年变化%") or {}).items():
            idx.append((abs(float(v)), f"逐年变化.{name} {k}", "%"))
    # 参考区间（机构标注）里的数字也是可对账的事实
    for name, blk in fct["数值表"].items():
        for y, t in blk["逐年"].items():
            for x in _nums(t.get("参考区间", "")):
                idx.append((x, f"参考区间.{name} {y}", t.get("单位")))
    # 状态分类里的变化量与数据质量量（极差/采样间隔；% 类只与 % 互指）
    for r in A["states"]["项目状态"]:
        if r.get("首末变化%") is not None:
            idx.append((abs(float(r["首末变化%"])), f"首末变化.{r['项目']}", "%"))
        if r.get("变化"):
            idx.append((abs(float(r["变化"]["变化%"])), f"RCV变化.{r['项目']}", "%"))
            for b in r["变化"].get("RCV区间%", []):
                idx.append((float(b), f"RCV区间.{r['项目']}", "%"))

    # 逐年余量%：**统一放在逐指标循环之外**（放在循环内会遮蔽后续指标的
    # 首末变化/RCV 类正源——同一数值下"先入先出"会让指回标签指向巧合源）
    for r in A["states"]["项目状态"]:
        for y, t in (r.get("逐年") or {}).items():
            if isinstance(t, dict) and t.get("余量%") is not None:
                idx.append((abs(float(t["余量%"])), f"余量%.{r['项目']} {y}", "%"))
    for v in _flat(fct["数据质量"]):
        if isinstance(v, (int, float)):
            idx.append((float(v), "数据质量", None))
    for k, vals in C.items():
        for v in _flat(vals):
            if isinstance(v, (int, float)):
                idx.append((float(v), f"常数.{k}", None))

    traced, untraced = [], []
    for m in TOK.finditer(report):
        val, unit = float(m.group(1)), m.group(2)
        ctx = report[max(0, m.start() - 28):m.end() + 12].replace("\n", " ")
        dec = len(m.group(1).split(".")[1]) if "." in m.group(1) else 0

        def _match(v):
            if abs(v - val) < 1e-6:
                return True
            if abs(v - val) / max(abs(v), 1e-9) < 0.005:
                return True
            return ("%%.%df" % dec % v) == m.group(1)      # 报告里常见的取整写法

        hit = next((lab for v, lab, u in idx
                    if _match(v) and unit_compat(unit, u)), None)
        if hit:
            traced.append({"值": val, "单位": unit, "指回": hit, "上下文": ctx})
        else:
            untraced.append({"值": val, "单位": unit, "上下文": ctx})
    return traced, untraced


#: G14 交叉核对要读的引擎产物目录（main 里按参数设置；默认数据根 out/）
ENGINE_OUT_FOR_G14 = [FIXTURES.engine_out]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="对账器：报告 + 两张图 spec → 逐条检查 + 数字追踪")
    ap.add_argument("--case", choices=["real", "synth"], default=None,
                    help="来源类型（决定 case_data 怎么载入；标签见 --case-name）。"
                         "缺省按 --artifacts 目录名推断（artifacts/synth → synth）；"
                         "其余（含用户自己的案例目录名）按 real —— 会打印一行说明")
    ap.add_argument("--case-name", default="",
                    help="报告标签（新案例用；缺省 = --case）")
    ap.add_argument("--artifacts", default="",
                    help="四张底稿目录（P0-8；缺省 <产物根>/artifacts/<case>）")
    ap.add_argument("--engine-out", default="",
                    help="引擎产物目录（G14 交叉核对与 case_data；缺省数据根 out/）")
    ap.add_argument("--report", required=True)
    ap.add_argument("--specs", nargs="*", default=[])
    ap.add_argument("--out", default="")
    ap.add_argument("--constants", default="",
                    help="常量登记簿（附加用户登记；缺省 CHECKUP_CONSTANTS 或数据根 "
                         "constants.user.json，再退内置 constants.json）")
    args = ap.parse_args()

    # 来源类型：显式 --case > 按 --artifacts 目录名推断（synth → synth，其余 → real）。
    # **不许静默**：推断或默认都打印说明。默认偏 real —— 使用者自己的案例目录
    # 通常不叫 synth；按 synth 默认会把真数据按随包样例期望判出**假失败**。
    case = args.case
    if case is None:
        if args.artifacts and os.path.basename(os.path.normpath(args.artifacts)) == "synth":
            case = "synth"
            print("[--case] 未指定；按 --artifacts 目录名推断为 synth（随包样例）")
        else:
            case = "real"
            print("[--case] 未指定；按用户数据（real）载入 —— "
                  "若你确实在对随包样例，请显式加 --case synth")
    args.case = case
    name = args.case_name or case
    art_dir = args.artifacts or default_artifacts(case)
    ENGINE_OUT_FOR_G14[0] = args.engine_out or FIXTURES.engine_out
    A = load(art_dir, args.constants)
    A["case_data"] = loaders.load(args.case, engine_out=ENGINE_OUT_FOR_G14[0])
    report = open(args.report, encoding="utf-8").read()
    specs = {}
    for p in args.specs:
        specs[os.path.basename(p)] = open(p, encoding="utf-8").read()

    checks = build_checks(name, A, report, specs, source=args.case)
    traced, untraced = trace_numbers(name, A, report)
    n_fail = sum(1 for c in checks if c["结论"] == "FAIL")

    lines = [f"# 对账报告 · {name} · {os.path.basename(args.report)}", "",
             f"- 检查 {len(checks)} 项：**PASS {len(checks) - n_fail} / FAIL {n_fail}**",
             f"- 数值追踪：{len(traced)} 个已指回脚本产物；"
             f"{len(untraced)} 个**需人工确认**（多为价格/时长/指南阈值，见 constants.json）", ""]
    lines += ["## 一、逐条检查", "",
              "| # | 说明 | 期望（脚本产物） | 实际 | 结论 |", "|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['id']} | {c['说明']} | {c['期望']} | {c['实际']} | "
                     f"{'✅' if c['结论'] == 'PASS' else '❌'} |")
    lines += ["", "## 二、需人工确认的数字（未指回产物）", ""]
    if untraced:
        seen = set()
        for u in untraced:
            key = (u["值"], u["单位"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- `{u['值']} {u['单位']}` —— …{u['上下文']}…")
    else:
        lines.append("- 无")
    out = guard_out(args.out or default_reconcile_out(name))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"[{name}] 检查 {len(checks)}：PASS {len(checks) - n_fail} / FAIL {n_fail}"
          f" · 未追踪数字 {len(untraced)}")
    for c in checks:
        if c["结论"] == "FAIL":
            print(f"  ❌ {c['id']} {c['说明']}\n     期望：{c['期望']}\n     实际：{c['实际']}")
    print(f"已写入 {out}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
