#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计文档 —— 让「文档说谎」变成立刻能发现的事。

来历：整理交付文档时，我在这几处写了不存在的东西：

* `check_reader` —— 真实函数叫 `check_carrier`
* `draft_catalog.py --src <目录>` —— 实际要 `--org` + 位置参数
* `id` 格式写成 `C-LIPID-001` —— 实际是 `C01`

三处都是**手写记忆**而非核对产物。所以这个检查器做七件事：

1. **相对链接必须可达**（按文档所在目录解析，再按技能根兜底）
2. **文档里出现的 `python3 xxx.py` 命令必须真实存在**
3. **`--flag` 必须在那个脚本的 `--help` 里**
4. **JSON 示例里的字段名必须真实存在**（拿真实产物对）
5. **入口表必须覆盖 references/ 全量** —— SKILL.md 的「何时读哪个文件」
   表格漏了哪份文档，就点名哪份（有文件却没进表 = 读不到）
6. **退出码按「工具 + 码」对账** —— SKILL.md 的「非零退出怎么办」表是
   `| 工具 | 码 | 症状 | 原因与处置 |`（一行一个「工具 + 码」）：同一个
   （工具, 码）两个含义 → **报错**；面向用户的工具声明了码却没进表 →
   警告（不算失败）；表里标了工具**没声明过**的码 → 报错。
   ⚠️ 见 `_check_exit_codes` 的 docstring：「同上」行与码 = `—` 的行怎么处理
7. **代码读到的环境变量必须登记** —— `engine/`、`scripts/`、`reconcile/`、
   `figures/` 里读到的每个名字都要出现在 README「权限与环境变量」总表里

判据保守：只报**可机械判定**的。任何需要理解语义的一律不报 ——
误报多了人就不看了，检查器也就废了。所以：

* 含义文本按**字面**比（去空白/去装饰后不同即算不同），不猜同义。
* 环境变量分两档：README **完全没有** → 报错；有但不在总表章节 → 警告；
  **tests/ 里读的不算**（测试夹具不是产品配置，逼人给它建条目只会制造误报）。
* 退出码的"码"只认整数（`—` = 与具体码无关的行，跳过）；工具的声明只认
  模块 docstring 里 ``退出码`` 后那几个 ``N = 含义``（散文里随口的数字不认）。
* 工具的覆盖范围只看 SKILL.md 命令速查里**真被调用**的名字，且维护工具
  **点名即豁免**（名单写在文档里，不硬编码）。

这个检查器**抓不到**什么（如实记）：

* 用「同上」简写把同一（工具, 码）写成两行矛盾含义 —— 6a 只判点名了工具名的行
* 表里工具名对、码对、但**含义文字**与工具实际行为不符（那要跑起来才知道）
* 文档里"说的对不对"（这套检查只查"写的东西存不存在、前后一致不一致"）

    python3 tools/audit_docs.py
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 引擎目录
#: 技能根（引擎目录的上一级）—— SKILL.md / QUICKSTART.md / README.md /
#: references/ 都在这儿（audit_docs 的 ROOT 是引擎目录，别搞混）
SKILL_ROOT = os.path.dirname(ROOT)

try:
    from checkup.console import init_console
except ImportError:                     # 直跑时把引擎根加进 sys.path
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from checkup.console import init_console
init_console()


def _refs_md(root: str) -> list[str]:
    """``references/`` 下所有 ``*.md``（相对技能根）。

    **非递归**：``references/library-index/`` 是知识库的索引数据（随库自备），
    不是本 skill 的渐进披露入口 —— 要求它进入口表只会产生噪声。
    """
    d = os.path.join(root, "references")
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join("references", f)
                  for f in os.listdir(d) if f.endswith(".md"))


def _default_docs(root: str) -> list[str]:
    """默认审计清单（相对**引擎目录**）：

    引擎文档（AGENTS.md / docs/）+ 技能根入口文档（README/SKILL/QUICKSTART）
    + ``references/`` 全量。SKILL.md 是最要紧的入口文档（命令最多），
    references/ 是渐进披露的目标 —— 这两处以前全靠人工核对，漏过。
    """
    engine = os.path.join(root, "engine")
    docs = ["AGENTS.md", "docs/数据契约.md"]
    for top in ("README.md", "SKILL.md", "QUICKSTART.md"):
        p = os.path.join(root, top)
        if os.path.exists(p):
            docs.append(os.path.relpath(p, engine))
    docs += [os.path.relpath(os.path.join(root, r), engine) for r in _refs_md(root)]
    return docs


