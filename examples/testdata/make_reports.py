#!/usr/bin/env python3
"""生成 3 年 / 2 机构 的合成中文体检报告（纯虚构，无任何真实个人数据）。

刻意构造跨机构差异，用于测试"跨年对齐"能力：
  - 同一项目不同命名（谷丙转氨酶 / ALT / 丙氨酸氨基转移酶）
  - 同一项目不同单位（mmol/L vs mg/dL）
  - 同一项目不同参考区间
  - 定性描述（甲状腺结节 3mm，TI-RADS 3 类 vs C-TIRADS 3 类）
  - 缺失项（某年未做）

输出：.txt（纯文本版）与 .pdf（文字版 PDF，用 PyMuPDF 生成）

说明（与工作区原版的差异）：本文件的渲染器改为**按 .txt 的显示列逐词定位**
（等效等宽排版），使 PDF 的列位与 .txt 一致、且与真实机构 PDF（每格独立定位）
同构；报告文本本身逐字未改（与同目录 .txt 互为真源）。
"""

import re
import unicodedata
from pathlib import Path

import fitz  # PyMuPDF

OUT = Path(__file__).parent

# ---------------------------------------------------------------------------
# 报告一：2023 年，仁和健康体检中心（机构 A）—— 全部用中文名 + 常规单位
# ---------------------------------------------------------------------------
R2023 = """仁和健康体检中心
年度健康体检报告

体检编号: RH-2023-0412-0088
姓  名: 受检者A        性  别: 男
年  龄: 41 岁          体检日期: 2023-04-12
身  高: 175 cm         体  重: 78 kg
BMI: 25.5 (超重)

=========================== 一般检查 ===========================
项目                结果            参考范围        状态
血压                132/86 mmHg     <130/85 mmHg    ↑偏高
静息心率            76 bpm          60-100 bpm      正常

=========================== 血常规 ===========================
项目                结果            参考范围        状态
白细胞 WBC          6.2 x10^9/L     3.5-9.5         正常
血红蛋白 HGB        151 g/L         130-175         正常
血小板 PLT          205 x10^9/L     125-350         正常

=========================== 肝功能 ===========================
项目                结果            参考范围        状态
谷丙转氨酶          52 U/L          9-50            ↑偏高
谷草转氨酶          34 U/L          15-40           正常
谷氨酰转肽酶 GGT    68 U/L          10-60           ↑偏高

=========================== 肾功能 ===========================
项目                结果            参考范围            状态
肌酐                88 umol/L       57-111              正常
尿素氮              5.4 mmol/L      3.1-8.0             正常
尿酸                498 umol/L      208-428             ↑偏高

=========================== 血脂 ===========================
项目                结果            参考范围        状态
总胆固醇            5.60 mmol/L     <5.2            ↑偏高
甘油三酯            1.90 mmol/L     <1.7            ↑偏高
低密度脂蛋白 LDL-C  3.62 mmol/L     <3.4            ↑偏高
高密度脂蛋白 HDL-C  0.98 mmol/L     >1.04           ↓偏低

=========================== 血糖 ===========================
项目                结果            参考范围        状态
空腹血糖            5.8 mmol/L      3.9-6.1         正常

=========================== 甲状腺 ===========================
项目                结果            参考范围        状态
TSH                 2.61 mIU/L      0.27-4.20       正常
FT3                 4.72 pmol/L     3.1-6.8         正常
FT4                 15.8 pmol/L     12.0-22.0       正常

=========================== 影像检查 ===========================
腹部B超: 肝脏形态大小正常，实质回声稍增强，未见明确占位。
         胆囊、胰腺、脾脏、双肾未见明显异常。
         诊断: 轻度脂肪肝。
甲状腺B超: 甲状腺右叶见一低回声结节，大小约 3 mm，边界清，形态规则。
           诊断: 甲状腺右叶结节（TI-RADS 3 类），建议随访。

=========================== 尿常规 ===========================
项目                结果            状态
尿蛋白              阴性 (-)        正常
尿糖                阴性 (-)        正常

=========================== 家族史 ===========================
父亲: 高血压病史, 62 岁时脑梗死。母亲: 2 型糖尿病。

=========================== 异常指标汇总及建议 ===========================
1. 谷丙转氨酶轻度升高 52 U/L, 结合B超提示轻度脂肪肝, 建议消化内科随诊。
2. 血脂异常 (TC 5.60, TG 1.90, LDL-C 3.62, HDL-C 0.98), 建议低脂饮食。
3. 尿酸偏高 498 umol/L, 建议限制高嘌呤饮食, 多饮水。
4. 血压边缘偏高 132/86 mmHg, 建议家庭血压监测。
5. 甲状腺右叶结节 3 mm (TI-RADS 3 类), 建议 12 个月复查甲状腺超声。

报告日期: 2023年04月12日
"""

