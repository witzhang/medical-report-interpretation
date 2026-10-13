#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D3 叙述层的回归测试。

金标准来自人工读报告得出的 [因果导图.md] A11 表，以及旧管线
``体检分析/data/summary_all.csv``。任何一条挂掉都说明抽取器退化。

可直接跑，也可用 pytest:
    python3 tests/test_narrative.py
    pytest tests/
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.narrative import extract_report  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
TPL = PATHS.legacy_template()   # 原内置默认版式（P0-2 后移入测试夹具）
PATHS.require_legacy()
SRC = PATHS.real_reports
LEGACY = PATHS.legacy("summary_all.csv")

PDFS = {
    "2024": "20240612体检报告.pdf",
    "2025": "20250507体检报告.pdf",
    "2026": "20260609体检报告.pdf",
}

_cache: dict = {}


def report(year: str):
    if year not in _cache:
        _cache[year] = extract_report(os.path.join(SRC, PDFS[year]), year, TPL)
    return _cache[year]


def narratives(year: str):
    return {i.item: i for i in report(year)[1]}


def scalars(year: str):
    return {o.item_raw: o for o in report(year)[2]}


def findings(year: str):
    return report(year)[3]


# ------------------------------------------------------------------ 日期

def test_exam_date_taken_from_inner_page():
    """体检日期一律取自内页，**不用文件名**。

    2026 那份文件名是 20260609，内页 16 处全部是 2026-08-20，相差 72 天。
    用文件名会直接推翻「年龄是否录入错误」的结论。
    """
    assert report("2024")[0].exam_date == "2024-06-12"
    assert report("2025")[0].exam_date == "2025-05-07"
    assert report("2026")[0].exam_date == "2026-08-20", \
        "2026 必须用内页日期 2026-08-20，不是文件名里的 20260609"


# ------------------------------------------------- 甲状腺：按语义归侧

def test_thyroid_side_attribution_2024():
    it = narratives("2024")["甲状腺"]
    got = {(m.side, m.raw) for m in it.measurements}
    assert got == {("右", "0.3x0.2cm"), ("左", "0.3x0.3cm")}, got
    assert (it.grade_system, it.grade_value) == ("TI-RADS", "2类")


def test_thyroid_side_attribution_2025_left_written_first():
    """2025 年原文把**左侧写在前面**。

    若按出现位置归侧，右侧会被读成 0.3x0.2 → 0.6x0.5 → 0.5x0.3，
    凭空造出「先跳升再回落」的假趋势（坑 19 的形状）。必须按语义归侧。
    """
    it = narratives("2025")["甲状腺"]
    got = {(m.side, m.raw) for m in it.measurements}
    assert got == {("左", "0.6x0.5cm"), ("右", "0.5x0.3cm")}, got
    assert (it.grade_system, it.grade_value) == ("TI-RADS", "3类")


def test_thyroid_side_attribution_2026_cystic_solid():
    it = narratives("2026")["甲状腺"]
    got = {(m.side, m.raw, m.descriptor) for m in it.measurements}
    assert got == {("右", "0.5x0.3cm", "囊实性"), ("左", "0.6x0.4cm", "囊实性")}, got
    assert (it.grade_system, it.grade_value) == ("TI-RADS", "2类")


def test_tirads_must_not_leak_to_neck_vessels():
    """小结块的每一行是一条独立小结，不能被粘成一行。

    粘行的后果：「甲状腺双侧叶结节（TI-RADS2类）」与「颈部血管未见明显异常」
    合并 → TI-RADS 误挂到颈部血管上。
    """
    for y in ("2024", "2025", "2026"):
        nv = narratives(y)["颈部血管"]
        assert nv.grade_system is None, f"{y} 颈部血管不该有分级，实得 {nv.grade_system}"
        assert nv.summary_lines == ["颈部血管未见明显异常"], nv.summary_lines


# ------------------------------------------------------ 影像三年趋势

