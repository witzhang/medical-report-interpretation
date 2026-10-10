# -*- coding: utf-8 -*-
"""外部数据根解析（P0-1）：把"挂载数据"从引擎目录里解绑。

引擎（skill）自带的是**代码与数据定义**（``checkup/`` ``catalog/`` ``tests/``
``tools/``）。测试与开发脚本还需要三类**外部数据**，它们不随 skill 分发：

    ① 报告目录            ``<根>/reports``（可用 ``CHECKUP_REAL_DIR`` 指定；缺省会探测）
    ② 旧管线金标准 CSV    ``<根>/体检分析/data/``（测试对照源；仅作者本机回归用）
    ③ 引擎产物            ``<根>/checkup-engine/out/``（报告核对层读它）
    另有 ``<根>/报告_v2/``（基准报告）与 ``<根>/research/testdata/``（回归集）。

解析优先级：

    1. 环境变量 ``CHECKUP_FIXTURES``（指向数据根；显式指定，无需标记）
    2. 从引擎目录的上一级向上找标记文件 ``.checkup-root``
    3. 从**当前工作目录**向上找标记文件 ``.checkup-root``

**怎么把自己的目录设为数据根**（二选一）::

    touch <你的数据目录>/.checkup-root        # 方式一：标记文件（推荐，git 风格向上查找）
    export CHECKUP_FIXTURES=<你的数据目录>     # 方式二：环境变量（任何位置都生效）

**缺数据 = 跳过，不是失败**（P0-1）：路径解析不做存在性断言；
调用方（``tests/_paths.py`` / 各脚本）自己决定"缺了就 skip / 报错"。
"""
from __future__ import annotations

import os
from typing import Optional

ENGINE_ROOT = os.path.dirname(os.path.abspath(__file__))

#: 数据根标记文件：放在数据目录里，供自动探测（从起点向上逐级查找）
ROOT_MARKER = ".checkup-root"


def _find_root_up(start: str) -> Optional[str]:
    """从 ``start`` 起向上逐级查找标记文件；命中返回所在目录，否则 ``None``。"""
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, ROOT_MARKER)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _discover_report_dirs(root: Optional[str]) -> list[str]:
    """在数据根下探测"报告目录"：通用名 ``reports`` 或形如 ``*体检报告*``。

    不写死任何人的目录名：先按名字排序，``reports`` 优先，其余取第一个中文习惯名。
    """
    if not root or not os.path.isdir(root):
        return []
    generic, chinese = [], []
    try:
        for name in sorted(os.listdir(root)):
            if not os.path.isdir(os.path.join(root, name)):
                continue
            if name == "reports":
                generic.append(os.path.join(root, name))
            elif "体检报告" in name and name != "报告_v2":
                chinese.append(os.path.join(root, name))
    except OSError:
        return []
    return generic + chinese


def resolve_root() -> Optional[str]:
    """按优先级解析数据根；解析不到返回 ``None``（不报错）。"""
    env = os.environ.get("CHECKUP_FIXTURES")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return (_find_root_up(os.path.dirname(ENGINE_ROOT))
            or _find_root_up(os.getcwd()))


class Fixtures:
    """数据根 → 各数据集路径（不保证存在；缺 = 未挂载）。"""

    def __init__(self, root: Optional[str]):
        self.root = root

    def _p(self, *parts: str) -> str:
        return os.path.join(self.root or "", *parts)

    @property
    def real_reports(self) -> str:
        """报告目录（P0-1 数据集①）。

        解析：``CHECKUP_REAL_DIR``（可写相对数据根的路径）→ 数据根下探测
        （``reports/`` 或 ``*体检报告*``）→ 兜底 ``<根>/reports``（不存在即"未挂载"）。
        """
        env = os.environ.get("CHECKUP_REAL_DIR")
        if env:
            return self._p(env)
        found = _discover_report_dirs(self.root)
        if found:
            return found[0]
        return self._p("reports")

    @property
    def legacy_data(self) -> str:
        """旧管线金标准 CSV 目录（数据集②）。"""
        return self._p("体检分析", "data")

    @property
    def engine_out(self) -> str:
        """引擎产物目录（数据集③；``run_pipeline --out``）。"""
        return self._p("checkup-engine", "out")

    @property
    def profile(self) -> str:
        """个体信息（**个人数据，不进 skill**；运行期由问诊提供时经 ``--profile`` 传入）。"""
        return self._p("checkup-engine", "profile.json")

    @property
    def reports_v2(self) -> str:
        """v6.3 基准报告目录（回归用）。"""
        return self._p("报告_v2")

    @property
    def synth_dir(self) -> str:
        """合成报告（P4 回归集）。"""
        return self._p("research", "testdata")

    def exists(self, path: str) -> bool:
        return os.path.exists(path)


#: 全局单例（导入时解析一次）
FIXTURES = Fixtures(resolve_root())

#: skill 根目录（引擎目录的上一级）——**运行期只读**（§5.6）：任何产物不得写进这里
SKILL_ROOT = os.path.dirname(ENGINE_ROOT)


def default_out_dir() -> str:
    """产物默认目录（写到**用户工作目录**，不写 skill 目录）。

    优先级：``CHECKUP_OUT`` → 数据根下 ``checkup-engine/out``（本机工作区布局）
    → 当前目录 ``体检解读_out``。
    """
    env = os.environ.get("CHECKUP_OUT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if FIXTURES.root:
        return os.path.join(FIXTURES.root, "checkup-engine", "out")
    return os.path.join(os.getcwd(), "体检解读_out")


def guard_out(path: str) -> str:
    """拒绝把产物写进 skill 目录（个人数据/产物不进包，§5.6）；返回原路径。

    **双侧 realpath**：符号链接绕不动（如 macOS 上 /tmp→/private/tmp，
    仅用 abspath 比较会让 /private/tmp/... 形态的目标漏网）。
    """
    ap = os.path.realpath(path)
    root = os.path.realpath(SKILL_ROOT)
    if ap == root or ap.startswith(root + os.sep):
        raise SystemExit(
            f"✗ 产物目录不能落在 skill 目录内（运行期只读）：{ap}\n"
            f"  请用 --out / CHECKUP_OUT 指向用户工作目录。")
    return path