# ---------------------------------------------------------------------------
# 报告二：2024 年，康泰综合医院（机构 B）—— 英文缩写名 + 美制单位 + 不同参考区间
# ---------------------------------------------------------------------------
R2024 = """康泰综合医院
健康体检报告单

报告编号: KT20240921-1174
姓名: 受检者A           性别: 男
年龄: 42 岁             检查日期: 2024-09-21
身高: 175 cm            体重: 80 kg
BMI: 26.1

-------------------- 一般项目 --------------------
项目                    结果            参考值          提示
血压 BP                 138/88 mmHg     <140/90        正常
脉搏                    78 次/分        60-100         正常

-------------------- 血细胞分析 --------------------
项目                    结果            参考值          提示
WBC 白细胞              6.8 x10^9/L     4.0-10.0       正常
HGB 血红蛋白            149 g/L         130-175        正常
PLT 血小板              212 x10^9/L     125-350        正常

-------------------- 肝功能 --------------------
项目                    结果            参考值          提示
丙氨酸氨基转移酶 ALT    61 U/L          0-40           H
天门冬氨酸氨基转移酶 AST 36 U/L         0-40           正常
r-谷氨酰转移酶 GGT      79 U/L          0-60           H

-------------------- 肾功能 --------------------
项目                    结果            参考值          提示
CREA 肌酐               1.02 mg/dL      0.60-1.20      正常
BUN 尿素氮              14 mg/dL        7-20           正常
UA 尿酸                 8.4 mg/dL       3.5-7.2        H

-------------------- 血脂 --------------------
项目                    结果            参考值          提示
CHOL 总胆固醇           218 mg/dL       <200           H
TG 甘油三酯             176 mg/dL       <150           H
LDL-C 低密度脂蛋白      141 mg/dL       <130           H
HDL-C 高密度脂蛋白      38 mg/dL        >40            L

-------------------- 血糖 --------------------
项目                    结果            参考值          提示
GLU 葡萄糖(空腹)        108 mg/dL       70-110         正常
HBA1C 糖化血红蛋白      5.9 %           4.0-6.0        正常

-------------------- 甲状腺功能 --------------------
项目                    结果            参考值          提示
TSH                     2.88 mIU/L      0.27-4.20      正常
FT3                     4.65 pmol/L     3.10-6.80      正常
FT4                     15.1 pmol/L     12.0-22.0      正常

-------------------- 超声检查 --------------------
腹部超声: 肝实质回声增强、细密，后方回声轻度衰减。诊断: 轻度脂肪肝。
甲状腺超声: 甲状腺右叶低回声结节，大小约 4 mm，边界清晰，形态规则，
           未见微钙化。诊断: 甲状腺右叶结节 (C-TIRADS 3 类)，建议随访。

-------------------- 尿常规 --------------------
尿蛋白    阴性(-)
尿糖      阴性(-)

-------------------- 结论与建议 --------------------
1. ALT 61 U/L、GGT 79 U/L 升高，脂肪肝，建议消化内科就诊。
2. 血脂异常，建议心内科评估。
3. 尿酸偏高，建议低嘌呤饮食。
4. 甲状腺结节 4 mm (C-TIRADS 3 类)，建议 12 个月复查。

检查日期: 2024年09月21日
"""

