#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**首见自动验收 + 分套餐基线**（D3）。

    python3 engine/tools/first_sight.py <新报告.pdf …> [--org <机构>] \
        [--package "心脑套餐"] [--out <产物目录>] [--write-baseline]

每份新报告第一次入池时自动跑一遍验收链（``accept()``）：

* **绿 → 归档**：写验收档案 + 把"机构 / 套餐 / 项数"记进样本池索引
  （``<out>/pool_index.json``），并（``--write-baseline`` 时）把该套餐的项数
  写进模板的 ``stats.package_baseline``；
* **红 → 报警**：非零退出 + 打印是哪条证据不绿（不静默入池）。

为什么必须自动跑：模板验收是"上模板时跑一次"的动作，而**新报告本身**才是
暴露改版的输入（改版不报错、只是悄悄少抽几项）。首见入库时把这条闸门关上，
改版就会在"进池那一刻"被抓出来，而不是等到报告写歪。

退出码：0 = 全绿已归档；1 = 有报告验收不通过（报警）；2 = 该机构**还没有模板**
（先跑 onboard_scan.py / accept_template.py）；3 = 用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
SKILL = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)

import fitz  # noqa: E402

from checkup import accept as ACCEPT  # noqa: E402
from checkup import assets  # noqa: E402
from checkup.pipeline import route_document  # noqa: E402

from checkup.console import init_console  # noqa: E402
init_console()

from devpaths import default_out_dir, guard_out  # noqa: E402


def _pool_index(path: str) -> dict:
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"orgs": {}}


def main() -> int:
    ap = argparse.ArgumentParser(description="首见自动验收：绿归档、红报警 + 分套餐基线")
    ap.add_argument("pdfs", nargs="+", help="新报告（首次入池的那份/那批）")
    ap.add_argument("--template", default="", help="直接指定模板（缺省按指纹路由）")
    ap.add_argument("--org", default="", help="机构 ID（只用于提示与索引）")
    ap.add_argument("--package", default="", help="套餐名（分套餐基线用，可空）")
    ap.add_argument("--out", default="", help="产物目录（缺省 <产物根>/first_sight）")
    ap.add_argument("--write-baseline", action="store_true",
                    help="把本次项数写进模板 stats.package_baseline（模板在包外时"
                         "写到产物目录）")
    args = ap.parse_args()

    out = guard_out(args.out or os.path.join(default_out_dir(), "first_sight"))
    os.makedirs(out, exist_ok=True)
    if args.template:
        from checkup.templates import Template  # noqa: PLC0415
        tpl = Template.from_dict(json.load(open(args.template, encoding="utf-8")))
        tpls = [tpl]
        missing = []
    else:
        tpls, missing, seen = [], [], set()
        for p in args.pdfs:
            r = route_document(p)
            if r.template is None:
                missing.append(p)
                continue
            key = (r.template.org_id, r.template.template_version)
            if key not in seen:            # 同一机构一次即可（多份样本一起验）
                seen.add(key)
                tpls.append(r.template)
    if missing:
        print("⚠ 下列报告**没有对应模板**（首次见该机构）：")
        for p in missing:
            print(f"   · {os.path.basename(p)}")
        print("   → 先跑：python3 engine/tools/onboard_scan.py <样本.pdf …> --org NEW-001 --search\n"
              "           python3 engine/tools/accept_template.py --pdfs … --search --install")
        return 2

    bad: list[str] = []
    index_path = os.path.join(out, "pool_index.json")
    index = _pool_index(index_path)
    for tpl in tpls:
        samples = list(args.pdfs)
        # 逐份的项数（分套餐基线记的是"每份报告的项数区间"，不是总和）
        per_sample: dict[str, int] = {}
        for p in samples:
            doc = fitz.open(p)
            try:
                per_sample[os.path.basename(p)] = len(
                    ACCEPT.extract_lab_results(doc, "0000", tpl))
            finally:
                doc.close()
        # **按套餐验收**（D3）：给了套餐就用该套餐自己的基线去比 ——
        # 否则"基础套餐 82 项"会被"心脑套餐 89 项"的基线判成抽漏
        use = tpl
        pkg_base = ((tpl.stats or {}).get("package_baseline") or {}).get(
            args.package) if args.package else None
        if pkg_base:
            use = Template.from_dict(tpl.to_dict())
            use.stats = dict(use.stats or {})
            use.stats["items_per_report"] = sorted(pkg_base)
        rep = ACCEPT.accept(use, samples)
        archive = os.path.join(out, f"{tpl.org_id}.accept.json")
        with open(archive, "w", encoding="utf-8") as f:
            json.dump(rep.to_dict(), f, ensure_ascii=False, indent=2)
        rec = assets.asset_record(tpl, archive, [os.path.basename(p) for p in samples])
        assets.save_asset_record(tpl, out, archive, rec["samples"])
        org = index["orgs"].setdefault(tpl.org_id, {"packages": {}, "samples": []})
        for p in samples:
            name = os.path.basename(p)
            if name not in org["samples"]:
                org["samples"].append(name)
        pkg = args.package or "(未标注套餐)"
        ent = org["packages"].setdefault(pkg, {"items_per_report": [], "samples": []})
        ent["items_per_report"] += [per_sample[n] for n in per_sample]
        ent["samples"] += [os.path.basename(p) for p in samples]
        if rep.verdict == "ok":
            print(f"✓ {tpl.org_id}：{len(samples)} 份样本 · 逐份 "
                  f"{list(per_sample.values())} 项 · 验收全绿 → 已归档"
                  f"（套餐「{pkg}」）")
            print(f"  档案：{archive}")
        else:
            bad.append(tpl.org_id)
            print(f"✗ {tpl.org_id}：验收 **{rep.verdict.upper()}** —— 不静默入池")
            for e in rep.evidences:
                if e.level in ("block", "review"):
                    print(f"   · {e.key} {e.title}：{e.detail}")
        if args.write_baseline:
            # **分套餐基线**（D3）：把本次项数记进该套餐的区间，而不是只留整体中位数
            stats = dict(tpl.stats or {})
            pb = dict(stats.get("package_baseline") or {})
            pb[pkg] = sorted(set(list(pb.get(pkg) or [])
                                 + list(per_sample.values())))
            stats["package_baseline"] = pb
            tpl.stats = stats
            tpl_path = os.path.join(out, f"{tpl.org_id}.json")
            tpl.save(tpl_path)
            print(f"  模板（含分套餐基线 {pb}）已写：{tpl_path}")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"\n样本池索引：{index_path}")
    if bad:
        print(f"✗ 有机构验收不通过：{bad}（改版/抽漏会在这一刻被抓出来）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
