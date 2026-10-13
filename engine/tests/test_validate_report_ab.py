#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v6.4「A/B 类声明」形态检查的回归测试。    python3 tests/test_validate_report_ab.py

来历：`references/report-form-v6.4.md` 明文要求「**A/B 类声明必须存在**」
（本报告的"红"**不等于**官方 A/B 类危急值），但 `scripts/validate_report.py`
与 `reconcile/reconcile.py` 都没查过它 —— 一份声明了 v6.4 的真实报告整篇
没有这句话，六道定稿门槛照样全绿。这条测试把缺的那一关锁住：

* **(a) 有声明**（四种写法变体：规范句 / 全角 Ａ／Ｂ + 🔴 + 折行 / 「红」+ ≠ /
  A、B + 无引号）→ 不报，且合成报告整体通过；
* **(b) 声明 v6.4 而整篇没有该声明** → 必报，消息含「A/B」「危急值」与应写原句，
  并指出该句应落在 §4「问题分级总览」附近；
* **(c) 不带版本声明的旧报告**（v6.3 语义）→ 不追溯，不因缺这句而失败；
* **(d) 与既有检查互不干扰**：缺章节 / 禁用话术照旧能报，A/B 那条与它们并存。

另加一组**精度**测试：三件（① A/B 或 A、B 或 A 类/AB 类 ② 危急值 ③ 否定关系）
缺任何一件、或隔着两行以上，都不算声明 —— 既不误报也不假通过。

文本全部现场合成，**不引用任何真实报告**。
"""
from __future__ import annotations

import os
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

from checkup.console import init_console  # noqa: E402
import validate_report as VR  # noqa: E402

TAG = "[v6.4·A/B声明]"          # 失败条目的标签（check() 里的 AB_DECL_TAG）

#: 四种写法变体——**每一种都该通过**。判据不是逐字认某一句话。
DECL_VARIANTS = [
    # ① 规范句（照 report-form-v6.4 §4；直引号 + **加粗**）
    '⚠️ **本报告的"红"不等于官方 A/B 类危急值**（血压 ≥180/110 伴急性症状、'
    'ALT ≥15 倍上限、肌酐 ≥707 μmol/L）——那是机构**紧急召回**口径；'
    '本报告的分级是给**个人健康管理**用的，更细、更早。',
    # ② 全角 Ａ／Ｂ + 🔴 +「并非」，且句子被换行截断（相邻两行）
    '本报告标的 🔴 并非机构那份紧急召回名单——\n'
    'Ａ／Ｂ 类危急值（血压 ≥180/110 伴急性症状）另有一套阈值，别混。',
    # ③ 「红」+ ≠ + AB 类 + "不一样"
    '本报告里的「红色」 ≠ 官方 **AB 类危急值**，两套口径不一样。',
    # ④ A、B 写法 + 无引号 + "两回事"
    '🔴 与官方 A、B 类危急值是两回事——本报告只管健康管理。',
]


def _ab_fails(fails: list[str]) -> list[str]:
    """只挑出 A/B 类声明那一条（其余既有检查的条目不算）。"""
    return [f for f in fails if f.startswith(TAG)]


def _check(text: str) -> list[str]:
    fails, _ = VR.check(text, VR.DEFAULT_REQUIRE, VR.DEFAULT_FORBID)
    return fails


def _report(version: str | None = None, decl: str | None = None, *,
            extra: str = "", drop: str = "") -> str:
    """合成一份最小 v6.4 报告：形态齐备、无禁用话术、无越界。

    ``version=None`` → 不写版本行（旧报告）；``decl=None`` → 不放 A/B 声明；
    ``extra`` 追加一段（用来触发既有检查）；``drop`` 删掉的字符串
    （用来制造"缺章节"）。
    """
    ver = f"- 版本：**{version}**（本合成文本仅用于测试）\n" if version else ""
    ab = f"\n{decl}\n" if decl else ""
    doc = f"""# 合成体检报告解读（测试用，非真实报告）

{ver}
## 前提假设声明
年龄 41 · 性别 女 · 未服药 · 无诊断 · 不吸烟 · 无家族史。

