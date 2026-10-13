# -*- coding: utf-8 -*-
"""对账层的路径解析（P0-8）：引擎位置、数据根、**产物一律写用户工作目录**。

三条硬规则：

1. **skill 目录运行期只读** —— 任何产物（artifacts / 对账记录 / 复核表）
   写用户工作目录；写进 skill 目录 → 直接报错（``guard_out``）。
2. 引擎目录解析：``CHECKUP_ENGINE`` → ``<上一级>/engine``（skill 布局）
   → ``<上一级>/checkup-engine``（工作区布局）。
3. 数据根（真实报告 / 旧管线金标准 / 引擎产物 / v6.3 基准报告）与引擎解绑，
   由 ``devpaths`` 统一解析（``CHECKUP_FIXTURES`` 优先）。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)


def _resolve_engine_dir() -> str:
    env = os.environ.get("CHECKUP_ENGINE")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    for cand in (os.path.join(PARENT, "engine"),          # skill 布局
                 os.path.join(PARENT, "checkup-engine")):  # 工作区布局
        if os.path.isdir(os.path.join(cand, "checkup")):
            return cand
    return os.path.join(PARENT, "engine")


ENGINE_DIR = _resolve_engine_dir()
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from devpaths import FIXTURES, SKILL_ROOT  # noqa: E402

#: skill 根目录（本层的上一级）——运行期只读
_SKILL_ROOT = SKILL_ROOT


def is_inside_skill(path: str) -> bool:
    # 双侧 realpath：符号链接（如 macOS /tmp→/private/tmp）绕不动
    ap = os.path.realpath(path)
    root = os.path.realpath(_SKILL_ROOT)
    return ap == root or ap.startswith(root + os.sep)


def guard_out(path: str) -> str:
    """拒绝把产物写进 skill 目录（§5.6）；返回原路径。"""
    if is_inside_skill(path):
        raise SystemExit(
            f"✗ 产物不能落在 skill 目录内（运行期只读）：{os.path.abspath(path)}\n"
            f"  请用 --out / CHECKUP_OUT 指向用户工作目录。")
    return path


def product_root() -> str:
    """运行期产物根（用户工作目录）。

    ``CHECKUP_OUT`` → ``<数据根>/体检解读_out`` → ``<当前目录>/体检解读_out``。
    """
    env = os.environ.get("CHECKUP_OUT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if FIXTURES.root:
        return os.path.join(FIXTURES.root, "体检解读_out")
    return os.path.join(os.getcwd(), "体检解读_out")


def default_artifacts(case: str) -> str:
    """四张底稿默认目录：``<产物根>/artifacts/<case>``。"""
    return os.path.join(product_root(), "artifacts", case)


def default_reconcile_out(case: str) -> str:
    """对账记录默认路径：``<产物根>/对账_<case>.md``。"""
    return os.path.join(product_root(), f"对账_{case}.md")


#: 供上层直接引用
def resolve_constants(explicit: str = "") -> dict:
    """对账常量登记簿。

    优先级：``--constants``/``CHECKUP_CONSTANTS`` > 数据根 ``constants.user.json``
    （可选，**附加登记**：键名 ``用户登记``，不覆盖引擎内置常量）> 内置
    ``constants.json``。问诊产生的数值（包年数、家庭自测血压等）登记于此，
    数字追踪与对账即可指回。
    """
    builtin = json.load(open(os.path.join(HERE, "constants.json"), encoding="utf-8"))
    user = explicit or os.environ.get("CHECKUP_CONSTANTS") or ""
    if not user and FIXTURES.root:
        cand = os.path.join(FIXTURES.root, "constants.user.json")
        user = cand if os.path.exists(cand) else ""
    if user:
        if not os.path.exists(user):
            raise SystemExit(f"✗ 常量文件不存在：{user}")
        try:
            extra = json.load(open(user, encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"✗ 常量文件不是合法 JSON：{user}\n  {e}") from e
        builtin = {**builtin, "用户登记": extra}
    return builtin


__all__ = ["ENGINE_DIR", "FIXTURES", "guard_out", "is_inside_skill",
           "product_root", "default_artifacts", "default_reconcile_out",
           "resolve_constants"]