#: 要审计的文档（相对 ROOT，也就是引擎目录）。
#:
#: **没有 CHANGELOG.md**：它是历史记录（记的是当时的命令），拿现在的脚本
#: 去核对它只会产生噪声 —— 这类"注定说谎"的文档不该进清单。
DOCS = _default_docs(SKILL_ROOT)

#: 本次审计的技能根（``audit()`` 会设它）—— 试跑用的临时副本按它拷
_AUDIT_ROOT = SKILL_ROOT
#: 试跑沙箱（按根缓存：临时树可能被改，一次审计从零拷一次）
_SANDBOX: dict[str, str] = {}


def _sandbox_tree_of(root: str) -> str:
    """整棵技能树的临时副本（试跑文档里的脚本用）。

    ⚠️ **只读审计绝不能改动仓库**：不是所有脚本都认 ``--help`` ——
    实测 ``build_catalog.py --help`` 会真的执行并重建 ``catalog/analytes.json``，
    把包内已登记的新机构规则覆写没了。所以试跑一律在临时副本里做。
    """
    if root not in _SANDBOX:
        import shutil
        import tempfile
        dst = os.path.join(tempfile.mkdtemp(prefix="audit_docs_"), "skill")
        shutil.copytree(root, dst,
                        ignore=shutil.ignore_patterns("__pycache__",
                                                      ".pytest_cache", "*.pyc", ".git"))
        _SANDBOX[root] = dst
    return _SANDBOX[root]


def _sandbox_tree() -> str:
    """当前审计根的临时副本（注意事项见 :func:`_sandbox_tree_of`）。"""
    return _sandbox_tree_of(_AUDIT_ROOT)


def _child_env() -> dict:
    """子进程环境：显式 UTF-8（Windows 上子进程打印符号不崩）。"""
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    return env


def _lineno(text: str, pos: int) -> int:
    """字符偏移 → 行号（报错要点名 file:line）。"""
    return text.count("\n", 0, pos) + 1


def _doc_path(root: str, md: str) -> str:
    """文档清单里的一条 → 真实路径。

    ``normpath`` 不是可选的：清单是**相对引擎目录**写的（如 ``../SKILL.md``），
    不归一化的话 ``<根>/engine/../SKILL.md`` 会因为中间那一级不存在而打不开
    （测试树里就常常没有 engine/ 这一级）。
    """
    return os.path.normpath(os.path.join(root, "engine", md))


def _section(root: str, md: str, head: str
             ) -> tuple[int, list[tuple[int, str]]] | None:
    """取 ``## <head>`` 章节的行（到下一个 ``## `` 或文末）。

    返回 ``(标题行号, [(行号, 行文本)])``；没有这一节返回 ``None``。
    """
    path = _doc_path(root, md)
    if not os.path.exists(path):
        return None
    lines = io.open(path, encoding="utf-8").read().split("\n")
    title = re.compile(r"^##\s*" + re.escape(head))
    hit = [i for i, txt in enumerate(lines) if title.match(txt)]
    if not hit:
        return None
    start = hit[0]
    out = []
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        out.append((i + 1, lines[i]))
    return (start + 1, out)


# ---------------------------------------------------------------- 1. 链接

def _rels(root: str, md: str) -> list[tuple[int, str, str, str]]:
    """文档里的相对链接 → ``(行号, 链接原文, 按文档目录解析, 按技能根解析)``。

    两个候选都要试：references/ 下的文档有时按**技能根**写路径
    （`contracts/…`、`examples/…`）而不是相对自己 —— 那是这个仓库的既有写法，
    不能因为"相对 references/ 找不到"就报断链（误报会让这检查废掉）。
    """
    path = _doc_path(root, md)
    text = io.open(path, encoding="utf-8").read()
    base = os.path.dirname(path)
    out = []
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        t = m.group(2)
        if t.startswith(("http", "#", "mailto")):
            continue
        rel = urllib.parse.unquote(t.split("#")[0])
        if not rel or any(c in rel for c in "<>…*"):
            continue                    # 占位/模板式链接：机械判不了，不报
        out.append((_lineno(text, m.start()), t,
                    os.path.normpath(os.path.join(base, rel)),
                    os.path.normpath(os.path.join(root, rel))))
    return out