def test_liver_hemangioma_three_years():
    """肝血管瘤：3.2x3.0 → 4.3x3.1 → 3.9x3.1 cm（呈「先跳升再回落」）。"""
    for y, want in (("2024", "3.2x3.0cm"), ("2025", "4.3x3.1cm"), ("2026", "3.9x3.1cm")):
        it = narratives(y)["肝脏"]
        assert want in [m.raw for m in it.measurements], (y, [m.raw for m in it.measurements])


def test_gallbladder_polyp_three_years():
    """胆囊息肉：0.4 → 0.5 → 0.4 cm。"""
    for y, want in (("2024", "长径约0.4cm"), ("2025", "长径约0.5cm"), ("2026", "长径约0.4cm")):
        it = narratives(y)["胆囊"]
        assert want in [m.raw for m in it.measurements], (y, [m.raw for m in it.measurements])


def test_liver_fat_attributed_to_liver():
    """2024 的肝脏小结含两条：肝血管瘤 + 脂肪肝（轻度）。"""
    it = narratives("2024")["肝脏"]
    assert "肝血管瘤" in it.summary_lines
    assert "脂肪肝（轻度）" in it.summary_lines


def test_bone_density_2025_absent():
    """骨密度只有 2024 与 2026 做；2025 **没做**。

    缺失 ≠ 阴性——不能把 2026 的 0.6 说成相对 2025 的变化。
    """
    assert "骨密度测值" in narratives("2024")
    assert "骨密度测值" not in narratives("2025")
    assert "骨密度测值" in narratives("2026")
    assert narratives("2024")["骨密度测值"].measurements[0].value == 0.8
    assert narratives("2026")["骨密度测值"].measurements[0].value == 0.6


def test_ct_channel_is_captured():
    """CT 叙述必须抽到。

    项目标签在 CT 页是**无冒号**形态（``肺部CT | 胸廓对称…``），且段名写作
    「CT检查」而不是「CT」——这两点任一没处理，整个 CT 通道就会静默丢失
    （双肺结节这类最需要随访的发现全在 CT 里）。
    """
    for y in ("2024", "2026"):
        it = narratives(y).get("肺部CT")
        assert it is not None, f"{y} 缺 CT 叙述项"
        assert it.modality == "CT", (y, it.modality)
        assert "条索影" in it.description_verbatim or "结节" in it.description_verbatim, \
            it.description_verbatim[:60]
        assert it.summary_lines, f"{y} CT 小结为空"


def test_chest_ct_missing_in_2025():
    """2025 **没做胸部 CT**。

    缺失 ≠ 阴性——不能把 2026 的肺结节说成相对 2025「新增」。
    """
    assert "肺部CT" not in narratives("2025")
    assert "肺部CT" in narratives("2024") and "肺部CT" in narratives("2026")
    # 2025 也没有骨密度
    assert "骨密度测值" not in narratives("2025")


def test_ecg_section_alias():
    """段名「心电图检查」与「心电图」必须归一到同一检查方式。"""
    for y in ("2024", "2025", "2026"):
        ecg = [i for i in report(y)[1] if i.item == "心电图"]
        assert ecg, f"{y} 心电图丢失"
        assert ecg[0].modality == "心电图", (y, ecg[0].modality)


def test_bone_density_has_no_unit():
    """骨密度无单位无部位——跟骨超声与 DXA 腰椎量纲不同，不可比。"""
    m = narratives("2024")["骨密度测值"].measurements[0]
    assert m.unit == "", "骨密度本就是无单位数值，抽到单位反而不对"


# ------------------------------------------------------ 标量（体格）

def test_key_scalars_match_reports():
    """体格检查关键项——这些是 combo.py/ascvd.py 去手工字典（D5）要用的值。"""
    want = {
        "2024": {"身高": 181.5, "体重": 72.1, "收缩压": 109.0, "舒张压": 74.0,
                 "腰围": 78.0, "臀围": 91.0},
        "2025": {"身高": 179.5, "体重": 72.9, "收缩压": 139.0, "舒张压": 84.0,
                 "腰围": 81.0, "臀围": 93.0},
        "2026": {"身高": 184.0, "体重": 70.1, "收缩压": 119.0, "舒张压": 78.0,
                 "腰围": 78.0, "臀围": 91.0},
    }
    for y, exp in want.items():
        got = scalars(y)
        for k, v in exp.items():
            assert got[k].value == v, f"{y} {k}: 期望 {v}，实得 {got[k].value}"


