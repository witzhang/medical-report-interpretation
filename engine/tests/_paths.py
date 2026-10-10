# -*- coding: utf-8 -*-
"""测试数据根（P0-1）：28 个测试文件原本把个人报告/旧管线 CSV/profile 的路径
硬编码在 ``dirname(引擎目录)`` 下。本模块把解析统一到这里（``devpaths.py``），
数据未挂载时 **skip，不 fail**：

* 在 pytest 下 —— 模块级 ``pytest.skip``（整个文件计为 skipped）；
* 独立运行（``python3 tests/test_x.py``，health.py 就是这么跑的）—— 打印
  ``0/0 通过（跳过：…）`` 后退出 0，**不把"数据没挂载"算成失败**。

用法（每个用到外部数据的测试文件顶部）：

    from _paths import PATHS  # noqa: E402
    PATHS.require_real()      # 该文件需要 ①真实报告目录
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ...
    SRC = P.real_reports
"""
from __future__ import annotations

import os
import sys
import unittest

_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

from devpaths import FIXTURES as _FIX  # noqa: E402


class DataMissing(unittest.SkipTest):
    """外部数据未挂载。``unittest.SkipTest`` 是 pytest 原生识别的跳过信号。"""


class _Paths:
    """测试用数据路径（未挂载时指向不存在的位置，调用方须先 ``require_*``）。"""

    def __init__(self, fix=_FIX):
        self._fix = fix
        self.root = fix.root
        self.real_reports = fix.real_reports
        self.profile = fix.profile
        self.reports_v2 = fix.reports_v2
        self.synth_dir = fix.synth_dir
        self.engine_out = fix.engine_out

    @property
    def legacy_dir(self) -> str:
        """旧管线金标准 CSV 目录本身（数据集②）。"""
        return self._fix.legacy_data

    def legacy(self, name: str) -> str:
        """旧管线金标准 CSV（数据集②）里的某个文件。"""
        return os.path.join(self._fix.legacy_data, name)

    # ------------------------------------------------------------ 守门

    def legacy_template(self):
        """测试夹具：原 ``DEFAULT_TEMPLATE``（抽取层内置默认版式）的字段值。

        P0-2 已把生产代码里的 ``DEFAULT_TEMPLATE`` 删除（禁止静默回落内置模板），
        但测试仍需要一份**固定版式的输入**。原值一字未改地落在
        ``tests/fixtures/legacy_default_template.json``。
        """
        import json
        from checkup.templates import Template
        fixture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "fixtures", "legacy_default_template.json")
        with open(fixture, encoding="utf-8") as f:
            return Template.from_dict(json.load(f))

    def require_real(self) -> None:
        self._need_dir(self.real_reports, "真实报告目录（数据集①）")

    def require_legacy(self, *names: str) -> None:
        if names:
            for n in names:
                self._need_file(self.legacy(n), f"旧管线金标准（数据集②）{n}")
        else:
            self._need_dir(self._fix.legacy_data, "旧管线金标准 CSV 目录（数据集②）")

    def require_engine_out(self) -> None:
        """引擎产物 out/（数据集③，先跑 run_pipeline --out）。

        **看文件而不是看目录**：空目录（或别的程序留下的空壳目录）不算"已挂载"——
        否则这些回归测试会在陌生机器上报 FileNotFoundError 而不是跳过。
        """
        self._need_file(
            os.path.join(self.engine_out, "normalized_observations.csv"),
            "引擎产物 out/normalized_observations.csv（数据集③，先跑 run_pipeline --out）")

    def require_profile(self) -> None:
        self._need_file(self.profile, "个体信息 profile.json（个人数据，不进 skill）")

    def require(self, *parts: str) -> str:
        """任意一个数据根下的路径；缺了即跳过。"""
        path = os.path.join(self.root or "", *parts)
        self._need_file(path, "数据")
        return path

    # ------------------------------------------------------------ 内部

    def _skip(self, msg: str) -> None:
        if "pytest" in sys.modules:
            import pytest
            pytest.skip(msg, allow_module_level=True)
        # 独立运行：打印可解析的 "0/0 通过" 行后退出 0（health.py 逐文件跑）
        print(f"0/0 通过（跳过：{msg}）")
        raise SystemExit(0)

    def _need_dir(self, path: str, what: str) -> None:
        if not os.path.isdir(path):
            self._skip(f"{what}未挂载：{path}（设 CHECKUP_FIXTURES 指向数据根）")

    def _need_file(self, path: str, what: str) -> None:
        if not os.path.exists(path):
            self._skip(f"{what}未挂载：{path}（设 CHECKUP_FIXTURES 指向数据根）")


PATHS = _Paths()

#: 兼容别名
P = PATHS