# ---------------------------------------------------------------- 2 & 3. 命令与参数

#: 文档里的命令。三种写法都要认：
#: ``python3 run_pipeline.py``（引擎根，老写法）· ``python3 scripts/x.py``（技能根）
#: · ``python3 "$SKILL/engine/tools/x.py"``（技能根文档的写法，引号可有可无）
_CMD = re.compile(r"python3\s+[\"']?(?:\./)?(?:\$\{?SKILL\}?/)?"
                  r"([A-Za-z_][\w/]*\.py)([^\n`]*)")


def _commands(root: str, md: str) -> list[tuple[int, str, str]]:
    """文档里的 `python3 xxx.py …` → ``(行号, 脚本, 参数尾巴)``。"""
    path = _doc_path(root, md)
    text = io.open(path, encoding="utf-8").read()
    return [(_lineno(text, m.start()), m.group(1), m.group(2))
            for m in _CMD.finditer(text)]


def _flags(cmd_tail: str) -> list[str]:
    return re.findall(r"(--[a-z][\w-]*)", cmd_tail)


def _probe(root: str, real: str) -> set[str] | None:
    """试跑 ``--help`` 取那个脚本认识的参数；``None`` = 没法试（跳过检查）。

    **必须在临时副本里跑**（见 :func:`_sandbox_tree_of`）。
    """
    rel = os.path.relpath(real, root)
    if rel.startswith(".."):
        return None                     # 不在技能树内：不试跑（只读原则）
    sandbox = _sandbox_tree()
    probe = os.path.join(sandbox, rel)
    if not os.path.exists(probe):
        return None
    cwd = os.path.join(sandbox, "engine")
    if not os.path.isdir(cwd):
        cwd = sandbox
    try:
        r = subprocess.run([sys.executable, probe, "--help"],
                           cwd=cwd, capture_output=True, text=True,
                           timeout=30, encoding="utf-8", errors="replace",
                           env=_child_env())
        return set(re.findall(r"(--[a-z][\w-]*)", r.stdout or ""))
    except Exception:  # noqa: BLE001
        return set()


# ---------------------------------------------------------------- 5. 入口表

#: 入口表里的引用：`references/xxx.md`（表格里用反引号写，不是 markdown 链接，
#: 所以链接检查覆盖不到它 —— 这正是要单独查一遍的原因）
_REF_IN_TABLE = re.compile(r"references/([^\s`)（）|、·，,]+\.md)")
#: 入口表里被反引号括起来的 .md 路径（反向检查：点了名的是不是真存在）
_MD_TICK = re.compile(r"`([^`\s]+\.md)`")


def _check_entry_table(root: str, errors: list[str], warns: list[str]) -> None:
    """检查 A：SKILL.md 入口表必须覆盖 references/ 全量（双向）。"""
    sec = _section(root, "../SKILL.md", "何时读哪个文件")
    if sec is None:
        if os.path.exists(os.path.join(root, "SKILL.md")):
            warns.append("../SKILL.md 里找不到「## 何时读哪个文件」章节 —— "
                         "入口表完整性没法检查（章节被改名了？）")
        return
    title, rows = sec
    text = "\n".join(txt for _, txt in rows)
    listed = set(_REF_IN_TABLE.findall(text))
    for f in (os.path.basename(r) for r in _refs_md(root)):
        if f not in listed:
            errors.append(f"../SKILL.md:{title} 入口表缺 references/{f}"
                          f"（有这份文档却没进表 → agent 读不到它）")
    for lineno, line in rows:
        for m in _MD_TICK.finditer(line):
            t = m.group(1)
            if t.startswith(("http", "/", "$", "#")) or "<" in t:
                continue
            if not os.path.exists(os.path.join(root, t)):
                errors.append(f"../SKILL.md:{lineno} 入口表点名了不存在的文件 → {t}")


# ---------------------------------------------------------------- 6. 退出码
#
# 表的形状：``## 非零退出怎么办`` 下面是 ``| 工具 | 码 | 症状 | 原因与处置 |``。
# 第一列是**工具范围**（可以是「同上」= 继承上一行；也可以是「任何命令」这种宽范围），
# 所以「同一个数字在不同工具里含义不同」是正常且必要的 —— 检查必须按
# **（工具, 码）** 作用域比，不能按"同一数字出现两次即冲突"来判。

