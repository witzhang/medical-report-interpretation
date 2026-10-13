# -*- coding: utf-8 -*-
"""pytest 会话级设置：控制台编码安全（P0-1/D1）。

测试文件本身会打印 ✓/✗；Windows 上 pytest 的输出也可能是管道。这里在会话
开始前统一把 stdout/stderr 调成"不会因编码崩"的模式（详情见 checkup/console.py）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.console import init_console  # noqa: E402

init_console()