# ---------------------------------------------------------------------------
# 报告三：2025 年，康泰综合医院（机构 B）—— 沿用美制单位；某项目缺失
# ---------------------------------------------------------------------------
R2025 = """康泰综合医院
健康体检报告单

报告编号: KT20251010-2031
姓名: 受检者A           性别: 男
年龄: 43 岁             检查日期: 2025-10-10
身高: 175 cm            体重: 83 kg
BMI: 27.1

-------------------- 一般项目 --------------------
项目                    结果            参考值          提示
血压 BP                 145/92 mmHg      <140/90        H
脉搏                    80 次/分         60-100         正常

-------------------- 血细胞分析 --------------------
项目                    结果            参考值          提示
WBC 白细胞              7.1 x10^9/L     4.0-10.0       正常
HGB 血红蛋白            147 g/L         130-175        正常
PLT 血小板              218 x10^9/L     125-350        正常

-------------------- 肝功能 --------------------
项目                    结果            参考值          提示
丙氨酸氨基转移酶 ALT    74 U/L          0-40           H
天门冬氨酸氨基转移酶 AST 42 U/L         0-40           H
r-谷氨酰转移酶 GGT      92 U/L          0-60           H

-------------------- 肾功能 --------------------
项目                    结果            参考值          提示
CREA 肌酐               1.08 mg/dL      0.60-1.20      正常
BUN 尿素氮              15 mg/dL        7-20           正常
UA 尿酸                 9.1 mg/dL       3.5-7.2        H

-------------------- 血脂 --------------------
项目                    结果            参考值          提示
CHOL 总胆固醇           226 mg/dL       <200           H
TG 甘油三酯             201 mg/dL       <150           H
LDL-C 低密度脂蛋白      148 mg/dL       <130           H
HDL-C 高密度脂蛋白      36 mg/dL        >40            L

-------------------- 血糖 --------------------
项目                    结果            参考值          提示
GLU 葡萄糖(空腹)        116 mg/dL       70-110         H
HBA1C 糖化血红蛋白      6.3 %           4.0-6.0        H
（注: 本次未检测胰岛素、C肽）

-------------------- 甲状腺功能 --------------------
项目                    结果            参考值          提示
TSH                     3.10 mIU/L      0.27-4.20      正常
FT3                     4.51 pmol/L     3.10-6.80      正常
FT4                     14.8 pmol/L     12.0-22.0      正常

-------------------- 超声检查 --------------------
腹部超声: 肝实质回声增强、细密，后方回声明显衰减。诊断: 中度脂肪肝。
甲状腺超声: 甲状腺右叶低回声结节，大小约 4 mm，边界清晰，形态规则。
           诊断: 甲状腺右叶结节 (C-TIRADS 3 类)，与前次相仿。

-------------------- 尿常规 --------------------
尿蛋白    阴性(-)
尿糖      阴性(-)

-------------------- 结论与建议 --------------------
1. ALT 74 U/L、AST 42 U/L、GGT 92 U/L 均升高，脂肪肝加重，建议消化内科就诊。
2. 血糖升高 (GLU 116 mg/dL, HbA1c 6.3%)，达糖尿病前期，建议内分泌科。
3. 血压 145/92 mmHg，达高血压诊断标准，建议心内科。
4. 血脂持续异常，建议心内科评估调脂治疗。
5. 甲状腺结节 4 mm，与前次相仿，建议继续年度复查。

检查日期: 2025年10月10日
"""

REPORTS = {
    "2023-04-12_仁和健康体检中心": R2023,
    "2024-09-21_康泰综合医院": R2024,
    "2025-10-10_康泰综合医院": R2025,
}


# 用系统里同时含中日韩与比例西文的字体，避免内置 CJK 字体把 ASCII 排成全角
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]

#: 左 margin 与半角字符宽 —— 模拟等宽排版：x = X0 + display_col * CW
X0 = 42.0
CW = 4.25
LINE_H = 11.5
FONT_SIZE = 8.5