#: 退出码表的章节标题（结构变了这条检查要同步）
_EXIT_TABLE_HEAD = "非零退出怎么办"
#: 表结构的最低要求：第一列表头 = 工具，且至少有这么多**数据行**
_TABLE_MIN_ROWS = 5
#: 工具 docstring 里的声明：``退出码：0 = …；1 = …``（分隔符可能是 ·／；／。）
_DECL_PAIR = re.compile(r"(\d)\s*=\s*([^·;；。\n]+)")


def _plain(s: str) -> str:
    """含义文本去装饰（``**``、反引号、前导的 ``：``）—— 给人看的那份。"""
    s = s.strip().replace("*", "").replace("`", "")
    return s.lstrip("：:，,、-— \u3000").strip()


def _norm(s: str) -> str:
    """比较含义是否相同用的键：再去空白与句尾标点（字面比，不猜同义）。"""
    return re.sub(r"[\s\u3000]+", "", _plain(s)).rstrip("…。:；;，,")


def _table_rows(rows: list[tuple[int, str]]) -> list[tuple[int, list[str]]]:
    """章节里的 ``| a | b | c |`` 行 → ``[(行号, [单元格…])]``（跳过分隔行）。"""
    out = []
    for lineno, line in rows:
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue                    # |---|---| 分隔行
        out.append((lineno, cells))
    return out


def _quick_tools(root: str) -> set[str]:
    """SKILL.md「## 命令速查」里真的被调用的 ``engine/tools/*.py`` 文件名。

    B2 只查**面向用户**的工具（在命令速查里出现的那些）；维护工具不在表内 ——
    它们按各自 ``--help`` 与输出处置（SKILL.md 在退出码表前点名）。
    """
    sec = _section(root, "../SKILL.md", "命令速查")
    if not sec:
        return set()
    text = "\n".join(txt for _, txt in sec[1])
    return set(re.findall(r"engine/tools/([A-Za-z_]\w*\.py)", text))


def _exempt_tools(text: str) -> set[str]:
    """退出码表前「**维护工具**不在本表范围：…」那句里点名的工具 → 豁免。

    豁免名单**写在文档里**（人看得见、改得动），这里只照读 —— 不硬编码工具名。
    """
    out: set[str] = set()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "维护工具" in line:           # 名单可能换行，一并读下一行
            for t in (line, lines[i + 1] if i + 1 < len(lines) else ""):
                out |= set(re.findall(r"([A-Za-z_]\w*\.py)", t))
    return out


def _declared_exit_codes(path: str) -> tuple[int, dict[int, str]]:
    """工具模块 docstring 声明的退出码 → ``(声明行号, {码: 含义})``。

    只认 docstring，且只认 ``退出码`` 后面那几个 ``N = 含义``（散文里随口的
    数字不认 —— 判据保守）。
    """
    text = io.open(path, encoding="utf-8").read()
    try:
        mod = ast.parse(text)
        doc = ast.get_docstring(mod) or ""
    except SyntaxError:
        return (0, {})
    base = mod.body[0].lineno if mod.body else 1
    out: dict[int, str] = {}
    line = 0
    for k, dline in enumerate(doc.split("\n")):
        if "退出码" not in dline:
            continue
        for m in _DECL_PAIR.finditer(dline.split("退出码", 1)[1]):
            out[int(m.group(1))] = m.group(2).strip()
        line = line or base + k
    return (line, out)


def _declared_tools(root: str) -> tuple[dict[str, dict[int, str]], dict[str, int]]:
    """``engine/tools/*.py`` 里声明了退出码的工具 → ``({工具: {码: 含义}}, {工具: 行号})``。"""
    tools: dict[str, dict[int, str]] = {}
    decl_at: dict[str, int] = {}
    tdir = os.path.join(root, "engine", "tools")
    for fn in sorted(os.listdir(tdir)) if os.path.isdir(tdir) else []:
        if not fn.endswith(".py"):
            continue
        line, codes = _declared_exit_codes(os.path.join(tdir, fn))
        if codes:
            tools[fn] = codes
            decl_at[fn] = line
    return tools, decl_at


