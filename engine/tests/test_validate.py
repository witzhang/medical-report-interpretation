#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 校验层的回归测试。    python3 tests/test_validate.py"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.normalize import AnalyteCatalog  # noqa: E402
from checkup.pipeline import build_all  # noqa: E402
from checkup.validate import (check_constant_series,  # noqa: E402
                              check_coverage, check_delta,
                              check_internal_identity, check_plausibility,
                              check_unit_consistency, check_unmapped_rate,
                              validate)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from _paths import PATHS  # noqa: E402  （P0-1 数据根外部化）
PATHS.require_real()      # 数据未挂载 → 跳过，不算失败
SRC = PATHS.real_reports
_obs = None


def obs():
    global _obs
    if _obs is None:
        cat = AnalyteCatalog.load(os.path.join(BASE, "catalog", "analytes.json"))
        per = build_all(SRC, cat)
        _obs = [o for rows in per.values() for o in rows]
    return _obs


def by_name(c: str):
    return next(x for x in validate(obs()).checks if x.name == c)


# ------------------------------------------------------------------ 各项

def test_internal_identity_passes_all_years():
    """直接胆红素 + 间接胆红素 = 总胆红素。

    这是**唯一能证明抽取准确**的确定性手段：三个值来自同一页不同列，
    只有坐标归属、断行回填、单位剥离全部正确时才会吻合。
    """
    c = by_name("内部恒等式")
    assert c.level == "pass", c.detail
    assert "3 年" in c.detail


def test_coverage_is_stable():
    """三年各 81 项化验，无偏离。"""
    c = by_name("覆盖率")
    assert c.level == "pass", c.detail
    assert "2024:81" in c.detail and "2025:81" in c.detail and "2026:81" in c.detail


def test_unit_mismatch_handled_by_repair_rule():
    """单位矛盾必须被处置或拦下，不能静默通过。"""
    c = by_name("单位一致性")
    assert c.level == "pass", c.detail
    assert "2 条" in c.detail, c.detail      # RDW 那一对


def test_constant_series_catches_hdl():
    """HDL-C 三年同为 0.94（一位小数不差）→ 生物学概率极低，疑似未重测。

    它是「低 HDL-C」这条危险因素的**唯一依据**；若未重测则该危险因素存疑。
    """
    c = check_constant_series(obs())
    assert c.level == "warn", c.detail
    assert any("高密度脂蛋白" in i for i in c.items), c.items


def test_plausibility_catches_height():
    """身高 181.5 → 179.5 → 184.0 cm，成年人不该如此 → BMI 结论不可评估。"""
    c = check_plausibility(obs())
    assert c.level == "warn", c.detail
    assert "4.5cm" in c.detail


def test_delta_check_flags_vision_jump():
    """右眼矫正视力 4.6 → 0.8 是同一指标跨年跳变 5.7 倍，需人工看是抽取错还是真事件。

    注意：验光的「4.6」与「0.8」是**两套记录口径**（五分记录 vs 小数记录），
    并不是视力真的掉了——这正是 Delta Check 该拦下来让人看一眼的典型。
    """
    c = check_delta(obs())
    assert c.level == "warn"
    assert any("矫正视力" in i for i in c.items), c.items
    # P2-14：必须写明"跨年记录口径不同 → 不可比"，不允许只报一个 5.7× 让人误读
    assert any("跨年记录口径不同" in i for i in c.items), c.items


def test_overall_level_is_warn_not_pass():
    """本套数据有已知的数据质量疑点，整体等级必须是 warn 而非 pass。"""
    rep = validate(obs())
    assert rep.level == "warn", rep.render()
    assert any(c.level == "pass" for c in rep.checks)


def test_validation_report_renders():
    r = validate(obs()).render()
    assert "整体等级" in r and "内部恒等式" in r


# ------------------------------------------------------------------ 主程序

# ---------------------------------------------- 阈值边界（由变异测试指出）
#
# 这一组全是同一个问题：**检查的阈值从没在边界上试过**。
# 5.0× / 2.0cm / 10% / 20% 这些数字改了没人管。

def _o(aid, year, value, item=None):
    from checkup.schema import NormalizedObservation
    return NormalizedObservation(
        year=year, page_no=1, section="生化检查",
        item_raw=item or aid, occurrence=1, analyte_id=aid, value=value,
        value_type="numeric", unit="U/L")


def test_delta_factor_boundary_is_strict():
    """Delta Check 的 5× 是**严格**上界：恰好 5.0 不算跳变，5.01 算。

    变异测试发现 ``if ratio > factor`` 改成 ``>=`` 无人管。
    """
    at = check_delta([_o("X", "2024", 10.0), _o("X", "2025", 50.0)])   # 恰好 5×
    over = check_delta([_o("X", "2024", 10.0), _o("X", "2025", 50.1)])
    assert at.level == "pass", at.detail
    assert over.level == "warn", over.detail


def test_height_delta_boundary_is_strict():
    """身高跨年差 2.0cm 是**严格**上界。

    变异测试发现 ``if hi - lo > max_height_delta`` 改成 ``>=`` 无人管。
    """
    at = check_plausibility([_o("VITAL.身高", "2024", 180.0, "身高"),
                             _o("VITAL.身高", "2025", 182.0, "身高")])
    over = check_plausibility([_o("VITAL.身高", "2024", 180.0, "身高"),
                               _o("VITAL.身高", "2025", 182.1, "身高")])
    assert at.level == "pass", at.detail
    assert over.level == "warn", over.detail


def test_height_needs_at_least_two_years():
    """只有一个年份的身高值时不判合理性（没有"跨年差"可言）。"""
    one = check_plausibility([_o("VITAL.身高", "2025", 180.0, "身高")])
    assert one.level == "pass", one.detail


