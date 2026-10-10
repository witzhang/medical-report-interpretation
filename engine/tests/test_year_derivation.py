#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""年份一律从数据推导（P0-3 回归锁）。

两条锁：
1. **变化对全部两两展开**：3 年数据 = 3 对；4 年数据必须出现 6 对
   （写死 (0,1)(1,2)(0,2) 会漏掉 4 年样本的 (2,3) 等中间相邻对）。
2. **输出层的年份列 = 数据里实际出现的年份**：换成 2023/2024/2025 时，
   三视图不得出现 2026（写死年份会得到空列）。

本文件**不依赖数据根**（用内存构造的观测），随包即可跑。

    python3 tests/test_year_derivation.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.judge import judge_all, judge_change  # noqa: E402
from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.render import render_clinical  # noqa: E402
from checkup.schema import NormalizedObservation  # noqa: E402
from checkup.tables import _decorated_section  # noqa: E402
from checkup.templates import LabTemplate, NarrativeTemplate, Template  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 用一个最小的合成目录：一条指标 + 它规范单位
CAT = AnalyteCatalog(
    analytes=[__import__("checkup.schema", fromlist=["x"]).AnalyteDefinition(
        analyte_id="CHEM.甘油三酯", name="甘油三酯", specimen="",
        value_type="numeric", canonical_unit="mmol/L", loinc_code=None,
        aliases=[], org_id="TEST", section="血脂", confirmed=True)],
    version="test")


def _obs(year: str, value: float, unit: str = "mmol/L",
         aid: str = "CHEM.甘油三酯") -> NormalizedObservation:
    return NormalizedObservation(
        year=year, page_no=1, section="血脂", item_raw="甘油三酯",
        occurrence=1, analyte_id=aid, value=value, value_type="numeric",
        unit=unit, ref_low=None, ref_high=1.7)


CVI = {"CHEM.甘油三酯": {"cvi": 20.0, "cvi_low": 15.0, "cvi_high": 25.0,
                         "n_studies": 9}}


def test_change_pairs_all_pairs_for_four_years():
    """4 年数据 → 6 对两两比较；3 年 → 3 对（保持原行为）。"""
    four = [_obs(y, 1.0 + 0.1 * i) for i, y in enumerate(
        ("2021", "2022", "2023", "2024"))]
    j = judge_change(four, dict(CVI), catalog=CAT)["CHEM.甘油三酯"]
    assert set(j.change) == {"2021→2022", "2021→2023", "2021→2024",
                             "2022→2023", "2022→2024", "2023→2024"}, sorted(j.change)
    three = [_obs(y, 1.0 + 0.1 * i) for i, y in enumerate(
        ("2023", "2024", "2025"))]
    j3 = judge_change(three, dict(CVI), catalog=CAT)["CHEM.甘油三酯"]
    assert set(j3.change) == {"2023→2024", "2024→2025", "2023→2025"}, sorted(j3.change)


def test_views_use_data_years_only():
    """三视图的年份列 = 数据年份：2023/2024/2025 时不得出现 2026。"""
    obs = [_obs("2023", 3.0), _obs("2024", 2.0), _obs("2025", 1.0)]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=CAT)
    text = render_clinical(js, [], [])
    for y in ("2023", "2024", "2025"):
        assert y in text, f"三视图缺年份列 {y}"
    assert "2026" not in text, "三视图出现数据里不存在的年份 2026"


def test_change_detail_header_uses_data_pairs():
    """变化轴明细的表头按**实际变化对**展开，不写死 Δ24→25 系列。"""
    from checkup.render import render_audit
    obs = [_obs("2023", 3.0), _obs("2024", 2.0), _obs("2025", 1.0)]
    js = judge_all(obs, os.path.join(BASE, "catalog", "cvi.json"), catalog=CAT)
    text = render_audit([], js, [], [], None)
    assert "Δ2023→2025" in text, "变化轴明细缺 Δ2023→2025 列"
    assert "Δ24→25" not in text, "变化轴明细仍在写死 Δ24→25"


def test_section_title_with_decorations():
    """装饰线包住的段标题要能取到段名（===== 血常规 ===== / ---- 肝功能 ----）。"""
    assert _decorated_section("===== 血常规 =====") == "血常规"
    assert _decorated_section("--------------------一般项目--------------------") == "一般项目"
    assert _decorated_section("──────── 一般检查 ────────") == "一般检查"
    # 守卫：表头/数据行不得被当成段标题
    assert _decorated_section("项目 结果 参考值") == ""
    assert _decorated_section("---- 2024 年 ----") == ""
    assert _decorated_section("检验项目") == ""


def test_flag_cells_are_ignored():
    """提示/状态列（第四列）的词不混进参考值（模板声明 flag_cells 时）。"""
    t = Template(org_id="TEST", lab=LabTemplate(flag_cells=["提示"]))
    assert t.lab.flag_cells == ["提示"]
    # 兼容性：老模板不带该键 → 默认空列表，行为不变
    t2 = Template.from_dict({"org_id": "TEST", "lab": {"page_marker": "报告单"}})
    assert t2.lab.flag_cells == []


# ------------------------------------------------------------------ 主程序

def _main() -> int:
    from checkup.console import init_console
    init_console()
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed, skipped = [], 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failed.append(name)
            print(f"  ✗ {name}\n      {e}")
        except BaseException as e:  # noqa: BLE001
            if type(e).__name__ in ("Skipped", "DataMissing"):
                skipped += 1
                print(f"  · {name} 跳过：{e}")
                continue
            failed.append(name)
            print(f"  ! {name} 异常: {type(e).__name__}: {e}")
        else:
            print(f"  ✓ {name}")
    done = len(tests) - skipped
    print(f"\n{done - len(failed)}/{done} 通过" + (f"（跳过 {skipped}）" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