def _check_exit_codes(root: str, errors: list[str], warns: list[str],
                      counts: dict) -> None:
    """检查 B：退出码表按 **（工具, 码）** 作用域对账。

    判据（工具名与码都从表行**结构**里取，不再正则扫整段文本）：

    * **6a（错误）**：同一个（工具, 码）有两个不同症状文本 → 报错，点名所有行。
      不同工具用同一个码不算冲突（引擎的 2=版式未注册、accept_template 的
      2=REVIEW、任何命令的 2=模板加载失败，都是正常且必要的）。
      **「同上」行不进 6a**：它没有可归属的工具名 —— 表里「同一码多种症状」的
      并列写法正是靠「同上」展开的（引擎三件套 / 码 2）。硬归属会把**有意的
      并列**判成冲突，而这两者机械上分不开；代价是用「同上」写出的两个矛盾
      含义会漏掉（宁可漏报，不猜）。要连「同上」一起判，把下面的
      ``if inherited or not sym`` 改成 ``if not sym`` 并自行承担并列误报。
    * **6b（警告）**：面向用户的工具（SKILL.md 命令速查里被调用、且 docstring
      声明了退出码）的每个**非零码**，表里要有「工具 = 它 且 码 = 该码」的行；
      找不到 → 警告（不算失败）。维护工具**点名即豁免**（名单写在文档里）。
    * **另加（错误）**：表里给某工具标了它**没声明过**的码 → 冲突。
    """
    sec = _section(root, "../SKILL.md", _EXIT_TABLE_HEAD)
    if sec is None:
        if os.path.exists(os.path.join(root, "SKILL.md")):
            warns.append("../SKILL.md 里找不到「## 非零退出怎么办」章节 —— "
                         "退出码检查没法做（章节被改名了？）")
        return
    rows = _table_rows(sec[1])
    head = _plain(rows[0][1][0]) if rows and rows[0][1] else ""
    if head != "工具" or len(rows) - 1 < _TABLE_MIN_ROWS:
        errors.append(
            f"../SKILL.md:{sec[0]} 退出码表的结构变了（第一列表头应为「工具」、"
            f"数据行 ≥ {_TABLE_MIN_ROWS}；实测表头「{head}」、"
            f"{max(len(rows) - 1, 0)} 行）—— audit_docs 的退出码检查需同步，已跳过")
        return

    # 展平成 (行号, 工具, 是否「同上」继承行, 码, 症状)
    table: list[tuple[int, str, bool, int, str]] = []
    last = ""
    for lineno, cells in rows[1:]:
        raw = _plain(cells[0]) if cells else ""
        inherited = (not raw) or raw.startswith("同上")
        if not inherited:
            last = raw
        sym = _plain(cells[2]) if len(cells) > 2 else ""
        for c in re.findall(r"\d+", cells[1] if len(cells) > 1 else ""):
            table.append((lineno, last, inherited, int(c), sym))

    # ---- 6a：同一个（工具, 码）只许有一个含义（「同上」行不进这一关）
    seen: dict[tuple[str, int], dict[str, str]] = {}
    where: dict[tuple[str, int], list[int]] = {}
    for lineno, tool, inherited, code, sym in table:
        if inherited or not sym:
            continue
        seen.setdefault((tool, code), {}).setdefault(_norm(sym), sym)
        where.setdefault((tool, code), []).append(lineno)
    for (tool, code), syms in sorted(seen.items()):
        if len(syms) > 1:
            lines = "/".join(str(n) for n in sorted(set(where[(tool, code)])))
            errors.append(
                f"../SKILL.md:{lines} 工具「{tool}」的退出码 {code} 有 "
                f"{len(syms)} 个不同症状文本："
                + "｜".join(f"「{s}」" for s in syms.values())
                + " —— 同一（工具, 码）只认一个含义")

    # ---- 6b：面向用户的工具，声明的码要被"说明过"（维护工具点名即豁免）
    tools, decl_at = _declared_tools(root)
    counts["tools"] = len(tools)
    exempt = _exempt_tools("\n".join(txt for _, txt in sec[1]))
    for fn in sorted((_quick_tools(root) - exempt) & set(tools)):
        codes = tools[fn]
        listed = {c for _, tool, _, c, _ in table if fn in tool}
        nonzero = sorted(c for c in codes if c)      # 0 = 正常退出，不算"码"
        if not any(fn in tool for _, tool, _, _, _ in table):
            warns.append(
                f"engine/tools/{fn}:{decl_at[fn]} 声明退出码 "
                f"{'/'.join(map(str, nonzero))}，但退出码表里没有它的行"
                f"（未被说明）")
            continue
        bad = sorted(c for c in listed if c not in codes)
        if bad:
            errors.append(
                f"../SKILL.md 退出码表把退出码 {'/'.join(map(str, bad))} 归给 {fn}，"
                f"但它声明的是 {'/'.join(map(str, sorted(codes)))}（冲突）")
        for c in sorted(c for c in nonzero if c not in listed):
            warns.append(
                f"engine/tools/{fn}:{decl_at[fn]} 声明退出码 {c}，"
                f"退出码表里没有「工具 = {fn} 且 码 = {c}」的行（未被说明）")