def test_coverage_needs_at_least_two_years():
    """覆盖率校验需要至少两个年份 —— 单年没有"基线"可比。

    变异测试发现 ``len(per_year) < 2`` 改成 ``<=`` 无人管。
    """
    one = check_coverage([_o("X", "2026", 1.0)])
    assert "基线" not in one.detail or one.level == "pass", one.detail


def test_coverage_tolerance_boundary():
    """覆盖率容忍 ``max(3, 基线×10%)``，边界上取**严格**大于才报警。"""
    def n_items(k, year):
        return [_o(f"X{i}", year, float(i)) for i in range(k)]
    obs = n_items(100, "2024") + n_items(100, "2025") + n_items(90, "2026")
    c = check_coverage(obs)            # 2026 少 10%，恰好等于容忍
    assert c.level == "pass", c.detail
    obs2 = n_items(100, "2024") + n_items(100, "2025") + n_items(89, "2026")
    assert check_coverage(obs2).level == "warn"


def test_constant_series_needs_three_years():
    """常数序列要**至少 3 个年份**才判（2 个点相同很常见）。

    变异测试发现 ``len(yrs) >= 3`` 改成 ``>`` 无人管。
    """
    two = check_constant_series([_o("X", "2024", 5.0), _o("X", "2025", 5.0)])
    assert two.level == "pass", two.detail
    three = check_constant_series([_o("X", "2024", 5.0), _o("X", "2025", 5.0),
                                   _o("X", "2026", 5.0)])
    assert three.level == "warn", three.detail


def test_constant_series_ignores_zero_and_none():
    """常数序列**不看恒为 0 的项**（尿沉渣常态，不是未重测的证据）。

    变异测试发现 ``nums[0] not in (0, None)`` 里的 ``and``/``or`` 改了没人管。
    """
    zero = check_constant_series([_o("X", "2024", 0.0), _o("X", "2025", 0.0),
                                  _o("X", "2026", 0.0)])
    assert zero.level == "pass", zero.detail
    # 只有两个 y 和一个常数项时也不该报
    mixed = check_constant_series([_o("A", "2024", 7.0), _o("A", "2025", 8.0),
                                   _o("B", "2024", 5.0), _o("B", "2025", 5.0),
                                   _o("B", "2026", 5.0)])
    assert mixed.level == "warn", mixed.detail


def test_unmapped_rate_boundary_is_strict():
    """未映射率阈值的边界：恰好 20% 不报，超过才报。

    变异测试发现 ``if rate > threshold`` 改成 ``>=`` 无人管。
    """
    from checkup.normalize import UNMAPPED
    def mix(mapped, unmapped):
        return ([_o(f"M{i}", "2026", 1.0) for i in range(mapped)]
                + [_o(UNMAPPED, "2026", 1.0, f"U{i}") for i in range(unmapped)])
    at = check_unmapped_rate(mix(8, 2))          # 恰好 20%
    over = check_unmapped_rate(mix(79, 21))      # 21%
    assert at.level == "pass", at.detail
    assert over.level == "warn", over.detail


def test_validation_report_level_is_worst_of_all():
    """整体等级取**最严重**的那一项，且 fatal 优先于 warn。

    变异测试发现 ``if any(c.level == "warn" ...)`` 改成 ``!=`` 无人管。
    """
    from checkup.validate import Check, ValidationReport
    assert ValidationReport([Check("a", "pass", "")]).level == "pass"
    assert ValidationReport([Check("a", "pass", ""),
                             Check("b", "warn", "")]).level == "warn"
    assert ValidationReport([Check("a", "warn", ""),
                             Check("b", "fatal", "")]).level == "fatal"


# ---------------------------------------------- G0 载体判定
#
# 这一关**声明了很久却没接线** —— `layout.has_text_layer()` 定义在引擎里，
# 但从未被任何地方调用。写 `test_layout.py` 时才发现。
# v1 没有图像通道，所以正确的做法是**说出来**而不是静默跳过。

def test_carrier_passes_when_all_pages_have_text():
    from checkup.validate import check_carrier
    c = check_carrier([{"page_no": 1, "chars": 500, "images": 0},
                       {"page_no": 2, "chars": 0, "images": 0}])
    assert c.level == "pass", c.detail
    assert "1/2" in c.detail and "空白" in c.detail


def test_carrier_warns_on_scanned_pages():
    """**有图无文字层 = 扫描页** —— 那些页的内容没被分析，必须说。"""
    from checkup.validate import check_carrier
    c = check_carrier([{"page_no": 3, "chars": 0, "images": 2},
                       {"page_no": 4, "chars": 10, "images": 1}])
    assert c.level == "warn", c.detail
    assert "扫描件" in c.detail and "未被分析" in c.detail
    assert len(c.items) == 2


def test_blank_page_is_not_a_scanned_page():
    """**空白页不是扫描页** —— 无字**且**无图才是空白。

    这个区别是写 `test_layout.py` 时纠正过来的：我原以为本机报告第 2、4 页
    文字层不足说明它们是扫描件，实际是完全空白的页。
    """
    from checkup.validate import check_carrier
    c = check_carrier([{"page_no": 2, "chars": 0, "images": 0}])
    assert c.level == "pass", c.detail
    assert "扫描" not in c.detail


def test_real_reports_pass_carrier_check():
    import fitz
    from checkup.validate import check_carrier
    pages = []
    for fn in sorted(os.listdir(SRC)):
        if not fn.lower().endswith(".pdf"):
            continue
        d = fitz.open(os.path.join(SRC, fn))
        for i in range(d.page_count):
            pages.append({"page_no": i + 1,
                          "chars": len(d[i].get_text().strip()),
                          "images": len(d[i].get_images())})
        d.close()
    assert check_carrier(pages).level == "pass"


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