def test_two_column_row_splits_both_items():
    """体格检查页是**双栏**「标签:值」表，一行两项。

    不切段的话右栏会被并进左栏，体重/收缩压/腰围会整批丢失。
    """
    got = scalars("2024")
    for k in ("身高", "体重", "收缩压", "舒张压", "腰围", "臀围"):
        assert k in got, f"{k} 丢失——双栏切段失效"


def test_qualifier_captured():
    """「21.9,体重正常」要拆成数值与定性结论。"""
    o = scalars("2024")["体重指数"]
    assert o.value == 21.9
    assert o.qualifier == "体重正常"


# ------------------------------------------------------ 机构结论

def test_institution_findings_match_legacy_pipeline():
    """机构自报异常项必须与旧管线 summary_all.csv 逐条一致。"""
    for y in ("2024", "2025", "2026"):
        assert len(findings(y)) == {"2024": 11, "2025": 7, "2026": 11}[y]

    if not os.path.exists(LEGACY):
        return
    old: dict = {}
    with open(LEGACY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            old.setdefault(r["year"], []).append(r["title"])
    for y in ("2024", "2025", "2026"):
        got = [v.title for v in findings(y)]
        assert got == old[y], f"{y}\n 新:{got}\n 旧:{old[y]}"


def test_main_report_section_carries_across_pages():
    """2026 的「主要疾病诊断」跨到第 6 页继续编号，段标题不重复。

    不继承 section，续页的条目会因 section 为空被整批丢掉。
    """
    got = [(v.page_no, v.no, v.title) for v in findings("2026")]
    assert (6, 8, "过敏性鼻炎") in got, got
    assert (6, 9, "鼻中隔偏曲") in got, got
    assert (6, 10, "屈光不正") in got, got
    # 第 6 页还有一个从 1 重新开始的新段
    assert (6, 1, "左肺陈旧性病变") in got, got


def test_noise_rows_excluded():
    """封面信、签名行、页眉页脚不得进入任何通道。

    封面那封 1400 字的信曾被当成一条标量读数（跨页累积所致）。
    """
    for y in ("2024", "2025", "2026"):
        for bad in ("先生", "医师", "保健号", "体检日期", "ID"):
            assert bad not in scalars(y), f"{y} 混入了噪音项 {bad}"
        o = scalars(y)
        assert max((len(v.value_text) for v in o.values()), default=0) < 200, \
            f"{y} 出现了超长文本，疑似跨页累积"


# ------------------------------------------------------------------ 主程序

# ------------------------------------------ 段标题判定的边界（变异测试指出）

def test_section_header_length_boundary():
    """段标题判定里 ``len(t) > 8`` 是**严格**上界：恰好 8 字仍算候选。

    变异测试发现 ``>`` 改成 ``>=`` 无人管 —— 边界值从没构造过。
    这一条决定"这一行是不是段标题"，判错会让整行被丢或混进项目里。
    """
    from checkup.narrative import _is_section_header
    sec_max = 70.0          # 默认模板的 section_x_max
    assert _is_section_header("A" * 8, 50.0, 1, TPL) in (True, False)
    # 恰好 8 字且只有一个词、x 在列内、无冒号数字 → 判为段标题
    assert _is_section_header("八个字八个字八个", 50.0, 1, TPL) is True
    # 9 字 → 超长，不算
    assert _is_section_header("九个字九个字九个字", 50.0, 1, TPL) is False


def test_section_header_column_boundary_is_inclusive():
    """``x0 <= section_x_max`` —— **恰好等于上限仍算**。"""
    from checkup.narrative import _is_section_header
    lim = PATHS.legacy_template().narrative.section_x_max
    assert _is_section_header("彩超", lim, 1, TPL) is True
    assert _is_section_header("彩超", lim + 0.1, 1, TPL) is False


def test_section_header_rejects_digits_and_colons():
    """带数字或冒号的不是段标题（那是数据行）。"""
    from checkup.narrative import _is_section_header
    for bad in ("2024年", "身高:", "体重：", "1.5"):
        assert _is_section_header(bad, 50.0, 1, TPL) is False, bad


# ------------------------------------------ 别名/分类的真缺口（变异测试指出）
#
# 上一轮我以为 narrative 的存活"多是等价变异体"。**那个假设是错的** ——
# 逐条核实后发现几处是真缺口：改一个 `and`/`or` 就能改变行为，而**没有任何
# 测试调过触发它的那个输入**。

def test_lab_like_section_is_not_a_narrative_modality():
    """``X检查`` 的别名展开**必须两者都成立**（后缀是「检查」**且**前缀是已知模态）。

    变异测试发现 ``and`` 改成 ``or`` 无人管 —— 因为从没调过
    ``canonical_modality("血液检查")``。改错之后它会返回 ``"血液"``，
    把一个**化验段**当成叙述模态。
    """
    from checkup.narrative import canonical_modality
    t = PATHS.legacy_template()
    # 已知模态 + 后缀 → 展开
    assert canonical_modality("彩超检查", t) == "彩超"
    assert canonical_modality("CT检查", t) == "CT"
    # **化验段名**不得被展开
    assert canonical_modality("血液检查", t) is None, \
        "「血液检查」不是叙述模态，不该被 X检查 规则展开"
    assert canonical_modality("尿液检查", t) is None
    assert canonical_modality("粪便检查", t) is None


def test_cover_page_requires_all_three_markers():
    """封面判定是 ``"您好" in t and "感谢" in t and "主要疾病诊断" not in t``。

    变异测试发现 ``and`` 改成 ``or`` 无人管。改成 ``or`` 之后，只要出现
    「主要疾病诊断」（那是**主检报告**的标志）就会被判成**封面**并整页跳过。
    """
    import fitz
    from checkup.narrative import classify_page

    def kind(text):
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 60), text, fontname="china-s", fontsize=10)
            return classify_page(page, PATHS.legacy_template())
        finally:
            doc.close()

    # 三个条件齐 → 封面
    assert kind("您好 感谢 尊敬的客户") == "cover"
    # **有「主要疾病诊断」→ 是主检报告，不是封面**（哪怕前两个词也在）
    assert kind("您好 感谢 主要疾病诊断") == "main_report"
    # 只有「您好」→ 不是封面
    assert kind("您好 某某医院") != "cover"