def _pick_font() -> str | None:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _dwidth(s: str) -> int:
    """显示列宽：CJK/全角记 2 列，其余记 1 列（与 .txt 的对齐口径一致）。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s)


#: .txt 里表格的**名义列位**（显示列）：项目 / 结果 / 参考值 / 提示
_TXT_COLS = [0, 24, 40, 55]

#: 「标签格」形如 ``姓名:`` ``检查日期:`` —— 头部信息块，不是表格行
_LABEL_CELL = re.compile(r"^[^\s:：]+[:：]")

#: 长项目名格把「数值 单位」挤在同一格（.txt 的排版瑕疵）→ 拆开还原成两列
_MERGED_VALUE = re.compile(
    r"^(?P<name>.+?)\s+(?P<val>[<>≤≥]?\d+(?:\.\d+)?)\s+"
    r"(?P<unit>[A-Za-z%/μµ][^\s]*)$")


def _cells_of(line: str) -> list[tuple[int, str]]:
    """按 2+ 空格切格 → [(起始显示列, 格内容)]；单空格保留在格内。"""
    col, out = 0, []
    for part in re.split(r"(\s{2,})", line):
        if not part.strip():
            col += _dwidth(part)
            continue
        out.append((col, part))
        col += _dwidth(part)
    return out


def _split_merged_value(cell: str) -> tuple[str, str] | None:
    """「天门冬氨酸氨基转移酶 AST 36 U/L」→ (名称, "36 U/L")；不是则 None。

    .txt 是逐项真源、也是随包的对账输入，一字不改；但个别长项目名行在
    .txt 里把数值挤进了名称格（排版瑕疵）。渲染 PDF 时把它还原到结果列 ——
    内容（名称/数值/单位）不变，只是列位规范化，使 PDF 成为规范表格。
    """
    m = _MERGED_VALUE.match(cell)
    if not m or _LABEL_CELL.match(cell):
        return None
    return m.group("name"), f"{m.group('val')} {m.group('unit')}"


def _table_cells(line: str) -> list[str] | None:
    """把一行整理成"表格行"的格列表；不是表格行返回 None。"""
    cells = [c for _, c in _cells_of(line)]
    if not (2 <= len(cells) <= 4):
        return None
    if _LABEL_CELL.match(cells[0]):        # 头部信息块（姓名:/检查日期:）
        return None
    merged = _split_merged_value(cells[0])
    if merged:
        cells = [merged[0], merged[1], *cells[1:]]
    return cells[:4]


def _column_plan(lines: list[str]) -> list[int]:
    """为表格行算**安全的固定列位**（显示列）。

    为什么不能直接用 .txt 的列位：抽取层按 x 的"最近列锚点"归属词，而 .txt
    的列距偏紧（长项目名格的尾巴会越过「项目列×结果列」的中线 → 被抽成结果）。
    这里按每个格实际最宽内容反推列距：**第 i+1 列的起点 > 第 i 列起点 +
    2×最宽内容宽**，保证任何格的任何词都落在自己一侧的中线内。
    """
    widest = [0, 0, 0, 0]
    for line in lines:
        cells = _table_cells(line)
        if not cells:
            continue
        for i, c in enumerate(cells):
            widest[i] = max(widest[i], _dwidth(c))
    cols = [_TXT_COLS[0]]
    for i in range(1, 4):
        cols.append(max(_TXT_COLS[i], cols[i - 1] + 2 * widest[i - 1] + 2))
    return cols


def _draw_line(page, line: str, fontname: str, y: float,
               cols: list[int]) -> None:
    """把一个文本行渲染到 PDF。

    **为什么按单元格逐格定位**：.txt 的"列"是空格对齐出来的显示列；如果整行
    一次 insert_text（比例字体），列位会随字宽漂移，而抽取层是按 **x 坐标**
    列定位的，列位一漂移就抽错列。这里每个格各自定位到它那一列（表格行用
    ``cols`` 的计划列位，其余行照 .txt 原样），等效于"每格独立定位"的真实
    机构 PDF；格内做整体插入（保留单空格），保证 word 切分稳定。
    """
    raw = _cells_of(line)
    planned: list[str] | None = None
    if cols and _table_cells(line) is not None:
        planned = []
        for _, cell in raw:
            merged = _split_merged_value(cell) if not planned else None
            if merged:                     # 首格「名称 数值 单位」→ 拆成两列
                planned += [merged[0], merged[1]]
            else:
                planned.append(cell)
        planned = planned[:4]
    if planned:
        for i, cell in enumerate(planned):
            page.insert_text((X0 + cols[i] * CW, y), cell,
                             fontname=fontname, fontsize=FONT_SIZE)
    else:
        for col, cell in raw:
            page.insert_text((X0 + col * CW, y), cell,
                             fontname=fontname, fontsize=FONT_SIZE)


def make_pdf(text: str, path: Path) -> None:
    """用 PyMuPDF 生成一个"文字版 PDF"（有文本层，非扫描件）。"""
    doc = fitz.open()
    page = doc.new_page()  # A4 默认 595x842 pt
    fontfile = _pick_font()
    fontname = "F0"
    if fontfile:
        page.insert_font(fontname=fontname, fontfile=fontfile)
    else:
        fontname = "china-s"  # 退化方案
    lines = text.split("\n")
    cols = _column_plan(lines)
    y = 50.0
    for line in lines:
        if y > 800:
            page = doc.new_page()
            if fontfile:
                page.insert_font(fontname=fontname, fontfile=fontfile)
            y = 50.0
        _draw_line(page, line, fontname, y, cols)
        y += LINE_H
    # 子集化嵌入字体：否则整份 CJK 字体被打进每个 PDF（单份 ~23 MB）
    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="生成合成中文体检报告（纯虚构，无任何真实个人数据）")
    ap.add_argument("--only", nargs="*", default=None,
                    help="只生成名字含这些关键字的报告（如 2024 2025）；"
                         "缺省生成全部 3 份")
    ap.add_argument("--out", default=str(OUT), help="输出目录（缺省=脚本所在目录）")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in REPORTS.items():
        if a.only and not any(k in name for k in a.only):
            continue
        (out / f"{name}.txt").write_text(text, encoding="utf-8")
        make_pdf(text, out / f"{name}.pdf")
        print(f"生成: {name}.txt / {name}.pdf")
    print("\n注意: 以上全部为虚构数据, 不含任何真实个人信息。")