## 目录
- 一、总体评估
- 二、逐项分析
- 健康状况总览 / 综合判断 / 正常与改善项
- 问题分级总览 / 行动建议
- 需干预或定期复查的项 / 年度复查即可的项
- 附录一：检查项目完整性评估
- 附录二：数值对照表
- 免责声明

## 一、总体评估

### 1. 健康状况总览
（合成）两句话说完。

### 2. 综合判断
（合成）第一句就是好消息。

### 3. 正常与改善项
（合成）血压 118/72 mmHg。

### 4. 问题分级总览
各级数量：🔴 0 项 · 🟡 1 项 · 🟢 1 项 · ⚪ 0 项
{ab}
**🔴 已达诊断或前期标准，需尽快就诊**：本次没有。
**🟡 需要干预或定期复查**：1 项。
**🟢 正常或轻微**：1 项。
**⚪ 未评估**：0 项。

### 5. 行动建议
（合成）按 ①…⑤ 分类的表。

## 二、逐项分析

#### 🟡【黄色】合成指标 —— 略高于参考范围
**结果**：12.0（参考 0–10）。
**白话解释**：（合成）这一项偏了一点。
**最坏会怎样**：（合成）长期偏着不好。
**要吃药吗**：目前不吃。
**严重度**：轻。
**趋势**：2024 年 9.0 → 2026 年 12.0（合成）。
**复查**：下次体检一起复查。

## 附录一：检查项目完整性评估
（合成）覆盖率四列小表。

## 附录二：数值对照表
（合成）项目 | 单位 | 参考范围 | 2026 | 原报告页

