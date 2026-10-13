# -*- coding: utf-8 -*-
"""控制台输出安全：**不许因为一个符号就把脚本崩掉**。

背景（Windows 实测）：入口脚本会打印 ``✓ ✗ ⚠ ⛔`` 等符号；当 stdout 是**管道**
（agent 采集输出、重定向、``| more``）时，Windows 用本地代码页（cp936 等）编码，
打印 ``✓``（U+2713）直接抛 ``UnicodeEncodeError`` —— 用户连报错原因都看不到。

处理策略（**分两种情况**，中文都不乱码）：

1. **输出被重定向/管道**（agent 采集、``| more``、写文件）→ 切 **UTF-8**：
   下游读 UTF-8 是常态（本包所有产物也都是 UTF-8），符号与中文**都完整**。
2. **真·交互终端** → **保留本地编码**，只设 ``errors="replace"``：
   中文照常显示，编不出的符号降级为 ``?``（不会崩）。
   Windows 交互终端想要完整符号：``chcp 65001`` 或设 ``PYTHONUTF8=1``
   （preflight 会提示）。
3. 另给一组**符号常量**（能用 Unicode 就用，编不出退 ASCII），新代码拼状态行用。
"""
from __future__ import annotations

import subprocess
import sys

_INITIALIZED = False


def run_text(cmd: list[str], **kwargs) -> "subprocess.CompletedProcess":
    """跑子进程并按 **UTF-8** 解码输出（跨平台安全的唯一入口）。

    子进程侧的 ``init_console()`` 会把**管道输出切成 UTF-8**；父进程若按
    本地码页（Windows=cp936/GBK）解码就抛 ``UnicodeDecodeError``（实测：
    双跑/边检脚本在 Windows 必崩）。所以父进程侧统一在这里指定
    ``encoding="utf-8"``；完整策略见模块头注释。
    """
    kwargs.setdefault("capture_output", True)
    kwargs["text"] = True
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **kwargs)


def init_console() -> None:
    """入口脚本第一行调用：把 stdout/stderr 调成"不会因编码崩"的模式。

    * 非交互（管道/重定向）→ **UTF-8 + errors=replace**（下游读 UTF-8，符号完整）
    * 交互终端 → 保留本地编码 + errors=replace（中文正确，符号降级不崩）
    """
    global _INITIALIZED
    for stream in (sys.stdout, sys.stderr):
        try:
            interactive = bool(getattr(stream, "isatty", lambda: False)())
            if interactive:
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001  （非 TextIOWrapper / 已替换过等）
            pass
    _INITIALIZED = True


def _encodable(s: str) -> bool:
    """当前 stdout 编码能否表示这个符号（编不出就换 ASCII）。"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        s.encode(enc)
        return True
    except Exception:  # noqa: BLE001
        return False


#: 状态符号：Unicode → ASCII 兜底
OK = "✓" if _encodable("✓") else "[OK]"
BAD = "✗" if _encodable("✗") else "[X]"
WARN = "⚠" if _encodable("⚠") else "[!]"
STOP = "⛔" if _encodable("⛔") else "[STOP]"
ARROW = "→" if _encodable("→") else "->"


def ascii_hint() -> str:
    """Windows/旧控制台下的建议（preflight 打印用；UTF-8 环境返回空串）。"""
    if _encodable("✓") and _encodable("中文"):
        return ""
    return ("提示：当前控制台编码对部分符号不友好（将显示为 ?）。"
            "Windows 建议先 `chcp 65001` 或设 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` "
            "以获得完整输出；不设也不影响功能（只是符号变 ?）")
