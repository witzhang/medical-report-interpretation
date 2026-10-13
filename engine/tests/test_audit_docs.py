#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档审计器（``tools/audit_docs.py``）的元测试 —— 检查器本身也要被检查。

连续几轮的失效都发生在"检查手段"这一层，所以新增的每一条检查都得先证明
**它真的会响**（在临时目录里造一份说谎的最小文档集，看它报不报），
再证明**它不乱响**（干净的最小技能树，一条都不许报 —— 误报会让工具废掉）。

覆盖（括号里是检查 B 的验收编号）：

* (i) 干净的最小技能树 —— 一条都不许报
* (a) 链接断 / 命令不存在 / flag 不存在 —— 三种"文档说谎"都要点名 file:line
* (b) 入口表缺一份 ``references/*.md``（双向：漏登 + 点错名）
* (ii) 面向用户的工具声明了退出码，表里却没有它的行 → 警告级
* (iii) 同一个（工具, 码）两个不同含义 → 报错，且点名两行
* (iv) ``码 = —`` 的行不参与冲突判定
* 退出码表结构变了（表头不是「工具」/ 数据行太少）→ 报错提醒同步检查
* 维护工具**点名即豁免**（名单写在 SKILL.md 里，不硬编码）
* (d) 环境变量：在 README 总表／只在别处／完全没有 —— 三档都要对
* 试跑必须发生在临时副本里（造一个"跑一次就留痕"的假工具）

    python3 tests/test_audit_docs.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(ENGINE, "tools"))

import audit_docs  # noqa: E402

#: 一个规规矩矩的假工具：认清 ``--out`` / ``--src``，声明了自己的退出码
GOOD_TOOL = '''\
# -*- coding: utf-8 -*-
"""一个假工具（测试用）。退出码：0 = 正常；1 = 有报警。"""
import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--src", default="")
    ap.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

#: 一个"跑一次就在自己旁边写一个 TOUCHED"的假工具 —— 用来验证试跑在副本里
NOISY_TOOL = '''\
# -*- coding: utf-8 -*-
"""一个会留痕的假工具（测试用）：跑一次就在自己旁边写一个 TOUCHED。"""
import os

open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "TOUCHED"), "w",
     encoding="utf-8").write("ran")
'''

#: 最小的「何时读哪个文件」章节 —— 有它才不会多出一条"章节找不到"的警告
ENTRY_SECTION = ("## 何时读哪个文件（渐进披露）\n\n"
                 "| 场景 | 读 |\n|---|---|\n"
                 "| 开工前 | `references/workflow.md` |\n\n")

#: 退出码表的表头（一行一个「工具 + 码」）
TABLE_HEAD = "| 工具 | 码 | 症状 | 原因与处置 |\n|---|---|---|---|\n"
#: 不涉及具体退出码的"任何命令"行（码 = —）—— 5 行里垫满，满足结构要求
ANY_ROWS = ("| **任何命令** | — | `import fitz` 失败 | 装 PyMuPDF |\n"
            "| **任何命令** | — | OFD / Word / Excel | 导成 PDF |\n"
            "| **任何命令** | — | 单位不可比 | 照当年机构区间判 |\n")


def _tree(files: dict) -> str:
    """造一棵最小技能树：``{相对路径: 内容}``，返回根目录。"""
    root = tempfile.mkdtemp(prefix="audit_docs_case_")
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    return root


def _joined(items: list) -> str:
    return "\n".join(items)


# ------------------------------------------------------------------ (i) 干净树

def test_clean_tree_reports_nothing():
    """(i) 干净的最小技能树：错误与警告都必须为空（不许乱响）。"""
    root = _tree({
        "engine/tools/fake_tool.py": GOOD_TOOL,
        "references/workflow.md": ("# 流程\n\n"
                                   "同级的相对链接：[契约](../contracts/c.md)\n\n"
                                   "**技能根**写法的链接：[根契约](contracts/c.md)\n"),
        "contracts/c.md": "# 契约\n",
        "SKILL.md": (
            "# 测试技能\n\n"
            "## 何时读哪个文件（渐进披露）\n\n"
            "| 场景 | 读 |\n|---|---|\n"
            "| 开工前 | `references/workflow.md` |\n\n"
            "## 命令速查\n\n"
            "```bash\n"
            "python3 $SKILL/engine/tools/fake_tool.py --out /tmp/x\n"
            "```\n\n"
            "## 非零退出怎么办（按\"工具 + 退出码\"对号入座）\n\n"
            "（「维护工具」不在本表范围：`health.py` / `mutate.py`。）\n\n"
            + TABLE_HEAD +
            "| **`fake_tool.py`**（测试） | 1 | 有报警 | 看对照卡确认后重跑 |\n"
            "| **引擎三件套**（`run_narrative`…） | 2 | 版式未注册 | 走接入三步 |\n"
            "| **任何命令** | 2 | 模板加载失败 | 移出非模板 json |\n"
            + ANY_ROWS),
    })
    errors, warns, counts = audit_docs.audit(root)
    assert errors == [], errors
    assert warns == [], warns
    assert counts["docs"] == 2, counts          # SKILL.md + references/workflow.md
    assert counts["links"] == 2 and counts["cmds"] == 1, counts


# ------------------------------------------------------------------ (a) 三种说谎

def test_broken_link_command_and_flag_are_reported():
    """(a) 链接断 / 命令不存在 / flag 不存在 —— 三种都要报，且点名 file:line。"""
    root = _tree({
        "engine/tools/fake_tool.py": GOOD_TOOL,
        "references/workflow.md": "# 流程\n",
        "SKILL.md": (
            "# 测试技能\n\n"
            "[断链](references/没有这份.md)\n\n"
            "```bash\n"
            "python3 $SKILL/engine/tools/fake_tool.py --nope\n"
            "python3 $SKILL/engine/tools/no_such_script.py --out /tmp/x\n"
            "```\n"),
    })
    errors, _, counts = audit_docs.audit(root, docs=["../SKILL.md"])
    text = _joined(errors)
    assert "链接失效" in text and "没有这份.md" in text, errors
    assert "文档里的命令不存在" in text and "no_such_script.py" in text, errors
    assert "没有参数 --nope" in text, errors
    assert counts["links"] == 1 and counts["cmds"] == 2, counts
    # 每条都点名 file:line（不然报错还得自己去搜）
    assert all(re.search(r"\.md:\d+ ", e) for e in errors), errors


# ------------------------------------------------------------------ (b) 入口表

def test_entry_table_missing_reference_is_reported():
    """(b) 入口表漏了一份 references/*.md → 报错并点名；点了不存在的名字也报。"""
    root = _tree({
        "references/workflow.md": "# 流程\n",
        "references/card-fields.md": "# 卡片字段\n",
        "SKILL.md": (
            "# 测试技能\n\n"
            "## 何时读哪个文件（渐进披露）\n\n"
            "| 场景 | 读 |\n|---|---|\n"
            "| 开工前 | `references/workflow.md` |\n"
            "| 写卡片 | `references/没有这份.md` |\n"),
    })
    errors, _, _ = audit_docs.audit(root, docs=["../SKILL.md"])
    text = _joined(errors)
    assert "入口表缺 references/card-fields.md" in text, errors
    assert "入口表点名了不存在的文件 → references/没有这份.md" in text, errors
    assert re.search(r"SKILL\.md:\d+", text), errors


# ------------------------------------------------------------------ (iii)(iv) 退出码冲突

def test_same_tool_and_code_with_two_meanings_is_reported():
    """(iii) 同一个（工具, 码）两个含义 → 报错并点名两行；(iv) 码=— 不参与判定。"""
    root = _tree({
        "SKILL.md": (
            "# 测试技能\n\n"
            "## 非零退出怎么办（按\"工具 + 退出码\"对号入座）\n\n"
            + TABLE_HEAD +
            "| **`fake_tool.py`**（测试） | 2 | `有告警` | 看清单改完再跑 |\n"
            "| **`fake_tool.py`**（测试） | 2 | `用法错误` | 按提示改参数 |\n"
            "| **任何命令** | — | `import fitz` 失败 | 装 PyMuPDF |\n"
            "| **任何命令** | — | `import fitz` 失败 | 装 PyMuPDF |\n"
            "| **引擎三件套** | 2 | 版式未注册 | 走接入三步 |\n"),
    })
    errors, _, _ = audit_docs.audit(root, docs=["../SKILL.md"])
    assert len(errors) == 1, errors          # (iv)：两条「码 = —」的行一条都不算
    text = errors[0]
    assert "退出码 2" in text and "有告警" in text and "用法错误" in text, text
    assert re.search(r"SKILL\.md:\d+/\d+ ", text), text     # 两行都要点名
    assert "任何命令" not in text and "fitz" not in text, text


def test_exit_table_structure_change_is_reported():
    """退出码表结构变了（表头不是「工具」/ 数据行太少）→ 报错提醒同步检查。"""
    old = _tree({"SKILL.md": (
        "# 测试技能\n\n"
        "## 非零退出怎么办（按退出码与提示处置）\n\n"
        "| 症状 | 原因 | 处置 |\n|---|---|---|\n"
        "| **退出码 2**：`版式未注册` | 机构没入库 | 走接入三步 |\n")})
    errors, _, _ = audit_docs.audit(old, docs=["../SKILL.md"])
    assert any("退出码表的结构变了" in e for e in errors), errors
    assert len(errors) == 1, errors           # 结构不符则 6a/6b 一并跳过（不刷屏）

    short = _tree({"SKILL.md": (
        "# 测试技能\n\n"
        "## 非零退出怎么办（按\"工具 + 退出码\"对号入座）\n\n"
        + TABLE_HEAD +
        "| **引擎三件套** | 2 | 版式未注册 | 走接入三步 |\n")})
    errors, _, _ = audit_docs.audit(short, docs=["../SKILL.md"])
    assert any("退出码表的结构变了" in e for e in errors), errors


# ------------------------------------------------------------------ (ii) 退出码覆盖

def test_tool_declaring_exit_codes_without_table_row_is_a_warning():
    """(ii) 面向用户的工具声明了码，表里没有它的行 → 警告（不升级为错误）。"""
    root = _tree({
        "engine/tools/fake_tool.py": GOOD_TOOL,       # 声明 0/1，表里不提它
        "references/workflow.md": "# 流程\n",
        "SKILL.md": (
            "# 测试技能\n\n"
            + ENTRY_SECTION +
            "## 命令速查\n\n"
            "```bash\n"
            "python3 $SKILL/engine/tools/fake_tool.py --out /tmp/x\n"
            "```\n\n"
            "## 非零退出怎么办（按\"工具 + 退出码\"对号入座）\n\n"
            + TABLE_HEAD +
            "| **引擎三件套** | 2 | 版式未注册 | 走接入三步 |\n"
            "| **任何命令** | 2 | 模板加载失败 | 移出非模板 json |\n"
            + ANY_ROWS),
    })
    errors, warns, counts = audit_docs.audit(root, docs=["../SKILL.md"])
    assert errors == [], errors
    assert len(warns) == 1, warns
    assert "fake_tool.py" in warns[0] and "退出码 1" in warns[0], warns
    assert "未被说明" in warns[0], warns
    assert counts["tools"] == 1, counts


def test_maintenance_tool_named_in_prose_is_exempt():
    """「维护工具」点名即豁免：表里没有它的行也不报（名单写在文档里）。"""
    root = _tree({
        "engine/tools/fake_tool.py": GOOD_TOOL,
        "references/workflow.md": "# 流程\n",
        "SKILL.md": (
            "# 测试技能\n\n"
            + ENTRY_SECTION +
            "## 命令速查\n\n"
            "```bash\n"
            "python3 $SKILL/engine/tools/fake_tool.py --out /tmp/x\n"
            "```\n\n"
            "## 非零退出怎么办（按\"工具 + 退出码\"对号入座）\n\n"
            "（「维护工具」不在本表范围：`fake_tool.py` / `health.py`。) \n\n"
            + TABLE_HEAD +
            "| **引擎三件套** | 2 | 版式未注册 | 走接入三步 |\n"
            "| **任何命令** | 2 | 模板加载失败 | 移出非模板 json |\n"
            + ANY_ROWS),
    })
    errors, warns, _ = audit_docs.audit(root, docs=["../SKILL.md"])
    assert errors == [] and warns == [], (errors, warns)


# ------------------------------------------------------------------ (d) 环境变量

def test_env_vars_must_be_in_the_readme_table():
    """(d) 代码读到的环境变量：在总表 → 静；只在别处 → 警告；没有 → 错误。"""
    root = _tree({
        "engine/checkup/x.py": (
            "import os\n\n"
            'A = os.environ.get("CHECKUP_TMP_A")\n'
            'C = os.environ["CHECKUP_TMP_C"]\n'),
        "engine/tools/y.py": (
            "import os\n\n"
            'B = os.environ.get("CHECKUP_TMP_B")\n'),
        "README.md": (
            "# 测试\n\n"
            "某处随口提到 CHECKUP_TMP_B（不在总表章节里）。\n\n"
            "## 权限与环境变量\n\n"
            "| 变量 | 指向 | 缺省 |\n|---|---|---|\n"
            "| `CHECKUP_TMP_A` | 测试用 | 空 |\n"),
        "SKILL.md": "# 测试技能\n",
    })
    errors, warns, counts = audit_docs.audit(root, docs=["../SKILL.md"])
    etext, wtext = _joined(errors), _joined(warns)
    assert "CHECKUP_TMP_A" not in etext and "CHECKUP_TMP_A" not in wtext, \
        (errors, warns)
    assert "CHECKUP_TMP_B" in wtext and "总表" in wtext, warns
    assert "CHECKUP_TMP_C" in etext and "完全没有登记" in etext, errors
    assert counts["envs"] == 3, counts
    # 写环境变量（塞给子进程）不算"读"：不报
    assert "PYTHONIOENCODING" not in etext, errors


# ------------------------------------------------------------------ 只读

def test_probe_runs_in_a_sandbox_copy():
    """试跑（`--help`）必须发生在临时副本里 —— 被审计的树一个字都不许动。"""
    root = _tree({
        "engine/tools/noisy.py": NOISY_TOOL,
        "SKILL.md": ("# 测试技能\n\n```bash\n"
                     "python3 $SKILL/engine/tools/noisy.py\n```\n"),
    })
    audit_docs.audit(root, docs=["../SKILL.md"])
    touched = os.path.join(root, "engine", "tools", "TOUCHED")
    assert not os.path.exists(touched), \
        "试跑污染了被审计的树 —— 必须先在临时副本里跑（见事故：build_catalog）"


# ------------------------------------------------------------------ 主程序

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
