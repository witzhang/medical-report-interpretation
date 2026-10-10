# -*- coding: utf-8 -*-
"""流水线枢纽：四条抽取通道 + 归一 → ``NormalizedObservation[]``。

左边接 L1（``narrative`` / ``tables``），右边供 L3（``validate``）与 L4（``judge``）消费。
中间只做搬运与结构统一，不做医学判断。
"""
from __future__ import annotations

import os
import re
from typing import Optional

import fitz

from .narrative import extract_report
from .normalize import UNMAPPED, AnalyteCatalog
from .schema import NormalizedObservation
from .tables import extract_lab_results, _split_ref
from .templates import (MatchResult, Template, default_registry,
                        require_template)

def _flag(value: Optional[float], low: Optional[float], high: Optional[float]):
    from .tables import _calc_flag
    return _calc_flag(value, low, high, "")


def build_observations(pdf_path: str, year: str,
                       catalog: AnalyteCatalog,
                       template: Template,
                       org_id: Optional[str] = None,
                       ) -> list[NormalizedObservation]:
    """一份报告 → 归一后的观测列表。版面假设全部来自 ``template``。

    模板是**必填**（P0-2）：匹配不上就由调用方按 UNKNOWN_LAYOUT 处理，这里不兜底。
    机构 id **从路由到的模板推导**（P0-1：不再有机构常量）——项目名映射是
    按机构登记的（``catalog.resolve(org_id, …)``），机构错了映射就错了。
    """
    template = require_template(template, "pipeline.build_observations")
    org_id = org_id or template.org_id
    doc = fitz.open(pdf_path)
    out: list[NormalizedObservation] = []

    def emit(source: str, page_no: int, section: str, item: str, occurrence: int,
             value_type: str, value: Optional[float], value_text: str, unit: str,
             ref_raw: str, pdf_flag: str, evidence: str) -> None:
        res = catalog.resolve(org_id, section, item, value_type, unit)
        # 名字与单位矛盾时按显式修复规则重归属（可审计，非隐式启发式）
        if res.unit_status == "mismatch":
            rep = catalog.repair_by_unit(section, res.analyte_id, unit)
            if rep:
                res = rep
        ref_low = ref_high = None
        op = ""
        if ref_raw:
            _, ref_unit, ref_low, ref_high, op = _split_ref(ref_raw)
            if not unit:
                unit = ref_unit
        out.append(NormalizedObservation(
            year=year, page_no=page_no, section=section, item_raw=item,
            occurrence=occurrence, analyte_id=res.analyte_id,
            matched_by=res.matched_by, unit_status=res.unit_status,
            repaired_by=res.repaired_by, source=source,
            value=value, value_text=value_text, value_type=value_type,
            unit=unit, ref_raw=ref_raw, ref_low=ref_low, ref_high=ref_high,
            ref_op=op, flag=_flag(value, ref_low, ref_high),
            pdf_flag=pdf_flag, evidence=evidence))

    # 通道 A1 · 化验
    for r in extract_lab_results(doc, year, template):
        emit("lab", r.page_no, r.section, r.item_raw, r.occurrence, r.value_type,
             r.value, r.result_raw, r.unit_raw, r.ref_raw, r.pdf_flag,
             f"{r.section}/{r.item_raw}={r.result_raw} (p{r.page_no})")

    # 通道 A2 · 标量 + 通道 B · 叙述
    _, narratives, observations, _ = extract_report(pdf_path, year, template)
    for o in observations:
        vt = "numeric" if o.value is not None else "qualitative"
        emit("scalar", o.page_no, o.section or "体格", o.item_raw, 1, vt,
             o.value, o.value_text, o.unit or "", "", "",
             f"{o.section}/{o.item_raw}={o.value_text} (p{o.page_no})")
    for it in narratives:
        meas = it.measurements[0].raw if it.measurements else it.description_verbatim[:24]
        emit("narrative", it.page_no, it.modality or "影像", it.item, 1, "narrative",
             None, it.description_verbatim, "", "", "",
             f"{it.modality}/{it.item}: {meas} (p{it.page_no})")

    doc.close()
    return out


def route_document(pdf_path: str, registry=None) -> MatchResult:
    """把一份报告路由到模板。匹配不上就返回 None —— **不猜**。"""
    registry = registry if registry is not None else default_registry(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    doc = fitz.open(pdf_path)
    try:
        return registry.match(doc)
    finally:
        doc.close()


def require_routed_template(pdf_path: str, registry=None) -> Template:
    """开发脚本用：按指纹取该 PDF 的模板。

    匹配不上 → **报错退出**（P0-2/P0-7：不许回落内置模板；宁可停下报
    UNKNOWN_LAYOUT，也不拿别的机构版式硬套）。
    """
    res = route_document(pdf_path, registry)
    if res.template is None:
        raise SystemExit(
            f"✗ 未能识别该报告版式（机构/版式未注册）：{os.path.basename(pdf_path)}\n"
            f"  {res.reason}\n"
            f"  处置：draft_template.py 起草该机构模板并人工确认后重跑，"
            f"或用 --org 指定已注册模板。")
    return res.template


def year_from_name(filename: str) -> Optional[str]:
    """从文件名取年份（4 位数字）；取不到返回 ``None``。

    年份**只从文件名读**（下游全链路的约定；体检日期与机构从内页读）。
    入口预检（``intake.check_filenames``）会提前拦下取不到年份的文件；
    这里返回 ``None`` 只用于库层的防御性分支。
    """
    m = re.search(r"(\d{4})", filename)
    return m.group(1) if m else None


def build_all(src_dir: str, catalog: AnalyteCatalog,
              org_id: Optional[str] = None,
              registry=None,
              routing: Optional[dict] = None,
              force_org: Optional[str] = None
              ) -> dict[str, list[NormalizedObservation]]:
    """整个目录的报告 → {年份: 观测列表}。

    ``routing`` 若传入，会被填入 {年份: MatchResult}，供调用方展示路由结果。
    模板匹配不上的报告**整份跳过**，绝不拿默认模板硬套。

    ``force_org`` 指定机构模板 id 时**跳过自动匹配**（用于已知机构、或版式
    漂移到匹配不上但你知道该用哪套的场景）。指定的 id 不存在则报错，
    **不静默退回默认模板** —— 那会让整份报告按错的版式解析。
    """
    out: dict[str, list[NormalizedObservation]] = {}
    for fn in sorted(os.listdir(src_dir)):
        if not fn.lower().endswith(".pdf"):
            continue
        year = year_from_name(fn)
        if year is None:     # 入口预检已拦；库层保持容错（跳过无年份文件）
            continue
        path = os.path.join(src_dir, fn)
        if force_org:
            forced = next((t for t in registry.templates
                           if t.org_id == force_org), None)
            if forced is None:
                raise ValueError(
                    f"--org {force_org} 不在模板注册表里；现有："
                    f"{[t.org_id for t in registry.templates]}")
            res = MatchResult(template=forced, score=1.0,
                              reason=f"强制指定 --org {force_org}")
        else:
            res = route_document(path, registry)
        if routing is not None:
            routing[year] = res
        if res.template is None:
            out[year] = []
            continue
        out[year] = build_observations(path, year, catalog,
                                       res.template, org_id)   # org_id=None → 用模板的
    return out
