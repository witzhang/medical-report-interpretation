# -*- coding: utf-8 -*-
"""输入预检（intake）：**先判"这是什么、能不能读"，再决定要不要往下走**。

设计目标（P2-6）：把"不支持"从一句报错变成 **"原因 + 下一步"**。
每条判定都给：`kind`（机器可判的类型）、`usable`（能不能用）、`degraded`（可用但
风险高）、`reason`（为什么）、`next_step`（用户该做什么）。

判定类型：

======  ============================================  ==========
kind    含义                                          可用性
======  ============================================  ==========
pdf_text        文字层 PDF（矢量文本 + 坐标）            ✅ 全承诺
pdf_pseudo_text 疑似 OCR 叠层（有文字层，但来自图像识别）⚠️ 降级（需显式 --allow-degraded）
pdf_scan        扫描件/图片 PDF（无文字层）             ❌ 无 OCR 通道
pdf_encrypted   加密 / 受保护 PDF                      ❌ 先解锁
ofd             OFD 电子单据                            ❌ 需导出 PDF
docx            Word 文档                               ❌ 需 PDF 版
xlsx / csv      表格                                    ❌ 需 PDF 版
image           图片（jpg/png/heic…）                   ❌ 需电子版
unknown         未知格式                                ❌
======  ============================================  ==========

判"疑似 OCR 叠层"的依据（强信号任一命中即判，弱信号只作提示）：
* 文本的**渲染模式为不可见**（render mode 3）——扫描件 + OCR 叠层的典型做法；
* 字体名含 ``glyphless`` / ``ocr`` / ``hidden`` / ``tesseract`` 等标记；
* （弱）页面被大图覆盖且仍有文字 —— 只提示，不改判定。
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Optional

#: 遇到不可用输入时的统一"下一步"话术（可直接转给用户/机构客服）
NEXT_STEP = {
    "pdf_scan": ("请到体检机构的 App / 微信公众号 / 官网下载**电子版 PDF**"
                 "（多数机构都提供，且在“报告下载/电子报告”入口）；"
                 "客服话术：『请把我的体检电子报告 PDF 发我一份』。"
                 "本工具不做 OCR —— 拍照/扫描件的数字无法逐字核对，宁可停下也不猜。"),
    "pdf_pseudo_text": ("这份 PDF 有文字层，但检测到它来自**图像识别（OCR 叠层）**，"
                        "数字可能已经读错。请提供机构导出的**电子原件**；"
                        "如确需继续，用 --allow-degraded 显式接受降级"
                        "（报告会标注数据质量等级，建议只作参考）。"),
    "pdf_encrypted": "文件有密码/权限限制，无法读取文字层。请解除保护后重试（或向机构索要未加密版）。",
    "ofd": "OFD 暂不支持。请用机构客户端把报告导出为 PDF（电子版），再重试。",
    "docx": "Word 暂不支持。请提供 PDF 版报告（机构 App 下载的电子报告通常是 PDF）。",
    "xlsx": "表格暂不支持。请提供 PDF 版报告，以便脚本从版面逐项取数。",
    "csv": "CSV 暂不支持。请提供 PDF 版报告。",
    "image": "图片没有文字层，无法逐字核对。请提供电子版 PDF。",
    "unknown": "未知格式。请提供体检机构导出的 PDF（电子报告）。",
}

KIND_LABEL = {
    "pdf_text": "文字层 PDF",
    "pdf_pseudo_text": "疑似 OCR 叠层 PDF",
    "pdf_scan": "扫描件/图片 PDF（无文字层）",
    "pdf_encrypted": "加密/受保护 PDF",
    "ofd": "OFD 电子单据",
    "docx": "Word 文档",
    "xlsx": "Excel/表格",
    "csv": "CSV 表格",
    "image": "图片",
    "companion": "非报告文件（忽略）",
    "unknown": "未知格式",
}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PSEUDO_FONT_HINTS = ("glyphless", "ocr", "hidden", "tesseract", "ocrmypdf", "aipdf", "scan")
#: 一页的文本少于此值且有大图 → 该页判为扫描页
PAGE_TEXT_MIN = 40
#: 整份 PDF 的文本少于该值 → 判为"没有可用文字层"（真实报告都在千字量级）
DOC_TEXT_MIN = 120


@dataclass
class FileVerdict:
    """一份输入文件的预检结论。"""

    path: str
    kind: str
    usable: bool
    degraded: bool = False
    ignored: bool = False      #: 非报告文件（.txt 真源/脚本/文档）——不算"不可用"
    reason: str = ""
    next_step: str = ""
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)

    def to_dict(self) -> dict:
        d = {"file": os.path.basename(self.path), "kind": self.kind,
             "label": self.label, "usable": self.usable, "degraded": self.degraded,
             "reason": self.reason, "next_step": self.next_step}
        if self.ignored:
            d["ignored"] = True
        if self.details:
            d["details"] = self.details
        return d


# ---------------------------------------------------------------- 单项判定

def _zip_has(path: str, member: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        return any(n == member or n.endswith("/" + member) for n in names)
    except Exception:  # noqa: BLE001
        return False


def _verdict(path: str, kind: str, usable: bool, *, degraded: bool = False,
             reason: str = "", details: Optional[dict] = None) -> FileVerdict:
    return FileVerdict(path=path, kind=kind, usable=usable, degraded=degraded,
                       reason=reason, next_step=NEXT_STEP.get(kind, ""),
                       details=details or {})


def classify_pdf(path: str) -> FileVerdict:
    """PDF 细分：文字层 / 疑似 OCR 叠层 / 扫描件 / 加密。"""
    import fitz  # noqa: PLC0415

    try:
        doc = fitz.open(path)
    except Exception as e:  # noqa: BLE001
        return _verdict(path, "unknown", False, reason=f"PDF 打开失败：{e}")

    try:
        if getattr(doc, "needs_pass", 0):
            return _verdict(path, "pdf_encrypted", False,
                            reason="文件已加密（needs_pass=1）")
        pages = doc.page_count
        texts = [doc[i].get_text() for i in range(pages)]
        total_chars = len("".join(texts).strip())
        scanned_pages = [i + 1 for i in range(pages)
                         if len(texts[i].strip()) < PAGE_TEXT_MIN
                         and len(doc[i].get_images()) > 0]
        # 疑似 OCR 叠层的强信号（**先判它**：OCR 扫描件同时有"大图 + 隐藏文本"，
        # 若先按字数判扫描件，这类会被误判成"完全没文字"）
        # 判定"没有可用文字层"：**逐页看**有没有任一页够得上文字层（40 字符）。
        # 不再用总字数阈值——那会误伤单页/小样本（测试用的迷你 PDF 就被误判过）。
        pages_with_text = [i + 1 for i in range(pages)
                           if len(texts[i].strip()) >= PAGE_TEXT_MIN]
        _scan_like = not pages_with_text
        invisible = normal = 0
        fonts: set[str] = set()
        for i in range(pages):
            for span in doc[i].get_texttrace():
                rt = span.get("type", 0)
                if rt == 3:
                    invisible += 1
                else:
                    normal += 1
                f = (span.get("font") or "").strip()
                if f:
                    fonts.add(f)
        spans = invisible + normal
        hint = sorted(f for f in fonts
                      if any(h in f.lower() for h in PSEUDO_FONT_HINTS))
        invisible_ratio = (invisible / spans) if spans else 0.0
        big_image = False
        for i in range(pages):
            page_area = abs(doc[i].rect.get_area()) or 1
            for img in doc[i].get_images(full=True):
                try:
                    cov = abs(doc[i].get_image_bbox(img).get_area()) / page_area
                except Exception:  # noqa: BLE001
                    continue
                if cov >= 0.6:
                    big_image = True
        strong = None
        if total_chars >= 20 and invisible_ratio >= 0.5:
            strong = f"文本渲染模式不可见（{invisible_ratio:.0%} 的文本片段是隐藏层）"
        elif total_chars >= 20 and hint:
            strong = f"字体名含 OCR/隐藏层标记：{', '.join(hint)}"
        if strong:
            return _verdict(
                path, "pdf_pseudo_text", True, degraded=True,
                reason=f"疑似 OCR 叠层：{strong} —— 数字可能来自图像识别、与原报告不一致",
                details={"pages": pages, "chars": total_chars,
                         "invisible_ratio": round(invisible_ratio, 3),
                         "fonts": sorted(fonts)[:8]})
        if _scan_like:
            return _verdict(path, "pdf_scan", False,
                            reason=f"没有一页有可用文字层（共 {total_chars} 字符；"
                                   f"{len(scanned_pages)}/{pages} 页为图片）",
                            details={"pages": pages, "chars": total_chars,
                                     "scanned_pages": scanned_pages})
        return _verdict(
            path, "pdf_text", True,
            reason=(f"文字层正常（{total_chars} 字符 / {pages} 页，"
                    f"{len(pages_with_text)} 页有文字层）"
                    + ("；⚠ 页面含大图覆盖，若为扫描套壳请留意" if big_image else "")),
            details={"pages": pages, "chars": total_chars,
                     "scanned_pages": scanned_pages,
                     "big_image": big_image})
    finally:
        doc.close()


def classify(path: str) -> FileVerdict:
    """一份文件 → 预检结论（不抛异常：任何异常都落成 unknown 判定）。"""
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if not os.path.isfile(path):
        return _verdict(path, "unknown", False, reason="文件不存在")
    try:
        if ext == ".pdf":
            return classify_pdf(path)
        if ext == ".ofd":
            if _zip_has(path, "OFD.xml"):
                return _verdict(path, "ofd", False, reason="OFD 电子单据（版式文档）")
            return _verdict(path, "unknown", False, reason="扩展名是 .ofd 但内容不是 OFD 包")
        if ext == ".docx":
            if _zip_has(path, "word/document.xml"):
                return _verdict(path, "docx", False, reason="Word 文档")
            return _verdict(path, "unknown", False, reason=".docx 内容异常")
        if ext in (".xlsx", ".xlsm"):
            if _zip_has(path, "xl/workbook.xml"):
                return _verdict(path, "xlsx", False, reason="Excel 工作簿")
            return _verdict(path, "unknown", False, reason=".xlsx 内容异常")
        if ext in (".xls", ".csv", ".tsv"):
            return _verdict(path, "csv", False, reason=f"表格文件（{ext}）")
        if ext in IMAGE_EXT:
            return _verdict(path, "image", False, reason=f"图片文件（{ext}）")
        if ext in (".txt", ".md", ".py", ".json", ".log", ".yml", ".yaml",
                   ".zip", ".tar", ".gz"):
            # 数据真源/脚本/文档等伴随文件：不是报告输入，**忽略**（不算不可用）
            v = _verdict(path, "companion", True,
                         reason=f"伴随文件（{ext}），不是体检报告输入")
            v.ignored = True
            return v
        return _verdict(path, "unknown", False, reason=f"未识别的扩展名 {ext}")
    except Exception as e:  # noqa: BLE001
        return _verdict(path, "unknown", False, reason=f"预检异常：{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 目录级

#: 目录扫描时忽略的杂项（缓存/隐藏文件/DOS 垃圾）
IGNORE_PREFIX = (".", "_")
IGNORE_SUFFIX = (".ds_store",)


def scan_dir(src: str) -> list[FileVerdict]:
    """扫一个目录（不递归）→ 逐文件预检结论（按文件名排序）。"""
    out: list[FileVerdict] = []
    for name in sorted(os.listdir(src)):
        low = name.lower()
        if name.startswith(IGNORE_PREFIX) or low.endswith(IGNORE_SUFFIX):
            continue
        out.append(classify(os.path.join(src, name)))
    return out


def summarize(verdicts: list[FileVerdict]) -> dict:
    """汇总：可用 / 降级 / 不可用 各几份（伴随文件不计入）。"""
    real = [v for v in verdicts if not v.ignored]
    usable = [v for v in real if v.usable and not v.degraded]
    degraded = [v for v in real if v.usable and v.degraded]
    blocked = [v for v in real if not v.usable]
    return {"total": len(real), "usable": len(usable),
            "degraded": len(degraded), "blocked": len(blocked),
            "ignored": len(verdicts) - len(real),
            "usable_files": [os.path.basename(v.path) for v in usable],
            "degraded_files": [os.path.basename(v.path) for v in degraded],
            "blocked_files": [os.path.basename(v.path) for v in blocked]}


# ---------------------------------------------------------------------------
# 入口公共层（run_narrative / run_normalize / run_pipeline 共用，别再各写一份）
# ---------------------------------------------------------------------------

def pick_src(cli_src: Optional[str]) -> str:
    """报告目录解析：``--src`` > ``CHECKUP_SRC`` > 数据根的报告目录；缺则**报错退出**。

    报错文案不给任何人的默认目录名 —— 只说清"怎么指定"。
    """
    from devpaths import FIXTURES  # 延迟导入：入口层不引入 import 期副作用
    src = cli_src or os.environ.get("CHECKUP_SRC") or FIXTURES.real_reports
    if not src or not os.path.isdir(src):
        # 退出码 2 是文档化契约（SKILL.md「非零退出怎么办」表：报告目录不存在 → 2）
        print("✗ 报告目录不存在"
              + (f"：{src}" if src else "")
              + "。\n  用法：--src <报告目录>（含体检报告 PDF）"
                "；或设 CHECKUP_SRC / CHECKUP_FIXTURES 指向数据根。")
        raise SystemExit(2)
    return src


def list_pdfs(src: str) -> list[str]:
    """目录内的 PDF 全路径（**大小写不敏感**、按文件名排序）。

    **全链路唯一的 PDF 判定**：不要再写 ``glob("*.pdf")`` —— 它在 Windows 上
    大小写不敏感、在 macOS/Linux 上敏感，同一目录会得到不同的文件集。
    """
    return [os.path.join(src, n) for n in sorted(os.listdir(src))
            if n.lower().endswith(".pdf")]


def require_year(filename: str) -> str:
    """从文件名取 4 位年份；取不到 → **停下报错**（年份是下游全链路的约定）。"""
    m = re.search(r"(\d{4})", filename)
    if not m:
        raise SystemExit(
            f"✗ 文件名里找不到 4 位年份：{filename}\n"
            f"  年份从**文件名**读取（用于区分年份；体检日期与机构仍从内页读）。\n"
            f"  处置：把文件改名带上年份，例如「2024 体检报告.pdf」。")
    return m.group(1)


def check_filenames(src: str) -> None:
    """文件名体检：每份 PDF 都要有 4 位年份；同一年份只允许一份。"""
    seen: dict[str, str] = {}
    for path in list_pdfs(src):
        fn = os.path.basename(path)
        year = require_year(fn)
        if year in seen:
            raise SystemExit(
                f"✗ 同一年份出现两份报告：{seen[year]} 与 {fn}\n"
                f"  每一年份只应有一份报告（否则后一份会**静默覆盖**前一份）。\n"
                f"  处置：移出多余文件，或改名区分年份。")
        seen[year] = fn


def preflight_or_exit(src: str, allow_degraded: bool = False) -> None:
    """**入口自动预检**（P2-6）：不问、不跳，收到输入第一件事就是判它能不能读。

    * 文件名年份检查（``check_filenames``）；
    * 不可用（扫描件/加密/OFD/表格/图片…）→ 打印原因与下一步，非零退出；
    * 疑似 OCR 叠层（数字可能来自图像识别）→ 需显式 ``--allow-degraded`` 才继续；
    * 可用 → 打印一行汇总继续跑。
    """
    check_filenames(src)
    files = list_pdfs(src)
    if not files:
        raise SystemExit(f"✗ 报告目录里没有 PDF：{src}")
    verdicts = [classify(p) for p in files]
    bad = [v for v in verdicts if not v.usable]
    deg = [v for v in verdicts if v.usable and v.degraded]
    ok = [v for v in verdicts if v.usable and not v.degraded]
    if bad:
        print("✗ 输入预检未通过（**停下，不要硬跑**）：")
        for v in bad:
            print(f"   · {os.path.basename(v.path)} —— {v.label}：{v.reason}")
            if v.next_step:
                print(f"     下一步：{v.next_step}")
        raise SystemExit(2)
    if deg and not allow_degraded:
        print("⚠ 输入里有『疑似 OCR 叠层』（数字可能来自图像识别，与原报告不一致）：")
        for v in deg:
            print(f"   · {os.path.basename(v.path)}：{v.reason}")
            print(f"     下一步：{v.next_step}")
        raise SystemExit(2)
    note = f"✓ 输入预检：{len(ok)} 份文字层 PDF"
    if deg:
        note += f" + {len(deg)} 份**降级**（OCR 叠层，已按 --allow-degraded 继续）"
    print(note)
