# -*- coding: utf-8 -*-
"""VLM 适配层：**只做两件事** —— 起草提案 / 第二读者。绝不直接产出报告数值。

设计纪律（见 REVIEW/推广 讨论）：

1. **VLM 不当抽取器**。它的输出只有两种去处：
   * 起草：给出"版面假设"的**提案**（列位/页标记/段标题列），必须过
     :mod:`checkup.accept` 的验收链才算数；
   * 第二读者：独立读一遍行内容，**只用于与引擎读数比对**；不一致 → 人工对照卡。
   任何情况下 VLM 的读数都不写进产物 —— "可复现/可审计/不编造"三条靠确定性引擎守。
2. **默认不联网**。``provider="openai"`` 必须显式开 ``allow_network``
   （CLI ``--allow-network`` 或 ``CHECKUP_VLM_ALLOW_NETWORK=1``），
   并在发送前把"发什么、发到哪、用什么模型"打印出来。真实报告默认不出本机。
3. **可复现**：温度 0、记录模型名与输入指纹；``provider="replay"`` 用一个
   预录响应文件回放，**离线**跑通与测试（免得把测试绑在网络上）。

环境变量：

    CHECKUP_VLM_PROVIDER   agent（默认：**由跑本 skill 的 agent 本人读图作答**）
                           | replay | openai | none
    CHECKUP_VLM_BASE       OpenAI 兼容端点，如 http://127.0.0.1:11434/v1
    CHECKUP_VLM_MODEL      模型名（记录进验收档案）
    CHECKUP_VLM_KEY        API Key（可选；本地模型多半不需要）
    CHECKUP_VLM_REPLAY     replay 模式的响应文件：{"<sha1(png)>": {...}} 或
                           {"default": {...}}
    CHECKUP_VLM_ALLOW_NETWORK  1 = 允许联网（等价于 CLI --allow-network）
    CHECKUP_VLM_MAX_ROWS   第二读者最多复核多少行（默认 12）
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

#: 第二读者提示词：只读，不推断
READ_ROW_PROMPT = (
    "这是一份体检报告表格里的一行（截图）。请**只读图**，输出严格 JSON：\n"
    '{"name": "项目名原文", "value": 数字或 null, "unit": "单位原文或空串", '
    '"ref": "参考区间原文或空串", "flag": "提示/旗标原文或空串"}\n'
    "规则：读不清的字段填 null；**不要推断、不要补全、不要换算单位**；"
    "只输出 JSON，不要解释。")

#: 起草提示词：给版面假设，不给数值
DRAFT_PROMPT = (
    "这是某体检机构报告的页面截图（可能有多页，按顺序给出）。"
    "请给出**化验表格的版面假设**，输出严格 JSON：\n"
    '{"page_marker": "判断"这一页是化验页"的通用短标记（如 检验报告单）",\n'
    ' "header_cells": ["表头词1","表头词2","表头词3"],\n'
    ' "flag_cells": ["提示列表头词，如 提示/状态；没有则空数组"],\n'
    ' "column_groups": 1 或 2（一页里并排几组"项目/结果/参考值"）,\n'
    ' "section_x_max": 段标题所在列右边界（页面坐标 pt，左上为原点）,\n'
    ' "required_anchors": ["机构专属字样（如机构名）"],\n'
    ' "confidence": 0-1 的小数,\n'
    ' "notes": "不确定的地方用一句话说明"}\n'
    "坐标系：左上为原点，单位 pt。**看不清的字段填 null**；不要猜数值。")


class VlmUnavailable(RuntimeError):
    """VLM 不可用（未配置 / 未允许联网）。消息里给"怎么配"。"""


@dataclass
class VlmConfig:
    provider: str = "agent"      # 默认：**agent 本人**（它本来就能看图）
    base: str = ""
    model: str = ""
    key: str = ""
    replay_path: str = ""
    answers_path: str = ""       #: agent 模式：agent 写下的 answers.json
    allow_network: bool = False
    timeout: int = 60

    @property
    def available(self) -> bool:
        if self.provider == "agent":
            # agent 模式**始终可用**：读图能力由宿主 agent 提供（能读就答，
            # 读不了图就提示用户换支持视觉的模型 —— 见 SKILL.md，不许静默跳过）
            return True
        if self.provider == "replay":
            return bool(self.replay_path) and os.path.isfile(self.replay_path)
        if self.provider == "openai":
            return bool(self.base and self.model)
        return False


def load_config(allow_network: bool = False) -> VlmConfig:
    """从环境变量读配置（``allow_network`` 或 ``CHECKUP_VLM_ALLOW_NETWORK=1``）。"""
    env_net = os.environ.get("CHECKUP_VLM_ALLOW_NETWORK", "") not in ("", "0")
    return VlmConfig(
        provider=(os.environ.get("CHECKUP_VLM_PROVIDER", "agent") or "agent").lower(),
        base=os.environ.get("CHECKUP_VLM_BASE", ""),
        model=os.environ.get("CHECKUP_VLM_MODEL", ""),
        key=os.environ.get("CHECKUP_VLM_KEY", ""),
        replay_path=os.environ.get("CHECKUP_VLM_REPLAY", ""),
        answers_path=os.environ.get("CHECKUP_VLM_ANSWERS", ""),
        allow_network=bool(allow_network or env_net))


class VlmClient:
    """VLM 客户端（起草提案 + 第二读者）。**永不写入产物**。"""

    def __init__(self, config: Optional[VlmConfig] = None):
        self.cfg = config or load_config()
        self.sent_bytes = 0
        self.calls = 0
        self._replay: Optional[dict] = None

    # ------------------------------------------------------------ 可用性/隐私

    @property
    def available(self) -> bool:
        return self.cfg.available

    def describe(self) -> str:
        if not self.available:
            return f"未启用（provider={self.cfg.provider}）"
        if self.cfg.provider == "agent":
            return (f"**agent 本人**（读行图作答；答案文件 "
                    f"{os.path.basename(self.cfg.answers_path) if self.cfg.answers_path else '未指定'}"
                    f"）· 已读 {self.calls} 条")
        if self.cfg.provider == "openai":
            return (f"openai 兼容端点 {self.cfg.base} · 模型 {self.cfg.model} · "
                    f"已发送 {self.calls} 次 / {self.sent_bytes} 字节（**只发页面截图**）")
        return f"replay（离线回放 {os.path.basename(self.cfg.replay_path)}）· {self.calls} 次"

    def _guard_network(self, n_bytes: int) -> None:
        if self.cfg.provider != "openai":
            return
        if not self.cfg.allow_network:
            raise VlmUnavailable(
                "VLM 需要联网但未授权。默认**不把报告发出去**。\n"
                "  要启用：加 --allow-network，或设 CHECKUP_VLM_ALLOW_NETWORK=1\n"
                f"  本次将发送：页面截图（约 {n_bytes} 字节）到 {self.cfg.base}"
                f"（模型 {self.cfg.model}）—— 只有渲染后的**图片**，无其它文件")
        print(f"  [VLM] 将发送页面截图（约 {n_bytes} 字节）到 {self.cfg.base}"
              f"（模型 {self.cfg.model}）—— 仅图片，不含原始文件")

    # ------------------------------------------------------------ 发送

    def _sha(self, png: bytes) -> str:
        return hashlib.sha1(png).hexdigest()

    def _call(self, png: bytes, prompt: str) -> Optional[dict]:
        """一次调用：replay 回放 / openai 兼容 HTTP。返回解析后的 dict。"""
        self._guard_network(len(png))
        self.calls += 1
        self.sent_bytes += len(png)
        if self.cfg.provider == "replay":
            if self._replay is None:
                with open(self.cfg.replay_path, encoding="utf-8") as f:
                    self._replay = json.load(f)
            return self._replay.get(self._sha(png)) or self._replay.get("default")
        if self.cfg.provider != "openai":
            raise VlmUnavailable(
                f"VLM provider={self.cfg.provider!r} 不可用。"
                f"配置：CHECKUP_VLM_PROVIDER=openai|replay 等（见 checkup/vlm.py）")
        payload = {
            "model": self.cfg.model,
            "temperature": 0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64,"
                               + base64.b64encode(png).decode("ascii")}},
                ],
            }],
        }
        req = urllib.request.Request(
            self.cfg.base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.cfg.key}"}
                        if self.cfg.key else {})})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return _strip_json(text)

    def _from_answers(self, key: str, png: bytes) -> Optional[dict]:
        """agent 模式：从 answers.json 取该行的作答（键=文件名 → sha1 → default）。"""
        if self._replay is None:
            path = self.cfg.answers_path
            if not path or not os.path.isfile(path):
                return None
            with open(path, encoding="utf-8") as f:
                self._replay = json.load(f)
        got = (self._replay.get(key) or self._replay.get(self._sha(png))
               or self._replay.get("default"))
        if not isinstance(got, dict):
            return None
        keep = ("name", "value", "unit", "ref", "flag")
        out = {k: got.get(k) for k in keep if got.get(k) is not None}
        return out or None

    # ------------------------------------------------------------ 两个任务

    def read_row(self, png: bytes, key: str = "") -> Optional[dict]:
        """第二读者：读一行 → {name,value,unit,ref,flag}；失败返回 None（不猜）。

        ``key``：行图的文件名（agent 模式按它取答案；replay 模式也可用）。
        """
        if self.cfg.provider == "agent":
            got = self._from_answers(key, png)
            self.calls += 1
            return got
        got = self._call(png, READ_ROW_PROMPT)
        if not isinstance(got, dict):
            return None
        keep = ("name", "value", "unit", "ref", "flag")
        out = {k: got.get(k) for k in keep if got.get(k) is not None}
        return out or None

    def propose_template(self, pages: list[bytes], org_id: str,
                         org_name: str = "") -> Optional[dict]:
        """起草提案：返回模板字段的 JSON（**提案**，须过验收链）。"""
        got = self._call(b"\n".join(pages), DRAFT_PROMPT)
        if not isinstance(got, dict) or not got.get("header_cells"):
            return None
        got.setdefault("org_id", org_id)
        if org_name:
            got.setdefault("org_name", org_name)
        return got


# ---------------------------------------------------------------- 工作单（agent 模式）

def write_work_order(out_dir: str, tasks: list[dict], *, kind: str,
                     extra_md: str = "") -> dict:
    """把"请 agent 读图作答"的任务写成工作单，返回路径字典。

    ``tasks``：``[{"key": "p1_r12", "png": b"...", "row_text": "...", "engine": "..."}]``
    产出：``clips/<key>.png`` · ``work_order.md`` · ``tasks.json`` · ``answers.example.json``
    """
    os.makedirs(os.path.join(out_dir, "clips"), exist_ok=True)
    for t in tasks:
        with open(os.path.join(out_dir, "clips", f"{t['key']}.png"), "wb") as f:
            f.write(t["png"])
    with open(os.path.join(out_dir, "tasks.json"), "w", encoding="utf-8") as f:
        # 只落"可公开的字段"：png 字节与 _ 开头的内部引用（行对象/页对象）都不进 JSON
        json.dump([{k: v for k, v in t.items()
                    if k != "png" and not k.startswith("_")} for t in tasks],
                  f, ensure_ascii=False, indent=2)
    example = {t["key"]: {"name": "", "value": None, "unit": "", "ref": "",
                          "flag": ""} for t in tasks}
    answers_path = os.path.join(out_dir, "answers.json")
    if not os.path.exists(answers_path):
        with open(answers_path + ".example", "w", encoding="utf-8") as f:
            json.dump(example, f, ensure_ascii=False, indent=2)
    prompt = READ_ROW_PROMPT if kind == "read_rows" else DRAFT_PROMPT
    md = [f"# VLM 工作单（{kind}）—— 由 agent 本人完成", "",
          "> 这份工作单是给**跑本 skill 的 agent** 的：读 `clips/` 下的图，把答案写进",
          f"> `{os.path.basename(answers_path)}`（键=文件名去掉 .png），然后重跑验收命令。",
          "> 若**你的模型不支持图像输入** → 停下来告诉用户：",
          "> 『当前模型不支持图像识别，请切换到支持视觉的模型，或配置 CHECKUP_VLM_* 外部端点』",
          "> —— ⛔ 不要静默跳过（VLM 是模板验收的『第二读者』，跳过等于少一道关）。", ""]
    if extra_md:
        md += [extra_md, ""]
    md += ["## 任务", "", "| 键 | 行原文（供对照，**不要照抄**，以图为准） |",
           "|---|---|"]
    for t in tasks:
        md.append(f"| `{t['key']}` | {t.get('row_text', '')[:80]} |")
    md += ["", "## 提示词与输出格式", "", "```text", prompt, "```", "",
           "答案文件示例见 `answers.json.example`。"]
    with open(os.path.join(out_dir, "work_order.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return {"work_order": os.path.join(out_dir, "work_order.md"),
            "clips": os.path.join(out_dir, "clips"),
            "answers": answers_path,
            "tasks": os.path.join(out_dir, "tasks.json")}


# ---------------------------------------------------------------- 解析/构造

def _strip_json(text: str) -> Optional[dict]:
    """从模型回复里抠出 JSON（容忍 ``` 代码块与前后废话）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:] if t.lower().startswith("json") else t
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except Exception:  # noqa: BLE001
        return None


def template_from_proposal(proposal: dict, pdf_path: str,
                           org_id: str = "", org_name: str = "") -> Any:
    """把 VLM 提案转成 :class:`checkup.templates.Template`（缺字段用默认值补齐）。

    ⚠️ 只是**提案**：必须过 ``checkup.accept.accept()`` 才能落盘使用。
    """
    import fitz  # noqa: PLC0415

    from .templates import LabTemplate, NarrativeTemplate, Template  # noqa: PLC0415

    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        size = [round(page.rect.width, 1), round(page.rect.height, 1)]
    finally:
        doc.close()
    lab_kw = {}
    for k in ("page_marker", "header_cells", "flag_cells", "column_groups",
              "section_x_max"):
        v = proposal.get(k)
        if v not in (None, "", []):
            lab_kw[k] = v
    anchors = [a for a in (proposal.get("required_anchors") or []) if a]
    return Template(
        org_id=org_id or proposal.get("org_id") or "VLM-001",
        org_name=org_name or proposal.get("org_name") or "",
        template_version=1,
        page_size=size,
        header_anchor_strings=list(lab_kw.get("header_cells", [])),
        required_anchors=anchors,
        lab=LabTemplate(**lab_kw),
        narrative=NarrativeTemplate(),
        stats={},
        notes=[f"VLM 起草提案（模型 {proposal.get('_model', '?')}）；"
               f"conf={proposal.get('confidence')}；{proposal.get('notes', '')}"],
    )
