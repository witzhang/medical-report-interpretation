# -*- coding: utf-8 -*-
"""资产化（D2 分发 + D4 管理）：模板/词表当**资产**管起来。

两件事，一个模块：

**D2 分发**（``export_bundle`` / ``load_bundle``）—— 模板只描述"纸长什么样"、
词表只描述"项目名的叫法"，**都不含个人数据**，因此可以当资产分享：
一份 ``bundle.json`` + 若干 ``<org>.json`` 拷到另一台机器即可加载
（加载优先级沿用已有的 ``CHECKUP_TEMPLATES`` 环境变量，见
``templates._extra_template_dirs``）。导出时**逐项断言不含个人数据**
（样本文件名、报告路径、验收档案里的报告名一律不进包）。

**D4 管理**（``asset_record`` / ``archive_superseded``）—— 模板 = 版本 + 验收档案
sha1 + 样本清单 + 基线，四件齐全才算"可复现的资产"；一机构只留**当前版**，
旧版归档到 ``archive/`` 子目录（加载器只扫一层 ``*.json``，不会读它 ——
这也是 B7 说的"版本并存有歧义"的处置办法）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Optional

from .templates import Template

#: 资产清单文件名
MANIFEST = "bundle.json"
#: 归档子目录（加载器不扫它）
ARCHIVE_DIR = "archive"
#: 清单格式版本（跨机器兼容用）
BUNDLE_FORMAT = 1


def sha1_of(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def _assert_no_personal_data(payload: dict) -> None:
    """**导出前的硬闸门**：清单里不许出现个人数据痕迹。

    检查的是"看起来像个人数据的东西"：PDF/报告文件名、绝对路径、样本名。
    模板字段本身（锚点串是机构名，不是个人信息）不算。
    """
    text = json.dumps(payload, ensure_ascii=False)
    for bad in (".pdf", ".PDF", "/Users/", "C:\\", "报告.pdf"):
        if bad in text:
            raise ValueError(
                f"资产包不许含个人数据痕迹（发现 {bad!r}）—— 样本文件名/报告路径"
                f"请留在验收档案里，不要进 bundle")


def export_bundle(dest: str, templates: list[Template],
                  catalog_path: Optional[str] = None,
                  note: str = "") -> dict:
    """导出一份**可分享**的模板包（不含个人数据）。

    写 ``<dest>/<org>.json`` + ``<dest>/bundle.json``（清单：格式版本、各模板
    版本与 sha1、可选词表）。返回清单 dict。
    """
    os.makedirs(dest, exist_ok=True)
    entries = []
    for t in templates:
        path = os.path.join(dest, f"{t.org_id}.json")
        t.save(path)
        entries.append({"org_id": t.org_id, "org_name": t.org_name,
                        "template_version": t.template_version,
                        "sha1": sha1_of(path),
                        "baseline": (t.stats or {}).get("items_per_report", []),
                        "file": os.path.basename(path)})
    manifest = {"bundle_format": BUNDLE_FORMAT, "note": note,
                "templates": entries}
    if catalog_path and os.path.exists(catalog_path):
        dst = os.path.join(dest, "analytes.json")
        shutil.copyfile(catalog_path, dst)
        manifest["catalog"] = {"file": "analytes.json", "sha1": sha1_of(dst)}
    _assert_no_personal_data(manifest)
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def load_bundle(dest: str) -> tuple[list[Template], dict]:
    """从资产包加载（**校验 sha1**：包被改坏要停下）；返回 (模板, 清单)。"""
    mpath = os.path.join(dest, MANIFEST)
    if not os.path.exists(mpath):
        raise FileNotFoundError(f"不是资产包（缺 {MANIFEST}）：{dest}")
    manifest = json.load(open(mpath, encoding="utf-8"))
    if manifest.get("bundle_format") != BUNDLE_FORMAT:
        raise ValueError(f"资产包格式不支持：{manifest.get('bundle_format')}"
                         f"（本机支持 {BUNDLE_FORMAT}）")
    out: list[Template] = []
    for e in manifest.get("templates", []):
        path = os.path.join(dest, e["file"])
        if not os.path.exists(path):
            raise FileNotFoundError(f"资产包缺文件：{e['file']}")
        got = sha1_of(path)
        if got != e["sha1"]:
            raise ValueError(f"资产包损坏：{e['file']} sha1 不符"
                             f"（清单 {e['sha1'][:8]} vs 实际 {got[:8]}）")
        out.append(Template.from_dict(json.load(open(path, encoding="utf-8"))))
    return out, manifest


# ---------------------------------------------------------------- 资产管理

def asset_record(template: Template, archive_path: str = "",
                 samples: Optional[list[str]] = None) -> dict:
    """一条**资产记录**（D4）：版本 + 验收档案 sha1 + 样本清单 + 基线。

    四件齐全 = 这份模板"是怎么被验证过的"可复现。档案不在时归档 sha1 留空，
    并把 ``archive_missing`` 标出来（不假装齐全）。
    """
    rec = {
        "org_id": template.org_id,
        "org_name": template.org_name,
        "template_version": template.template_version,
        "valid_from": template.valid_from,
        "baseline": (template.stats or {}).get("items_per_report", []),
        "samples": list(samples or []),
        "archive": os.path.basename(archive_path) if archive_path else "",
        "archive_sha1": "",
        "archive_missing": not (archive_path and os.path.exists(archive_path)),
        "notes": list(template.notes),
    }
    if not rec["archive_missing"]:
        rec["archive_sha1"] = sha1_of(archive_path)
        try:
            arch = json.load(open(archive_path, encoding="utf-8"))
            rec["verdict"] = arch.get("verdict", "")
            rec["template_sha1"] = arch.get("template_sha1", "")
            if not rec["samples"]:
                rec["samples"] = list(arch.get("samples") or [])
        except Exception:  # noqa: BLE001
            rec["archive_missing"] = True
    return rec


def save_asset_record(template: Template, out_dir: str,
                      archive_path: str = "",
                      samples: Optional[list[str]] = None) -> str:
    """把资产记录写到 ``<out_dir>/<org>.asset.json``（与验收档案同处）。"""
    rec = asset_record(template, archive_path, samples)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{template.org_id}.asset.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return path


def archive_superseded(tdir: str, org_id: str, keep_version: int) -> list[str]:
    """把某机构的**旧版**移进 ``archive/``（D4：一机构只留当前版）。

    为什么不用删的：模板是资产，旧版还要能查"当年用的哪一版"。
    为什么必须移走：``valid_from`` 不参与路由，多版本并存 = 歧义（B7）。
    返回被归档的文件名清单。
    """
    moved: list[str] = []
    if not os.path.isdir(tdir):
        return moved
    adir = os.path.join(tdir, ARCHIVE_DIR)
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith(".json") or fn.endswith(".accept.json"):
            continue
        path = os.path.join(tdir, fn)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if d.get("org_id") != org_id or d.get("template_version") == keep_version:
            continue
        os.makedirs(adir, exist_ok=True)
        shutil.move(path, os.path.join(adir, fn))
        moved.append(fn)
    return moved