# ---------------------------------------------------------------- 7. 环境变量

#: 代码里"读环境变量"的写法（写/塞进子进程环境不算）。除了 os.environ，
#: 还有把 environ 传参后 ``env.get(…)`` / ``env[…]`` 的形态。
_ENV_PATS = (
    re.compile(r"os\.environ\.get\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]+)[\"']\s*\](?!\s*=[^=])"),
    re.compile(r"\benviron\.get\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"\benv\.get\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"\benv(?:iron)?\[\s*[\"']([A-Z][A-Z0-9_]+)[\"']\s*\](?!\s*=[^=])"),
)
#: 后三条（env/environs 形态）只在"确实在鼓捣 environ"的文件里才认，
#: 免得把同名的普通字典当成环境变量
_ENV_PLUMBING = 3


def _env_reads(root: str) -> list[tuple[str, int, str]]:
    """扫四个目录的 ``.py`` → ``[(相对路径, 行号, 变量名)]``（已去重、排序）。

    **不扫 ``tests/``**：那里读环境变量是为了构造场景（夹具名如
    ``CHECKUP_TMP_A``），不是产品配置 —— 逼人为测试夹具建 README 条目
    只会制造误报，而误报多了这个检查就废了。
    """
    hits: set[tuple[str, int, str]] = set()
    for sub in ("engine", "scripts", "reconcile", "figures"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, sub)):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", ".pytest_cache", "tests")]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    text = io.open(path, encoding="utf-8").read()
                except OSError:
                    continue
                plumbing = "environ" in text
                rel = os.path.relpath(path, root)
                for i, line in enumerate(text.split("\n"), 1):
                    for k, pat in enumerate(_ENV_PATS):
                        if k >= _ENV_PLUMBING and not plumbing:
                            continue
                        for m in pat.finditer(line):
                            hits.add((rel, i, m.group(1)))
    return sorted(hits)


def _check_env_vars(root: str, errors: list[str], warns: list[str],
                    counts: dict) -> None:
    """检查 C：代码读到的环境变量必须在 README「权限与环境变量」总表里。"""
    reads = _env_reads(root)
    names = sorted({n for _, _, n in reads})
    counts["envs"] = len(names)
    if not names:
        return
    rp = os.path.join(root, "README.md")
    readme = io.open(rp, encoding="utf-8").read() if os.path.exists(rp) else ""
    sec = _section(root, "../README.md", "权限与环境变量")
    table = "\n".join(txt for _, txt in sec[1]) if sec else ""
    first: dict[str, tuple[str, int]] = {}
    for rel, ln, name in reads:
        first.setdefault(name, (rel, ln))
    for name in names:
        rel, ln = first[name]
        sites = sum(1 for _, _, n in reads if n == name)
        if name not in readme:
            errors.append(f"{rel}:{ln} 读环境变量 {name}（{sites} 处），"
                          f"README 里完全没有登记")
        elif name not in table:
            warns.append(f"{rel}:{ln} 读环境变量 {name}（{sites} 处），"
                         f"但不在 README「权限与环境变量」总表章节里")


# ---------------------------------------------------------------- 4. JSON 示例

#: 真实产物里没有、但允许出现在示例里的"讲解用"键
_JSON_OK = {"level", "checks", "name", "detail", "items"}