def test_real_reports_classify_as_expected():
    """本机报告的页分类必须与已知结构一致（3 份合计 35 页）。"""
    import fitz
    from checkup.narrative import classify_page
    counts = {}
    for fn in sorted(os.listdir(SRC)):
        if not fn.lower().endswith(".pdf"):
            continue
        doc = fitz.open(os.path.join(SRC, fn))
        try:
            for i in range(doc.page_count):
                k = classify_page(doc[i], PATHS.legacy_template())
                counts[k] = counts.get(k, 0) + 1
        finally:
            doc.close()
    assert counts.get("lab", 0) == 6, counts      # 每份 2 页化验单
    assert counts.get("main_report", 0) >= 3, counts
    assert sum(counts.values()) == 35, counts


def test_bare_number_fallback_is_bounded():
    """裸数值兜底（骨密度「0.8」）**只在受限条件下**生效。

    条件是 ``len(clauses) == 1 or (idx == len(clauses)-1 and len(clause) <= 12)``。
    变异测试把里面四个运算符逐个改过，**没有一个被测试抓住** ——
    因为这个兜底从没被正面测过，也没被反面测过。
    放宽它会把叙述里的数字误当测量值。
    """
    from checkup.narrative import _split_measurements
    # 单一分句的裸数值 → 采纳（骨密度的形态）
    one = _split_measurements("0.8")
    assert one and abs(one[0].value - 0.8) < 1e-9, [m.raw for m in one]
    # 多分句里的**非末尾**裸数值 → 不采纳
    multi = _split_measurements("0.8，另见 1.2")
    assert all("0.8" != m.raw for m in multi), [m.raw for m in multi]
    # 末尾但很长的分句 → 不采纳（超过 12 字）
    long_tail = _split_measurements("某某某，这是一个很长的描述句子后面跟着 0.8")
    assert all("0.8" != m.raw for m in long_tail), [m.raw for m in long_tail]


