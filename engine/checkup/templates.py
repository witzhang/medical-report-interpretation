# -*- coding: utf-8 -*-
"""报告模板注册表（框架 D1）。

上一轮的跨机构测试暴露了一个硬假设：

    SECTION_X_MAX = 60.0   # 段标题 x≈47 vs 项目名 x≈61

这是**对一家机构版式的硬编码**。项目名若落在段标题列，每个项目都会被当成
段标题而整页丢弃。换一家机构就得改代码——这不是能扩展的形态。

本模块把这些假设全部提出来变成**数据**：

    template_registry/<机构>/<版本>.json

新增机构 = 加一个 JSON，**不改代码**。这也是框架 §五 里 Azure composed models
「分类器 + 多模板路由」与专利 US10853638「版面指纹 + 已知表单库」的落地。

原则（沿用项目既有的）：**新机构/改版只新增 version，永不修改旧版**。

⚠️ **版本并存有歧义（B7）**：``valid_from`` / ``valid_to`` / ``supersedes`` 三个字段
**不参与路由** —— ``TemplateRegistry.match()`` 只看锚点串/页尺寸/栏数。同一个
``org_id`` 有多个版本并存时，两条模板常常**同分**，于是"谁先被加载谁赢"
（按文件名排序），结果不可预期。治理口径：

1. **一机构只留"当前版"** 在模板目录里；旧版移到目录之外（如 ``archive/``
   子目录 —— 加载器只扫一层 ``*.json``，不会读它）。
2. 真要按年份路由，把年份**显式传进来**（``pipeline.route_document(doc, year=…)``
   这一路已有 ``year_from_name``），而不是指望模板自己按 ``valid_from`` 挑。
3. ``default_registry()`` 在加载后检测到"同机构多版本"会**打告警**
   （``TemplateRegistry.ambiguity()`` 也能拿到清单），把歧义摆到明面上。
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .layout import rows_of_page

#: 目录
REGISTRY_DIR = "templates"

#: **验收档案**的文件名后缀（如 ``NEW-001.accept.json``）。
#: 档案由 ``tools/accept_template.py`` 生成，记录"这套模板是怎么被验收通过的"
#: （样本/证据/结论/模板哈希）。它**不是模板**，加载器必须跳过 ——
#: 否则"凡 .json 即模板"的假设会把档案喂给 ``Template(**d)`` 直接崩掉
#: （Windows 实测：接入一家新机构即毒化整个模板库）。
#: 兼容说明：早期版本的档案曾写在模板目录里，这里靠后缀识别并跳过，无需人工清理。
ARCHIVE_SUFFIX = ".accept.json"

#: **资产记录**（D4，``<org>.asset.json``）与**资产包清单**（D2，``bundle.json``）：
#: 也不是模板，加载器必须一并跳过 —— 否则"接入一家新机构 + 给它建资产记录"就会
#: 毒化整个模板库（实测报 ``Template.__init__() got an unexpected keyword
#: argument 'bundle_format'``）—— 与当初"验收档案毒化模板库"是同一类事故。
ASSET_SUFFIX = ".asset.json"
BUNDLE_MANIFEST = "bundle.json"


class TemplateLoadError(RuntimeError):
    """模板目录里某个文件加载失败（**消息必须带文件名**，供 fail-loud 报错）。"""


@dataclass
class LabTemplate:
    """化验单通道的版面假设。"""

    page_marker: str = "检验报告单"
    header_cells: list[str] = field(default_factory=lambda: ["检验项目", "结果", "参考值"])
    #: 一页里并排几组「检验项目/结果/参考值」。本机构是 2（双栏）。
    column_groups: int = 2
    #: 段标题所在的列右边界（本机构：段标题 x≈47，项目名 x≈61）
    section_x_max: float = 60.0
    #: 表头里"提示/状态"列的字样（如「提示」「状态」「异常提示」）。
    #: 配置后该列的词不参与抽取 —— 旗标由引擎自算（``_calc_flag``），
    #: 报告画的箭头/字母只作交叉参考，不采信。缺省空 = 没有这一列。
    flag_cells: list[str] = field(default_factory=list)
    #: 表头里**单位列**的字样（如「单位」）。参考值列与单位列分开排版时，
    #: 配置后单位列的词收进 ``unit_cell``（不混进参考值）—— 参考值保持干净，
    #: 双实现交叉（E3）不会被单位后缀淹没，而单位仍随结果进入产物。
    unit_cells: list[str] = field(default_factory=list)
    section_stop: list[str] = field(
        default_factory=lambda: ["检验项目", "结果", "参考值"])
    noise_pattern: str = (r"^(审核医生|检验者|检查者|审核日期|操作员|主检医生|医师|"
                          r"体检日期|保健号|体检号|姓名|性别|年龄|ID)"
                          r"|^第[\d\-]+页|检验报告单")


@dataclass
class NarrativeTemplate:
    """叙述/标量通道的版面假设。"""

    #: 标签列右边界（项目标签 / 段标题）
    label_x_max: float = 130.0
    #: 段标题列右边界（比标签列更窄；两者之间是"小结列表"的第二级缩进）
    section_x_max: float = 70.0
    narrative_sections: list[str] = field(default_factory=lambda: [
        "彩超", "CT", "MRI", "磁共振", "超声", "心电图", "经颅多普勒", "TCD",
        "骨密度", "胸片", "钼靶", "DR", "X线", "胃镜", "肠镜", "肺功能",
        "动脉硬化", "人体成分"])
    noise_labels: list[str] = field(default_factory=lambda: [
        "医师", "检验者", "审核医生", "操作员", "主检医生", "审核日期",
        "姓名", "性别", "年龄", "保健号", "体检号", "单位", "体检日期", "ID"])
    noise_row_pattern: str = (r"体检日期[:：]|保健号|体检号|^姓名[:：]|^ID[:：]|"
                              r"^第[\d\-]+页|体\s*检\s*报\s*告|检验报告单|"
                              r"^单位$|^性别$|^年龄$")
    #: 签名行是**一个文字块**（如「医师：马斌」），不命中标签正则，要单独拦
    noise_inline_pattern: str = (r"^(医师|检验者|检查者|检查医师|审核医生|操作员|"
                                 r"主检医生|审核日期)[:：]")
    #: 项目名/段名 → 规范检查方式的别名表：``[[正则, 规范名], ...]``。
    #: 有的机构把检查方式写进项目名（「甲状腺彩超」「【CT-平扫】胸部」）、
    #: 段名里反而没有；覆盖判定只认规范名（彩超/CT/心电图），故在此折算。
    modality_aliases: list[list[str]] = field(default_factory=list)
    #: 检查方式证据：``[[正则, 规范名] 或 [正则, 规范名, 项目名], ...]``。
    #: 用于「检查确实做了、结果行里却没有可解析项目名」（如心电图只剩项目
    #: 清单/抬头字样）：全文扫到即补一条叙述项（带匹配原文，不含测量值），
    #: 供覆盖判定与报告引用；仅当该年已有同方式条目时不补。
    modality_evidence: list[list[str]] = field(default_factory=list)
    #: 整页排除字样：页面文本命中即由叙述通道整页跳过（如「历年检验结果
    #: 汇总表」——多年数值并排，混进来会污染当年数据）。
    page_exclude_markers: list[str] = field(default_factory=list)
    #: 小结行 → 检查项目的器官关键词
    organ_keys: list[list[str]] = field(default_factory=lambda: [
        ["甲状腺", "甲状腺"],
        ["颈部血管", "颈部血管"], ["颈动脉", "颈部血管"],
        ["肝脏", "肝脏"], ["肝", "肝脏"],
        ["胆囊", "胆囊"], ["胆", "胆囊"],
        ["胰腺", "胰腺"], ["胰", "胰腺"],
        ["脾脏", "脾脏"], ["脾", "脾脏"],
        ["双肾", "双肾"], ["肾", "双肾"],
        ["输尿管", "输尿管"], ["膀胱", "膀胱"], ["前列腺", "前列腺"],
        ["子宫", "子宫"], ["卵巢", "卵巢"], ["乳腺", "乳腺"],
        ["肺", "肺"], ["肺", "肺部CT"], ["胸部", "肺部CT"],
    ])


@dataclass
class Template:
    """一个机构的一套版式。"""

    org_id: str
    org_name: str = ""
    template_version: int = 1
    valid_from: str = ""
    valid_to: Optional[str] = None
    supersedes: Optional[int] = None
    #: 版面指纹：用于把进来的文档路由到正确模板
    page_size: list[float] = field(default_factory=lambda: [595.0, 842.0])
    header_anchor_strings: list[str] = field(
        default_factory=lambda: ["检验报告单", "检验项目", "参考值"])
    #: **必须全部命中**才算这家机构。用于放机构专属字样（如自家报告抬头），
    #: 否则别家报告会因为通用表头词命中而被错配过来。
    required_anchors: list[str] = field(default_factory=list)
    #: 主检报告页的识别字样（任一命中即该页为主检报告页）。默认是示例/合成
    #: 机构用的两个词；真实机构常写「主检结论及建议」「主要健康问题」等，
    #: 在模板里自配（跨页的续页要选一个续页上也有的词，如「建议您」）。
    main_report_markers: list[str] = field(
        default_factory=lambda: ["主要疾病诊断", "其他阳性发现"])
    #: 体检日期标签（在题头/页脚里的叫法）。空 = 内置默认五个。有的机构
    #: 只写「审核日期」「日期」——配在这里，日期仍一律取自内页。
    exam_date_labels: list[str] = field(default_factory=list)
    lab: LabTemplate = field(default_factory=LabTemplate)
    narrative: NarrativeTemplate = field(default_factory=NarrativeTemplate)
    #: 抽取基线，用于**漂移监控**：同机构改版后项数会突然偏离
    #: ``{"items_per_report": [81,81,81], "items_per_page": {"10": 72, "11": 9}}``
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        d = dict(d)
        lab = LabTemplate(**(d.pop("lab", {}) or {}))
        nar = NarrativeTemplate(**(d.pop("narrative", {}) or {}))
        return cls(lab=lab, narrative=nar, **d)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


#: ⚠️ **已删除**「内置默认模板」常量（P0-2）。原先它是抽取层的兜底：
#: 模板匹配不上时，各函数以它为默认参数静默套用本机构版式，
#: 于是换机构既不会报错、也走不到 ``UNKNOWN_LAYOUT``，交出一份"没发现问题"的
#: 报告 —— 最危险的失败模式。现在模板是**必填输入**：拿不到就报错、停下。
#: （调用点见 ``require_template``；``TemplateRegistry.load`` 不再兜底。）


def require_template(t: Optional["Template"], where: str = "") -> "Template":
    """显式取模板：None → **报错**，禁止任何形式的静默回落（P0-2）。"""
    if t is None:
        raise ValueError(
            "未提供报告模板（template=None）：本引擎不内置任何默认版式。"
            "模板指纹匹配不上时必须停下（UNKNOWN_LAYOUT）、非零退出，"
            "不许拿别的机构的版式硬套。" + (f"（调用点：{where}）" if where else ""))
    return t


# ---------------------------------------------------------------- 注册表

@dataclass
class MatchResult:
    template: Optional[Template]
    score: float
    reason: str


class TemplateRegistry:
    """模板注册表：加载 + 按指纹路由。"""

    def __init__(self, templates: Optional[list[Template]] = None):
        self.templates: list[Template] = list(templates or [])

    @classmethod
    def load(cls, directory: str) -> "TemplateRegistry":
        """从目录加载全部 ``*.json``。

        ⚠️ 目录不存在 / 空目录 → **空注册表**（P0-2）：不再回落内置默认模板。
        空注册表匹配任何文档都返回 ``None`` → 上层按 UNKNOWN_LAYOUT 处理
        （FATAL，非零退出），而不是拿内置模板硬套。"""
        tpls: list[Template] = []
        if os.path.isdir(directory):
            for fn in sorted(os.listdir(directory)):
                if fn.endswith((ARCHIVE_SUFFIX, ASSET_SUFFIX)) \
                        or fn == BUNDLE_MANIFEST:
                    continue          # 档案/资产记录/资产包清单：都不是模板
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(directory, fn)
                try:
                    with open(path, encoding="utf-8") as f:
                        tpls.append(Template.from_dict(json.load(f)))
                except Exception as e:  # noqa: BLE001
                    # **fail loud 且带文件名**：静默跳过会让"模板没生效"无声无息
                    raise TemplateLoadError(
                        f"{fn}：{type(e).__name__}: {e}") from None
        return cls(tpls)

    def add(self, t: Template) -> None:
        self.templates.append(t)

    def ambiguity(self) -> list[tuple[str, list[int]]]:
        """**同机构多版本并存**的清单（B7）：``[(org_id, [版本…]), …]``。

        这些机构的路由**有歧义**：``match()`` 不读 ``valid_from``，版本间分数
        并列时按加载顺序取胜。空列表 = 没有歧义。
        """
        by_org: dict[str, list[int]] = {}
        for t in self.templates:
            by_org.setdefault(t.org_id, []).append(t.template_version)
        return sorted((org, sorted(vs)) for org, vs in by_org.items()
                      if len(vs) > 1)

    def match(self, doc, min_score: float = 0.5) -> MatchResult:
        """按指纹选模板。**匹配不上就返回 None，让调用方决定，不猜。**

        指纹三项：

        1. **锚点串命中率** —— 标题/表头字样是否出现
        2. **页尺寸**
        3. **单行表头词出现数 ÷ 3 = 栏数** —— 这一项才是区分**单栏与双栏**
           的关键。只看锚点串的话，单栏与双栏报告长得一模一样（同样有
           「检验项目/结果/参考值」），会把单栏机构错路由到双栏模板，
           然后抽出 0 项。
        """
        # 必须扫**全文档**：本机构报告里锚点串全在化验页（第 10/12 页），
        # 只看前几页会 0/3 命中，把自家报告判成 UNKNOWN_LAYOUT。
        text = "\n".join(doc[i].get_text() for i in range(doc.page_count))
        page = doc[0]
        size = [round(page.rect.width, 1), round(page.rect.height, 1)]

        best, best_score, best_reason = None, 0.0, "无模板命中"
        for t in self.templates:
            # 机构专属字样缺一不可 —— 否则别家报告会被通用表头词带进来
            if any(r not in text for r in t.required_anchors):
                continue
            hits = sum(1 for s in t.header_anchor_strings if s in text)
            anchor = hits / max(1, len(t.header_anchor_strings))
            size_ok = (abs(size[0] - t.page_size[0]) < 2
                       and abs(size[1] - t.page_size[1]) < 2)
            obs = observed_column_groups(doc, t.lab.header_cells)
            # **栏数是硬门槛，不是打分项**：观测到 1 栏而模板写 2 栏，
            # 这个模板根本不可能解析这份文档，直接淘汰。
            # 若只当作扣分项，单栏机构会因为锚点串与页尺寸都相符而拿到 0.8 分，
            # 被错路由到双栏模板，然后抽出 0 项。
            if obs and obs != t.lab.column_groups:
                continue
            score = 0.6 * anchor + (0.2 if size_ok else 0.0) + 0.2
            if score > best_score:
                best, best_score = t, score
                best_reason = (f"锚点 {hits}/{len(t.header_anchor_strings)}"
                               f"，页尺寸{'相符' if size_ok else '不符'}"
                               f"，栏数观测 {obs} vs 模板 {t.lab.column_groups}")
        if best_score < min_score:
            return MatchResult(None, best_score,
                               f"最高分 {best_score:.2f} < {min_score:.2f}"
                               f"（{best_reason}）→ UNKNOWN_LAYOUT")
        return MatchResult(best, best_score, best_reason)


def observed_column_groups(doc, header_cells: list[str]) -> int:
    """扫全文，用「单行里表头词最多出现几个 ÷ 3」推断栏数。"""
    cells = set(header_cells)
    best = 0
    for i in range(doc.page_count):
        for row in rows_of_page(doc[i], i + 1):
            n = sum(1 for w in row.words if w.text in cells)
            if n > best:
                best = n
    return best // 3 if best >= 3 else 0


# ---------------------------------------------------------------- 便捷

def default_registry(base_dir: str) -> TemplateRegistry:
    """加载模板：**包内** ``<base>/catalog/templates/`` + **用户侧**模板目录。

    任一文件加载失败 → 打印"原因 + 下一步"并以**退出码 2** 结束（与
    SKILL.md「非零退出怎么办」的口径一致），而不是抛裸 traceback。
    ``*.accept.json``（验收档案）会被自动跳过（兼容早期版本落在同目录的档案）。

    用户侧目录（你的机构版式**不进分发包**，放本地即可）：

    1. 环境变量 ``CHECKUP_TEMPLATES`` 指向的目录；或
    2. 数据根下的 ``templates/``；或
    3. 工作区布局 ``<数据根>/checkup-engine/catalog/templates/``。
    """
    try:
        reg = _load_all(base_dir)
    except TemplateLoadError as e:
        print(f"✗ 模板加载失败：{e}\n"
              f"  处置：把该文件移出模板目录；或修好它（模板字段需与 "
              f"checkup.templates.Template 一致）。\n"
              f"  提示：验收档案（*{ARCHIVE_SUFFIX}）已自动跳过，不需要手工清理。",
              file=sys.stderr)
        raise SystemExit(2) from None
    for org, vs in reg.ambiguity():
        print(f"⚠ **版本并存有歧义**：{org} 有 {len(vs)} 个模板版本 {vs} 同时在模板目录里。"
              f"``valid_from`` 不参与路由 → 分数并列时按加载顺序取胜。\n"
              f"  处置：一机构只留**当前版**，旧版移到模板目录之外（加载器只扫一层 "
              f"*.json）。", file=sys.stderr)
    return reg


def _load_all(base_dir: str) -> TemplateRegistry:
    """内部：包内目录 + 用户侧目录，任一失败即抛 :class:`TemplateLoadError`。"""
    reg = TemplateRegistry.load(os.path.join(base_dir, "catalog", REGISTRY_DIR))
    for d in _extra_template_dirs():
        # 两侧**同一策略**：坏文件就停下、报出是哪个文件（不静默 continue ——
        # 那会让"用户自备模板没生效"无声无息，与"缺数据=跳过"是两码事）
        extra = TemplateRegistry.load(d)
        for t in extra.templates:
            if all(t.org_id != x.org_id or t.template_version != x.template_version
                   for x in reg.templates):
                reg.templates.append(t)
    return reg


def _extra_template_dirs() -> list[str]:
    """用户侧模板目录（不存在即跳过；不写死任何人的目录名）。"""
    env = os.environ.get("CHECKUP_TEMPLATES")
    if env:
        return [os.path.abspath(os.path.expanduser(env))]
    out: list[str] = []
    try:
        import devpaths  # 引擎根在 sys.path 上（入口脚本/测试都会加）
        root = devpaths.FIXTURES.root
    except Exception:
        root = None
    if root:
        out.append(os.path.join(root, "templates"))
        out.append(os.path.join(root, "checkup-engine", "catalog", REGISTRY_DIR))
    return out


def compile_patterns(t: Template) -> dict[str, Any]:
    """把模板里的正则字符串编译好，避免每次调用都重编译。"""
    return {
        "lab_noise": re.compile(t.lab.noise_pattern),
        "nar_noise_row": re.compile(t.narrative.noise_row_pattern),
        "nar_noise_inline": re.compile(t.narrative.noise_inline_pattern),
    }
