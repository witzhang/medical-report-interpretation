#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资产化（D2 分发 / D3 分套餐基线 / D4 管理）的回归测试。    python3 tests/test_assets.py

验收条件（工作单）：
* D2：一份"可分享模板包"能被**另一台机器**（另一个加载目录）直接加载；
* D3：分套餐基线生效（同机构不同套餐各比各的，不再误报）；
* D4：资产记录四件齐全（版本 / 验收档案 sha1 / 样本清单 / 基线）；一机构一当前版。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)

from checkup import assets  # noqa: E402
from checkup.console import init_console  # noqa: E402
from checkup.templates import (Template, TemplateRegistry,  # noqa: E402
                              default_registry)
from checkup.validate import check_drift  # noqa: E402


def _tmp(name: str = "") -> str:
    d = tempfile.mkdtemp(prefix="asset_")
    return os.path.join(d, name) if name else d


def _tpl(org: str = "ASSET-001", version: int = 1) -> Template:
    return Template(org_id=org, org_name="示范医院", template_version=version,
                    required_anchors=["示范医院"],
                    stats={"items_per_report": [22, 22]})


# ------------------------------------------------------------------ D2

def test_bundle_round_trips_on_another_machine():
    """D2：导出的资产包能被**另一个加载目录**直接加载（且 sha1 校验通过）。"""
    dest = _tmp()
    manifest = assets.export_bundle(dest, [_tpl()], note="测试包")
    assert manifest["templates"][0]["org_id"] == "ASSET-001"
    assert manifest["templates"][0]["baseline"] == [22, 22]
    # "另一台机器" = 另一个目录 + 走 CHECKUP_TEMPLATES 的加载路径
    tpls, man = assets.load_bundle(dest)
    assert [t.org_id for t in tpls] == ["ASSET-001"]
    assert man["bundle_format"] == assets.BUNDLE_FORMAT
    old = os.environ.get("CHECKUP_TEMPLATES")
    os.environ["CHECKUP_TEMPLATES"] = dest
    try:
        reg = default_registry(ENGINE)
    finally:
        if old is None:
            os.environ.pop("CHECKUP_TEMPLATES", None)
        else:
            os.environ["CHECKUP_TEMPLATES"] = old
    assert any(t.org_id == "ASSET-001" for t in reg.templates), \
        [t.org_id for t in reg.templates]


def test_bundle_refuses_personal_data_and_detects_corruption():
    """D2：包里不许有个人数据痕迹；包被改坏要**停下报错**。"""
    dest = _tmp()
    assets.export_bundle(dest, [_tpl()])
    # ① 个人数据闸门：样本文件名/绝对路径不得进清单
    try:
        assets._assert_no_personal_data({"samples": ["某机构体检报告.pdf"]})
    except ValueError as e:
        assert "个人数据" in str(e)
    else:
        raise AssertionError("含 .pdf 的清单必须被拦下")
    # ② 损坏检测：改一个字节 → sha1 不符
    path = os.path.join(dest, "ASSET-001.json")
    d = json.load(open(path, encoding="utf-8"))
    d["lab"]["section_x_max"] = 999.0
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        assets.load_bundle(dest)
    except ValueError as e:
        assert "损坏" in str(e)
    else:
        raise AssertionError("改坏的资产包必须被拦下")


# ------------------------------------------------------------------ D4

def test_asset_record_has_version_sha_samples_baseline():
    """D4：资产记录四件齐全；档案缺失时**如实标出来**（不假装齐全）。"""
    dest = _tmp()
    arch = os.path.join(dest, "ASSET-001.accept.json")
    os.makedirs(dest, exist_ok=True)
    json.dump({"verdict": "ok", "samples": ["a.pdf", "b.pdf"],
               "template_sha1": "abc123"}, open(arch, "w", encoding="utf-8"))
    rec = assets.asset_record(_tpl(), arch)
    assert rec["template_version"] == 1
    assert rec["archive_sha1"] and rec["archive_sha1"] == assets.sha1_of(arch)
    assert rec["samples"] == ["a.pdf", "b.pdf"]      # 从档案补链
    assert rec["baseline"] == [22, 22]
    assert rec["archive_missing"] is False
    # 档案不在 → archive_missing 标 True（不假装齐全）
    rec2 = assets.asset_record(_tpl(), os.path.join(dest, "nope.json"))
    assert rec2["archive_missing"] is True and rec2["archive_sha1"] == ""


def test_archive_superseded_keeps_one_current_version():
    """D4：一机构只留**当前版** —— 旧版移进 archive/，加载器随即只看得到一版。"""
    tdir = _tmp()
    os.makedirs(tdir, exist_ok=True)
    _tpl(version=1).save(os.path.join(tdir, "ASSET-001.json"))
    _tpl(version=2).save(os.path.join(tdir, "ASSET-001-v2.json"))
    _tpl("OTHER-002").save(os.path.join(tdir, "OTHER-002.json"))
    reg = TemplateRegistry.load(tdir)
    assert reg.ambiguity() == [("ASSET-001", [1, 2])]        # 先确认歧义存在
    moved = assets.archive_superseded(tdir, "ASSET-001", keep_version=2)
    assert moved == ["ASSET-001.json"], moved
    reg2 = TemplateRegistry.load(tdir)
    assert reg2.ambiguity() == []                            # 歧义消失
    assert {t.org_id for t in reg2.templates} == {"ASSET-001", "OTHER-002"}
    # 旧版还在（资产可查），只是不在加载目录里
    assert os.path.exists(os.path.join(tdir, assets.ARCHIVE_DIR, "ASSET-001.json"))


# ------------------------------------------------------------------ D3

def test_package_baseline_avoids_false_drift_alarm():
    """D3：分套餐基线 —— 同机构不同套餐各比各的，不再误报改版。"""
    stats = {"items_per_report": [89, 82],
             "package_baseline": {"心脑套餐": [89], "基础套餐": [82]}}
    # 用套餐基线：各自对得上 → pass
    assert check_drift([{"year": "2024", "items": 89}], stats,
                       package="心脑套餐").level == "pass"
    assert check_drift([{"year": "2026", "items": 82}], stats,
                       package="基础套餐").level == "pass"
    # 不指定套餐（只有整体基线）→ 82 相对中位数 85.5 偏离 4% 仍在容差内；
    # 真正会误报的是"套餐项数差得远"的情形：构造 89 vs 基线 [82]
    stats2 = {"items_per_report": [72, 72],          # 基础套餐整体基线
              "package_baseline": {"心脑套餐": [89]}}  # 心脑套餐自己的基线
    c_org = check_drift([{"year": "2024", "items": 89}], stats2)
    c_pkg = check_drift([{"year": "2024", "items": 89}], stats2, package="心脑套餐")
    assert c_org.level == "warn", c_org.detail        # 整体基线下会误报
    assert c_pkg.level == "pass", c_pkg.detail         # 套餐基线下正确
    assert "心脑套餐" in c_pkg.detail


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