## 免责声明
（合成）本解读不构成诊断，不替代医生面诊。
{extra}
"""
    return doc.replace(drop, "") if drop else doc


# ------------------------------------------------------------------ (a) 有声明

def test_v64_with_declaration_is_not_flagged():
    """(a) 声明 v6.4 + 有该声明（含变体写法）→ 不报；合成报告整体通过。"""
    for i, decl in enumerate(DECL_VARIANTS, 1):
        fails = _check(_report("v6.4", decl))
        assert not _ab_fails(fails), f"变体 {i} 被误报：{decl[:30]}… → {_ab_fails(fails)}"
        assert fails == [], f"变体 {i} 的合成报告应整体通过，实际：{fails}"


def test_v64_declaration_may_wrap_across_two_lines():
    """(a) 子项：句子被换行截断（跨相邻两行）仍算命中 —— 别只认整行。"""
    wrapped = '本报告标的 🔴，与官方\nA/B 类危急值不是一套口径。'
    assert not _ab_fails(_check(_report("v6.4", wrapped))), wrapped


def test_ab_decl_line_hit_is_never_blank():
    """(a) 子项：命中时返回值必须**非空**且本身就能判成命中。

    写第一版时它会把空行当命中行返回（声明在空行的下一行时）—— 消费方
    ``if ab_decl_line(text):`` 会把 ``""`` 当成"没命中"，扫全仓时把真实
    报告误判为缺声明。这里把该不变量钉住。
    """
    for i, decl in enumerate(DECL_VARIANTS, 1):
        hit = VR.ab_decl_line(_report("v6.4", decl))
        assert hit and hit.strip(), f"变体 {i} 命中却返回空：{hit!r}"
        assert VR._decl_hit(hit), f"变体 {i} 返回的这一段自己就不满足判据：{hit!r}"
    # 声明前面紧挨一个空行（真实报告的排法）时也一样
    spaced = _report("v6.4", DECL_VARIANTS[0])
    assert VR.ab_decl_line(spaced), spaced[:80]
    assert VR.ab_decl_line("\n\n本报告的「红」不等于官方 A/B 类危急值。\n")


# ------------------------------------------------------------------ (b) 缺声明

def test_v64_without_declaration_fails_with_actionable_message():
    """(b) 声明 v6.4 + 整篇没有该声明 → FAIL；消息含 A/B 与 危急值，并给出原句。"""
    text = _report("v6.4", None)
    fails = _check(text)
    ab = _ab_fails(fails)
    assert len(ab) == 1, f"应恰好报一条 A/B 声明缺失：{fails}"
    msg = ab[0]
    assert "A/B" in msg and "危急值" in msg, msg
    assert "不等于" in msg, f"消息里应给出该写的原句：{msg}"
    assert "问题分级总览" in msg and "§4" in msg, f"应指出该放的位置：{msg}"


def test_declaration_is_the_only_difference_in_an_otherwise_clean_report():
    """(b) 精度：同一份合成报告，只差这一句 —— 加上它即刻全绿，别的都不差。"""
    without, with_ = _report("v6.4", None), _report("v6.4", DECL_VARIANTS[0])
    f_without, f_with = _check(without), _check(with_)
    assert f_with == [], f_with
    assert set(f_without) - set(f_with) == set(_ab_fails(f_without)), \
        sorted(set(f_without) - set(f_with))


# ------------------------------------------------------------------ (c) 旧报告不追溯

def test_v63_report_without_declaration_is_not_retroactive():
    """(c) 不带版本声明的旧报告（v6.3 语义）→ 不受影响，不因缺声明而失败。"""
    old = _report(None, None)
    fails = _check(old)
    assert not _ab_fails(fails), fails
    assert not [f for f in fails if f.startswith("[v6.4")], \
        f"v6.4 形态分支不该对旧报告生效：{fails}"
    assert fails == [], f"旧报告本来就该整体通过：{fails}"


def test_same_text_fails_only_when_it_declares_v64():
    """(c) 对照：同一段文字，声明 v6.4 才报、不声明就不报（追溯边界就在版本行）。"""
    assert not _ab_fails(_check(_report(None, None)))
    assert _ab_fails(_check(_report("v6.4", None)))


# ------------------------------------------------------------------ (d) 互不干扰

def test_ab_check_coexists_with_existing_prose_and_section_checks():
    """(d) 有声明时，既有「缺章节」「禁用话术」照旧能报，且不误报 A/B 那条。"""
    text = _report("v6.4", DECL_VARIANTS[0], extra="⑤ 习惯：每周适当运动三次。",
                   drop="附录二：数值对照表")
    fails = _check(text)
    assert not _ab_fails(fails), fails
    assert any("附录二：数值对照表" in f for f in fails), fails
    assert any("适当运动" in f for f in fails), fails


def test_ab_failure_coexists_with_existing_failures():
    """(d) 缺声明时，A/B 那条与既有条目**并存**（互不吞并）。"""
    text = _report("v6.4", None, extra="⑤ 习惯：每周适当运动三次。",
                   drop="附录二：数值对照表")
    fails = _check(text)
    assert len(_ab_fails(fails)) == 1, fails
    assert any("附录二：数值对照表" in f for f in fails), fails
    assert any("适当运动" in f for f in fails), fails


# ------------------------------------------------------------------ 精度（防擦边球假通过）

def test_needs_all_three_components():
    """三件缺一不可：只有 A/B + 危急值（无否定）、或有否定却不提 A/B 与危急值 → 报。"""
    negatives = [
        "官方 A/B 类危急值包括：血压 ≥180/110 伴急性症状。",        # 缺否定
        "机构给的 A/B 类危急值阈值是另一套，本报告不管。",            # 缺否定
        "本报告的「红」不是「绿」——两回事。",                        # 缺 A/B 与 危急值
    ]
    for bad in negatives:
        fails = _check(_report("v6.4", bad))
        assert len(_ab_fails(fails)) == 1, f"负例应报而没报：{bad!r} → {fails}"


def test_components_must_be_adjacent():
    """三件分处**相邻两行之外**不算命中（防隔很远也"拼"出一句声明）。

    这里 ①（「红」+ 否定）在第 1 行、②＋①（A/B 类危急值）在第 4 行 ——
    中间隔着两行内容行，任何"本行 / 本行+下一行"的窗口都凑不齐三件。
    """
    far = ("本报告的「红」不等于「绿」。\n"
           "机构的口径是另一套风控节奏。\n"
           "这里还有别的一行。\n"
           "它把 A/B 类危急值单列了。\n")
    fails = _check(_report("v6.4", far))
    assert len(_ab_fails(fails)) == 1, f"隔行拼装不该通过：{fails}"


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