def _check_json_examples(root: str, docs: list[str], errors: list[str]) -> None:
    """JSON 示例里的字段名必须能在真实产物里找到。"""
    try:
        real = json.load(open(os.path.join(root, "engine", "out",
                                           "report_2026.json"),
                              encoding="utf-8"))
        real_keys = set(real.keys()) | set(real["document"].keys())
        if real.get("observations"):
            real_keys |= set(real["observations"][0].keys())
    except (OSError, ValueError, KeyError):
        real_keys = set()
    if not real_keys:
        return
    for md in docs:
        path = _doc_path(root, md)
        s = io.open(path, encoding="utf-8").read()
        for blk in re.findall(r"```jsonc\n(.*?)```", s, re.S):
            for key in re.findall(r'"(\w+)":', blk):
                if key not in real_keys and key not in _JSON_OK:
                    errors.append(
                        f"{md} JSON 示例里的字段 {key!r} 在真实产物里不存在")


# ---------------------------------------------------------------- 主流程

def audit(root: str = "", docs: list[str] | None = None
          ) -> tuple[list[str], list[str], dict]:
    """跑全部检查，返回 ``(错误, 警告, 计数)``。

    不打印、不退出 —— 测试要能在**临时目录**里造一棵最小技能树来跑它
    （见 ``tests/test_audit_docs.py``）。
    """
    global _AUDIT_ROOT, _SANDBOX
    _AUDIT_ROOT = os.path.abspath(root or SKILL_ROOT)
    _SANDBOX = {}
    if docs is None:
        docs = list(DOCS) if _AUDIT_ROOT == SKILL_ROOT \
            else _default_docs(_AUDIT_ROOT)
    engine = os.path.join(_AUDIT_ROOT, "engine")
    docs = [d for d in docs if os.path.exists(_doc_path(_AUDIT_ROOT, d))]

    errors: list[str] = []
    warns: list[str] = []
    counts = {"docs": len(docs), "links": 0, "cmds": 0, "flags": 0,
              "envs": 0, "tools": 0}

    # ---- 1. 链接
    for md in docs:
        for lineno, raw, as_file, as_root in _rels(_AUDIT_ROOT, md):
            counts["links"] += 1
            if not (os.path.exists(as_file) or os.path.exists(as_root)):
                errors.append(f"{md}:{lineno} 链接失效 → {raw}")

    # ---- 2 & 3. 命令与参数
    helps: dict[str, set | None] = {}
    for md in docs:
        for lineno, script, tail in _commands(_AUDIT_ROOT, md):
            counts["cmds"] += 1
            # 文档里的命令可能相对**引擎根**（老写法 `python3 run_pipeline.py`）
            # 或相对**技能根**（新写法 `python3 scripts/eval_synth.py`）→ 两处都试
            real = ""
            for cand in (os.path.join(engine, script),
                         os.path.join(_AUDIT_ROOT, script)):
                if os.path.exists(cand):
                    real = os.path.normpath(cand)
                    break
            if not real:
                errors.append(f"{md}:{lineno} 文档里的命令不存在 → python3 {script}")
                continue
            if real not in helps:
                helps[real] = _probe(_AUDIT_ROOT, real)
            for f in _flags(tail):
                counts["flags"] += 1
                if helps[real] is None:
                    continue            # 不在技能树内：没试跑，跳过（不猜）
                if f not in helps[real]:
                    errors.append(f"{md}:{lineno} python3 {script} 没有参数 {f}")

    # ---- 4. JSON 示例
    _check_json_examples(_AUDIT_ROOT, docs, errors)

    # ---- 5/6/7. 跨文档一致性
    _check_entry_table(_AUDIT_ROOT, errors, warns)
    _check_exit_codes(_AUDIT_ROOT, errors, warns, counts)
    _check_env_vars(_AUDIT_ROOT, errors, warns, counts)
    return errors, warns, counts


def main() -> int:
    errors, warns, counts = audit()
    print(f"审计 {counts['docs']} 份文档 · {counts['links']} 个链接 · "
          f"{counts['cmds']} 条命令 · {counts['flags']} 个参数 · "
          f"{counts['envs']} 个环境变量 · {counts['tools']} 份工具退出码声明")
    print("=" * 84)
    if errors or warns:
        print()
    for p in errors:
        print(f"  ✗ {p}")
    for p in warns:
        print(f"  ⚠ {p}")
    if errors:
        print()
        print(f"共 {len(errors)} 处错误"
              + (f" · {len(warns)} 处警告" if warns else "")
              + " —— 文档与产物不符")
        return 1
    if warns:
        print()
    print("✓ 文档里没有可机械判定的不实内容"
          + (f"（另有 {len(warns)} 处警告）" if warns else ""))
    print("  （这只查「写的东西存不存在」，查不了「说的对不对」）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