def test_descending_order_key_covers_all_changes():
    """分类页的键必须覆盖全部页 —— 本机 35 页一个不漏。

    变异测试发现分页/循环里的 ``+``/``-`` 改动无人管；这条锁住"总数"这个
    比页码更本质的关系。
    """
    import fitz
    from checkup.narrative import classify_page
    total = 0
    for fn in sorted(os.listdir(SRC)):
        if fn.lower().endswith(".pdf"):
            doc = fitz.open(os.path.join(SRC, fn))
            try:
                total += doc.page_count
            finally:
                doc.close()
    assert total == 35, total


def test_dept_summary_keeps_eye_advice():
    """分科小结通道：2025 年眼科的「散瞳查眼底」**必须保留**。

    这是真实漏过的一条机构建议 —— 眼科/内科/外科这类非影像科室不产出
    NarrativeItem，小结行曾整段丢弃。若这条测试挂掉，说明又丢原文了。
    """
    from checkup.narrative import extract_dept_summaries
    eye = {d.year: d for d in extract_dept_summaries(
        os.path.join(SRC, PDFS["2025"]), "2025", TPL)}
    lines = next(d.lines for d in eye.values() if d.section == "眼科")
    assert "屈光不正" in lines, lines
    assert "散瞳查眼底" in lines, lines


def test_dept_summary_not_repeated_for_modality_sections():
    """影像/功能科室的小结归叙述通道，**不得**在分科小结里重复出现。

    这条防的是"多开一条通道顺手把结论抄第二遍"——同一句原文出现在两处，
    下游对账就无法判断该信哪一个。
    """
    from checkup.narrative import extract_dept_summaries
    for year in PDFS:
        secs = {d.section for d in extract_dept_summaries(
            os.path.join(SRC, PDFS[year]), year, TPL)}
        for banned in ("彩超", "CT", "心电图", "经颅多普勒", "骨密度"):
            assert banned not in secs, (year, banned, secs)


def test_dept_summary_departments_match_pages():
    """分科小结的科室集合逐年可比对 —— 2026 年科室最多（含内科/外科/耳鼻喉科）。

    2024/2025 套餐里没有这些科室；这直接支撑"覆盖率按年重算"这一结论。
    """
    from checkup.narrative import extract_dept_summaries
    got = {y: sorted({d.section for d in extract_dept_summaries(
        os.path.join(SRC, PDFS[y]), y, TPL)}) for y in PDFS}
    assert got["2024"] == ["一般项目", "眼科"], got["2024"]
    assert got["2025"] == ["一般项目", "眼科"], got["2025"]
    assert got["2026"] == ["一般项目", "内科", "外科", "眼科", "神经内科",
                           "耳鼻喉科"], got["2026"]


def test_finding_detail_continues_across_page():
    """主检条目的说明与建议**跨页不断**：2026 第 7 条横跨 p5/p6。

    已踩的坑：`cur` 每页重置 → 续页首行因"没有当前条目"被静默丢弃，
    第 7 条的「建议您肝胆外科随诊，定期复查」整句消失。建议原文是对账的
    依据（"机构到底写没写增强/建议什么"），截断会让判断失真。
    """
    fs = findings("2026")
    it = next(f for f in fs if f.title == "肝血管瘤")
    assert "多发可见片状稍低密度" in it.detail, it.detail
    assert "建议您肝胆外科随诊，定期复查" in it.detail, it.detail


def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, e))
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((name, e))
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
