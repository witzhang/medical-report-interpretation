#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境预检（QUICKSTART 第 2 步）：把"能不能跑"一次性说清。

    python3 engine/tools/preflight.py

逐项检查并给出**可执行的下一步**：

1. Python 版本 ≥ 3.9（``from __future__ import annotations`` 的前提）
2. ``import fitz``（PyMuPDF，唯一第三方依赖）→ 失败给 pip 命令
3. 模板目录可读且至少一套模板
4. 输出目录可写（默认用户工作目录；**不写 skill 目录**）
5. skill 只读守卫真的会拒绝写包内（跑一次 ``guard_out``）

退出码 0 = 全部通过；1 = 有失败项（每项都带处置命令）。
"""
from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

OK, BAD = "✓", "✗"
results: list[tuple[str, bool, str]] = []


from checkup.console import init_console  # noqa: E402
init_console()

def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {OK if ok else BAD} {name}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    print("=" * 72)
    print("环境预检（Medical Report Interpretation）")
    print("=" * 72)

    # 1) Python 版本
    v = sys.version_info
    check("Python ≥ 3.9", v >= (3, 9), f"当前 {v.major}.{v.minor}.{v.micro}")

    # 2) PyMuPDF
    try:
        import fitz  # noqa: F401
        import fitz as _f
        check("PyMuPDF（import fitz）", True, str(getattr(_f, "version", "?")))
    except Exception as e:  # noqa: BLE001
        check("PyMuPDF（import fitz）", False, f"{e}")
        print(f"      处置：python3 -m pip install -r {os.path.join(BASE, 'requirements.txt')}")

    # 3) 模板目录：**真加载**（不是数文件名——数名字曾把验收档案当模板算，
    #    给出"3 套"的假绿，而该目录此刻加载即崩）
    tdir = os.path.join(BASE, "catalog", "templates")
    try:
        from checkup.templates import (ARCHIVE_SUFFIX,  # noqa: PLC0415
                                       TemplateRegistry)
        reg = TemplateRegistry.load(tdir)
        n = len(reg.templates)
        desc = "、".join(f"{t.org_id} v{t.template_version}" for t in reg.templates)
        archives = ([f for f in os.listdir(tdir) if f.endswith(ARCHIVE_SUFFIX)]
                    if os.path.isdir(tdir) else [])
        check("模板可加载", n > 0,
              f"{tdir}（{n} 套：{desc or '—'}）"
              + (f"；已跳过 {len(archives)} 份验收档案" if archives else ""))
        if n == 0:
            print("      处置：新机构先跑 engine/tools/accept_template.py 起草+验收落盘")
    except Exception as e:  # noqa: BLE001
        check("模板可加载", False, f"{tdir}：{e}")
        print("      处置：把报错点名的文件移出模板目录；或按提示修好它")
    # 用户侧模板目录（可选）
    from checkup.templates import _extra_template_dirs  # noqa: PLC0415
    for d in _extra_template_dirs():
        if os.path.isdir(d):
            try:
                extra = TemplateRegistry.load(d)
                names = "、".join(f"{t.org_id} v{t.template_version}"
                                  for t in extra.templates)
                print(f"  · 用户侧模板（可选）：✓ {d}（{len(extra.templates)} 套："
                      f"{names or '—'}）")
            except Exception as e:  # noqa: BLE001
                check("用户侧模板可加载", False, f"{d}：{e}")
                print("      处置：把报错点名的文件移出该目录")

    # 4) 输出目录可写（默认用户工作目录；**不写 skill 目录**）
    #    "默认位置在 skill 目录内"（新手从 clone 根直接跑预检时的常态）不是
    #    失败——自动避让到临时目录做可写性探测，并给出运行时的 --out 指引。
    import tempfile  # noqa: PLC0415
    from devpaths import default_out_dir, guard_out  # noqa: E402
    try:
        out = guard_out(default_out_dir())
    except SystemExit:
        out = ""
    probe_dir = out or os.path.join(tempfile.gettempdir(), "checkup_preflight")
    try:
        os.makedirs(probe_dir, exist_ok=True)
        probe = os.path.join(probe_dir, ".preflight_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        if out:
            check("输出目录可写", True, out)
        else:
            # 措辞要点：这里探的是**临时目录**能写，而不是"默认位置可用"。
            # 默认位置若落在 skill 目录，运行期会被 guard_out 直接拒绝 —— 说清这点，
            # 否则新手会把它读成"默认位置没问题，不用给 --out"。
            check("输出目录可写", True,
                  f"可写性 OK（当前默认位置 {default_out_dir()} 落在 skill 目录内，"
                  f"**运行期会被拒绝**）")
            print("      · 请显式指定产物目录：--out <你的目录>（或 export CHECKUP_OUT=…），"
                  "例：--out /tmp/体检解读_out")
    except Exception as e:  # noqa: BLE001
        check("输出目录可写", False, f"{probe_dir}：{e}")
        print("      处置：用 --out / CHECKUP_OUT 指到可写目录")

    # 5) 只读守卫
    try:
        guard_out(os.path.join(os.path.dirname(BASE), "should_not_write"))
        guard_ok = False
    except SystemExit:
        guard_ok = True
    except Exception:  # noqa: BLE001
        guard_ok = False
    check("skill 目录只读守卫生效", guard_ok,
          "会把试图写入 skill 目录的操作拦下" if guard_ok else "守卫未生效（不该发生）")

    # 6) 指引库（可选：只影响"某国标准"类问答，不影响解读主链路）
    lib = os.environ.get("CHECKUP_LIB", "")
    if lib and os.path.isdir(lib):
        n = sum(len(fs) for _, _, fs in os.walk(lib))
        print(f"  · 指引库（可选）：✓ 可达（{lib}，约 {n} 个文件）")
    elif lib:
        print(f"  · 指引库（可选）：✗ 路径不存在（{lib}）—— 问答将如实答"
              f"『资料不在本机』；结构与来源见 references/library.md")
    else:
        print("  · 指引库（可选）：未设置 CHECKUP_LIB —— 只影响『某国标准』类问答；"
              "清单见 references/library.md")

    # 7) 数据根（可选，不阻塞）
    import devpaths  # noqa: E402
    root = devpaths.FIXTURES.root
    if root:
        how = ("CHECKUP_FIXTURES 环境变量" if os.environ.get("CHECKUP_FIXTURES")
               else f"向上找到 {devpaths.ROOT_MARKER} 标记")
        print(f"  · 数据根：✓ {root}（来自 {how}）—— 缺省报告目录与私有模板从这里找")
    else:
        print("  · 数据根（可选）：未设置 —— 跑合成 demo 不需要；要用自备报告时：")
        print(f"      方式一（推荐）：touch <你的数据目录>/{devpaths.ROOT_MARKER}")
        print("      方式二：export CHECKUP_FIXTURES=<你的数据目录>")

    from checkup.console import ascii_hint  # noqa: PLC0415
    hint = ascii_hint()
    if hint:
        print(f"  · {hint}")

    n_bad = sum(1 for _, ok, _ in results if not ok)
    print("-" * 72)
    if n_bad:
        print(f"{BAD} 有 {n_bad} 项未通过 —— 按上面的处置命令处理后重跑。")
        return 1
    print(f"{OK} 环境 OK。下一步二选一：")
    print("  · 先跑合成 demo（不需要任何自有数据）："
          "python3 scripts/eval_synth.py --out /tmp/eval_synth")
    print("  · 直接用自备报告："
          "python3 engine/run_pipeline.py --src <报告目录> --out <产物目录>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
