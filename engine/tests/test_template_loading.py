#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板加载健壮性回归（P0-1/D2 · D3 · D7）——**防"档案毒化模板库"再发生**。

三条锁（对应 Windows 实测缺陷 D2/D3/D4）：

1. 模板目录里同时躺着**验收档案**（``*.accept.json``）→ 只能得到模板，不得崩；
2. 目录里有**真·非模板** json → 报错必须**带文件名**（fail loud，不静默吞）；
3. ``default_registry`` 把失败翻成"**退出码 2** + 原因 + 下一步"，不抛裸 traceback。

约定：本文件可在 pytest 与"独立直跑"两种方式下运行；**环境变量一律用
:func:`_isolate` 上下文隔离并恢复** —— 曾因不回滚 ``CHECKUP_TEMPLATES``
污染同进程的其它测试（test_accept 的真实案例用例被带崩）。

    python3 tests/test_template_loading.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from checkup.templates import (ARCHIVE_SUFFIX, TemplateLoadError,  # noqa: E402
                              TemplateRegistry, default_registry)

try:                       # pytest 可选（独立直跑时用等价的假异常）
    import pytest  # noqa: E402
except ImportError:  # pragma: no cover
    pytest = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIPPED = os.path.join(BASE, "catalog", "templates", "SYN-002.json")


class _Raises:
    """最小 `pytest.raises` 替身（独立直跑时用）。"""

    def __init__(self, exc):
        self.exc = exc
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        assert et is not None, f"应当抛 {self.exc.__name__}"
        assert issubclass(et, self.exc), \
            f"抛的是 {et.__name__}，应为 {self.exc.__name__}"
        self.value = ev
        return True


def raises(exc):
    return pytest.raises(exc) if pytest is not None else _Raises(exc)


@contextlib.contextmanager
def _isolate(env_value: str):
    """把用户侧模板目录固定到 ``env_value``，**退出时恢复**（防测试间串扰）。"""
    old = os.environ.get("CHECKUP_TEMPLATES")
    os.environ["CHECKUP_TEMPLATES"] = env_value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CHECKUP_TEMPLATES", None)
        else:
            os.environ["CHECKUP_TEMPLATES"] = old


def _none_dir() -> str:
    return os.path.join(tempfile.mkdtemp(prefix="none_"), "none")


def _tmp_templates(*, archive: bool = False, bad_json: bool = False) -> str:
    """造一份"包内目录"布局：<tmp>/catalog/templates/…"""
    root = tempfile.mkdtemp(prefix="tpl_")
    tdir = os.path.join(root, "catalog", "templates")
    os.makedirs(tdir)
    shutil.copy(SHIPPED, os.path.join(tdir, "SYN-002.json"))
    if archive:
        # 与 accept_template.py 落盘内容同形：带 samples/verdict 等**非模板字段**
        with open(os.path.join(tdir, f"SYN-002{ARCHIVE_SUFFIX}"), "w",
                  encoding="utf-8") as f:
            json.dump({"org_id": "SYN-002", "samples": ["某报告.pdf"],
                       "verdict": "ok", "template_sha1": "x", "vlm": "",
                       "evidences": []}, f, ensure_ascii=False)
    if bad_json:
        with open(os.path.join(tdir, "NOT_A_TEMPLATE.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"not_a_template": 1}, f)
    return root


def test_archive_next_to_template_is_skipped():
    """① 档案与模板同目录（早期约定落在包里的形态）→ 只得到模板，不得崩。"""
    with _isolate(_none_dir()):
        root = _tmp_templates(archive=True)
        reg = TemplateRegistry.load(os.path.join(root, "catalog", "templates"))
        assert [t.org_id for t in reg.templates] == ["SYN-002"], reg.templates


def test_archive_does_not_break_default_registry():
    """档案在场时 default_registry 正常返回（Windows 上被毒化的目录免手工清理）。"""
    with _isolate(_none_dir()):
        root = _tmp_templates(archive=True)
        reg = default_registry(root)
        assert [t.org_id for t in reg.templates] == ["SYN-002"]


def test_bad_json_raises_with_filename():
    """② 真非模板 json → 异常消息**必须带文件名**（否则用户不知道动哪个文件）。"""
    with _isolate(_none_dir()):
        root = _tmp_templates(bad_json=True)
        with raises(TemplateLoadError) as ei:
            TemplateRegistry.load(os.path.join(root, "catalog", "templates"))
        assert "NOT_A_TEMPLATE.json" in str(ei.value)


def test_default_registry_turns_failure_into_exit_2():
    """③ fail loud 且按 skill 口径翻成"退出码 2 + 原因 + 下一步"。"""
    with _isolate(_none_dir()):
        root = _tmp_templates(bad_json=True)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with raises(SystemExit) as ei:
                default_registry(root)
        assert ei.value.code == 2
        err = buf.getvalue()
        assert "NOT_A_TEMPLATE.json" in err
        assert "处置" in err                   # 原因 + 下一步，而不是裸 traceback
        assert ARCHIVE_SUFFIX in err            # 顺带说明档案会被跳过


def test_no_silent_skip_in_user_dir():
    """D3：用户侧目录里的坏文件**不再被静默吞**（原来 except: continue）。"""
    root = _tmp_templates()
    bad_dir = tempfile.mkdtemp(prefix="user_tpl_")
    with open(os.path.join(bad_dir, "oops.json"), "w", encoding="utf-8") as f:
        f.write('{"bad": 1}')
    with _isolate(bad_dir):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            with raises(SystemExit) as ei:
                default_registry(root)
        assert ei.value.code == 2
        assert "oops.json" in buf.getvalue()


def test_env_is_restored_after_isolation():
    """自检：隔离上下文退出后环境变量必须还原（防同进程测试串扰）。"""
    old = os.environ.get("CHECKUP_TEMPLATES")
    with _isolate("/tmp/whatever-tpl"):
        assert os.environ["CHECKUP_TEMPLATES"] == "/tmp/whatever-tpl"
    assert os.environ.get("CHECKUP_TEMPLATES") == old


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
